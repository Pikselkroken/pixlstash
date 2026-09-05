"""In-place rotate: the EXIF-orientation facet and its undo (#950, §21.5).

``POST /pictures/rotate`` turns a photo by rewriting one EXIF field and copying
every pixel byte through. What makes it undoable - where a crop or a re-encode is
not - is that the operation log stores the **whole prior state**: the orientation
the file had, absolutely, and nothing else.

The assertions here are the ones that fail if that stops being true:

1. **Only the orientation is recorded.** Every other consequence of a rotate -
   the face and detection boxes, the pixel digest, the thumbnail dimensions - is
   derived, and a derived value in the recorded state is a second source of truth
   waiting to drift. A test asserting the boxes come back is not enough on its
   own: they could come back *because they were snapshotted*, which is the
   failure. So the box assertion and the "state contains only orientation"
   assertion are made together, on the same operation.
2. **Undo is idempotent.** A recorded *delta* ("this was turned left") would pass
   a single-undo test and turn the picture twice on a retried one. Applying the
   recorded state twice must leave the file byte-identical.
3. **Undo does not walk around a locked set.** The freeze lives at
   ``apply_state_in_session``; an empty-diff design would have skipped it.
4. **Authorization in both directions, at both layers.** The route is
   ``PICTURE_SCOPED`` on ``body_ids="picture_ids"``, so *write-enabled* is
   settled by the auth middleware (a READ token cannot POST here at all) and
   *reaches this picture* by the gate. Section 6 asserts each layer's negative
   next to the positive it must not over-block, and pins that a batch mixing an
   in-scope and an out-of-scope id is refused **whole**.
"""

import gc
import io
import json
import os
import secrets
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlmodel import delete, select

from pixlstash.db_models import (
    Detection,
    Face,
    Operation,
    Picture,
    PictureSet,
    PictureSetMember,
    User,
    UserToken,
)
from pixlstash.db_models.reference_folder import ReferenceFolder
from pixlstash.server import Server
from pixlstash.services import operation_log_service
from pixlstash.tasks import TaskType
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.orientation import read_orientation
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


# The two finders whose whole job is to undo what a rotate does to the derived
# columns. ``apply_orientation`` NULLs them precisely so these sweeps re-derive
# them, which makes "the column is NULL" a state with a background thread racing
# to end it - the sweep interval floors at 0.05 s while there is work, and the
# uploads this module makes keep the planner in exactly that fast cycle.
#
# That is not a product bug to fix, it is the product working; the assertions
# below just cannot be read from a live planner. Detached, the NULL stands still
# and "the rotate NULLed it" is what the assertion actually observes.
_REGENERATION_FINDERS = (
    # ThumbnailGenerationTask selects on `thumbnail_width IS NULL` and writes the
    # regenerated bitmap's dimensions back. On a loaded runner it landed between
    # the rotate's response and the row read, and shard 8 of the CI gate failed
    # on `assert 48 is None` - 48 being the width of the *re*-derived 48x64
    # bitmap, not the stale 64x48 one, so the sweep was demonstrably the writer.
    TaskType.THUMBNAIL_GENERATION,
    # ImageEmbeddingTask selects on `image_embedding IS NULL` and owns the
    # perceptual hash beside it - the same race, one assertion further down.
    TaskType.IMAGE_EMBEDDING,
)


def _disable_regeneration_finders(server):
    """Take the re-derivation sweeps out of this module's planner.

    The planner keeps running: ``POST /pictures/import`` refuses a picture
    outright unless a ``TaskType.FACE_EXTRACTION`` finder is registered
    (``vault.worker_unavailable_reason``), and nearly every test here uploads.
    Only the two finders above go, and neither is consulted by the import path.

    Thumbnail columns are still populated - the import writes them itself
    (``ImageUtils`` renders the bitmap from the uploaded bytes), so a picture
    still arrives with dimensions for the rotate to clear.

    Returns the names of the finders it removed, so ``reset_operation_log`` can
    re-check before every test that they are still gone.
    """
    for task_type in _REGENERATION_FINDERS:
        server.vault._planner_work_finders.pop(task_type)
    # detach_finders() edits the planner's finder structures under its own lock,
    # so this is safe against the loop thread that is running right now.
    return server.vault._work_planner.detach_finders(_REGENERATION_FINDERS)


# ---------------------------------------------------------------------------
# One server for the whole module (see CLAUDE.md: rebuild the assertion, not the
# environment). Per-test isolation is the operation-log truncation below.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _env():
    temp_dir = tempfile.TemporaryDirectory()
    try:
        os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
        server_config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(server_config_path, "w") as handle:
            handle.write(json.dumps({"port": 8000}))
        server = Server(server_config_path)
        disabled_finders = _disable_regeneration_finders(server)
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield client, server, disabled_finders
        finally:
            # The detachment does not need undoing: it edits this server's own
            # planner, and closing the server destroys it.
            server.close()
    finally:
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture
def client(_env):
    return _env[0]


