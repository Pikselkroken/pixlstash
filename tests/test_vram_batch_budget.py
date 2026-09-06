import pytest

from pixlstash.inference.vram_budget import (
    MAX_CONCURRENT_GPU_IMAGES,
    ORT_ARENA_SHARE,
    VramBudget,
    WD14_BASE_MB,
    WD14_PER_ITEM_MB,
)
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.tasks.missing_tag_finder import MissingTagFinder


class _FakeWD14Service:
    def __init__(self, capacity):
        self._capacity = capacity

    def batch_capacity(self):
        return self._capacity


class _FakeEngine:
    def __init__(self, vram_budget, wd14_service, device="cuda"):
        self.vram_budget = vram_budget
        self.wd14_service = wd14_service
        self.device = device


def _build_workflow_for_budget_tests(
    budget_mb: int = 4096,
    onnx_capacity: int = 64,
    use_wd14: bool = True,
    use_pixlstash_tagger: bool = False,
    device: str = "cuda",
):
    vram_budget = VramBudget.__new__(VramBudget)
    vram_budget._device = device
    vram_budget._max_vram_usage_mb = budget_mb
    engine = _FakeEngine(vram_budget, _FakeWD14Service(onnx_capacity), device=device)
    return TaggingWorkflow(
        engine=engine, use_wd14=use_wd14, use_pixlstash_tagger=use_pixlstash_tagger
    )


def test_vram_batch_cap_constrains_by_budget():
    small_budget = _build_workflow_for_budget_tests(budget_mb=2048)
    large_budget = _build_workflow_for_budget_tests(budget_mb=8192)

    cap_small = small_budget._vram_limited_batch_cap(base_mb=900, per_item_mb=220)
    cap_large = large_budget._vram_limited_batch_cap(base_mb=900, per_item_mb=220)

    assert cap_large > cap_small
    assert cap_small >= 1


def test_estimated_task_vram_stays_within_budget_window():
    workflow = _build_workflow_for_budget_tests(
        budget_mb=4096,
        onnx_capacity=64,
    )

    estimate_mb = workflow.estimated_vram_mb(image_count=64)

    assert estimate_mb <= 4096
    assert estimate_mb >= 1200


def test_vram_cap_noop_on_cpu_mode():
    workflow = _build_workflow_for_budget_tests(device="cpu")

    cap = workflow._vram_limited_batch_cap(base_mb=900, per_item_mb=220)

    assert cap == 10_000


def test_suggested_tag_task_size_tracks_effective_batch():
    workflow = _build_workflow_for_budget_tests(
        budget_mb=4096,
        onnx_capacity=64,
    )

    assert workflow.effective_wd14_batch_size() == 10
    assert workflow.effective_pixlstash_tagger_batch_size() == 10
    assert workflow.suggested_task_size() == 10


def test_pixlstash_tagger_and_wd14_use_same_effective_batch_size():
    workflow = _build_workflow_for_budget_tests(
        budget_mb=4096,
        onnx_capacity=64,
    )

    assert (
        workflow.effective_pixlstash_tagger_batch_size()
        == workflow.effective_wd14_batch_size()
    )


def test_incremental_vram_estimate_is_below_full_estimate():
    workflow = _build_workflow_for_budget_tests(
        budget_mb=4096,
        onnx_capacity=64,
    )

    full_estimate = workflow.estimated_vram_mb(image_count=64)
    incremental_estimate = workflow.estimated_incremental_vram_mb(image_count=64)

    assert incremental_estimate < full_estimate
    assert incremental_estimate >= 256


