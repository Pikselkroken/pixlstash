"""The library's own picture root is scanned like a reference folder.

Since v1.11 a layout turns the root into a folder tree the owner reorganises
by hand. A rename nobody follows was a row the purge sweep deleted an hour
later, tags and all. ``ReferenceFolderScanTask`` with ``folder_id=None`` walks
the root, follows moves by pixel hash, indexes what the owner dropped in and
drops what they deleted - the same code path a reference folder gets, with
``Picture.file_path`` kept relative to the root.
"""

import os
import tempfile
import time

import pytest
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Character, Picture, Tag
from pixlstash.db_models.external_move_review import ExternalMoveReview
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.server import Server
from pixlstash.services import move_reconciliation_service
from pixlstash.tasks import TaskType
from pixlstash.tasks.missing_file_purge_finder import MissingFilePurgeFinder
from pixlstash.tasks.reference_folder_scan_finder import ReferenceFolderScanFinder
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.library_layout import DEFAULT_LAYOUT, format_layout
from pixlstash.utils.path_mapper import PathMapper

# Same reasoning as tests/test_reference_folder_moves.py: the planner is live
# for the module server, and each of these would race a scan run by hand.
_CONFLICTING_FINDERS = (
    TaskType.THUMBNAIL_GENERATION,
    TaskType.REFERENCE_FOLDER_SCAN,
    TaskType.MISSING_FILE_PURGE,
    TaskType.TAGGER,
)


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path) as srv:
            for task_type in _CONFLICTING_FINDERS:
                srv.vault._planner_work_finders.pop(task_type)
            srv.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)
            yield srv


def _make_image(path, color, *, settled=True):
    """A flat image at *path*, backdated past the root scan's settle window
    unless ``settled=False`` - a fresh file is left to whoever is writing it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (8, 8), color=color).save(path, format="PNG")
    if settled:
        old = time.time() - 600
        os.utime(path, (old, old))
    return path


def _run_root_scan(server):
    root = server.vault.image_root
    task = ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=None,
        folder_path=root,
        resolved_path=root,
    )
    return task._run_task()


def _managed(server, prefix):
    """Managed pictures under *prefix* (root-relative), oldest id first."""
    return server.vault.db.run_task(
        lambda s: list(
            s.exec(
                select(Picture)
                .where(Picture.reference_folder_id.is_(None))
                .where(Picture.file_path.startswith(prefix))
                .order_by(Picture.id)
            ).all()
        )
    )


def _tag(server, picture_id, tag):
    def _add(session: Session):
        session.add(Tag(picture_id=picture_id, tag=tag))
        session.commit()

    server.vault.db.run_task(_add)


def _tags(server, picture_id):
    rows = server.vault.db.run_task(
        lambda s: s.exec(select(Tag).where(Tag.picture_id == picture_id)).all()
    )
    return sorted(t.tag for t in rows if not t.tag.startswith("__"))


def test_a_file_dropped_into_the_root_is_indexed_as_a_managed_picture(server):
    root = server.vault.image_root
    _make_image(os.path.join(root, "drop", "Mira", "one.png"), (10, 20, 30))

    result = _run_root_scan(server)

    assert result["status"] == "active"
    (pic,) = _managed(server, "drop/")
    assert pic.file_path == "drop/Mira/one.png", "stored relative, / joined"
    assert pic.reference_folder_id is None
    assert pic.pixel_sha, "hashed on import, so a later rename can be followed"
    thumb = ImageUtils.get_thumbnail_path(root, pic.file_path)
    assert os.path.isfile(thumb), thumb
    assert not os.path.exists(os.path.join(root, "drop", "Mira", "one_thumb.webp"))


def test_a_rename_in_the_root_keeps_the_row_and_carries_the_thumbnail(server):
    root = server.vault.image_root
    original = _make_image(os.path.join(root, "ren", "Mira", "a.png"), (1, 2, 3))
    _run_root_scan(server)
    (pic,) = _managed(server, "ren/")
    _tag(server, pic.id, "portrait")
    old_thumb = ImageUtils.get_thumbnail_path(root, pic.file_path)
    assert os.path.isfile(old_thumb)

    renamed = os.path.join(root, "ren", "Mira", "angela2.png")
    os.rename(original, renamed)
    result = _run_root_scan(server)

    assert result["moved_picture_ids"] == [pic.id]
    assert result["removed_count"] == 0 and result["new_count"] == 0
    (after,) = _managed(server, "ren/")
    assert after.id == pic.id, "the row followed the file; nothing was re-imported"
    assert after.file_path == "ren/Mira/angela2.png"
    assert after.original_file_name == "angela2.png"
    assert _tags(server, pic.id) == ["portrait"]
    new_thumb = ImageUtils.get_thumbnail_path(root, after.file_path)
    assert os.path.isfile(new_thumb), "the bitmap moved with the picture"
    assert not os.path.exists(old_thumb)
    assert after.thumbnail_width is not None, "carried, so nothing to re-render"


def test_a_file_deleted_from_the_root_drops_its_row(server):
    root = server.vault.image_root
    path = _make_image(os.path.join(root, "gone", "b.png"), (4, 5, 6))
    _run_root_scan(server)
    assert len(_managed(server, "gone/")) == 1

    os.remove(path)
    result = _run_root_scan(server)

    assert result["removed_count"] == 1
    assert _managed(server, "gone/") == []


def test_a_freshly_written_file_is_left_to_its_writer(server):
    """PixlStash's own import writes the file, then the row. A scan in between
    must not claim the file; the owner's drop is picked up once it settles."""
    root = server.vault.image_root
    path = _make_image(os.path.join(root, "fresh", "f.png"), (3, 3, 3), settled=False)

    result = _run_root_scan(server)
    assert result["new_count"] == 0
    assert _managed(server, "fresh/") == []

    old = time.time() - 600
    os.utime(path, (old, old))
    result = _run_root_scan(server)
    assert result["new_count"] == 1
    assert [p.file_path for p in _managed(server, "fresh/")] == ["fresh/f.png"]


