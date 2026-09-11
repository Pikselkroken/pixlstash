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
import shutil
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
from pixlstash.vault import Vault
from pixlstash.tasks import TaskType, reference_folder_scan_task
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
    """A Server per test. A module-scoped one outlived the other modules a CI
    shard runs beside it and came back to "no such table" once their servers
    had torn the shared vault location down under it; the few seconds a
    fresh server costs per case buy a library that is really empty."""
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


def _managed(server):
    """The library's own pictures, oldest id first."""
    return server.vault.db.run_task(
        lambda s: list(
            s.exec(
                select(Picture)
                .where(Picture.reference_folder_id.is_(None))
                .order_by(Picture.id)
            ).all()
        )
    )


def _due(finder):
    """The next planning cycle after something changed: a closed gate is asked
    again only after `_GATE_RETRY_S`, and `mark_root_due` is what clears that
    deadline (and the scan interval) so the tests do not wait it out."""
    finder.mark_root_due()
    return finder


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
    assert _due(finder).find_task() is None, "the owner has not been asked yet"

    _record(server, STATE_ABANDONED)
    assert _due(finder).find_task() is None, "an abort is not an answer to import"

    _record(server, STATE_PENDING)
    assert _due(finder).find_task() is None, (
        "a pending record is the commit still running - the question, not the "
        "answer. Counting it lets the 300 s root scan race the commit: it "
        "builds its own rows with the sidecar probe and insert() reuses them "
        "without applying the owner's caption choices"
    )
    _settle_pending(server, STATE_SUPERSEDED)

    _record(server, STATE_DEFERRED, mode="reference")
    assert _due(finder).find_task() is None, (
        "a reference-folder commit registers some other folder; it says "
        "nothing about the root's own pictures"
    )

    _record(server, STATE_DEFERRED)
    task = _due(finder).find_task()
    assert task is not None and task.params["folder_id"] is None, (
        "organise later is an answer: index everything, map nothing"
    )
    assert finder.first_import_answered() is True


def test_a_library_that_holds_a_picture_is_scanned_as_before(server):
    """Nothing changes for an existing library: a picture row, however it got
    there, is the answer."""
    _drop_picture(server.vault.image_root, "first.png")
    finder = _finder(server)
    assert _due(finder).find_task() is None

    def add(session: Session):
        session.add(Picture(file_path="first.png", pixel_sha="x" * 64))
        session.commit()

    server.vault.db.run_task(add)
    task = _due(finder).find_task()
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
    assert _due(finder).find_task() is None
    # Nothing was cached, so the commit settling still releases the scan.
    _settle_pending(server, STATE_DONE)
    task = _due(finder).find_task()
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
    assert _due(finder).find_task() is None
    # Nothing was cached, so a later import that does settle still releases
    # the scan: the newest record decides.
    _record(server, STATE_DONE)
    task = _due(finder).find_task()
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
    assert _due(finder).find_task() is None


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
    assert _due(finder).find_task() is None


def test_the_same_finder_closes_the_gate_again_for_a_later_import(server):
    """A finder lives as long as the process. Once one import has settled it
    must still notice the next one starting: a positive answer is not
    remembered, it is asked again before every due scan."""
    _record(server, STATE_DONE)
    finder = _finder(server)
    assert finder.first_import_answered() is True
    task = _due(finder).find_task()
    assert task is not None and task.params["folder_id"] is None

    _record(server, STATE_PENDING)
    finder.mark_root_due()
    assert finder.first_import_answered() is False
    assert _due(finder).find_task() is None


def test_a_closed_gate_is_not_asked_on_every_planner_sweep(server, monkeypatch):
    """The planner sweeps up to twenty times a second and the importer wakes
    it after every chunk; while the gate says no it is asked again only after
    a short deadline, not on every sweep."""
    _record(server, STATE_PENDING)
    finder = _finder(server)
    calls = []
    real = finder.first_import_answered

    def counted():
        # Counted on the finder itself: the server's other finders share the
        # database handle, and their own reads are not the question here.
        calls.append(1)
        return real()

    monkeypatch.setattr(finder, "first_import_answered", counted)
    now = 1_000.0
    assert finder._root_task([], now) is None
    assert finder._root_task([], now + 0.05) is None
    assert finder._root_task([], now + 1.0) is None
    assert len(calls) == 1, "one query per retry window, not one per sweep"
    assert finder._root_task([], now + 6.0) is None
    assert len(calls) == 2