def test_missing_tags_finder_uses_suggested_task_size():
    class FakeTaggingWorkflow:
        def suggested_task_size(self):
            return 3

    class FakeEngine:
        wd14_enabled = True
        pixlstash_tagger_enabled = False
        tagging_workflow = FakeTaggingWorkflow()
        tagger_settings = {"active_tag_plugin": "wd14"}

    class FakeRegistry:
        def active_suppressed_ids(self):
            return set()

        def is_suppressed(self, _picture_id):
            return False

    class FakeDB:
        def __init__(self):
            self.image_root = "/tmp"
            self.unprocessable_images = FakeRegistry()

        def run_immediate_read_task(self, callback):
            class FakeTag:
                def __init__(self):
                    self.tag = "__tag"

            class Picture:
                def __init__(self, pic_id):
                    self.id = pic_id
                    self.tags = [FakeTag()]

            return [Picture(i) for i in range(1, 30)]

    finder = MissingTagFinder(
        database=FakeDB(),
        engine_getter=lambda: FakeEngine(),
    )

    task = finder.find_task()

    assert task is not None
    assert task.params["batch_size"] == 3


def test_larger_budget_gives_bigger_batch_than_smaller_budget():
    small_budget = _build_workflow_for_budget_tests(
        budget_mb=4096,
        onnx_capacity=64,
    )
    large_budget = _build_workflow_for_budget_tests(
        budget_mb=8192,
        onnx_capacity=64,
    )

    small_batch = small_budget.effective_wd14_batch_size()
    large_batch = large_budget.effective_wd14_batch_size()

    assert large_batch > small_batch
    assert large_budget.effective_pixlstash_tagger_batch_size() == large_batch
    assert large_budget.suggested_task_size() == large_batch


def test_ort_session_options_cap_the_arena_only_when_a_budget_is_set():
    budgeted = VramBudget.__new__(VramBudget)
    budgeted._device = "cuda"
    budgeted._max_vram_usage_mb = 8192
    options = budgeted.ort_cuda_provider_options(ORT_ARENA_SHARE["wd14"])
    assert options["gpu_mem_limit"] == int(8192 * 0.40) * 1024**2
    assert options["arena_extend_strategy"] == "kSameAsRequested"
    assert options["cudnn_conv_algo_search"] == "HEURISTIC"

    unlimited = VramBudget("cuda")
    options = unlimited.ort_cuda_provider_options(ORT_ARENA_SHARE["wd14"])
    assert "gpu_mem_limit" not in options, "no budget, no invented cap"
    assert options["arena_extend_strategy"] == "kSameAsRequested"
    assert options["cudnn_conv_algo_search"] == "HEURISTIC"

    # WD14 is the one capped arena; it must leave torch (CLIP, the PixlStash
    # tagger) and the uncapped InsightFace sessions real headroom.
    assert ORT_ARENA_SHARE["wd14"] < 0.6
    assert ORT_ARENA_SHARE["insightface_session"] is None

    uncapped = budgeted.ort_cuda_provider_options(None)
    assert uncapped == {"cudnn_conv_algo_search": "HEURISTIC"}, (
        "an uncapped session gets neither a limit nor a strategy that only "
        "pays off against one"
    )


def _wd14_limit_mb(budget) -> int:
    """The ``gpu_mem_limit`` WD14's session is actually built with, in MiB."""
    options = budget.ort_cuda_provider_options(
        ORT_ARENA_SHARE["wd14"], min_limit_mb=budget.wd14_arena_limit_mb()
    )
    return options["gpu_mem_limit"] // 1024**2


def _budget(budget_mb: int) -> VramBudget:
    budget = VramBudget.__new__(VramBudget)
    budget._device = "cuda"
    budget._max_vram_usage_mb = budget_mb
    return budget


#: WD14's ORT arena as measured against the real model (RTX 5090, ORT 1.28 CUDA
#: EP): ``385 + 111·n`` MiB. Independent of the constants the code derives its
#: floor from, which is what lets these assertions fail for the right reason.
def _measured_arena_mb(batch: int) -> int:
    return 385 + 111 * batch


