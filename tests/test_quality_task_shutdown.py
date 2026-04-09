import time
from types import SimpleNamespace

from pixlstash.tasks.quality_task import QualityTask
from pixlstash.utils.image_processing.image_utils import ImageUtils


class _FakeDb:
    def __init__(self):
        self.image_root = "/tmp"


def test_quality_task_on_cancel_stops_preload_thread(monkeypatch):
    pictures = [SimpleNamespace(id=i, file_path=f"img-{i}.jpg") for i in range(1, 100)]
    task = QualityTask(database=_FakeDb(), pictures=pictures)

    monkeypatch.setattr(
        ImageUtils,
        "resolve_picture_path",
        staticmethod(lambda image_root, file_path: file_path),
    )

    def _slow_loader(_file_path):
        time.sleep(0.02)
        return None

    monkeypatch.setattr(ImageUtils, "load_image_or_video", staticmethod(_slow_loader))

    task.on_queued()
    time.sleep(0.08)
    task.on_cancel()

    assert task._preload_thread is not None
    assert not task._preload_thread.is_alive()
