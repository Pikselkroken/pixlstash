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
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    Project,
    ReferenceFolder,
    Tag,
)
from pixlstash.db_models.external_move_review import ExternalMoveReview
from pixlstash.db_models.operation import Operation
from pixlstash.server import Server
from pixlstash.services import move_reconciliation_service
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.library_layout import DEFAULT_LAYOUT, format_layout
from pixlstash.tasks import TaskType
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask

# ``Server.__init__`` calls ``vault.start()``, so the planner is live for the
# whole life of a module-scoped server and its sweeps land *inside* these tests
# rather than sitting in the long backoff a freshly built per-test vault had.
# Each of these owns something a test here writes by hand or asserts on:
_CONFLICTING_FINDERS = (
    # ThumbnailGenerationTask selects on ``thumbnail_width IS NULL`` and writes
    # the regenerated bitmap's dimensions back, so it can both repopulate a row
    # this module expects to stay blank and re-render a bitmap whose absence is
    # being asserted. Same reasoning as tests/test_inline_rotate.py.
    TaskType.THUMBNAIL_GENERATION,
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


def _make_folder(server, folder_dir, layout=None):
    """Register a reference folder that the background scanner will leave alone.

    ``ReferenceFolderScanFinder`` selects on ``last_scanned``, not on status: a
    row with NULL there is due for a scan *immediately*, and every folder in the
    database is a candidate. So between this insert and the first ``_run_scan``
    the planner is free to scan the same folder concurrently, and both passes
    import the same files - the CI gate failed on exactly that, twice, with
    ``assert 2 == 1`` and ``assert 4 == 2``: every row doubled.

    Stamping ``last_scanned`` now puts the folder outside the finder's rescan
    interval from the moment it exists, which closes the window by construction
    rather than by relying on the finder staying detached. That matters because
    the detachment is done once against the vault the fixture saw, and a vault
    can be rebuilt underneath it (``library_switch_service`` assigns a freshly
    built one, with every finder re-attached). The scan task itself re-stamps
    the column when it completes, so later scans stay outside the interval too.
    """
    os.makedirs(folder_dir, exist_ok=True)

    def _insert(session: Session):
        folder = ReferenceFolder(
            folder=folder_dir,
            label="refs",
            status="active",
            last_scanned=time.time(),
            layout=layout,
        )
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
    assert [os.path.basename(p.file_path) for p in indexed] == ["mira_042.png"]
    picture_id = indexed[0].id
    thumb_width = indexed[0].thumbnail_width
    assert thumb_width is not None, "the import must have rendered a thumbnail"
    _tag(server, picture_id, "mira")
    _mark(server, picture_id, "keepme")

    # Renamed as well as relocated, so the basename genuinely changes: the
    # download name is taken from it, and the explicit move route resets it too.
    moved_to = os.path.join(folder_dir, "Characters", "Mira", "final", "final_042.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)

    _run_scan(server, folder_id, folder_dir)

    after = _pictures(server, folder_dir)
    assert len(after) == 1, "a move must not leave a second row behind"
    assert after[0].description == "keepme", "the row itself must survive the move"
    assert after[0].id == picture_id
    assert after[0].file_path == moved_to
    assert _tags(server, picture_id) == ["mira"]
    # The thumbnail lives at sha256(file_path), so it has to travel with the
    # file. Nothing sweeps .ref_thumbs by anything but a row's current
    # file_path, so a bitmap left at the old name would never be collected.
    image_root = server.vault.db.image_root
    assert not os.path.exists(ImageUtils.get_thumbnail_path(image_root, original)), (
        "the bitmap at the old path-derived name would be an unreachable orphan"
    )
    assert os.path.exists(ImageUtils.get_thumbnail_path(image_root, moved_to))
    assert after[0].thumbnail_width == thumb_width, (
        "the bitmap was carried, so it must not be blanked for re-rendering"
    )
    assert after[0].original_file_name == "final_042.png", (
        "a renamed file must not keep downloading under its old name"
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
    assert sorted(
        os.path.basename(p.file_path) for p in _pictures(server, folder_dir)
    ) == ["gone.png", "kept.png"]

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


def test_a_scrapheap_row_does_not_swallow_an_unrelated_new_file(server, tmp_path):
    """A soft-deleted row must not claim a new file of the same content.

    ``fetch_existing`` loads ``deleted=True`` rows on purpose, so without the
    exclusion a hidden scrapheap picture whose file really was deleted pairs
    with an unrelated arrival: the new path is taken out of ``new_paths`` as a
    move, and what the user gets is not a new picture but a soft-deleted one
    they cannot see.
    """
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    doomed = _make_image(os.path.join(folder_dir, "binned.png"), (10, 20, 30))

    _run_scan(server, folder_id, folder_dir)
    indexed = _pictures(server, folder_dir)
    assert [os.path.basename(p.file_path) for p in indexed] == ["binned.png"]

    def _scrapheap(session: Session):
        pic = session.get(Picture, indexed[0].id)
        pic.deleted = True
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_scrapheap)

    # The binned file goes, and an unrelated file of the same content arrives.
    os.remove(doomed)
    _make_image(os.path.join(folder_dir, "fresh.png"), (10, 20, 30))
    _run_scan(server, folder_id, folder_dir)

    live = [p for p in _pictures(server, folder_dir) if not p.deleted]
    assert [os.path.basename(p.file_path) for p in live] == ["fresh.png"], (
        "the arrival is its own picture, not a scrapheap row quietly relabelled"
    )


def test_an_unhashed_unchanged_file_blocks_matching(server, tmp_path):
    """A stable row with no pixel_sha means 'unknown collision', not 'no collision'.

    ``pixel_sha`` is nullable and backfilled in the background, so a present
    unchanged file can be invisible to the ambiguity count - and it is exactly
    the file whose existence would have refused the match.
    """
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    doomed = _make_image(os.path.join(folder_dir, "a.png"), (10, 20, 30))
    twin = _make_image(os.path.join(folder_dir, "b.png"), (10, 20, 30))

    _run_scan(server, folder_id, folder_dir)
    indexed = _pictures(server, folder_dir)
    assert sorted(os.path.basename(p.file_path) for p in indexed) == ["a.png", "b.png"]
    doomed_id = next(p.id for p in indexed if p.file_path == doomed)
    _mark(server, doomed_id, "keepme")

    # The twin loses its hash the way an un-backfilled row would have it.
    def _blank_sha(session: Session):
        pic = session.exec(select(Picture).where(Picture.file_path == twin)).one()
        pic.pixel_sha = None
        session.add(pic)
        session.commit()

    server.vault.db.run_task(_blank_sha)

    os.remove(doomed)
    _make_image(os.path.join(folder_dir, "c.png"), (10, 20, 30))
    _run_scan(server, folder_id, folder_dir)

    after = _pictures(server, folder_dir)
    assert not any(p.description == "keepme" for p in after), (
        "the unchanged twin is invisible to the count while its hash is NULL, "
        "so the match cannot be shown to be 1:1 and must not be followed"
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
    assert sorted(os.path.basename(p.file_path) for p in indexed) == ["a.png", "b.png"]
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
    assert sorted(os.path.basename(p.file_path) for p in indexed) == [
        "copy_a.png",
        "copy_b.png",
    ]
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


def test_a_move_in_a_laid_out_folder_is_queued_for_reconciliation(server, tmp_path):
    """v1.11 Phase 5: a laid-out root's move is a fact worth reconciling.

    The queue only needs the file to have moved and the root to have a
    layout - classifying what it means happens later, live, in
    move_reconciliation_service (see tests/test_library_layout.py).
    """
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, layout=format_layout(DEFAULT_LAYOUT))
    original = _make_image(
        os.path.join(folder_dir, "2024 Shoots", "mira.png"), (10, 20, 30)
    )
    _run_scan(server, folder_id, folder_dir)
    picture_id = _pictures(server, folder_dir)[0].id

    moved_to = os.path.join(folder_dir, "Client Nordvik", "mira.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)
    result = _run_scan(server, folder_id, folder_dir)

    assert result["external_moved_picture_ids"] == [picture_id]
    assert result["external_moves_queued_for_review"] == [picture_id]

    rows = server.vault.db.run_task(
        lambda s: s.exec(
            select(ExternalMoveReview).where(
                ExternalMoveReview.picture_id == picture_id
            )
        ).all()
    )
    assert len(rows) == 1
    assert rows[0].old_path == original
    assert rows[0].new_path == moved_to


def test_a_move_in_a_folder_without_a_layout_is_not_queued(server, tmp_path):
    """The move is still followed; there is just nothing to reconcile it against."""
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir)
    original = _make_image(
        os.path.join(folder_dir, "2024 Shoots", "mira.png"), (10, 20, 30)
    )
    _run_scan(server, folder_id, folder_dir)
    picture_id = _pictures(server, folder_dir)[0].id

    moved_to = os.path.join(folder_dir, "Client Nordvik", "mira.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)
    result = _run_scan(server, folder_id, folder_dir)

    assert result["external_moved_picture_ids"] == [picture_id]
    assert result["external_moves_queued_for_review"] == []

    rows = server.vault.db.run_task(
        lambda s: s.exec(
            select(ExternalMoveReview).where(
                ExternalMoveReview.picture_id == picture_id
            )
        ).all()
    )
    assert rows == []


def _make_project(server, name):
    def _insert(session: Session):
        project = Project(name=name)
        session.add(project)
        session.commit()
        session.refresh(project)
        return project.id

    return server.vault.db.run_task(_insert)


def _assign_project(server, picture_id, project_id):
    def _update(session: Session):
        pic = session.get(Picture, picture_id)
        pic.project_id = project_id
        session.add(pic)
        session.add(PictureProjectMember(picture_id=picture_id, project_id=project_id))
        session.commit()

    server.vault.db.run_task(_update)


def _pending_review(server, picture_id):
    return server.vault.db.run_task(
        lambda s: s.exec(
            select(ExternalMoveReview).where(
                ExternalMoveReview.picture_id == picture_id
            )
        ).first()
    )


def test_applying_an_unambiguous_move_swaps_the_project_and_clears_the_queue(
    server, tmp_path
):
    """The end-to-end path: scan queues it, apply reconciles it, undo is recorded."""
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, layout=format_layout(DEFAULT_LAYOUT))
    old_project = _make_project(server, "2024 Shoots")
    new_project = _make_project(server, "Client Nordvik")
    original = _make_image(
        os.path.join(folder_dir, "2024 Shoots", "mira.png"), (10, 20, 30)
    )
    _run_scan(server, folder_id, folder_dir)
    picture_id = _pictures(server, folder_dir)[0].id
    _assign_project(server, picture_id, old_project)

    moved_to = os.path.join(folder_dir, "Client Nordvik", "mira.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)
    _run_scan(server, folder_id, folder_dir)

    review = _pending_review(server, picture_id)
    assert review is not None

    result = move_reconciliation_service.apply_reviews(server.vault, [review.id])
    assert result["applied_picture_ids"] == [picture_id]

    picture = server.vault.db.run_task(lambda s: s.get(Picture, picture_id))
    assert picture.project_id == new_project

    memberships = server.vault.db.run_task(
        lambda s: sorted(
            m.project_id
            for m in s.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == picture_id
                )
            ).all()
        )
    )
    assert memberships == [new_project]
    assert _pending_review(server, picture_id) is None, "the row must clear either way"

    operations = server.vault.db.run_task(
        lambda s: s.exec(
            select(Operation).where(
                Operation.op_type
                == move_reconciliation_service.OP_EXTERNAL_MOVE_RECONCILE
            )
        ).all()
    )
    assert len(operations) == 1, (
        "an applied reconciliation must be one undoable operation"
    )


def test_dismissing_a_move_clears_the_queue_without_changing_membership(
    server, tmp_path
):
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, layout=format_layout(DEFAULT_LAYOUT))
    old_project = _make_project(server, "2024 Shoots (dismiss)")
    original = _make_image(
        os.path.join(folder_dir, "2024 Shoots (dismiss)", "mira.png"), (10, 20, 30)
    )
    _run_scan(server, folder_id, folder_dir)
    picture_id = _pictures(server, folder_dir)[0].id
    _assign_project(server, picture_id, old_project)

    moved_to = os.path.join(folder_dir, "Client Nordvik (dismiss)", "mira.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)
    _run_scan(server, folder_id, folder_dir)

    review = _pending_review(server, picture_id)
    assert review is not None

    result = move_reconciliation_service.dismiss_reviews(server.vault, [review.id])
    assert result["dismissed_review_ids"] == [review.id]

    picture = server.vault.db.run_task(lambda s: s.get(Picture, picture_id))
    assert picture.project_id == old_project, "dismissing must not touch any assignment"
    assert _pending_review(server, picture_id) is None


def test_the_http_routes_round_trip_an_unambiguous_move(server, tmp_path):
    """GET /moves/pending, POST /moves/apply - the wiring, not the classification.

    Everything about *what* gets classified is tested at the service level
    above; this is the one place that proves the route table, the response
    models and the authz gate actually agree on the shape.
    """
    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, layout=format_layout(DEFAULT_LAYOUT))
    old_project = _make_project(server, "2024 Shoots (http)")
    new_project = _make_project(server, "Client Nordvik (http)")
    original = _make_image(
        os.path.join(folder_dir, "2024 Shoots (http)", "mira.png"), (10, 20, 30)
    )
    _run_scan(server, folder_id, folder_dir)
    picture_id = _pictures(server, folder_dir)[0].id
    _assign_project(server, picture_id, old_project)

    moved_to = os.path.join(folder_dir, "Client Nordvik (http)", "mira.png")
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)
    _run_scan(server, folder_id, folder_dir)

    client = TestClient(server.api)
    login = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login.status_code == 200, login.text

    pending = client.get("/api/v1/moves/pending")
    assert pending.status_code == 200, pending.text
    body = pending.json()
    review_ids = [
        item["review_id"]
        for item in body["unambiguous"]
        if item["picture_id"] == picture_id
    ]
    assert review_ids, body

    applied = client.post("/api/v1/moves/apply", json={"review_ids": review_ids})
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied_picture_ids"] == [picture_id]

    picture = server.vault.db.run_task(lambda s: s.get(Picture, picture_id))
    assert picture.project_id == new_project

    dismiss_empty = client.post("/api/v1/moves/dismiss", json={"review_ids": []})
    assert dismiss_empty.status_code == 200
    assert dismiss_empty.json()["dismissed_review_ids"] == []