@pytest.fixture
def server(_env):
    return _env[1]


@pytest.fixture(autouse=True)
def reset_operation_log(_env):
    """Every test starts from an empty log.

    These assertions read "the newest operation" and "the recorded state", so an
    earlier test's rows would be read as this test's - an assertion passing for
    the wrong reason. Truncating ``operation`` is the whole reset: nothing
    references it by foreign key and only request-driven code writes it.

    ``picture`` is deliberately not wiped. Each test uploads its own picture and
    asserts on that id, and wiping would force the schedulers to be stopped first
    (SQLite reuses ids, and a finder that has claimed one never releases it).

    The second check is that the re-derivation finders are still detached, so a
    later test cannot silently run with a sweep refilling the columns a rotate
    NULLs. It lives here rather than in a "runs last" canary because the CI gate
    shards tests individually - a canary would only guard its own shard.
    """
    _client, server, disabled_finders = _env

    def _reset(session):
        session.exec(delete(Operation))
        session.commit()

    server.vault.db.run_task(_reset)
    running = server.vault._work_planner.registered_finder_names()
    assert running.isdisjoint(disabled_finders), (
        "a finder that re-derives the columns a rotate NULLs is running again: "
        f"{sorted(running & disabled_finders)}"
    )
    assert _operations(server) == [], (
        "the operation log must be empty at the start of every test; the "
        "truncation above is what makes this module's shared Server safe"
    )
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_counter = [0]


def _upload(client, fmt="JPEG", size=(64, 48)):
    """Upload a fresh, content-distinct picture and return its id."""
    _counter[0] += 1
    n = _counter[0]
    image = Image.new("RGB", size, color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    ext = "jpg" if fmt == "JPEG" else fmt.lower()
    mime = "image/jpeg" if fmt == "JPEG" else f"image/{fmt.lower()}"
    result = upload_pictures_and_wait(
        client, [("file", (f"rot{n}.{ext}", buf.getvalue(), mime))]
    )
    return result["results"][0]["picture_id"]


def _operations(server, **filters):
    return operation_log_service.list_operations(server.vault, limit=100, **filters)


def _recorded_state(server):
    """``(before_state, after_state)`` of the newest operation, as dicts."""

    def _read(session):
        row = session.exec(select(Operation).order_by(Operation.id.desc())).first()
        assert row is not None, "expected an operation to have been recorded"
        return json.loads(row.before_state or "{}"), json.loads(row.after_state or "{}")

    return server.vault.db.run_task(_read)


def _picture_row(server, picture_id):
    def _read(session):
        picture = session.get(Picture, picture_id)
        return {
            "orientation": picture.orientation,
            "width": picture.width,
            "height": picture.height,
            "pixel_sha": picture.pixel_sha,
            "size_bytes": picture.size_bytes,
            "thumbnail_width": picture.thumbnail_width,
            "file_path": picture.file_path,
            "image_embedding": picture.image_embedding,
            "perceptual_hash": picture.perceptual_hash,
        }

    return server.vault.db.run_task(_read)


def _file_path(server, picture_id):
    return ImageUtils.resolve_picture_path(
        server.vault.image_root, _picture_row(server, picture_id)["file_path"]
    )


def _file_bytes(server, picture_id):
    with open(_file_path(server, picture_id), "rb") as handle:
        return handle.read()


def _seed_face(server, picture_id, bbox):
    def _write(session):
        face = Face(picture_id=picture_id, bbox=bbox, face_index=0)
        session.add(face)
        session.commit()
        session.refresh(face)
        return face.id

    return server.vault.db.run_task(_write)


def _seed_detection(server, picture_id, bbox):
    def _write(session):
        detection = Detection(picture_id=picture_id, bbox=bbox, label="cat", score=0.9)
        session.add(detection)
        session.commit()
        session.refresh(detection)
        return detection.id

    return server.vault.db.run_task(_write)


def _face_bbox(server, face_id):
    return server.vault.db.run_task(lambda s: s.get(Face, face_id).bbox)


def _detection_bbox(server, detection_id):
    return server.vault.db.run_task(lambda s: s.get(Detection, detection_id).bbox)


def _lock_picture(server, picture_id, name="frozen"):
    def _lock(session):
        picture_set = PictureSet(name=name, locked=True)
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture_id))
        session.commit()

    server.vault.db.run_task(_lock)


def _rotate(client, picture_ids, direction="cw"):
    return client.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": list(picture_ids), "direction": direction},
    )


# ---------------------------------------------------------------------------
# 1. What gets recorded
# ---------------------------------------------------------------------------


