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
from datetime import datetime

import pytest
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Character, Picture, Tag
from pixlstash.db_models.external_move_review import ExternalMoveReview
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models.picture_move import PictureMove
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

    assert pic.id in result["moved_picture_ids"]
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
    # A file that stays: a scan finding nothing at all is read as an
    # unmounted root and keeps every row, so the root must not be empty.
    keeper = _make_image(os.path.join(root, "gone", "keep.png"), (7, 7, 7))
    _run_root_scan(server)
    assert len(_managed(server, "gone/")) == 2

    os.remove(path)
    _run_root_scan(server)

    assert [p.file_path for p in _managed(server, "gone/")] == ["gone/keep.png"]
    assert os.path.isfile(keeper)


def test_a_freshly_written_file_is_left_to_its_writer(server):
    """PixlStash's own import writes the file, then the row. A scan in between
    must not claim the file; the owner's drop is picked up once it settles."""
    root = server.vault.image_root
    path = _make_image(os.path.join(root, "fresh", "f.png"), (3, 3, 3), settled=False)

    _run_root_scan(server)
    assert _managed(server, "fresh/") == []

    old = time.time() - 600
    os.utime(path, (old, old))
    _run_root_scan(server)
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

    _run_root_scan(server)
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

    assert pic.id in result["external_moved_picture_ids"]
    assert pic.id in result["external_moves_queued_for_review"]
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


def _settle_root(root):
    """Backdate every file under *root* past the settle window, so the scan's
    copy-in-flight deferral is not what a test is accidentally measuring."""
    old = time.time() - 600
    for base, _dirs, files in os.walk(root):
        for name in files:
            os.utime(os.path.join(base, name), (old, old))


def _insert_managed(server, relative_path, **columns):
    """A managed Picture row at *relative_path*, inserted directly."""

    def _add(session: Session):
        pic = Picture(file_path=relative_path, reference_folder_id=None, **columns)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic.id

    return server.vault.db.run_task(_add)


def _update_picture(server, picture_id, **columns):
    def _set(session: Session):
        pic = session.get(Picture, picture_id)
        for name, value in columns.items():
            setattr(pic, name, value)
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_set)


def test_an_empty_root_is_not_read_as_a_deleted_library(server):
    """An unmounted drive is an empty directory that exists and is readable -
    Vault.__init__ creates the mount point - so a scan that finds nothing must
    not conclude the owner deleted everything."""
    root = server.vault.image_root
    _make_image(os.path.join(root, "mount", "m.png"), (11, 12, 13))
    _run_root_scan(server)
    (pic,) = _managed(server, "mount/")

    with tempfile.TemporaryDirectory() as empty_root:
        result = ReferenceFolderScanTask(
            database=server.vault.db,
            folder_id=None,
            folder_path=empty_root,
            resolved_path=empty_root,
        )._run_task()

    assert result["removed_count"] == 0, "nothing was scanned, so nothing is gone"
    assert [p.id for p in _managed(server, "mount/")] == [pic.id]


def test_rows_under_a_folder_the_scan_never_entered_are_kept(server):
    """`snapshots/` and `tmp/` are pruned, and so are dot-folders. A row whose
    file lies under one was never looked for; its absence from the listing is a
    question nobody asked, not a deletion."""
    root = server.vault.image_root
    kept = {}
    for folder in ("snapshots", "tmp", ".hidden"):
        relative = f"{folder}/kept.png"
        _make_image(os.path.join(root, folder, "kept.png"), (21, 22, 23))
        kept[relative] = _insert_managed(
            server, relative, pixel_sha=f"kept-{folder}", size_bytes=1
        )
    _settle_root(root)

    _run_root_scan(server)

    for relative, picture_id in kept.items():
        assert [p.id for p in _managed(server, relative)] == [picture_id], relative


