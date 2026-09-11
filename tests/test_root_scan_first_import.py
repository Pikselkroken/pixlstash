"""The boot-time root scan waits for the first import offer to be answered.

A fresh library over a folder that already holds pictures is asked what those
pictures are (the first-run import offer). The root scan is due the moment
the backend boots, so on a small library it indexed everything before the
screen came up and the questions never appeared. It waits until the newest
``local_import`` commit has settled, or, with no such record at all, until
the library holds a picture. See ``root_import_answered``.
"""

import os
import tempfile
import time

import pytest
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models.folder_mapping_commit import (
    STATE_ABANDONED,
    STATE_DEFERRED,
    STATE_DONE,
    STATE_PENDING,
    FolderMappingCommit,
)
from pixlstash.db_models.picture import Picture
from pixlstash.server import Server
from pixlstash.tasks import TaskType
from pixlstash.tasks.reference_folder_scan_finder import ReferenceFolderScanFinder
from pixlstash.utils.path_mapper import PathMapper

_CONFLICTING_FINDERS = (
    TaskType.THUMBNAIL_GENERATION,
    TaskType.REFERENCE_FOLDER_SCAN,
    TaskType.MISSING_FILE_PURGE,
    TaskType.TAGGER,
)


@pytest.fixture
def server():
    """A Server per test: the gate is about a library that is really empty."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path) as srv:
            for task_type in _CONFLICTING_FINDERS:
                srv.vault._planner_work_finders.pop(task_type)
            srv.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)
            yield srv


def _drop_picture(root, name):
    path = os.path.join(root, name)
    Image.new("RGB", (8, 8), color=(3, 4, 5)).save(path, format="PNG")
    old = time.time() - 600
    os.utime(path, (old, old))


def _record(server, state, mode="local_import"):
    def write(session: Session):
        session.add(
            FolderMappingCommit(
                task_id=f"t-{mode}-{state}-{time.monotonic_ns()}",
                root_path=server.vault.image_root,
                mode=mode,
                state=state,
            )
        )
        session.commit()

    server.vault.db.run_task(write)


def _settle_pending(server, state):
    def write(session: Session):
        for row in session.exec(
            select(FolderMappingCommit).where(
                FolderMappingCommit.state == STATE_PENDING
            )
        ).all():
            row.state = state
            session.add(row)
        session.commit()

    server.vault.db.run_task(write)


def _managed(server):
    return server.vault.db.run_task(
        lambda s: list(
            s.exec(select(Picture).where(Picture.reference_folder_id.is_(None)))
        )
    )


def _finder(server):
    return ReferenceFolderScanFinder(
        database=server.vault.db,
        path_mapper=PathMapper(),
        image_root=server.vault.image_root,
    )


def _due(finder):
    """Clear the interval and the gate's retry deadline: the next cycle asks."""
    finder.mark_root_due()
    return finder.find_task()


def test_a_fresh_library_over_pictures_waits_for_the_offer_to_be_answered(server):
    _drop_picture(server.vault.image_root, "loose.png")
    finder = _finder(server)
    assert _due(finder) is None, "the owner has not been asked yet"

    _record(server, STATE_PENDING)
    assert _due(finder) is None, "the import is still running"
    _settle_pending(server, STATE_ABANDONED)
    assert _due(finder) is None, "an abort is not an answer to import"

    _record(server, STATE_DEFERRED)
    task = _due(finder)
    assert task is not None, "organise later is an answer: index everything"
    task._run_task()
    assert [p.file_path for p in _managed(server)] == ["loose.png"]


def test_a_settled_import_answers_and_a_scan_queued_before_it_recheck(server):
    _drop_picture(server.vault.image_root, "loose.png")
    _record(server, STATE_DONE)
    finder = _finder(server)
    task = _due(finder)
    assert task is not None

    # Accepted while the task sat in the queue: the task asks again and skips.
    _record(server, STATE_PENDING)
    assert task._run_task()["status"] == "skipped"
    assert _managed(server) == []


def test_a_reference_commit_says_nothing_about_the_root(server):
    _drop_picture(server.vault.image_root, "loose.png")
    _record(server, STATE_DONE, mode="reference")
    assert _due(_finder(server)) is None


def test_a_library_that_already_holds_pictures_is_scanned_as_before(server):
    _drop_picture(server.vault.image_root, "loose.png")
    server.vault.db.run_task(
        lambda s: (s.add(Picture(file_path="old.png", pixel_sha="x" * 40)), s.commit())
    )
    assert _due(_finder(server)) is not None