def test_rotate_records_only_the_orientation(client, server):
    """The stop condition: a derived value appearing in the recorded state."""
    picture_id = _upload(client)
    assert _picture_row(server, picture_id)["orientation"] == 1

    resp = _rotate(client, [picture_id], "cw")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rotated_picture_ids"] == [picture_id]
    assert body["unsupported_picture_ids"] == []
    assert body["skipped_picture_ids"] == []
    assert (body["batch_id"] or "").startswith("srv-")

    before, after = _recorded_state(server)
    key = str(picture_id)
    assert before == {key: {"orientation": 1}}, (
        "before_state must carry the orientation and nothing else - a bbox, a "
        "pixel_sha or a thumbnail dimension here is a derived value snapshotted, "
        "which is the second source of truth §21 exists to prevent"
    )
    assert after == {key: {"orientation": 6}}

    assert _operations(server)[0]["op_type"] == "pictures.rotate"
    assert _operations(server)[0]["summary"] == "Rotated 1 picture right"


def test_the_direction_is_not_in_the_op_type(client, server):
    """All three directions record one op_type; only the value differs."""
    recorded = {}
    for direction, expected in (("cw", 6), ("ccw", 8), ("180", 3)):
        picture_id = _upload(client)
        assert _rotate(client, [picture_id], direction).status_code == 200
        _before, after = _recorded_state(server)
        recorded[direction] = (
            _operations(server)[0]["op_type"],
            after[str(picture_id)]["orientation"],
        )
        assert expected == recorded[direction][1]
    assert {op_type for op_type, _ in recorded.values()} == {"pictures.rotate"}


def test_rotate_rewrites_the_file_and_re_derives_what_follows(client, server):
    """The pixels are copied through; the container key and thumbnail are not."""
    picture_id = _upload(client)
    before = _picture_row(server, picture_id)
    assert before["thumbnail_width"] is not None, (
        "the import populates the thumbnail dimensions; without them the NULL "
        "asserted below would prove nothing about the rotate"
    )
    original_pixels = Image.open(_file_path(server, picture_id)).tobytes()

    assert _rotate(client, [picture_id], "cw").status_code == 200

    after = _picture_row(server, picture_id)
    assert read_orientation(_file_path(server, picture_id)) == 6
    assert after["orientation"] == 6
    # RAW dimensions describe the stored bitmap, which did not move.
    assert (after["width"], after["height"]) == (before["width"], before["height"])
    assert Image.open(_file_path(server, picture_id)).tobytes() == original_pixels, (
        "an in-place rotate must not re-encode the image"
    )
    # Derived, and re-derived: the container's bytes changed.
    assert after["pixel_sha"] != before["pixel_sha"]
    assert after["size_bytes"] == os.path.getsize(_file_path(server, picture_id))
    assert after["thumbnail_width"] is None, (
        "the thumbnail dimensions must be NULLed so MissingThumbnailFinder "
        "regenerates the bitmap"
    )


def test_rotate_requeues_the_embedding_and_perceptual_hash(client, server):
    """Both describe the decoded image, which now decodes at a new rotation.

    Left stale they are worse than absent: the near-duplicate tiers would compare
    a turned picture against its own pre-turn neighbours and mis-group it. The
    repair is the codebase's standard one - NULL the column and let the finder
    that selects on it queue the work.
    """
    picture_id = _upload(client)

    def _seed(session):
        picture = session.get(Picture, picture_id)
        picture.image_embedding = b"\x01" * 8
        picture.perceptual_hash = "deadbeefdeadbeef"
        session.add(picture)
        session.commit()

    server.vault.db.run_task(_seed)
    assert _picture_row(server, picture_id)["perceptual_hash"] is not None

    assert _rotate(client, [picture_id], "cw").status_code == 200

    after = _picture_row(server, picture_id)
    assert after["image_embedding"] is None, (
        "MissingImageEmbeddingFinder selects on image_embedding IS NULL; leaving "
        "it set strands a stale embedding of the pre-rotate decode"
    )
    assert after["perceptual_hash"] is None


# ---------------------------------------------------------------------------
# 2. Undo
# ---------------------------------------------------------------------------


def test_undo_restores_the_boxes_without_ever_recording_them(client, server):
    """Boxes come back because they are re-derived, not because they were saved."""
    picture_id = _upload(client)
    face_bbox = [10, 5, 20, 15]
    detection_bbox = [0, 0, 30, 12]
    face_id = _seed_face(server, picture_id, face_bbox)
    detection_id = _seed_detection(server, picture_id, detection_bbox)

    assert _rotate(client, [picture_id], "cw").status_code == 200
    assert _face_bbox(server, face_id) != face_bbox, (
        "face boxes live in EXIF-corrected space, so a rotate must move them"
    )
    assert _detection_bbox(server, detection_id) != detection_bbox

    before, after = _recorded_state(server)
    for state in (before, after):
        assert set(state[str(picture_id)]) == {"orientation"}, (
            "the boxes must not appear in the recorded state - if they do, the "
            "restore below proves nothing about re-derivation"
        )

    resp = client.post(f"{API}/operations/undo")
    assert resp.status_code == 200, resp.text
    assert _face_bbox(server, face_id) == face_bbox
    assert _detection_bbox(server, detection_id) == detection_bbox
    assert read_orientation(_file_path(server, picture_id)) == 1


