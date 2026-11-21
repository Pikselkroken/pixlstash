"""Batch face assignment by picture_ids: best-face selection heuristics.

Covers the bootstrap heuristic for a character with no reference faces yet:
single-face pictures in the batch are assigned first and then
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
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import Character, Picture
from pixlstash.db_models.face import Face
from pixlstash.scoring import (
    compute_character_likeness_for_faces,
    match_faces_to_characters,
)
from pixlstash.server import Server
from pixlstash.services import operation_log_service
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


def _stack(client, picture_ids):
    response = client.post(f"{API_PREFIX}/stacks", json={"picture_ids": picture_ids})
    assert response.status_code == 200, response.text
    return response.json()


def test_record_failure_rolls_back_face_assignment(monkeypatch):
    """A face-to-character join cannot survive without its undo receipt."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            login = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login.status_code == 200
            picture_id = _upload(client, 1)[0]
            character_id = _create_character(client, "Atomic")
            face_id = _add_face(server, picture_id, ALONG_X, SMALL_BBOX)
            monkeypatch.setattr(
                operation_log_service,
                "record_operation_in_session",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    RuntimeError("face receipt failed")
                ),
            )

            with pytest.raises(RuntimeError, match="face receipt failed"):
                client.post(
                    f"{API_PREFIX}/characters/{character_id}/faces",
                    json={"face_ids": [face_id]},
                )

            assert _face_character_ids(server, [face_id]) == {face_id: None}
            assert (
                operation_log_service.list_operations(
                    server.vault, op_type="characters.assign"
                )
                == []
            )


def test_authoritative_face_assignment_rejects_a_mismatched_picture():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            assert (
                client.post(
                    "/login",
                    json={"username": "testuser", "password": "testpassword"},
                ).status_code
                == 200
            )
            first, second = _upload(client, 2)
            character_id = _create_character(client, "Mismatch")
            first_face_id = _add_face(server, first, ALONG_X, SMALL_BBOX)
            second_face_id = _add_face(server, second, ALONG_X, SMALL_BBOX)
            _stack(client, [first, second])

            response = client.post(
                f"{API_PREFIX}/characters/{character_id}/faces",
                json={
                    "face_assignments": [
                        {"picture_id": second, "face_id": first_face_id}
                    ]
                },
            )

            assert response.status_code == 422, response.text
            assert _face_character_ids(server, [first_face_id, second_face_id]) == {
                first_face_id: None,
                second_face_id: None,
            }


