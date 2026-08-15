"""In-place rotate: the EXIF-orientation facet and its undo (#950, §21.5).

``POST /pictures/rotate`` turns a photo by rewriting one EXIF field and copying
every pixel byte through. What makes it undoable — where a crop or a re-encode is
not — is that the operation log stores the **whole prior state**: the orientation
the file had, absolutely, and nothing else.

The assertions here are the ones that fail if that stops being true:

1. **Only the orientation is recorded.** Every other consequence of a rotate —
   the face and detection boxes, the pixel digest, the thumbnail dimensions — is
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
4. **Authorization in both directions.** ``OWNER_ONLY`` is the odd tier on this
   surface, so the negative (a scoped share token is refused) and the positive
   (the owner still works) are asserted side by side — over-blocking would be its
   own regression.
"""

import gc
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import delete, select

from pixlstash.db_models import (
    Detection,
    Face,
    Operation,
    Picture,
    PictureSet,
    PictureSetMember,
)
from pixlstash.db_models.reference_folder import ReferenceFolder
from pixlstash.server import Server
from pixlstash.services import operation_log_service
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.orientation import read_orientation
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


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
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield client, server
        finally:
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
    earlier test's rows would be read as this test's — an assertion passing for
    the wrong reason. Truncating ``operation`` is the whole reset: nothing
    references it by foreign key and only request-driven code writes it.

    ``picture`` is deliberately not wiped. Each test uploads its own picture and
    asserts on that id, and wiping would force the schedulers to be stopped first
    (SQLite reuses ids, and a finder that has claimed one never releases it).
    """
    _client, server = _env

    def _reset(session):
        session.exec(delete(Operation))
        session.commit()

    server.vault.db.run_task(_reset)
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
        "before_state must carry the orientation and nothing else — a bbox, a "
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
    repair is the codebase's standard one — NULL the column and let the finder
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
            "the boxes must not appear in the recorded state — if they do, the "
            "restore below proves nothing about re-derivation"
        )

    resp = client.post(f"{API}/operations/undo")
    assert resp.status_code == 200, resp.text
    assert _face_bbox(server, face_id) == face_bbox
    assert _detection_bbox(server, detection_id) == detection_bbox
    assert read_orientation(_file_path(server, picture_id)) == 1


def test_applying_a_recorded_state_twice_is_a_no_op(client, server):
    """Idempotence — the property a stored delta could not have."""
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
# 4. Cache token
# ---------------------------------------------------------------------------


def test_a_180_rotate_changes_the_thumbnail_cache_token(client, server):
    """W and H are unchanged by a 180° turn, so the token needs the orientation.

    Thumbnails are served ``max-age=3600``. On dimensions alone the URL would be
    byte-identical after the rotate and the browser would paint the pre-rotate
    bitmap for up to an hour.
    """
    picture_id = _upload(client)

    def _token(session):
        picture = session.get(Picture, picture_id)
        # Pin the dimensions so the comparison isolates the orientation: a
        # regenerated 180° thumbnail genuinely has the same width and height.
        picture.thumbnail_width = 320
        picture.thumbnail_height = 240
        session.add(picture)
        session.commit()
        return ImageUtils.thumbnail_cache_token(320, 240, picture.orientation)

    before = server.vault.db.run_task(_token)
    assert before == "320x240"

    assert _rotate(client, [picture_id], "180").status_code == 200
    after = server.vault.db.run_task(_token)
    assert after != before, (
        "a 180° rotate leaves the thumbnail's width and height unchanged, so the "
        "cache token must carry the orientation or the browser serves a stale "
        "bitmap from an identical URL"
    )
    assert after == "320x240o3"


# ---------------------------------------------------------------------------
# 5. Concurrency
# ---------------------------------------------------------------------------


def test_two_concurrent_rotates_do_not_lose_one(client, server):
    """The current orientation is read on the DB queue, not in the handler.

    Read in the handler, two clockwise rotates arriving together would both see
    1, both write 6, and one turn would vanish. Read inside the recorded task —
    which the DB queue serialises — the second sees 6 and writes 3.
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


def test_a_scoped_share_token_cannot_rotate_but_the_owner_can(client, server):
    """OWNER_ONLY: the first write path that alters the owner's original bytes.

    Both directions in one test on purpose — a negative that passes because the
    credential was missing rather than because the scope was refused is a silent
    coverage loss, and over-blocking the owner is its own regression.
    """
    picture_id = _upload(client)
    set_id = client.post(f"{API}/picture_sets", json={"name": "shared"}).json()[
        "picture_set"
    ]["id"]

    def _add(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))
        session.commit()

    server.vault.db.run_task(_add)

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
    token = minted.json()["token"]

    scoped = TestClient(server.api)
    # Positive control: the route resolves and the credential works elsewhere, so
    # the 403 below is a scope refusal rather than a 404 or a dead path.
    reachable = scoped.get(
        f"{API}/pictures/{picture_id}/metadata",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reachable.status_code == 200, reachable.text

    refused = scoped.post(
        f"{API}/pictures/rotate",
        json={"picture_ids": [picture_id], "direction": "cw"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert refused.status_code == 403, refused.text
    # And through the ?token= query-param path, which bypasses the header.
    refused_query = scoped.post(
        f"{API}/pictures/rotate",
        params={"token": token},
        json={"picture_ids": [picture_id], "direction": "cw"},
    )
    assert refused_query.status_code == 403, refused_query.text

    assert read_orientation(_file_path(server, picture_id)) == 1
    assert _operations(server) == []

    # Positive: the owner is not over-blocked.
    assert _rotate(client, [picture_id], "cw").status_code == 200
    assert read_orientation(_file_path(server, picture_id)) == 6