def test_applying_a_recorded_state_twice_is_a_no_op(client, server):
    """Idempotence - the property a stored delta could not have."""
    picture_id = _upload(client)
    assert _rotate(client, [picture_id], "ccw").status_code == 200
    before, _after = _recorded_state(server)
    image_root = server.vault.image_root

    def _apply(session):
        operation_log_service.apply_state_in_session(
            session, before, "undo an operation", image_root=image_root
        )
        session.commit()

    server.vault.db.run_task(_apply)
    once = _file_bytes(server, picture_id)
    assert read_orientation(_file_path(server, picture_id)) == 1

    server.vault.db.run_task(_apply)
    assert _file_bytes(server, picture_id) == once, (
        "a second application of the same recorded state must change nothing; a "
        "delta ('turned left') would have turned the picture a second time"
    )
    assert _picture_row(server, picture_id)["orientation"] == 1


def test_redo_turns_it_back(client, server):
    picture_id = _upload(client)
    assert _rotate(client, [picture_id], "cw").status_code == 200
    assert client.post(f"{API}/operations/undo").status_code == 200
    assert read_orientation(_file_path(server, picture_id)) == 1

    assert client.post(f"{API}/operations/redo").status_code == 200
    assert read_orientation(_file_path(server, picture_id)) == 6
    assert _picture_row(server, picture_id)["orientation"] == 6


def test_undo_of_a_rotate_on_a_locked_picture_is_refused(client, server):
    """The freeze lives at the restore sink; an empty-diff design would skip it."""
    picture_id = _upload(client)
    assert _rotate(client, [picture_id], "cw").status_code == 200
    _lock_picture(server, picture_id, name="frozen-rotate")

    resp = client.post(f"{API}/operations/undo")
    assert resp.status_code == 423, resp.text
    assert read_orientation(_file_path(server, picture_id)) == 6, (
        "a refused undo must not half-apply: the file stays as the rotate left it"
    )
    assert _operations(server)[0]["status"] == "applied"


def test_rotating_a_locked_picture_is_refused(client, server):
    picture_id = _upload(client)
    _lock_picture(server, picture_id, name="frozen-forward")

    resp = _rotate(client, [picture_id], "cw")
    assert resp.status_code == 423, resp.text
    assert read_orientation(_file_path(server, picture_id)) == 1
    assert _operations(server) == []


# ---------------------------------------------------------------------------
# 3. Which pictures are eligible
# ---------------------------------------------------------------------------


def test_a_reference_folder_picture_is_reported_unsupported(client, server):
    """Someone else's file on a possibly read-only mount is never rewritten."""
    picture_id = _upload(client)

    def _attach(session):
        folder = ReferenceFolder(folder="/tmp/pixlstash-ref-rotate", label="ref")
        session.add(folder)
        session.commit()
        session.refresh(folder)
        picture = session.get(Picture, picture_id)
        picture.reference_folder_id = folder.id
        session.add(picture)
        session.commit()

    server.vault.db.run_task(_attach)

    resp = _rotate(client, [picture_id], "cw")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unsupported_picture_ids"] == [picture_id]
    assert body["rotated_picture_ids"] == []
    assert body["batch_id"] is None
    assert read_orientation(_file_path(server, picture_id)) == 1
    assert _operations(server) == [], "a rotate that changed nothing records nothing"


def test_a_png_rotates_in_place_too(client, server):
    """The eXIf chunk path, and the IDAT bytes copied through untouched."""
    picture_id = _upload(client, fmt="PNG")
    original_pixels = Image.open(_file_path(server, picture_id)).tobytes()

    resp = _rotate(client, [picture_id], "180")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rotated_picture_ids"] == [picture_id]
    assert read_orientation(_file_path(server, picture_id)) == 3
    assert Image.open(_file_path(server, picture_id)).tobytes() == original_pixels


def test_a_bad_direction_is_refused(client, server):
    picture_id = _upload(client)
    resp = _rotate(client, [picture_id], "sideways")
    assert resp.status_code == 400, resp.text
    assert _operations(server) == []


# ---------------------------------------------------------------------------
# 4. Cache version
# ---------------------------------------------------------------------------