def test_a_move_pixlstash_recorded_is_repointed_not_deleted(server):
    """LayoutMoveTask journals the pair before it renames anything. A scan that
    lands inside that window sees a vanished path; with an identical file
    elsewhere in the root, hash pairing refuses, and without the journal the row
    (tags, sets, score) is hard-deleted just before the engine repoints it."""
    root = server.vault.image_root
    original = _make_image(os.path.join(root, "jrn", "a.png"), (31, 32, 33))
    twin = os.path.join(root, "jrn", "twin.png")
    with open(original, "rb") as fh:
        payload = fh.read()
    with open(twin, "wb") as fh:
        fh.write(payload)
    old = time.time() - 600
    os.utime(twin, (old, old))
    _run_root_scan(server)
    moving = next(p for p in _managed(server, "jrn/") if p.file_path == "jrn/a.png")
    _tag(server, moving.id, "keepme")

    renamed = os.path.join(root, "jrn", "a2.png")
    os.rename(original, renamed)

    def _journal(session: Session):
        session.add(
            PictureMove(
                picture_id=moving.id,
                old_path="jrn/a.png",
                new_path="jrn/a2.png",
                moved_at=datetime.utcnow(),
            )
        )
        session.commit()

    server.vault.db.run_task(_journal)
    _settle_root(root)

    result = _run_root_scan(server)

    assert moving.id not in result["moved_picture_ids"], (
        "the twin makes hash pairing refuse"
    )
    after = _managed(server, "jrn/")
    assert sorted(p.file_path for p in after) == ["jrn/a2.png", "jrn/twin.png"], (
        "the journal says PixlStash moved it: repointed, not deleted and "
        "re-imported as a second row"
    )
    assert next(p for p in after if p.file_path == "jrn/a2.png").id == moving.id
    assert _tags(server, moving.id) == ["keepme"]


def test_one_unhashed_scrapheap_row_does_not_block_every_rename(server):
    """MissingPixelShaFinder skips deleted rows, so a scrapheap row with a NULL
    pixel_sha never gets one. Refusing every move while any unhashed file sits
    in the root would make that permanent, for the whole library."""
    root = server.vault.image_root
    stray = _make_image(os.path.join(root, "unh", "stray.png"), (41, 42, 43))
    original = _make_image(os.path.join(root, "unh", "m.png"), (44, 45, 46))
    Image.new("RGB", (64, 64), color=(41, 42, 43)).save(stray, format="PNG")
    old = time.time() - 600
    os.utime(stray, (old, old))
    assert os.path.getsize(stray) != os.path.getsize(original), "distinct sizes"
    _run_root_scan(server)
    scrapped = next(
        p for p in _managed(server, "unh/") if p.file_path.endswith("stray.png")
    )
    moving = next(p for p in _managed(server, "unh/") if p.file_path.endswith("m.png"))
    # A scrapheap row whose file is still on disk and whose hash will never be
    # backfilled: exactly the row that used to freeze move-following for good.
    _update_picture(server, scrapped.id, deleted=True, pixel_sha=None)

    os.rename(original, os.path.join(root, "unh", "m2.png"))
    _settle_root(root)
    result = _run_root_scan(server)

    assert moving.id in result["moved_picture_ids"]
    after = _managed(server, "unh/")
    assert {p.id for p in after} == {moving.id, scrapped.id}, "nothing re-imported"
    assert next(p for p in after if p.id == moving.id).file_path == "unh/m2.png"


def test_the_purge_sweep_leaves_root_owned_rows_to_the_scan(server):
    """The gate is not a one-shot: once a root scan owns the root, the hourly
    sweep must never delete a root-owned row, or it races the scan's deferral
    and writes file_removed=True on a file the owner only renamed."""
    root = server.vault.image_root
    _make_image(os.path.join(root, "swp", "s.png"), (51, 52, 53))
    _run_root_scan(server)
    assert _managed(server, "swp/")

    db = server.vault.db
    assert MissingFilePurgeFinder(db).find_task() is not None, (
        "without a root scan the sweep is still the only reader"
    )
    assert MissingFilePurgeFinder(db, is_ready=lambda: True).find_task() is None, (
        "every picture here is root-owned, so the sweep has nothing to do"
    )
