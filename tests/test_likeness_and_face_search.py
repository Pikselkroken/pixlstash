import tempfile
import os

import numpy as np
from fastapi.testclient import TestClient
from sqlmodel import select, func

from pixlstash.server import Server
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.scoring.character_likeness import count_pictures_by_character_likeness
from tests.utils import (
    upload_pictures_and_wait,
    wait_for_faces,
    poll_until_zero,
    API_PREFIX,
)
from tests.test_server import random_images


def _wait_for_image_embeddings(server, picture_ids, timeout_s=120):
    """Block until all given pictures have a stored image_embedding."""
    id_set = list(picture_ids)

    def _count_missing(session):
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.id.in_(id_set))
            .where(Picture.image_embedding.is_(None))
        ).one()
        return result[0] if isinstance(result, tuple) else (result or 0)

    poll_until_zero(server, _count_missing, "image embeddings", timeout_s=timeout_s)


def test_likeness_search_basic():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            images = [
                ("file", ("img1.png", random_images[0], "image/png")),
                ("file", ("img2.png", random_images[1], "image/png")),
            ]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            picture_ids = [r["picture_id"] for r in import_status["results"]]

            # Wait for CLIP image embeddings to be computed before querying.
            _wait_for_image_embeddings(server, picture_ids)

            # POST to likeness-search
            resp = client.post(
                f"{API_PREFIX}/pictures/likeness-search"
                f"?source_picture_ids={picture_ids[0]}&top_n=10&threshold=0.01"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert isinstance(data, list)
            # Should include the source image itself
            ids = [r["picture_id"] for r in data]
            assert picture_ids[0] in ids


def test_score_character_likeness_basic():
    """The stateless gate-scoring endpoint returns one result per uploaded file
    without importing anything, and reports frames as ineligible when there is
    nothing to score against."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            # A reference character with no reference faces yet.
            resp = client.post(f"{API_PREFIX}/characters", json={"name": "Gate Ref"})
            assert resp.status_code == 200, resp.text
            character_id = resp.json()["character"]["id"]

            files = [
                ("files", ("a.png", random_images[0], "image/png")),
                ("files", ("b.png", random_images[1], "image/png")),
            ]
            resp = client.post(
                f"{API_PREFIX}/pictures/score_character_likeness",
                files=files,
                data={"reference_character_id": character_id},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["reference_character_id"] == character_id

            results = data["results"]
            assert len(results) == 2
            assert [r["index"] for r in results] == [0, 1]
            # Nothing imported: the uploaded frames never become vault pictures.
            for r in results:
                assert {"index", "character_likeness", "eligible"} <= set(r)
                # The character has no reference faces (and the random-noise
                # frames have no detectable face), so nothing is scorable.
                assert r["eligible"] is False
                assert r["character_likeness"] is None

            # Scoring must not have imported the frames as pictures.
            def _picture_count(session):
                result = session.exec(select(func.count()).select_from(Picture)).one()
                return result[0] if isinstance(result, tuple) else (result or 0)

            assert server.vault.db.run_task(_picture_count) == 0


def test_score_character_likeness_combine_param():
    """The gate-scoring endpoint accepts a valid `combine` strategy and rejects
    an unknown one with 400."""
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

            resp = client.post(f"{API_PREFIX}/characters", json={"name": "Gate Ref"})
            assert resp.status_code == 200, resp.text
            character_id = resp.json()["character"]["id"]

            # A valid combine strategy passes validation. (It may still 503 in
            # an environment without an inference engine, so assert only that it
            # is not rejected as a bad request — the happy path is covered by
            # test_score_character_likeness_basic.)
            resp = client.post(
                f"{API_PREFIX}/pictures/score_character_likeness",
                files=[("files", ("a.png", random_images[0], "image/png"))],
                data={"reference_character_id": character_id, "combine": "max"},
            )
            assert resp.status_code != 400, resp.text

            # An unknown combine strategy is rejected with 400 before any
            # scoring or inference is attempted.
            resp = client.post(
                f"{API_PREFIX}/pictures/score_character_likeness",
                files=[("files", ("a.png", random_images[0], "image/png"))],
                data={"reference_character_id": character_id, "combine": "bogus"},
            )
            assert resp.status_code == 400, resp.text
            assert "combine" in resp.json()["detail"].lower()


def test_compute_character_likeness_combine_modes():
    """compute_character_likeness_for_faces reduces across reference faces per
    the combine strategy, and defaults to the legacy softmax behaviour."""
    import types
    import numpy as np
    from pixlstash.picture_scoring import compute_character_likeness_for_faces

    def face(vec, fid=None):
        return types.SimpleNamespace(
            features=np.asarray(vec, dtype=np.float32).tobytes(), id=fid
        )

    # Candidate strongly matches reference A, weakly matches reference B.
    refs = [face([1.0, 0.0, 0.0]), face([0.0, 1.0, 0.0])]
    cands = [face([0.9, 0.1, 0.0], fid=42)]

    def score(mode=None):
        kwargs = {} if mode is None else {"combine": mode}
        return compute_character_likeness_for_faces(refs, cands, **kwargs)[42]

    sim_a, sim_b = score("max"), score("min")
    assert sim_a > sim_b  # max picks the strong reference, min the weak one
    assert abs(score("mean") - (sim_a + sim_b) / 2.0) < 1e-5
    # softmax (the default) leans toward the best match: between mean and max.
    assert score("mean") < score("softmax") <= sim_a
    # Omitting combine must equal the explicit softmax default (backward compat).
    assert abs(score() - score("softmax")) < 1e-9


def test_score_character_likeness_requires_auth():
    """The scoring endpoint rejects unauthenticated requests."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                f"{API_PREFIX}/pictures/score_character_likeness",
                files=[("files", ("a.png", random_images[0], "image/png"))],
                data={"reference_character_id": 1},
            )
            assert resp.status_code == 401, resp.text