def test_the_batch_thumbnail_endpoint_serves_a_version_carrying_the_orientation(
    client, server
):
    """Through the real endpoint, because the helper alone proves nothing.

    ``POST /pictures/thumbnails`` loads pictures with an explicit
    ``select_fields`` allowlist, and the rows outlive that session - so a field
    the handler reads but the allowlist omits is DEFERRED, and reading it raises
    ``DetachedInstanceError`` for every picture in the batch. ``getattr(pic, …,
    None)`` does not soften it: SQLAlchemy raises that, not ``AttributeError``.

    This shipped broken and was caught by hand, because the sibling test below
    calls ``thumbnail_cache_version`` inside a DB task on a session-bound row - it
    exercises the formula and never the endpoint that has to produce it. Assert
    on the response.
    """
    picture_id = _upload(client)

    def _set_dims(session):
        picture = session.get(Picture, picture_id)
        picture.thumbnail_width = 320
        picture.thumbnail_height = 240
        session.add(picture)
        session.commit()

    server.vault.db.run_task(_set_dims)

    def _thumbnail_url():
        resp = client.post(f"{API}/pictures/thumbnails", json={"ids": [picture_id]})
        assert resp.status_code == 200, resp.text
        entry = resp.json().get(str(picture_id))
        assert entry is not None, (
            f"the endpoint returned no entry for picture {picture_id}; a deferred "
            f"field read on a detached row is logged and swallowed per picture, "
            f"so the symptom is a missing entry rather than a 500"
        )
        return entry["thumbnail"]

    before = _thumbnail_url()
    assert "v=320x240" in before

    assert _rotate(client, [picture_id], "180").status_code == 200
    server.vault.db.run_task(_set_dims)  # a regenerated 180° bitmap is the same size

    assert _thumbnail_url() != before, (
        "the served URL must change after a 180° rotate, or the browser paints "
        "the pre-rotate bitmap from an identical URL for up to an hour"
    )


def test_a_180_rotate_changes_the_thumbnail_cache_version(client, server):
    """W and H are unchanged by a 180° turn, so the version needs the orientation.

    Thumbnails are served ``max-age=3600``. On dimensions alone the URL would be
    byte-identical after the rotate and the browser would paint the pre-rotate
    bitmap for up to an hour.

    This is the formula; the endpoint test above is the one that proves a client
    can actually obtain it.
    """
    picture_id = _upload(client)

    def _version(session):
        picture = session.get(Picture, picture_id)
        # Pin the dimensions so the comparison isolates the orientation: a
        # regenerated 180° thumbnail genuinely has the same width and height.
        picture.thumbnail_width = 320
        picture.thumbnail_height = 240
        session.add(picture)
        session.commit()
        return ImageUtils.thumbnail_cache_version(320, 240, picture.orientation)

    before = server.vault.db.run_task(_version)
    assert before == "320x240"

    assert _rotate(client, [picture_id], "180").status_code == 200
    after = server.vault.db.run_task(_version)
    assert after != before, (
        "a 180° rotate leaves the thumbnail's width and height unchanged, so the "
        "cache version must carry the orientation or the browser serves a stale "
        "bitmap from an identical URL"
    )
    assert after == "320x240o3"


def test_the_thumbnail_route_regenerates_a_turned_bitmap_on_the_next_request(
    client, server
):
    """The stored bitmap is stale the moment the file is turned, so serve a new one.

    ``apply_orientation`` NULLs the dimensions to re-queue ``ThumbnailGenerationTask``
    and leaves the ``_thumb.webp`` on disk. Until that sweep lands, ``GET
    /pictures/thumbnails/{id}.webp`` used to hand the pre-rotate bitmap back -
    its source-newer-than-thumbnail check ran only for reference-folder
    pictures - and the grid painted the photo the wrong way round, correcting
    itself later when the sweep announced. This module has that sweep detached
    (``_REGENERATION_FINDERS``), so a bitmap that comes back turned can only
    have been rebuilt by the route.

    The 64x48 source thumbnails to a landscape bitmap; a quarter turn makes it
    portrait, which is the cheapest thing to assert on the response bytes.
    """
    picture_id = _upload(client)

    def _served_size():
        resp = client.get(f"{API}/pictures/thumbnails/{picture_id}.webp")
        assert resp.status_code == 200, resp.text
        with Image.open(io.BytesIO(resp.content)) as bitmap:
            return bitmap.size

    before_w, before_h = _served_size()
    assert before_w > before_h, "expected the 64x48 source to thumbnail landscape"

    assert _rotate(client, [picture_id], "cw").status_code == 200

    after_w, after_h = _served_size()
    assert after_w < after_h, (
        f"the thumbnail route served {after_w}x{after_h} after a quarter turn - "
        f"the pre-rotate bitmap. The grid paints that until the background sweep "
        f"gets round to the picture, which is the wrong-then-right refresh."
    )


def test_a_rotated_png_is_SERVED_turned_because_no_browser_turns_it(client, server):
    """The bug that made the whole feature look broken on a ComfyUI library.

    An in-place rotate writes the EXIF tag and leaves the pixels, which is only
    correct where the renderer applies the tag. Measured 2026-08-18 by writing a
    tag with `write_orientation` and reading `naturalWidth`/`naturalHeight`
    back: Chromium 148 and Firefox 150 both apply it for JPEG and both IGNORE it
    for PNG, exactly as they ignore WebP's. So a rotated PNG showed a turned
    thumbnail beside an unturned full view - and around five-sixths of a ComfyUI
    library is PNG.

    The fix is in the media route, so the assertion has to be on the RESPONSE
    BYTES rather than on the file or the column: those are both correct either
    way, which is precisely why nothing caught this. A landscape source served
    portrait is the whole claim.

    JPEG is the control in the sibling test below: it must keep streaming
    untouched, because turning it here as well would turn it twice on screen.
    """
    picture_id = _upload(client, fmt="PNG", size=(64, 48))

    def _served_size():
        resp = client.get(f"{API}/pictures/{picture_id}.png")
        assert resp.status_code == 200, resp.text
        with Image.open(io.BytesIO(resp.content)) as served:
            return served.size

    assert _served_size() == (64, 48)

    assert _rotate(client, [picture_id], "cw").status_code == 200

    assert _served_size() == (48, 64), (
        "the media route served the PNG unturned. No browser applies a PNG's "
        "eXIf orientation, so this response is the only thing that can turn it "
        " - the lightbox shows these bytes beside an already-turned thumbnail"
    )