def test_a_scan_handed_out_before_a_pending_import_walks_nothing(server, monkeypatch):
    """The handoff, not the read. The finder's gate said yes and handed the
    task out; `record_pending_commit` then wrote a pending record before the
    runner started it. The task asks the same question again at start, so the
    stale answer it was handed out on cannot make it walk."""
    _drop_picture(server.vault.image_root, "handoff.png")
    _record(server, STATE_DONE)
    task = _due(_finder(server)).find_task()
    assert task is not None and task.params["folder_id"] is None

    walked: list[str] = []
    real_walk = os.walk

    def spy(path, *args, **kwargs):
        walked.append(path)
        return real_walk(path, *args, **kwargs)

    monkeypatch.setattr(reference_folder_scan_task.os, "walk", spy)

    _record(server, STATE_PENDING)
    assert task._run_task() == {"status": "skipped", "folder_id": None}
    assert walked == [], "the gate closed after the hand-out; nothing is walked"
    assert _managed(server) == [], "so no row is built and no move reconciled"

    # The control: the same task, with the record settled, scans as before.
    _settle_pending(server, STATE_DONE)
    assert task._run_task()["status"] == "active"
    assert server.vault.image_root in walked
    assert [p.file_path for p in _managed(server)] == ["handoff.png"]


def test_an_import_that_starts_mid_walk_stops_the_scan_one_directory_later(
    server, monkeypatch
):
    """The window the re-check alone cannot close.

    `root_import_answered` reads the database and releases it, and the task
    does more work before `os.walk` starts. `vault.db.local_import_running`
    is raised by `record_pending_commit` BEFORE its row and before its own
    walk, and the scan asks it once per directory, so an import accepted mid
    walk costs at most the one directory already in hand - whose rows the
    commit's `insert()` reuses - rather than the whole tree.
    """
    root = server.vault.image_root
    _drop_picture(root, "top.png")
    sub = os.path.join(root, "later")
    os.makedirs(sub, exist_ok=True)
    _drop_picture(sub, "deep.png")
    _record(server, STATE_DONE)
    task = _due(_finder(server)).find_task()
    assert task is not None and task.params["folder_id"] is None

    walked: list[str] = []
    real_walk = os.walk

    def spy(path, *args, **kwargs):
        for entry in real_walk(path, *args, **kwargs):
            walked.append(entry[0])
            yield entry
            # The owner's click landing between two directories, which is
            # exactly where `record_pending_commit` raises the flag.
            server.vault.db.local_import_running.set()

    monkeypatch.setattr(reference_folder_scan_task.os, "walk", spy)
    try:
        assert task._run_task() == {"status": "skipped", "folder_id": None}
        assert walked == [root, sub], (
            "one directory, not the rest of the tree: the flag is asked per "
            "directory, so the walk stops at the next one"
        )
        assert _managed(server) == [], (
            "the scan returns before it builds anything, so the import owns "
            "every file including the ones this walk had already listed"
        )

        # The control: the same task, walking plainly with the flag down,
        # indexes both directories.
        monkeypatch.undo()
        server.vault.db.local_import_running.clear()
        assert task._run_task()["status"] == "active"
        assert sorted(p.file_path for p in _managed(server)) == [
            os.path.join("later", "deep.png"),
            "top.png",
        ]
    finally:
        server.vault.db.local_import_running.clear()
        shutil.rmtree(sub, ignore_errors=True)


def test_a_vault_resuming_a_pending_import_starts_with_the_flag_up(tmp_path):
    """A crash mid-import is resumed at the next start-up, so the flag that
    holds the root scan off it has to survive the restart. `Vault.__init__`
    seeds it from the record; an in-memory flag alone would come up down and
    let the boot scan race the resumed commit."""
    root = str(tmp_path / "resumed-library")
    with Vault(image_root=root, disable_background_workers=True) as vault:
        assert not vault.db.local_import_running.is_set(), "a fresh library"

        def write(session: Session):
            session.add(
                FolderMappingCommit(
                    task_id="interrupted-import",
                    root_path=root,
                    mode="local_import",
                    expected_pictures=1,
                    state=STATE_PENDING,
                )
            )
            session.commit()

        vault.db.run_task(write)

    with Vault(image_root=root, disable_background_workers=True) as resumed:
        assert resumed.db.local_import_running.is_set()