def _face_features(seed: int) -> bytes:
    """Deterministic synthetic face feature vector (float32 bytes)."""
    rng = np.random.default_rng(seed)
    return rng.normal(size=8).astype(np.float32).tobytes()


def _add_face(server, pic_id, char_id, seed, face_index=0):
    """Insert a Face row with synthetic features directly into the DB."""

    def _add(session):
        session.add(
            Face(
                picture_id=pic_id,
                frame_index=0,
                face_index=face_index,
                character_id=char_id,
                bbox_="0,0,10,10",
                features=_face_features(seed),
            )
        )
        session.commit()

    server.vault.db.run_task(_add)


def _seed_likeness_stack_scenario(client, server):
    """Seed the character-view stack scenario for CHARACTER_LIKENESS tests.

    Layout (character X = the viewed character, R = the likeness reference):

    - stack 1: leader ``a`` (position 0, NO face for X) + child ``b`` (X face).
      Scoped-leader semantics must represent this stack by ``b``; the naive
      global-leader clause dropped it entirely.
    - stack 2: leader ``d`` (position 0, X face) + child ``e`` (no face).
      Must still be represented by ``d`` (no over-blocking).
    - ``c``: unstacked picture with an X face.
    - ``r``: unstacked picture with R's reference face.

    Returns:
        (x_id, ref_id, ids) where ids is a dict of picture ids by key a-e, r.
    """
    images = [
        ("file", (f"{key}.png", random_images[i], "image/png"))
        for i, key in enumerate(["a", "b", "c", "d", "e", "r"])
    ]
    import_status = upload_pictures_and_wait(client, images)
    assert import_status["status"] == "completed"
    ids = dict(
        zip(
            ["a", "b", "c", "d", "e", "r"],
            [r["picture_id"] for r in import_status["results"]],
        )
    )

    resp = client.post(f"{API_PREFIX}/characters", json={"name": "Viewed X"})
    assert resp.status_code == 200, resp.text
    x_id = resp.json()["character"]["id"]
    resp = client.post(f"{API_PREFIX}/characters", json={"name": "Likeness Ref"})
    assert resp.status_code == 200, resp.text
    ref_id = resp.json()["character"]["id"]

    # Stack 1: a is the position-0 leader, b the child.
    resp = client.post(
        f"{API_PREFIX}/stacks", json={"picture_ids": [ids["a"], ids["b"]]}
    )
    assert resp.status_code == 200, resp.text
    # Stack 2: d is the position-0 leader, e the child.
    resp = client.post(
        f"{API_PREFIX}/stacks", json={"picture_ids": [ids["d"], ids["e"]]}
    )
    assert resp.status_code == 200, resp.text

    _add_face(server, ids["r"], ref_id, seed=1)
    _add_face(server, ids["b"], x_id, seed=2)
    _add_face(server, ids["c"], x_id, seed=3)
    _add_face(server, ids["d"], x_id, seed=4)

    return x_id, ref_id, ids