def test_applying_a_move_to_a_non_unique_set_name_is_skipped_not_guessed(
    server, tmp_path
):
    """Set names are not DB-unique - a collision must be refused, not applied.

    The refusal happens at entity resolution, after the picture already left
    the queue by review_id, so the caller has to be able to tell "applied
    nothing because it was refused" apart from "applied nothing because there
    was nothing to apply".
    """

    def _make_duplicate_sets(session):
        first = PictureSet(name="Summer (skip)")
        second = PictureSet(name="Summer (skip)")
        session.add_all([first, second])
        session.commit()

    server.vault.db.run_task(_make_duplicate_sets)

    folder_dir = str(tmp_path / "refs")
    folder_id = _make_folder(server, folder_dir, layout=format_layout(DEFAULT_LAYOUT))
    project_id = _make_project(server, "2024 Shoots (skip)")
    original = _make_image(
        os.path.join(folder_dir, "2024 Shoots (skip)", "mira.png"), (10, 20, 30)
    )
    _run_scan(server, folder_id, folder_dir)
    picture_id = _pictures(server, folder_dir)[0].id
    _assign_project(server, picture_id, project_id)

    moved_to = os.path.join(
        folder_dir, "2024 Shoots (skip)", "Summer (skip)", "mira.png"
    )
    os.makedirs(os.path.dirname(moved_to), exist_ok=True)
    os.rename(original, moved_to)
    _run_scan(server, folder_id, folder_dir)

    review = _pending_review(server, picture_id)
    assert review is not None

    result = move_reconciliation_service.apply_reviews(server.vault, [review.id])
    assert result["applied_picture_ids"] == []
    assert result["skipped_review_ids"] == [review.id]

    members = server.vault.db.run_task(
        lambda s: s.exec(
            select(PictureSetMember).where(PictureSetMember.picture_id == picture_id)
        ).all()
    )
    assert members == [], "a non-unique name must never be guessed"
    assert _pending_review(server, picture_id) is None, "the row must clear either way"


