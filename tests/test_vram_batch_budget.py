from pixlstash.inference.vram_budget import VramBudget
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
    use_custom: bool = False,
    device: str = "cuda",
):
    vram_budget = VramBudget.__new__(VramBudget)
    vram_budget._device = device
    vram_budget._max_vram_usage_mb = budget_mb
    engine = _FakeEngine(vram_budget, _FakeWD14Service(onnx_capacity), device=device)
    return TaggingWorkflow(engine=engine, use_wd14=use_wd14, use_custom=use_custom)


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

    assert workflow._effective_wd14_batch_size() == 10
    assert workflow._effective_custom_batch_size() == 10
    assert workflow.suggested_task_size() == 10


def test_custom_and_wd14_use_same_effective_batch_size():
    workflow = _build_workflow_for_budget_tests(
        budget_mb=4096,
        onnx_capacity=64,
    )

    assert workflow._effective_custom_batch_size() == workflow._effective_wd14_batch_size()


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
    class FakeTagger:
        def suggested_tag_task_size(self):
            return 3

        def max_concurrent_images(self):
            return 64

    class FakeDB:
        def __init__(self):
            self.image_root = "/tmp"

        def run_immediate_read_task(self, callback):
            class Picture:
                def __init__(self, pic_id):
                    self.id = pic_id

            return [Picture(i) for i in range(1, 30)]

    finder = MissingTagFinder(
        database=FakeDB(),
        picture_tagger_getter=lambda: FakeTagger(),
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

    small_batch = small_budget._effective_wd14_batch_size()
    large_batch = large_budget._effective_wd14_batch_size()

    assert large_batch > small_batch
    assert large_budget._effective_custom_batch_size() == large_batch
    assert large_budget.suggested_task_size() == large_batch