def test_a_removal_waits_while_a_copy_is_still_settling(server):
    """Copy-then-delete across filesystems is a removal plus a young file; the
    row is kept so the next scan can pair them."""
    root = server.vault.image_root
    original = _make_image(os.path.join(root, "cp", "g.png"), (5, 6, 7))
    _run_root_scan(server)
    (pic,) = _managed(server, "cp/")

    with open(original, "rb") as fh:
        payload = fh.read()
    os.remove(original)
    with open(os.path.join(root, "cp", "g-copy.png"), "wb") as fh:
        fh.write(payload)  # fresh mtime: settling

    result = _run_root_scan(server)
    assert result["removed_count"] == 0
    assert result["new_count"] == 0
    assert [p.id for p in _managed(server, "cp/")] == [pic.id]


def test_the_root_scan_never_indexes_what_pixlstash_writes_itself(server):
    root = server.vault.image_root
    for folder in (".pixlstash-thumbnails", ".staging", "snapshots", "tmp"):
        _make_image(os.path.join(root, folder, "internal.png"), (7, 8, 9))

    _run_root_scan(server)

    for folder in (".pixlstash-thumbnails", ".staging", "snapshots", "tmp"):
        assert _managed(server, f"{folder}/") == [], folder


def test_an_owner_move_in_a_laid_out_root_is_queued_for_review(server):
    root = server.vault.image_root

    def _lay_out(session: Session):
        settings = session.exec(select(LibrarySettings)).first()
        if settings is None:
            settings = LibrarySettings()
        settings.layout = format_layout(DEFAULT_LAYOUT)
        session.add(settings)
        session.add(Character(name="Mira"))
        session.commit()

    server.vault.db.run_task(_lay_out)
    original = _make_image(os.path.join(root, "Unassigned", "c.png"), (9, 9, 9))
    _run_root_scan(server)
    (pic,) = _managed(server, "Unassigned/c.png")

    os.makedirs(os.path.join(root, "Mira"), exist_ok=True)
    os.rename(original, os.path.join(root, "Mira", "c.png"))
    result = _run_root_scan(server)

    assert result["external_moved_picture_ids"] == [pic.id]
    assert result["external_moves_queued_for_review"] == [pic.id]
    reviews = server.vault.db.run_task(
        lambda s: s.exec(
            select(ExternalMoveReview).where(ExternalMoveReview.picture_id == pic.id)
        ).all()
    )
    assert [(r.old_path, r.new_path) for r in reviews] == [
        ("Unassigned/c.png", "Mira/c.png")
    ], "queued in the stored (relative) form"
    # And the queue can read it: the root is found through image_root, and
    # the folder Mira names a person the picture is not yet a member of.
    summary = server.vault.db.run_task(
        move_reconciliation_service.pending_summary_in_session, root
    )
    queued = [
        entry
        for bucket in summary.values()
        for entry in bucket
        if entry["picture_id"] == pic.id
    ]
    assert len(queued) == 1, summary
    assert any(a["name"] == "Mira" for a in queued[0]["additions"]), queued[0]


def test_the_finder_hands_out_the_root_scan_once_per_interval(server):
    finder = ReferenceFolderScanFinder(
        database=server.vault.db,
        path_mapper=PathMapper(),
        image_root=server.vault.image_root,
    )
    assert finder.root_scan_complete() is False

    task = finder.find_task()
    assert task is not None and task.params["folder_id"] is None
    assert finder.find_task() is None, "not again inside the rescan interval"

    finder.mark_root_due()
    assert finder.find_task() is not None, "a filesystem event makes it due"

    finder._note_root_scanned()
    assert finder.root_scan_complete() is True


def test_the_purge_sweep_waits_for_the_first_root_scan():
    assert MissingFilePurgeFinder(None, is_ready=lambda: False).find_task() is None
    assert (
        ReferenceFolderScanFinder(
            None, PathMapper(), image_root=None
        ).root_scan_complete()
        is True
    ), "no root to scan means nothing to wait for"


def test_the_root_scan_marks_itself_complete_when_it_finishes(server):
    seen = []
    root = server.vault.image_root
    ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=None,
        folder_path=root,
        resolved_path=root,
        on_root_scanned=lambda: seen.append(time.time()),
    )._run_task()
    assert len(seen) == 1
