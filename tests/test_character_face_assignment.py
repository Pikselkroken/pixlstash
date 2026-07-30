"""Batch face assignment by picture_ids: best-face selection heuristics.

Covers the bootstrap heuristic for a character with no reference faces yet
(issue #645): single-face pictures in the batch are assigned first and then
serve as the comparison set for the multi-face pictures, so group shots pick
the same identity instead of simply the largest face.

Seeding follows the deterministic pattern of ``test_likeness_and_face_search``:
faces are inserted directly with explicit unit-ish feature vectors, so every
similarity in the assertions is arithmetic rather than a property of a real
face model.
"""

import os
import tempfile

import numpy as np
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models.face import Face
from pixlstash.server import Server
from tests.test_server import random_images
from tests.utils import API_PREFIX, upload_pictures_and_wait

ALONG_X = np.asarray([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32).tobytes()
ALONG_Y = np.asarray([0, 1, 0, 0, 0, 0, 0, 0], dtype=np.float32).tobytes()
# 0.9 of the way toward +x: a strong match for ALONG_X, weak for ALONG_Y.
NEAR_X = np.asarray([0.9, 0.436, 0, 0, 0, 0, 0, 0], dtype=np.float32).tobytes()

SMALL_BBOX = [0, 0, 10, 10]
LARGE_BBOX = [0, 0, 200, 200]


def _add_face(server, pic_id, features, bbox, face_index=0, character_id=None):
    """Insert one Face row with explicit features and bbox; return its id."""
    holder = {}

    def _add(session):
        face = Face(
            picture_id=pic_id,
            frame_index=0,
            face_index=face_index,
            character_id=character_id,
            bbox=bbox,
            features=features,
        )
        session.add(face)
        session.commit()
        session.refresh(face)
        holder["id"] = face.id

    server.vault.db.run_task(_add)
    return holder["id"]


def _face_character_ids(server, face_ids):
    """Map face id to its current character_id, read straight from the DB."""

    def _fetch(session):
        rows = session.exec(
            select(Face.id, Face.character_id).where(Face.id.in_(list(face_ids)))
        ).all()
        return {int(fid): cid for fid, cid in rows}

    return server.vault.db.run_task(_fetch)


def _upload(client, count):
    """Upload ``count`` noise pictures and return their picture ids."""
    images = [
        ("file", (f"img{i}.png", random_images[i], "image/png")) for i in range(count)
    ]
    import_status = upload_pictures_and_wait(client, images)
    assert import_status["status"] == "completed"
    return [r["picture_id"] for r in import_status["results"]]


def _create_character(client, name):
    resp = client.post(f"{API_PREFIX}/characters", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["character"]["id"]


def _assign_pictures(client, character_id, picture_ids):
    resp = client.post(
        f"{API_PREFIX}/characters/{character_id}/faces",
        json={"picture_ids": picture_ids},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    return body


def test_new_character_bootstraps_multi_face_pictures_from_solo_shots():
    """A brand-new character assigned a mixed batch: the solo shots are
    assigned directly and their faces become the comparison set for the group
    shot, so the group shot assigns the matching (smaller) face instead of the
    largest bystander. The multi-face picture comes first in the request to
    prove the two-pass ordering, not the request order, decides."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            group_pid, solo1_pid, solo2_pid = _upload(client, 3)
            character_id = _create_character(client, "Newcomer")

            solo1_fid = _add_face(server, solo1_pid, ALONG_X, SMALL_BBOX)
            solo2_fid = _add_face(server, solo2_pid, ALONG_X, SMALL_BBOX)
            bystander_fid = _add_face(
                server, group_pid, ALONG_Y, LARGE_BBOX, face_index=0
            )
            target_fid = _add_face(server, group_pid, NEAR_X, SMALL_BBOX, face_index=1)

            body = _assign_pictures(
                client, character_id, [group_pid, solo1_pid, solo2_pid]
            )

            assigned = _face_character_ids(
                server, [solo1_fid, solo2_fid, bystander_fid, target_fid]
            )
            assert assigned[solo1_fid] == character_id
            assert assigned[solo2_fid] == character_id
            assert assigned[target_fid] == character_id, (
                f"group shot assigned the wrong face: {assigned}, response {body}"
            )
            assert assigned[bystander_fid] is None, (
                f"largest bystander face was assigned: {assigned}"
            )
            assert set(body["face_ids"]) == {solo1_fid, solo2_fid, target_fid}


def test_new_character_all_multi_face_keeps_largest_face_fallback():
    """With no reference faces and no single-face pictures to bootstrap from,
    the largest-face fallback still applies."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            (group_pid,) = _upload(client, 1)
            character_id = _create_character(client, "Newcomer")

            largest_fid = _add_face(
                server, group_pid, ALONG_Y, LARGE_BBOX, face_index=0
            )
            smaller_fid = _add_face(server, group_pid, NEAR_X, SMALL_BBOX, face_index=1)

            body = _assign_pictures(client, character_id, [group_pid])

            assigned = _face_character_ids(server, [largest_fid, smaller_fid])
            assert assigned[largest_fid] == character_id, (
                f"largest-face fallback did not apply: {assigned}, response {body}"
            )
            assert assigned[smaller_fid] is None
            assert body["face_ids"] == [largest_fid]


def test_existing_reference_faces_still_drive_selection():
    """A character that already has reference faces keeps the pre-existing
    behaviour: the multi-face picture assigns the face most like the
    references, even when it is not the largest."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            ref_pid, group_pid = _upload(client, 2)
            character_id = _create_character(client, "Established")

            # Pre-assigned reference face: makes
            # select_reference_faces_for_character return a non-empty set.
            ref_fid = _add_face(
                server, ref_pid, ALONG_X, SMALL_BBOX, character_id=character_id
            )
            bystander_fid = _add_face(
                server, group_pid, ALONG_Y, LARGE_BBOX, face_index=0
            )
            target_fid = _add_face(server, group_pid, NEAR_X, SMALL_BBOX, face_index=1)

            body = _assign_pictures(client, character_id, [group_pid])

            assigned = _face_character_ids(server, [ref_fid, bystander_fid, target_fid])
            assert assigned[ref_fid] == character_id
            assert assigned[target_fid] == character_id, (
                f"reference-likeness selection regressed: {assigned}, response {body}"
            )
            assert assigned[bystander_fid] is None
            assert body["face_ids"] == [target_fid]