def test_a_rotated_jpeg_is_still_streamed_untouched(client, server):
    """The control, and the reason `BROWSER_ORIENTED_FORMATS` is not just empty.

    The browser DOES apply a JPEG's orientation, so transposing it server-side
    as well would turn it twice on screen. The bytes must come back with their
    stored (unturned) dimensions and their orientation tag intact.
    """
    picture_id = _upload(client, fmt="JPEG", size=(64, 48))
    assert _rotate(client, [picture_id], "cw").status_code == 200

    resp = client.get(f"{API}/pictures/{picture_id}.jpeg")
    assert resp.status_code == 200, resp.text
    with Image.open(io.BytesIO(resp.content)) as served:
        assert served.size == (64, 48), (
            "the JPEG came back transposed; the browser will turn it again"
        )
        assert served.getexif().get(0x0112) == 6, (
            "the orientation tag was stripped, so the browser has nothing to "
            "apply and the picture renders flat"
        )


def test_a_served_rotated_png_keeps_its_comfyui_provenance(client, server):
    """A re-encode drops PNG text chunks, and this response is what gets saved.

    `workflow` / `prompt` are how this library recovers a picture's graph, and
    "Save image as" in the lightbox hands the user exactly these bytes. Losing
    them would be a worse bug than the unturned view this branch fixes.
    """
    _counter[0] += 1
    image = Image.new("RGB", (64, 48), color=(11, 22, 33))
    info = PngInfo()
    info.add_text("workflow", '{"nodes": []}')
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=info)
    result = upload_pictures_and_wait(
        client, [("file", ("provenance.png", buf.getvalue(), "image/png"))]
    )
    picture_id = result["results"][0]["picture_id"]

    assert _rotate(client, [picture_id], "ccw").status_code == 200

    resp = client.get(f"{API}/pictures/{picture_id}.png")
    assert resp.status_code == 200, resp.text
    with Image.open(io.BytesIO(resp.content)) as served:
        assert served.size == (48, 64), "expected the turned render"
        assert served.text.get("workflow") == '{"nodes": []}', (
            "the transposing render dropped the PNG text chunks, so a saved "
            "copy of a rotated picture loses its ComfyUI graph"
        )


def test_the_grid_projection_carries_the_orientation(client, server):
    """The lightbox's cache-buster is built from a grid row, so it has to be in one.

    ``mediaVersion`` (frontend/src/utils/media.js) keys the full-size media
    URL's ``?v=`` on the orientation, and the grid's ``prefetchFullImage`` and
    the lightbox's neighbour preloads build that URL from the same row. Drop the
    field from ``Picture.grid_fields()`` - or from ``GridPicture``, which SILENTLY
    strips anything it does not declare - and all three go back to agreeing on
    one unchanging URL, so a turned picture opens on the prefetched pre-rotate
    bytes.
    """
    picture_id = _upload(client)

    def _grid_row():
        resp = client.get(f"{API}/pictures", params={"fields": "grid"})
        assert resp.status_code == 200, resp.text
        rows = [row for row in resp.json() if row["id"] == picture_id]
        assert rows, f"picture {picture_id} is not in the grid listing"
        return rows[0]

    assert "orientation" in _grid_row(), (
        "the grid projection dropped `orientation`; GridPicture drops any key it "
        "does not declare, so this fails silently at the response model too"
    )

    assert _rotate(client, [picture_id], "cw").status_code == 200
    assert _grid_row()["orientation"] == 6


# ---------------------------------------------------------------------------
# 5. Concurrency
# ---------------------------------------------------------------------------


def test_two_concurrent_rotates_do_not_lose_one(client, server):
    """The current orientation is read on the DB queue, not in the handler.

    Read in the handler, two clockwise rotates arriving together would both see
    1, both write 6, and one turn would vanish. Read inside the recorded task -
    which the DB queue serialises - the second sees 6 and writes 3.
    """
    picture_id = _upload(client)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in [
                pool.submit(_rotate, client, [picture_id], "cw") for _ in range(2)
            ]
        ]
    assert [resp.status_code for resp in results] == [200, 200]
    assert all(resp.json()["rotated_picture_ids"] == [picture_id] for resp in results)

    assert read_orientation(_file_path(server, picture_id)) == 3, (
        "two clockwise quarter turns must compose: 1 -> 6 -> 3"
    )
    assert _picture_row(server, picture_id)["orientation"] == 3
    assert len(_operations(server, op_type="pictures.rotate")) == 2