def test_authoritative_face_assignment_is_exact_stack_atomic_and_undoable():
    """The reviewed face wins verbatim; stack siblings use normal selection."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            assert (
                client.post(
                    "/login",
                    json={"username": "testuser", "password": "testpassword"},
                ).status_code
                == 200
            )
            reference_id, reviewed_id, sibling_id = _upload(client, 3)
            character_id = _create_character(client, "Stack atomic")
            _add_face(
                server,
                reference_id,
                ALONG_X,
                SMALL_BBOX,
                character_id=character_id,
            )

            reviewed_winner = _add_face(
                server, reviewed_id, ALONG_Y, SMALL_BBOX, face_index=0
            )
            reviewed_rerank_winner = _add_face(
                server, reviewed_id, ALONG_X, LARGE_BBOX, face_index=1
            )
            sibling_bystander = _add_face(
                server, sibling_id, ALONG_Y, LARGE_BBOX, face_index=0
            )
            sibling_target = _add_face(
                server, sibling_id, NEAR_X, SMALL_BBOX, face_index=1
            )
            _stack(client, [reviewed_id, sibling_id])

            assigned = client.post(
                f"{API_PREFIX}/characters/{character_id}/faces",
                json={
                    "face_assignments": [
                        {"picture_id": reviewed_id, "face_id": reviewed_winner}
                    ]
                },
            )

            assert assigned.status_code == 200, assigned.text
            assert set(assigned.json()["face_ids"]) == {
                reviewed_winner,
                sibling_target,
            }
            assert _face_character_ids(
                server,
                [
                    reviewed_winner,
                    reviewed_rerank_winner,
                    sibling_bystander,
                    sibling_target,
                ],
            ) == {
                reviewed_winner: character_id,
                reviewed_rerank_winner: None,
                sibling_bystander: None,
                sibling_target: character_id,
            }

            undone = client.post(f"{API_PREFIX}/operations/undo")
            assert undone.status_code == 200, undone.text
            assert set(undone.json()["picture_ids"]) == {reviewed_id, sibling_id}
            assert _face_character_ids(
                server,
                [
                    reviewed_winner,
                    reviewed_rerank_winner,
                    sibling_bystander,
                    sibling_target,
                ],
            ) == {
                reviewed_winner: None,
                reviewed_rerank_winner: None,
                sibling_bystander: None,
                sibling_target: None,
            }


def test_face_search_mixed_legacy_reference_widths_returns_422():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            assert (
                client.post(
                    "/login",
                    json={"username": "testuser", "password": "testpassword"},
                ).status_code
                == 200
            )
            first, second, candidate = _upload(client, 3)
            character_id = _create_character(client, "Mixed widths")
            _add_face(
                server,
                first,
                np.asarray([1, 0, 0, 0], dtype=np.float32).tobytes(),
                SMALL_BBOX,
                character_id=character_id,
            )
            _add_face(
                server,
                second,
                np.asarray([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32).tobytes(),
                SMALL_BBOX,
                character_id=character_id,
            )
            _add_face(
                server,
                candidate,
                np.asarray([1, 0, 0, 0], dtype=np.float32).tobytes(),
                SMALL_BBOX,
            )

            response = client.post(
                f"{API_PREFIX}/pictures/face-search",
                params={
                    "source_character_id": character_id,
                    "exclude_character_id": character_id,
                },
            )

            assert response.status_code == 422, response.text
            assert "incompatible embedding widths [4, 8]" in response.json()["detail"]


def test_suggest_more_max_winner_is_assigned_without_softmax_reranking():
    """Search and assignment share one winning-face decision."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            assert (
                client.post(
                    "/login",
                    json={"username": "testuser", "password": "testpassword"},
                ).status_code
                == 200
            )
            picture_ids = _upload(client, 11)
            reference_ids, candidate_id = picture_ids[:10], picture_ids[10]
            character_id = _create_character(client, "Reducer parity")

            ref_a = np.asarray([1.0, 0.0], dtype=np.float32).tobytes()
            ref_b = np.asarray([0.5, 0.8660254], dtype=np.float32).tobytes()
            for index, picture_id in enumerate(reference_ids):
                _add_face(
                    server,
                    picture_id,
                    ref_a if index == 0 else ref_b,
                    SMALL_BBOX,
                    character_id=character_id,
                )
            candidate_a = _add_face(
                server, candidate_id, ref_a, SMALL_BBOX, face_index=0
            )
            candidate_b = _add_face(
                server,
                candidate_id,
                np.asarray([0.8660254, 0.5], dtype=np.float32).tobytes(),
                LARGE_BBOX,
                face_index=1,
            )

            def old_softmax_winner(session):
                refs = session.exec(
                    select(Face).where(Face.picture_id.in_(reference_ids))
                ).all()
                candidates = session.exec(
                    select(Face).where(Face.picture_id == candidate_id)
                ).all()
                scores = compute_character_likeness_for_faces(refs, candidates)
                return max(scores, key=scores.get)

            assert server.vault.db.run_task(old_softmax_winner) == candidate_b

            search = client.post(
                f"{API_PREFIX}/pictures/face-search",
                params={
                    "source_character_id": character_id,
                    "exclude_character_id": character_id,
                    "combine": "max",
                    "top_n": 100,
                },
            )
            assert search.status_code == 200, search.text
            match = next(
                row for row in search.json() if row["picture_id"] == candidate_id
            )
            assert match["face_id"] == candidate_a

            assigned = client.post(
                f"{API_PREFIX}/characters/{character_id}/faces",
                json={
                    "face_assignments": [
                        {
                            "picture_id": match["picture_id"],
                            "face_id": match["face_id"],
                        }
                    ]
                },
            )
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["face_ids"] == [candidate_a]
            assert _face_character_ids(server, [candidate_a, candidate_b]) == {
                candidate_a: character_id,
                candidate_b: None,
            }


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
    """A character that already has reference faces keeps the reference-driven
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


# --- a picture of several people (a run loading their LoRAs) ---------------


def _vec(*values):
    return np.asarray(values, dtype=np.float32).tobytes()


@pytest.fixture
def library():
    """An in-memory vault: matching reads faces, no server needed."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _person(session, name, *references):
    """A person whose reference faces (one picture each) have *references*."""
    character = Character(name=name)
    session.add(character)
    session.commit()
    for features in references:
        picture = Picture(file_path=f"{name}.png", deleted=False, score=5)
        session.add(picture)
        session.commit()
        session.add(
            Face(
                picture_id=picture.id,
                face_index=0,
                character_id=character.id,
                bbox=SMALL_BBOX,
                features=features,
            )
        )
    session.commit()
    return character.id


