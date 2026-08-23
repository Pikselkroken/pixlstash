"""The reference-folder scan follows a file moved inside the folder.

A move is one path disappearing and another appearing in the same scan pass.
Handled as a removal plus an import it costs the picture id, and with it the
tags, score, faces and set/stack membership hanging off it, so reorganizing
your own folders used to wipe everything PixlStash had added. The scan now
matches the two halves by pixel hash and updates ``file_path`` instead.

Ambiguous matches are deliberately not followed: identical pixels at several
paths are copies whose rows can differ, and guessing would move one picture's
work onto another picture's file.
"""

import os
import tempfile

import pytest
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Picture, ReferenceFolder, Tag
from pixlstash.server import Server
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.tasks import TaskType
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask

# ``Server.__init__`` calls ``vault.start()``, so the planner is live for the
# whole life of a module-scoped server and its sweeps land *inside* these tests
# rather than sitting in the long backoff a freshly built per-test vault had.
# Each of these owns something a test here writes by hand or asserts on:
_CONFLICTING_FINDERS = (
    # ReferenceFolderScanFinder scans the same folders these tests scan by hand.
    # A sweep between ``_make_folder`` and ``_run_scan`` imports the files first,
    # and the manual scan then imports them again: duplicate rows for one path,
    # which breaks ``len(...) == 1`` and strands the curated row at the dead path
    # because ``existing_by_path`` keeps only the last row per file_path.
    TaskType.REFERENCE_FOLDER_SCAN,
    # MissingFilePurgeTask deletes any picture whose file is absent. Between the
    # ``os.rename`` and the scan that follows it, the moved picture is exactly
    # that, so the sweep destroys the row under test.
    TaskType.MISSING_FILE_PURGE,
    # TagTask deletes a picture's existing Tag rows before writing its own, so a
    # sweep landing after ``_tag`` removes the seeded label ``_tags`` asserts on.
    TaskType.TAGGER,
)


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path) as srv:
            for task_type in _CONFLICTING_FINDERS:
                srv.vault._planner_work_finders.pop(task_type)
            # detach_finders() edits the planner's structures under its own lock,
            # so this is safe against the loop thread running right now.
            srv.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)
            yield srv


def _make_folder(server, folder_dir):
    os.makedirs(folder_dir, exist_ok=True)

    def _insert(session: Session):
        folder = ReferenceFolder(folder=folder_dir, label="refs", status="active")
        session.add(folder)
        session.commit()
        session.refresh(folder)
        return folder.id

    return server.vault.db.run_task(_insert)


