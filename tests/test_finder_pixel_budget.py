"""Image-task batches are cut by decoded size as well as by count (#2750)."""

from types import SimpleNamespace

from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.missing_image_embedding_finder import MissingImageEmbeddingFinder
from pixlstash.tasks.missing_tag_finder import MissingTagFinder
from pixlstash.worker_config import IMAGE_TASK_PIXEL_BUDGET

MP = 1_000_000


def _pic(pid, width, height):
    return SimpleNamespace(id=pid, width=width, height=height, tags=[])


def _claim(pictures, batch_limit, pixel_budget=None):
    finder = MissingTagFinder(database=None, engine_getter=lambda: None)
    return [p.id for p in finder._filter_and_claim(pictures, batch_limit, pixel_budget)]


def test_budget_stops_before_the_picture_that_would_pass_it():
    pictures = [_pic(i, 4000, 1000) for i in range(1, 6)]  # 4 MP each
    assert _claim(pictures, 10, pixel_budget=12 * MP) == [1, 2, 3]
    assert _claim(pictures, 10) == [1, 2, 3, 4, 5]
    assert _claim(pictures, 2, pixel_budget=12 * MP) == [1, 2]
    # Stops, rather than skipping to a smaller picture that would still fit.
    sizes = [_pic(1, 4 * MP, 1), _pic(2, 4 * MP, 1), _pic(3, 6 * MP, 1)]
    assert _claim([*sizes, _pic(4, 1, 1)], 10, pixel_budget=12 * MP) == [1, 2]


def test_an_oversized_first_picture_still_runs_alone():
    pictures = [_pic(1, 20000, 10000), _pic(2, 10, 10)]
    assert _claim(pictures, 10, pixel_budget=12 * MP) == [1]
    # The next sweep picks up where the cut left off.
    assert _claim(pictures[1:], 10, pixel_budget=12 * MP) == [2]


def test_unrecorded_dimensions_count_as_a_large_photo():
    pictures = [_pic(1, None, 100), _pic(2, 100, 0), _pic(3, 0, None), _pic(4, 1, 1)]
    # Each unrecorded picture counts as 24 MP.
    assert _claim(pictures, 10, pixel_budget=72 * MP + 1) == [1, 2, 3, 4]
    assert _claim(pictures, 10, pixel_budget=72 * MP) == [1, 2, 3]
    assert _claim(pictures, 10, pixel_budget=72 * MP - 1) == [1, 2]
    assert _claim(pictures, 10, pixel_budget=24 * MP) == [1]


def test_embedding_fetch_work_selects_the_dimensions():
    seen = []

    class _Session:
        def exec(self, stmt):
            seen.append(stmt)
            return SimpleNamespace(all=lambda: [])

    ImageEmbeddingTask.fetch_work(_Session(), aesthetic_disabled=True)
    names = [column.name for column in seen[0].selected_columns]
    assert names == ["id", "file_path", "width", "height"]


class _Db:
    def __init__(self, rows):
        self._rows = rows
        self.unprocessable_images = SimpleNamespace(
            active_suppressed_ids=set, is_suppressed=lambda _pid: False
        )
        self.tag_resets = SimpleNamespace(current=lambda: 0)

    def run_immediate_read_task(self, fn, *args):
        return self._rows


def test_tag_finder_cuts_a_batch_of_large_photos():
    per = 24 * MP
    rows = [_pic(i, 6000, 4000) for i in range(1, 65)]
    workflow = SimpleNamespace(suggested_task_size=lambda: 64)
    engine = SimpleNamespace(
        tagger_settings={"active_tag_plugin": "wd14"}, tagging_workflow=workflow
    )
    task = MissingTagFinder(_Db(rows), lambda: engine).find_task()
    assert task.params["picture_ids"] == list(
        range(1, IMAGE_TASK_PIXEL_BUDGET // per + 1)
    )
    assert len(task.params["picture_ids"]) < 64


def test_embedding_finder_cuts_by_size_and_hands_the_task_pairs():
    per = 24 * MP
    rows = [
        SimpleNamespace(id=i, file_path=f"p{i}.jpg", width=6000, height=4000)
        for i in range(1, 129)
    ]
    workflow = SimpleNamespace(suggested_batch_size=lambda: 128)
    engine = SimpleNamespace(clip_embedding_workflow=workflow)
    task = MissingImageEmbeddingFinder(_Db(rows), lambda: engine).find_task()
    count = IMAGE_TASK_PIXEL_BUDGET // per
    assert task.params["picture_ids"] == list(range(1, count + 1))
    assert task._batch == [(i, f"p{i}.jpg") for i in range(1, count + 1)]
    assert count < 128