def _group(session, *faces):
    """A picture with *faces*, each ``(features, bbox)`` or with a character."""
    picture = Picture(file_path="group.png", deleted=False)
    session.add(picture)
    session.commit()
    rows = []
    for index, (features, bbox, *named) in enumerate(faces):
        face = Face(
            picture_id=picture.id,
            face_index=index,
            bbox=bbox,
            features=features,
            character_id=named[0] if named else None,
        )
        session.add(face)
        rows.append(face)
    session.commit()
    return rows


def test_two_peoples_faces_are_matched_by_likeness_not_size(library):
    a = _person(library, "a", ALONG_X)
    b = _person(library, "b", ALONG_Y)
    like_b, like_a = _group(library, (ALONG_Y, LARGE_BBOX), (NEAR_X, SMALL_BBOX))
    assert match_faces_to_characters(library, [like_b, like_a], [a, b]) == {
        like_a.id: a,
        like_b.id: b,
    }


def test_the_strongest_pair_is_taken_first(library):
    """f1 is most like a (0.83), so a gets it, and b takes f2 though it is
    unlike them (0.00): b is in the run, so b gets the face left."""
    a = _person(library, "a", _vec(-0.293, 0.035, -0.956))
    b = _person(library, "b", _vec(-0.174, -0.918, -0.356))
    f1, f2 = _group(
        library,
        (_vec(-0.506, -0.452, -0.734), SMALL_BBOX),
        (_vec(0.524, 0.219, -0.823), SMALL_BBOX),
    )
    likeness = compute_character_likeness_for_faces(
        [library.exec(select(Face).where(Face.character_id == a)).one()], [f1, f2]
    )
    assert likeness[f1.id] > likeness[f2.id]
    assert match_faces_to_characters(library, [f1, f2], [a, b]) == {
        f1.id: a,
        f2.id: b,
    }


def test_the_less_obvious_person_still_gets_the_face_left(library):
    """b's face came out unlike b, but a takes the obvious one and b the rest."""
    a = _person(library, "a", ALONG_X)
    b = _person(library, "b", ALONG_Y)
    like_a, stranger = _group(
        library, (NEAR_X, SMALL_BBOX), (_vec(0, 0, 1, 0, 0, 0, 0, 0), LARGE_BBOX)
    )
    assert match_faces_to_characters(library, [like_a, stranger], [a, b]) == {
        like_a.id: a,
        stranger.id: b,
    }


def test_a_named_face_and_its_person_are_left_out(library):
    a = _person(library, "a", ALONG_X)
    b = _person(library, "b", ALONG_Y)
    named, free = _group(library, (NEAR_X, SMALL_BBOX, b), (ALONG_X, SMALL_BBOX))
    assert match_faces_to_characters(library, [named, free], [a, b]) == {free.id: a}


def test_one_person_with_no_references_gets_the_largest_face_left(library):
    a = _person(library, "a", ALONG_X)
    newcomer = _person(library, "newcomer")
    like_a, small, large = _group(
        library,
        (ALONG_X, LARGE_BBOX),
        (ALONG_Y, SMALL_BBOX),
        (ALONG_Y, [0, 0, 100, 100]),
    )
    assert match_faces_to_characters(
        library, [like_a, small, large], [newcomer, a]
    ) == {like_a.id: a, large.id: newcomer}


def test_people_with_no_references_take_the_largest_faces_in_turn(library):
    """Nothing to score them by, so in order, until the faces run out."""
    first = _person(library, "first")
    second = _person(library, "second")
    third = _person(library, "third")
    small, large = _group(library, (ALONG_X, SMALL_BBOX), (ALONG_Y, LARGE_BBOX))
    assert match_faces_to_characters(
        library, [small, large], [first, second, third]
    ) == {large.id: first, small.id: second}