# ---------------------------------------------------------------------------
# 6. Authorization, both directions
# ---------------------------------------------------------------------------


def _shared_set(client, server, picture_ids, name):
    """Create a picture set holding ``picture_ids`` and return its id."""
    set_id = client.post(f"{API}/picture_sets", json={"name": name}).json()[
        "picture_set"
    ]["id"]

    def _add(session):
        for picture_id in picture_ids:
            session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))
        session.commit()

    server.vault.db.run_task(_add)
    return set_id


def _mint_read_token(client, set_id):
    minted = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_id,
        },
    )
    assert minted.status_code == 200, minted.text
    return minted.json()["token"]


def _forge_write_token(server, set_id):
    """Mint a *write-enabled* picture-set-scoped token by writing the hub row.

    ``create_token`` refuses any scope but ``ALL``/``READ``, so a write-enabled
    resource-scoped token has no mint path through the API today - the shape is
    nonetheless fully honoured downstream, and it is the principal this route's
    ``PICTURE_SCOPED`` declaration exists for. The auth middleware builds a
    ``TokenScope`` for **every** non-``ALL`` scope and admits a non-GET only for
    a scope in ``auth.WRITE_ENABLED_SCOPES``, and ``enforce_picture_scope`` reads
    ``resource_type``/``resource_id`` and never ``scope`` - so this row exercises
    exactly the "write-enabled, and does the grant reach the picture" path the
    gate is being asked to decide. Forged rather than minted for the same reason
    ``tests/test_snapshots_auth.py`` forges: the row is the thing under test.

    **The write-ness is now declared rather than accidental (issue #962).** The
    middleware used to refuse a non-GET only when ``scope == "READ"``, so this
    forged ``"WRITE"`` reached the route by skipping a comparison rather than by
    satisfying one. It now keys on ``auth.WRITE_ENABLED_SCOPES``, which names
    ``"WRITE"`` for exactly this shape; an unrecognised scope is refused. So
    this row still writes, and it does so because something says it may.
    """
    token_value = secrets.token_urlsafe(32)

    def _add(session):
        owner = session.exec(select(User)).first()
        assert owner is not None, "owner user must exist for the token to match"
        session.add(
            UserToken(
                user_id=owner.id,
                library_uuid=server._active_library_uuid(),
                token_hash=bcrypt.hash(token_value),
                token_prefix=token_value[:8],
                created_at=datetime.utcnow(),
                description="write-enabled picture-set token (test only)",
                scope="WRITE",
                resource_type="picture_set",
                resource_id=set_id,
            )
        )
        session.commit()

    server.hub_engine.run_task(_add)
    return token_value


def _as(server, token):
    """A client that presents ``token`` as a bearer credential."""
    scoped = TestClient(server.api)
    scoped.headers["Authorization"] = f"Bearer {token}"
    return scoped


def test_a_write_enabled_grant_that_reaches_the_picture_can_rotate_it(client, server):
    """The positive the PICTURE_SCOPED declaration exists for.

    The in-place write is a metadata-only orientation splice - pixels byte for
    byte identical, exactly reversible, refused at the sink for a reference
    folder - so a write-enabled grant that already reaches the picture is
    entitled to it, the same as every other per-picture mutation here.
    """
    picture_id = _upload(client)
    set_id = _shared_set(client, server, [picture_id], "write-grant")
    scoped = _as(server, _forge_write_token(server, set_id))

    resp = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [picture_id], "direction": "cw"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["rotated_picture_ids"] == [picture_id]
    assert read_orientation(_file_path(server, picture_id)) == 6, (
        "the grant covers this picture, so the orientation must actually change"
    )
    assert _picture_row(server, picture_id)["orientation"] == 6


def test_a_write_enabled_grant_that_excludes_the_picture_is_refused_by_the_gate(
    client, server
):
    """The gate's negative: write-enabled, but this picture is not in the grant."""
    granted_id = _upload(client)
    outside_id = _upload(client)
    set_id = _shared_set(client, server, [granted_id], "write-grant-partial")
    scoped = _as(server, _forge_write_token(server, set_id))

    # Positive control: the credential works and the route resolves, so the 403
    # below is a membership refusal and not a dead path or a bad token. The
    # middleware runs ahead of routing and answers a nonexistent path with the
    # same 403, which is what this control rules out.
    allowed = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [granted_id], "direction": "cw"},
    )
    assert allowed.status_code == 200, allowed.text

    refused = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [outside_id], "direction": "cw"},
    )
    assert refused.status_code == 403, refused.text
    assert read_orientation(_file_path(server, outside_id)) == 1, (
        "a refused rotate must not touch the file"
    )
    assert _picture_row(server, outside_id)["orientation"] == 1