def _make_image(path, color, size=8):
    """A flat image at ``path``. ``size`` is the square edge in pixels.

    A large flat BMP is how a sampled-hash collision is produced on purpose: the
    format is uncompressed, so the file is far past the 128 KiB sampling
    threshold and every sampled window is the same repeated pixel.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fmt = "BMP" if path.lower().endswith(".bmp") else "PNG"
    Image.new("RGB", (size, size), color=color).save(path, format=fmt)
    return path


def _run_scan(server, folder_id, folder_dir):
    task = ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=folder_id,
        folder_path=folder_dir,
        resolved_path=folder_dir,
    )
    return task._run_task()


def _pictures(server, folder_dir):
    """Every picture indexed under this test's folder, newest id last.

    Scoped by path rather than by ``reference_folder_id`` because each test
    makes its own folder row against the shared module server, so a query by
    folder id would still have to be told which id, and the path each test
    already owns says it directly. ``tmp_path`` is per-test, so the prefix
    cannot collide with another test in this module.
    """
    return server.vault.db.run_task(
        lambda s: list(
            s.exec(
                select(Picture)
                .where(Picture.file_path.startswith(folder_dir))
                .order_by(Picture.id)
            ).all()
        )
    )


def _tag(server, picture_id, tag):
    def _add(session: Session):
        session.add(Tag(picture_id=picture_id, tag=tag))
        session.commit()

    server.vault.db.run_task(_add)


def _mark(server, picture_id, marker):
    """Stamp a field the scan never writes, so a re-imported row is detectable.

    SQLite reuses free rowids, so a deleted-and-re-added picture can come back
    with the same id: an id assertion alone would pass for the very behaviour
    these tests exist to catch. A marker only the test writes cannot survive a
    re-import.
    """

    def _update(session: Session):
        pic = session.get(Picture, picture_id)
        pic.description = marker
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_update)


def _tags(server, picture_id):
    rows = server.vault.db.run_task(
        lambda s: s.exec(select(Tag).where(Tag.picture_id == picture_id)).all()
    )
    return sorted(t.tag for t in rows if not t.tag.startswith("__"))


def test_moved_file_keeps_its_picture(server, tmp_path):
    """Moving a file into a subfolder must not cost the row or its tags."""
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    original = _make_image(os.path.join(folder_dir, "mira_042.png"), (10, 20, 30))

    _run_scan(server, folder_id, folder_dir)
    indexed = _pictures(server, folder_dir)
    assert len(indexed) == 1
    picture_id = indexed[0].id
    _tag(server, picture_id, "mira")
    _mark(server, picture_id, "keepme")

    moved_to = os.path.join(folder_dir, "Characters", "Mira", "final", "mira_042.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)

    _run_scan(server, folder_id, folder_dir)

    after = _pictures(server, folder_dir)
    assert len(after) == 1, "a move must not leave a second row behind"
    assert after[0].description == "keepme", "the row itself must survive the move"
    assert after[0].id == picture_id
    assert after[0].file_path == moved_to
    assert _tags(server, picture_id) == ["mira"]
    # The thumbnail lives at sha256(file_path), so the stored bitmap is not
    # reachable from the new path. Blank dimensions are what the thumbnail
    # sweep keys on; leaving them set would point the picture at nothing.
    assert after[0].thumbnail_width is None, (
        "a followed move must invalidate the thumbnail it just orphaned"
    )


def test_deleted_file_is_still_removed(server, tmp_path):
    """The rescue must not turn a genuine deletion into a kept row.

    The deletion is paired with an unrelated *addition* so the matcher is
    actually entered. Without one, ``new_paths`` is empty and
    ``_match_moved_paths`` returns on its first line, which would test the
    early return rather than the pairing this is about.
    """
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    doomed = _make_image(os.path.join(folder_dir, "gone.png"), (10, 20, 30))
    _make_image(os.path.join(folder_dir, "kept.png"), (200, 100, 50))

    _run_scan(server, folder_id, folder_dir)
    assert len(_pictures(server, folder_dir)) == 2

    os.remove(doomed)
    # Different pixels, so this is not the deleted file arriving elsewhere.
    _make_image(os.path.join(folder_dir, "unrelated.png"), (7, 200, 90))
    _run_scan(server, folder_id, folder_dir)

    remaining = _pictures(server, folder_dir)
    assert sorted(os.path.basename(p.file_path) for p in remaining) == [
        "kept.png",
        "unrelated.png",
    ]


def test_a_sampled_hash_collision_of_a_different_size_is_not_a_move(server, tmp_path):
    """The size is part of the key, so an equal digest alone cannot pair two files.

    ``calculate_hash_from_file_path`` samples 8 x 8 KiB windows of anything over
    128 KiB and does not mix the size into the digest, which its own docstring
    says makes it a candidate key rather than an identity. Driving the matcher
    directly is what pins that: a collision between two real image files is
    awkward to construct on purpose, and the guard has to hold regardless of how
    the stored ``pixel_sha`` came to be written.
    """
    folder_dir = str(tmp_path / "refs")
    os.makedirs(folder_dir, exist_ok=True)
    arrived = _make_image(os.path.join(folder_dir, "arrived.png"), (10, 20, 30))
    task = ReferenceFolderScanTask(
        database=server.vault.db,
        folder_id=0,
        folder_path=folder_dir,
        resolved_path=folder_dir,
    )
    gone = os.path.join(folder_dir, "gone.png")
    sha = ImageUtils.calculate_hash_from_file_path(arrived)
    real_size = os.path.getsize(arrived)

    def _match(size_bytes):
        return task._match_moved_paths(
            {gone: Picture(file_path=gone, pixel_sha=sha, size_bytes=size_bytes)},
            {arrived},
            {gone},
        )

    assert _match(real_size) == {gone: arrived}, (
        "the same digest and the same size is the move this feature follows"
    )
    assert _match(real_size + 1) == {}, (
        "the same digest at a different size is a different file; pairing them "
        "would rebind one picture's tags and memberships onto another's file"
    )


def test_an_unchanged_twin_makes_the_match_ambiguous(server, tmp_path):
    """A stable file sharing the key blocks the pairing, not just a new one.

    Deleting one copy and separately adding another looks 1:1 when only the
    removed and new sets are counted, but an untouched identical file in the
    folder means there is no telling which row the arrival belongs to.
    """
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    doomed = _make_image(os.path.join(folder_dir, "a.png"), (10, 20, 30))
    _make_image(os.path.join(folder_dir, "b.png"), (10, 20, 30))  # the stable twin

    _run_scan(server, folder_id, folder_dir)
    indexed = _pictures(server, folder_dir)
    assert len(indexed) == 2
    doomed_id = next(p.id for p in indexed if p.file_path == doomed)
    _mark(server, doomed_id, "keepme")

    os.remove(doomed)
    _make_image(os.path.join(folder_dir, "c.png"), (10, 20, 30))
    _run_scan(server, folder_id, folder_dir)

    after = _pictures(server, folder_dir)
    assert sorted(os.path.basename(p.file_path) for p in after) == ["b.png", "c.png"]
    assert not any(p.description == "keepme" for p in after), (
        "an unchanged file shares the key, so the arrival cannot be attributed "
        "to the removal; re-import rather than guess"
    )


def test_ambiguous_pixel_match_is_not_followed(server, tmp_path):
    """Two copies moved at once must not have their rows paired by guess."""
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    # Same colour, so both files carry the same pixel hash.
    first = _make_image(os.path.join(folder_dir, "copy_a.png"), (10, 20, 30))
    second = _make_image(os.path.join(folder_dir, "copy_b.png"), (10, 20, 30))

    _run_scan(server, folder_id, folder_dir)
    indexed = _pictures(server, folder_dir)
    assert len(indexed) == 2
    _mark(server, indexed[0].id, "keepme")

    sub_dir = os.path.join(folder_dir, "sub")
    os.makedirs(sub_dir, exist_ok=True)
    os.rename(first, os.path.join(sub_dir, "copy_a.png"))
    os.rename(second, os.path.join(sub_dir, "copy_b.png"))
    _run_scan(server, folder_id, folder_dir)

    after = _pictures(server, folder_dir)
    assert len(after) == 2
    assert not any(p.description == "keepme" for p in after), (
        "a 2:2 pixel match is ambiguous; the scan must re-import rather than "
        "bind one picture's row to the other's file"
    )