def test_a_reference_folder_cannot_be_given_a_layout_over_http(server, tmp_path):
    """v1.11 ships the field disarmed.

    Arming it turns the automatic mover loose inside a tree the owner curates
    by hand, with no UI, no preview and no migration route in front of it. The
    Phase 5 machinery behind it is still exercised at the service level above -
    those tests insert the layout directly - so refusing here disarms the
    reachable surface without giving up the coverage.
    """
    folder_dir = str(tmp_path / "refs-layout")
    folder_id = _make_folder(server, folder_dir)

    client = TestClient(server.api)
    login = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert login.status_code == 200, login.text

    armed = client.patch(
        f"/api/v1/reference-folders/{folder_id}",
        json={"layout": format_layout(DEFAULT_LAYOUT)},
    )
    assert armed.status_code == 400, armed.text
    assert "curate yourself" in armed.json()["detail"]

    # Turning it off stays available - that is the default and the way back.
    disarmed = client.patch(
        f"/api/v1/reference-folders/{folder_id}", json={"layout": None}
    )
    assert disarmed.status_code == 200, disarmed.text

    row = server.vault.db.run_task(lambda s: s.get(ReferenceFolder, folder_id))
    assert row.layout is None

    # An unrelated edit is not collateral damage.
    relabelled = client.patch(
        f"/api/v1/reference-folders/{folder_id}", json={"label": "still editable"}
    )
    assert relabelled.status_code == 200, relabelled.text