def test_character_likeness_stack_scoped_leader_listing_and_count():
    """CHARACTER_LIKENESS with stack_leaders_only uses scoped-leader semantics:
    a stack whose global leader has no face for the viewed character is
    represented by its lowest-positioned in-scope member instead of vanishing,
    while stacks whose leader IS in scope are still returned (no over-blocking).
    The count helper agrees with the listing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            x_id, ref_id, ids = _seed_likeness_stack_scenario(client, server)

            # fields=grid implies stack_leaders_only on /pictures.
            resp = client.get(
                f"{API_PREFIX}/pictures",
                params={
                    "sort": "CHARACTER_LIKENESS",
                    "reference_character_id": ref_id,
                    "character_id": x_id,
                    "fields": "grid",
                    "descending": "true",
                },
            )
            assert resp.status_code == 200, resp.text
            listed_ids = [row["id"] for row in resp.json()]

            # Stack 1 is represented by exactly its in-scope member b (the
            # stack must not be dropped), stack 2 by its actual leader d, and
            # the unstacked picture c is listed as itself.
            assert ids["b"] in listed_ids, "stack with out-of-scope leader was dropped"
            assert ids["d"] in listed_ids, "stack with in-scope leader was over-blocked"
            assert ids["c"] in listed_ids
            assert ids["a"] not in listed_ids
            assert ids["e"] not in listed_ids
            assert sorted(listed_ids) == sorted([ids["b"], ids["c"], ids["d"]])

            # The count helper must yield exactly what the listing yields.
            count = count_pictures_by_character_likeness(
                server, x_id, stack_leaders_only=True
            )
            assert count == len(listed_ids)


def test_pictures_count_character_likeness_matches_stream():
    """/pictures/count with sort=CHARACTER_LIKENESS returns an integer (not
    null) equal to the likeness stream row count, and agrees with the normal
    (sort-less) count for the same character view."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            x_id, ref_id, ids = _seed_likeness_stack_scenario(client, server)

            params = {
                "sort": "CHARACTER_LIKENESS",
                "reference_character_id": ref_id,
                "descending": "true",
                "character_id": x_id,
                "stack_leaders_only": "true",
            }
            resp = client.get(f"{API_PREFIX}/pictures/count", params=params)
            assert resp.status_code == 200, resp.text
            likeness_count = resp.json()["count"]
            assert isinstance(likeness_count, int)

            # The likeness stream must deliver exactly that many rows.
            resp = client.get(
                f"{API_PREFIX}/pictures/stream",
                params={**params, "fields": "grid", "batch_limit": 1000},
            )
            assert resp.status_code == 200, resp.text
            stream = resp.json()
            assert stream["done"] is True
            assert len(stream["pictures"]) == likeness_count == 3

            # The normal (sort-less) count the frontend grid uses must agree
            # with the likeness count for the same character view.
            resp = client.get(
                f"{API_PREFIX}/pictures/count",
                params={"character_id": x_id, "stack_leaders_only": "true"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["count"] == likeness_count


def test_face_search_basic():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            images = [
                ("file", ("img1.png", random_images[0], "image/png")),
                ("file", ("img2.png", random_images[1], "image/png")),
            ]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            picture_ids = [r["picture_id"] for r in import_status["results"]]

            # Poll until face extraction has had a chance to run (may be empty for
            # random-noise images, which is a valid outcome).
            all_faces = wait_for_faces(client, picture_ids[0], timeout_s=60)
            # Exclude sentinel records (face_index == -1), which have no embedding.
            faces = [f for f in all_faces if f.get("face_index", 0) != -1]
            if not faces:
                # No real faces detected in random noise images — nothing to assert
                return
            face_id = faces[0]["id"]

            # POST to face-search using the stored face embedding
            resp = client.post(
                f"{API_PREFIX}/pictures/face-search?source_face_id={face_id}&top_n=10"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert isinstance(data, list)
            assert data
