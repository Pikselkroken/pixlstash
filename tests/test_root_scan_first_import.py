"""The boot-time root scan waits for the first import offer to be answered.

A fresh library whose folder already holds pictures is asked what those
pictures are - the first-run offer, and "Add a library" - and the app makes
the offer only while the library is empty. The root scan is due the moment
the backend boots, so on a small library it indexed everything before the
screen came up and the questions never appeared. It now waits until the
library holds a picture or a local_import commit has SETTLED - done or
deferred. A pending one is the commit still running, and treating it as an
answer puts the root scan into a race with it.
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
    STATE_SUPERSEDED,
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


@pytest.fixture(scope="module")
def _module_server():
    """One Server for the module: starting it is what these tests cost, and
    every case here reads the same two tables. Same shape as
    tests/test_library_root_scan.py."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path) as srv:
            for task_type in _CONFLICTING_FINDERS:
                srv.vault._planner_work_finders.pop(task_type)
            srv.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)
            yield srv


@pytest.fixture
def server(_module_server):
    """The shared server with an empty library: no commit record, no picture
    row and nothing in the root folder, so each case starts from "the owner
    has not been asked yet" the way a per-test Server used to."""
    srv = _module_server

    def wipe(session: Session):
        for model in (FolderMappingCommit, Picture):
            for row in session.exec(select(model)).all():
                session.delete(row)
        session.commit()

    root = srv.vault.image_root
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            os.remove(path)
    srv.vault.db.run_task(wipe)
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
                task_id=f"t-{mode}-{state}",
                root_path=server.vault.image_root,
                mode=mode,
                expected_pictures=1,
                state=state,
            )
        )
        session.commit()

    server.vault.db.run_task(write)


def _settle_pending(server, state):
    """Move every pending record to *state*, the way the commit's own settle
    (or `record_pending_commit` superseding it) does. A pending row never
    survives alongside a newer answer in the real flow."""

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


def _finder(server):
    return ReferenceFolderScanFinder(
        database=server.vault.db,
        path_mapper=PathMapper(),
        image_root=server.vault.image_root,
    )


def test_a_fresh_library_over_pictures_is_not_scanned_until_the_offer_is_answered(
    server,
):
    _drop_picture(server.vault.image_root, "loose.png")
    finder = _finder(server)
    assert finder.first_import_answered() is False
    assert finder.find_task() is None, "the owner has not been asked yet"

    _record(server, STATE_ABANDONED)
    assert finder.find_task() is None, "an abort is not an answer to import"

    _record(server, STATE_PENDING)
    assert finder.find_task() is None, (
        "a pending record is the commit still running - the question, not the "
        "answer. Counting it lets the 300 s root scan race the commit: it "
        "builds its own rows with the sidecar probe and insert() reuses them "
        "without applying the owner's caption choices"
    )
    _settle_pending(server, STATE_SUPERSEDED)

    _record(server, STATE_DEFERRED, mode="reference")
    assert finder.find_task() is None, (
        "a reference-folder commit registers some other folder; it says "
        "nothing about the root's own pictures"
    )

    _record(server, STATE_DEFERRED)
    task = finder.find_task()
    assert task is not None and task.params["folder_id"] is None, (
        "organise later is an answer: index everything, map nothing"
    )
    assert finder.first_import_answered() is True


def test_a_library_that_holds_a_picture_is_scanned_as_before(server):
    """Nothing changes for an existing library: a picture row, however it got
    there, is the answer."""
    _drop_picture(server.vault.image_root, "first.png")
    finder = _finder(server)
    assert finder.find_task() is None

    def add(session: Session):
        session.add(Picture(file_path="first.png", pixel_sha="x" * 64))
        session.commit()

    server.vault.db.run_task(add)
    task = finder.find_task()
    assert task is not None and task.params["folder_id"] is None


def test_a_running_local_import_holds_the_root_scan_off_though_it_has_committed_rows(
    server,
):
    """`local_import_pictures` commits every chunk and wakes the planner while
    its record is still pending, so the library holds pictures long before the
    owner's answer is applied. The pending record wins, and the answer is not
    cached: the commit's own settle is what releases the scan."""
    _drop_picture(server.vault.image_root, "chunk.png")

    def add(session: Session):
        session.add(Picture(file_path="chunk.png", pixel_sha="y" * 64))
        session.commit()

    server.vault.db.run_task(add)
    _record(server, STATE_PENDING)

    finder = _finder(server)
    assert finder.first_import_answered() is False, (
        "a chunk the running commit already inserted is not the owner's answer"
    )
    assert finder.find_task() is None
    # Nothing was cached, so the commit settling still releases the scan.
    _settle_pending(server, STATE_DONE)
    task = finder.find_task()
    assert task is not None and task.params["folder_id"] is None


def test_an_aborted_local_import_holds_the_root_scan_off_over_its_own_rows(server):
    """An abort settles the record `abandoned` and leaves every chunk the
    commit had already inserted indexed. Those rows are the files the owner
    just refused, so reading them as the answer would scan the root and import
    exactly what the abort declined."""
    _drop_picture(server.vault.image_root, "aborted.png")

    def add(session: Session):
        session.add(Picture(file_path="aborted.png", pixel_sha="z" * 64))
        session.commit()

    server.vault.db.run_task(add)
    _record(server, STATE_ABANDONED)

    finder = _finder(server)
    assert finder.first_import_answered() is False, (
        "a chunk the aborted commit had already inserted is not an answer to "
        "import; scanning on it imports the very files the owner declined"
    )
    assert finder.find_task() is None
    # Nothing was cached, so a later import that does settle still releases
    # the scan: the newest record decides.
    _record(server, STATE_DONE)
    task = finder.find_task()
    assert task is not None and task.params["folder_id"] is None
    assert finder.first_import_answered() is True


def test_an_abort_after_an_earlier_import_still_holds_the_root_scan_off(server):
    """The reverse order. Records are kept, so a `done` from last week must
    not outrank the abort the owner just made: the newest record is the
    answer, and it says bring nothing in."""
    _drop_picture(server.vault.image_root, "kept.png")

    def add(session: Session):
        session.add(Picture(file_path="kept.png", pixel_sha="y" * 64))
        session.commit()

    server.vault.db.run_task(add)
    _record(server, STATE_DONE)
    _record(server, STATE_ABANDONED)

    finder = _finder(server)
    assert finder.first_import_answered() is False
    assert finder.find_task() is None


def test_a_superseded_local_import_holds_the_root_scan_off_too(server):
    """`record_pending_commit` supersedes a pending record of either mode, so a
    partial local import can be replaced by a reference commit and stay the
    newest local-import row. Its chunks are as unanswered as a pending one's."""
    _drop_picture(server.vault.image_root, "partial.png")

    def add(session: Session):
        session.add(Picture(file_path="partial.png", pixel_sha="x" * 64))
        session.commit()

    server.vault.db.run_task(add)
    _record(server, STATE_SUPERSEDED)
    _record(server, STATE_DONE, mode="reference")

    finder = _finder(server)
    assert finder.first_import_answered() is False
    assert finder.find_task() is None


def test_the_same_finder_closes_the_gate_again_for_a_later_import(server):
    """A finder lives as long as the process. Once one import has settled it
    must still notice the next one starting: a positive answer is not
    remembered, it is asked again before every due scan."""
    _record(server, STATE_DONE)
    finder = _finder(server)
    assert finder.first_import_answered() is True
    task = finder.find_task()
    assert task is not None and task.params["folder_id"] is None

    _record(server, STATE_PENDING)
    finder.mark_root_due()
    assert finder.first_import_answered() is False
    assert finder.find_task() is None
