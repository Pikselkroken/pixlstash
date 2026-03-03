from pixlvault.picture_tagger import PictureTagger
from pixlvault.tasks.missing_tags_finder import MissingTagsFinder


def _build_tagger_for_budget_tests(
    budget_mb: int = 4096,
    expected_tasks: int = 2,
    max_concurrent: int = 64,
    onnx_capacity: int = 64,
    custom_batch: int = 16,
):
    tagger = PictureTagger.__new__(PictureTagger)
    tagger._device = "cuda"
    tagger._max_vram_usage_mb = budget_mb
    tagger._expected_concurrent_tag_tasks = expected_tasks
    tagger._onnx_batch_capacity = onnx_capacity
    tagger._custom_tagger_batch = custom_batch
    tagger.max_concurrent_images = lambda: max_concurrent
    return tagger


def test_vram_batch_cap_is_queue_aware():
    single_task = _build_tagger_for_budget_tests(expected_tasks=1)
    two_tasks = _build_tagger_for_budget_tests(expected_tasks=2)

    cap_single = single_task._vram_limited_batch_cap(base_mb=900, per_item_mb=220)
    cap_two = two_tasks._vram_limited_batch_cap(base_mb=900, per_item_mb=220)

    assert cap_two < cap_single
    assert cap_two <= 4


def test_estimated_task_vram_stays_within_budget_window():
    tagger = _build_tagger_for_budget_tests(
        budget_mb=4096,
        expected_tasks=2,
        max_concurrent=64,
        onnx_capacity=64,
        custom_batch=32,
    )

    estimate_mb = tagger.estimate_task_vram_mb(image_count=64)

    assert estimate_mb <= 4096
    assert estimate_mb >= 1200


def test_vram_cap_noop_on_cpu_mode():
    tagger = _build_tagger_for_budget_tests()
    tagger._device = "cpu"

    cap = tagger._vram_limited_batch_cap(base_mb=900, per_item_mb=220)

    assert cap == 10_000


def test_suggested_tag_task_size_tracks_effective_batch():
    tagger = _build_tagger_for_budget_tests(
        budget_mb=4096,
        expected_tasks=2,
        max_concurrent=64,
        onnx_capacity=64,
        custom_batch=32,
    )

    assert tagger._effective_wd14_batch_size() == 3
    assert tagger._effective_custom_batch_size() == 3
    assert tagger.suggested_tag_task_size() == 3


def test_custom_and_wd14_use_same_effective_batch_size():
    tagger = _build_tagger_for_budget_tests(
        budget_mb=4096,
        expected_tasks=2,
        max_concurrent=64,
        onnx_capacity=64,
        custom_batch=64,
    )

    assert tagger._effective_custom_batch_size() == tagger._effective_wd14_batch_size()


def test_incremental_vram_estimate_is_below_full_estimate():
    tagger = _build_tagger_for_budget_tests(
        budget_mb=4096,
        expected_tasks=2,
        max_concurrent=64,
        onnx_capacity=64,
        custom_batch=32,
    )

    full_estimate = tagger.estimate_task_vram_mb(image_count=64)
    incremental_estimate = tagger.estimate_task_incremental_vram_mb(image_count=64)

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

    finder = MissingTagsFinder(
        database=FakeDB(),
        picture_tagger_getter=lambda: FakeTagger(),
    )

    task = finder.find_task()

    assert task is not None
    assert task.params["batch_size"] == 3


def test_runtime_headroom_rounds_down_effective_batch_size():
    tagger = _build_tagger_for_budget_tests(
        budget_mb=8192,
        expected_tasks=2,
        max_concurrent=64,
        onnx_capacity=64,
        custom_batch=32,
    )

    tagger._query_process_vram_mb = lambda: 6404

    assert tagger._effective_wd14_batch_size() == 7
    assert tagger._effective_custom_batch_size() == 7
    assert tagger.suggested_tag_task_size() == 7