@pytest.mark.parametrize("budget_mb", [512, 1024, 2048, 4096, 8192, 16384, 32768])
def test_the_wd14_arena_holds_the_batch_measured_against_the_arena_model(budget_mb):
    """The cap must cover the batch's ARENA cost, not its process cost.

    Asserting against the same pair the floor is built from would be a
    tautology - ``max(share, f(x)) >= f(x)`` holds for any ``f`` - so this
    asserts against the independent measurement the constants carry headroom
    over. That is what the runtime actually enforces.
    """
    workflow = _build_workflow_for_budget_tests(budget_mb=budget_mb, onnx_capacity=64)
    budget = workflow._engine.vram_budget

    batch = workflow.effective_wd14_batch_size()
    limit_mb = _wd14_limit_mb(budget)

    assert limit_mb >= _measured_arena_mb(batch), (
        f"WD14's arena is capped at {limit_mb} MiB but running the {batch} "
        f"images this budget hands it measures {_measured_arena_mb(batch)} MiB"
    )
    assert limit_mb <= budget_mb, "the configured budget is still the ceiling"


def test_the_configured_arena_share_still_decides_at_the_shipped_default():
    """``ORT_ARENA_SHARE`` must stay a knob the code honours.

    Flooring the cap with WD14's *process* VRAM (900 + 220·n) instead of its
    arena cost - roughly twice the figure - made the floor beat the share at
    every budget from 0.5 GB to 32 GB, so 0.40 silently applied nowhere. At the
    shipped 2 GB default the share is 819 MiB and the batch is 3, measuring
    ~718 MiB: the share covers it, so the share must decide.
    """
    assert _wd14_limit_mb(_budget(2048)) == int(2048 * ORT_ARENA_SHARE["wd14"]), (
        "the arena floor has displaced the configured share at the default "
        "budget, where the share already covers the batch"
    )


def test_the_arena_floor_rescues_a_budget_too_small_for_one_image():
    """The real defect the floor exists for, and it is at the bottom end.

    WD14's 40 % of a 1 GB budget is 409 MiB: measured, that loads the model
    (409 MiB) and cannot run a single image (496 MiB). The share alone is a
    guaranteed allocation failure there, so the floor has to take over.
    """
    limit_mb = _wd14_limit_mb(_budget(1024))

    assert limit_mb > int(1024 * ORT_ARENA_SHARE["wd14"])
    assert limit_mb >= _measured_arena_mb(1), "cannot run even one image"


def test_the_batch_sizer_is_untouched_by_the_arena_work():
    """The arena pair must never leak into batch sizing.

    ``limited_batch_cap`` is calibrated against process VRAM and feeds the
    scheduler's admission model; costing the arena is a separate question, and
    conflating them again is exactly the regression to catch.
    """
    for budget_mb in (2048, 4096, 8192):
        workflow = _build_workflow_for_budget_tests(
            budget_mb=budget_mb, onnx_capacity=64
        )
        budget = workflow._engine.vram_budget
        assert workflow.effective_wd14_batch_size() == min(
            MAX_CONCURRENT_GPU_IMAGES,
            budget.limited_batch_cap(WD14_BASE_MB, WD14_PER_ITEM_MB),
        )


def test_the_arena_floor_never_shrinks_the_share_or_survives_an_unset_budget():
    """The floor only ever raises the cap, and only when there is one."""
    budgeted = _budget(8192)

    share_only = budgeted.ort_cuda_provider_options(ORT_ARENA_SHARE["wd14"])
    with_tiny_floor = budgeted.ort_cuda_provider_options(
        ORT_ARENA_SHARE["wd14"], min_limit_mb=1
    )
    assert with_tiny_floor["gpu_mem_limit"] == share_only["gpu_mem_limit"]

    unlimited = VramBudget("cuda")
    assert unlimited.wd14_arena_limit_mb() == 0
    assert "gpu_mem_limit" not in unlimited.ort_cuda_provider_options(
        ORT_ARENA_SHARE["wd14"], min_limit_mb=99_999
    ), "no budget, no invented cap - not even from the floor"