def test_a_mixed_batch_is_refused_whole_and_rotates_neither(client, server):
    """One out-of-scope id poisons the whole batch - no partial application.

    The gate resolves ``picture_ids`` element by element and raises on the first
    id outside the grant, *before* the handler body runs, so there is no window
    in which the in-scope picture is already turned. A partial application here
    would be a bug: the caller's next retry would turn it a second time.
    """
    granted_id = _upload(client)
    outside_id = _upload(client)
    set_id = _shared_set(client, server, [granted_id], "write-grant-mixed")
    scoped = _as(server, _forge_write_token(server, set_id))

    refused = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [granted_id, outside_id], "direction": "cw"},
    )
    assert refused.status_code == 403, refused.text
    assert read_orientation(_file_path(server, granted_id)) == 1, (
        "the in-scope picture must NOT have been rotated: a mixed batch is "
        "refused as a whole, never partially applied"
    )
    assert read_orientation(_file_path(server, outside_id)) == 1
    assert _operations(server) == [], "a wholly refused batch records nothing"

    # And the order of the ids does not decide it.
    refused_reversed = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [outside_id, granted_id], "direction": "cw"},
    )
    assert refused_reversed.status_code == 403, refused_reversed.text
    assert read_orientation(_file_path(server, granted_id)) == 1
    assert _operations(server) == []


def test_a_read_only_token_is_refused_by_the_middleware_not_the_gate(client, server):
    """The other layer: READ tokens never reach the gate on this route.

    ``POST /pictures/rotate`` is deliberately absent from
    ``READ_SAFE_POST_PATHS``, so the auth middleware refuses a READ token's POST
    before routing. That is what makes *write-enabled* the operative condition
    and leaves the gate to answer only *does this grant reach this picture*. The
    ``"Token is read-only"`` body is how the two refusals are told apart - the
    gate's says "not authorised to access this picture".
    """
    picture_id = _upload(client)
    set_id = _shared_set(client, server, [picture_id], "read-only-share")
    token = _mint_read_token(client, set_id)
    scoped = _as(server, token)

    # Positive control: the credential is live and the picture is in its grant,
    # so the refusal below is about the *method*, not a dead path or a bad token.
    reachable = scoped.get(f"{API}/pictures/{picture_id}/metadata")
    assert reachable.status_code == 200, reachable.text

    refused = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [picture_id], "direction": "cw"},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == "Token is read-only", (
        "the READ refusal must come from the auth middleware's non-GET block, "
        "not from the gate - if this ever reads as a membership refusal the "
        "route has been added to READ_SAFE_POST_PATHS"
    )

    # The ?token= query-param path bypasses the header and must refuse identically.
    refused_query = TestClient(server.api).post(
        f"{API}/pictures/rotate",
        params={"token": token},
        json={"picture_ids": [picture_id], "direction": "cw"},
    )
    assert refused_query.status_code == 403, refused_query.text
    assert refused_query.json()["detail"] == "Token is read-only"

    assert read_orientation(_file_path(server, picture_id)) == 1
    assert _operations(server) == []


def test_the_owner_is_not_over_blocked(client, server):
    """Over-blocking is its own regression; the owner path stays open."""
    picture_id = _upload(client)
    assert _rotate(client, [picture_id], "cw").status_code == 200
    assert read_orientation(_file_path(server, picture_id)) == 6


def test_undo_announces_that_the_pixels_changed(client, server):
    """A bare ``updated`` leaves the grid painting the pre-rotate bitmap.

    A rotate rewrites the FILE, so the card's thumbnail URL changes - and that
    URL comes from the batch-thumbnail endpoint, never from
    ``GET /pictures/{id}/metadata``. The client's targeted refresh re-reads
    metadata, so without the field being named it repaints the same stale tile
    and the undo looks like it did nothing.

    The forward rotate is safe because the client that issued it refreshes the
    URL itself. Undo arrives over the socket with no such local hook, which is
    why this is asserted on the *undo*.
    """
    picture_id = _upload(client)
    assert _rotate(client, [picture_id], "cw").status_code == 200

    emitted: list[dict] = []
    real_notify = server.vault.notify

    def _capture(event_type, data=None):
        if isinstance(data, dict) and "change_kind" in data:
            emitted.append({"event": event_type, **data})
        return real_notify(event_type, data)

    server.vault.notify = _capture
    try:
        assert client.post(f"{API}/operations/undo").status_code == 200
    finally:
        server.vault.notify = real_notify

    announcements = [
        event
        for event in emitted
        if picture_id in (event.get("picture_ids") or [])
        and event.get("change_kind") == "updated"
    ]
    assert announcements, "the undo announced nothing about the rotated picture"
    assert any("pixels" in (event.get("fields") or []) for event in announcements), (
        "an orientation restore must name `pixels`, or the client refreshes the "
        "card's metadata and goes on painting the pre-rotate thumbnail"
    )
