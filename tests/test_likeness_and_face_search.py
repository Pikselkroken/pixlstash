import tempfile
import os

import numpy as np
import pytest
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
            # is not rejected as a bad request - the happy path is covered by
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
    from pixlstash.scoring import compute_character_likeness_for_faces

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
            rows = resp.json()
            listed_ids = [row["id"] for row in rows]
            # The grid's likeness pill reads this field. ``GridPicture`` is the
            # enforced response model and drops any key it does not declare,
            # which is how the pill went missing.
            assert all(isinstance(row.get("character_likeness"), float) for row in rows)

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
            assert all(
                isinstance(row.get("character_likeness"), float)
                for row in stream["pictures"]
            )

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
                # No real faces detected in random noise images - nothing to assert
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
            # Every match names the face that produced its score, so a caller
            # assigning the results does not have to redo the comparison.
            assert all(row.get("face_id") for row in data), data


# ── Character-scoped face search ("Suggest more pictures of Alice", #636) ──


def _vector_features(vec) -> bytes:
    """Pack an explicit vector as float32 bytes, the way Face.features stores it."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def _add_face_with_features(server, pic_id, char_id, features, face_index=0):
    """Insert one Face row with the given raw features and return its id."""
    holder = {}

    def _add(session):
        face = Face(
            picture_id=pic_id,
            frame_index=0,
            face_index=face_index,
            character_id=char_id,
            bbox_="0,0,10,10",
            features=features,
        )
        session.add(face)
        session.commit()
        session.refresh(face)
        holder["id"] = face.id

    server.vault.db.run_task(_add)
    return holder["id"]


def _seed_character_face_search_scenario(client, server):
    """Seed a character with two reference faces plus un-assigned candidates.

    All embeddings are explicit unit-ish vectors so every similarity in the
    assertions is arithmetic rather than a property of a real face model:

    - ``alice`` reference faces point along +x (cosine 1.0 with each other).
    - ``near``  is 0.98 of the way to +x  → a strong match.
    - ``far``   points along +y           → cosine 0.0, a non-match.
    - ``two_faces`` holds both a +y face and a 0.9-to-+x face, so the endpoint
      has to name the *second* one as the winner.

    Returns:
        ``(alice_id, ids, face_ids)``.
    """
    keys = ["ref1", "ref2", "near", "far", "two_faces"]
    images = [
        ("file", (f"{key}.png", random_images[i], "image/png"))
        for i, key in enumerate(keys)
    ]
    import_status = upload_pictures_and_wait(client, images)
    assert import_status["status"] == "completed"
    ids = dict(zip(keys, [r["picture_id"] for r in import_status["results"]]))

    resp = client.post(f"{API_PREFIX}/characters", json={"name": "Alice"})
    assert resp.status_code == 200, resp.text
    alice_id = resp.json()["character"]["id"]

    along_x = _vector_features([1, 0, 0, 0, 0, 0, 0, 0])
    along_y = _vector_features([0, 1, 0, 0, 0, 0, 0, 0])

    face_ids = {}
    # Alice's own pictures: these are the reference faces AND the rows that
    # exclude_character_id must remove from the results.
    face_ids["ref1"] = _add_face_with_features(server, ids["ref1"], alice_id, along_x)
    face_ids["ref2"] = _add_face_with_features(server, ids["ref2"], alice_id, along_x)
    # Un-assigned candidates.
    face_ids["near"] = _add_face_with_features(
        server, ids["near"], None, _vector_features([0.98, 0.199, 0, 0, 0, 0, 0, 0])
    )
    face_ids["far"] = _add_face_with_features(server, ids["far"], None, along_y)
    face_ids["two_faces_wrong"] = _add_face_with_features(
        server, ids["two_faces"], None, along_y, face_index=0
    )
    face_ids["two_faces_right"] = _add_face_with_features(
        server,
        ids["two_faces"],
        None,
        _vector_features([0.9, 0.436, 0, 0, 0, 0, 0, 0]),
        face_index=1,
    )

    return alice_id, ids, face_ids


def _face_search(client, **params):
    """POST /pictures/face-search with query params; return the parsed body."""
    resp = client.post(f"{API_PREFIX}/pictures/face-search", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_face_search_by_character_ranks_and_excludes_assigned():
    """source_character_id searches with a character's reference faces, and
    exclude_character_id drops the pictures already assigned to them, which is
    what makes the result set the un-assigned candidates only (#636).

    Asserts both directions: the strong match is found and ranked above the
    non-match (no over-blocking), and the already-assigned references are gone.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            alice_id, ids, _face_ids = _seed_character_face_search_scenario(
                client, server
            )

            # Without the exclusion, Alice's own reference pictures come back
            # (they match themselves perfectly).
            data = _face_search(
                client, source_character_id=alice_id, top_n=500, threshold=0.5
            )
            by_id = {row["picture_id"]: row for row in data}
            assert ids["ref1"] in by_id, data
            assert ids["near"] in by_id, data

            # With it, only the un-assigned candidates remain.
            data = _face_search(
                client,
                source_character_id=alice_id,
                exclude_character_id=alice_id,
                top_n=500,
                threshold=0.5,
            )
            by_id = {row["picture_id"]: row for row in data}
            assert ids["ref1"] not in by_id, "already-assigned picture was not excluded"
            assert ids["ref2"] not in by_id, "already-assigned picture was not excluded"
            # In-scope candidates still arrive: over-blocking is its own bug.
            assert ids["near"] in by_id, data
            assert ids["two_faces"] in by_id, data
            # The non-match is below the threshold.
            assert ids["far"] not in by_id, data
            # Ranked by likeness: the 0.98 match beats the 0.9 one.
            assert by_id[ids["near"]]["likeness"] > by_id[ids["two_faces"]]["likeness"]
            assert [row["picture_id"] for row in data].index(ids["near"]) == 0


def test_face_search_names_the_best_matching_face():
    """The reported face_id is the face that produced the picture's score, not
    just the picture's first face - a bulk assignment writes to that row."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            alice_id, ids, face_ids = _seed_character_face_search_scenario(
                client, server
            )

            data = _face_search(
                client,
                source_character_id=alice_id,
                exclude_character_id=alice_id,
                top_n=500,
                threshold=0.5,
            )
            by_id = {row["picture_id"]: row for row in data}
            assert by_id[ids["two_faces"]]["face_id"] == face_ids["two_faces_right"], (
                "the wrong face of a two-face picture was named as the match"
            )
            assert by_id[ids["near"]]["face_id"] == face_ids["near"]


def test_face_search_reference_scores_expose_per_reference_agreement():
    """`include_reference_scores` returns the winning face's similarity to EVERY
    reference, which is the only thing that can answer "how many of this
    person's reference faces agree?".

    `likeness` alone cannot: it is the `combine` (max, for a character query),
    so a candidate that resembles exactly one reference perfectly outranks one
    that resembles all of them well. The two references here are deliberately
    orthogonal, which makes that difference arithmetic rather than a judgement
    about a face model.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            keys = ["ref_x", "ref_y", "only_x", "both"]
            images = [
                ("file", (f"{key}.png", random_images[i], "image/png"))
                for i, key in enumerate(keys)
            ]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            ids = dict(zip(keys, [r["picture_id"] for r in import_status["results"]]))

            resp = client.post(f"{API_PREFIX}/characters", json={"name": "Bea"})
            assert resp.status_code == 200, resp.text
            bea_id = resp.json()["character"]["id"]

            # Two references pointing 90° apart: the same person shot in two
            # conditions the model does not reconcile.
            _add_face_with_features(
                server, ids["ref_x"], bea_id, _vector_features([1, 0, 0, 0, 0, 0, 0, 0])
            )
            _add_face_with_features(
                server, ids["ref_y"], bea_id, _vector_features([0, 1, 0, 0, 0, 0, 0, 0])
            )
            # Matches one reference perfectly and the other not at all.
            _add_face_with_features(
                server,
                ids["only_x"],
                None,
                _vector_features([1, 0, 0, 0, 0, 0, 0, 0]),
            )
            # Matches both, less well than `only_x` matches its one: cos 45° to
            # each, so max() ranks it BELOW the single-reference match.
            half = float(np.sqrt(0.5))
            _add_face_with_features(
                server,
                ids["both"],
                None,
                _vector_features([half, half, 0, 0, 0, 0, 0, 0]),
            )

            # Off by default: no existing consumer pays for the extra floats.
            data = _face_search(
                client,
                source_character_id=bea_id,
                exclude_character_id=bea_id,
                top_n=500,
                threshold=0.5,
            )
            assert all(row.get("reference_likeness") is None for row in data), data

            data = _face_search(
                client,
                source_character_id=bea_id,
                exclude_character_id=bea_id,
                top_n=500,
                threshold=0.5,
                include_reference_scores=True,
            )
            by_id = {row["picture_id"]: row for row in data}
            assert ids["only_x"] in by_id, data
            assert ids["both"] in by_id, data

            for row in data:
                refs = row["reference_likeness"]
                assert len(refs) == 2, row
                # `likeness` is the combine of the row; for a character query
                # that is max, so the two must agree.
                assert row["likeness"] == pytest.approx(max(refs), abs=1e-4), row

            # The single-reference match scores HIGHER overall...
            assert by_id[ids["only_x"]]["likeness"] > by_id[ids["both"]]["likeness"]

            # ...yet only one of its references agrees at a 0.70 cut, while the
            # weaker overall match satisfies both. That inversion is exactly
            # what the agreement filter exists to express.
            def _agreeing(picture_key, cut):
                return sum(
                    1 for v in by_id[ids[picture_key]]["reference_likeness"] if v >= cut
                )

            assert _agreeing("only_x", 0.70) == 1
            assert _agreeing("both", 0.70) == 2


def test_face_search_rejects_more_than_one_source_id():
    """The three source ids are mutually exclusive; asking with two is a 400."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            resp = client.post(
                f"{API_PREFIX}/pictures/face-search",
                params={"source_character_id": 1, "source_face_id": 2},
            )
            assert resp.status_code == 400, resp.text
            assert "exactly one source" in resp.json()["detail"]


def test_face_search_by_character_still_scope_filters_for_a_share_token():
    """A resource-scoped share token searching by character gets only the
    pictures inside its grant.

    `/pictures/face-search` is in READ_SAFE_POST_PATHS, so a share token really
    does reach this handler, and source_character_id accepts any character id.
    The scope filter is what keeps that from becoming a whole-library read.
    Asserted in both directions: the out-of-scope match is gone AND the in-scope
    one still arrives, because over-blocking is its own regression.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            alice_id, ids, _face_ids = _seed_character_face_search_scenario(
                client, server
            )

            # A set holding only `near`; `two_faces` is deliberately left out.
            resp = client.post(
                f"{API_PREFIX}/picture_sets", json={"name": "Shared few"}
            )
            assert resp.status_code == 200, resp.text
            set_id = resp.json().get("id") or resp.json().get("picture_set", {}).get(
                "id"
            )
            assert set_id, resp.text
            resp = client.post(
                f"{API_PREFIX}/picture_sets/{set_id}/members/{ids['near']}"
            )
            assert resp.status_code in (200, 201), resp.text

            resp = client.post(
                f"{API_PREFIX}/users/me/token",
                json={
                    "description": "set share",
                    "scope": "READ",
                    "resource_type": "picture_set",
                    "resource_id": set_id,
                },
            )
            assert resp.status_code == 200, resp.text
            token = resp.json()["token"]

            anon = TestClient(server.api)
            resp = anon.post(
                f"{API_PREFIX}/pictures/face-search",
                params={
                    "source_character_id": alice_id,
                    "top_n": 500,
                    "threshold": 0.5,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text
            returned = {row["picture_id"] for row in resp.json()}
            assert ids["near"] in returned, "in-scope match was over-blocked"
            assert ids["two_faces"] not in returned, "out-of-scope picture leaked"
            assert ids["ref1"] not in returned, "out-of-scope picture leaked"


def test_face_search_character_without_reference_faces_is_422():
    """A character with no face carrying an embedding cannot be searched with,
    and says so rather than returning a silently empty result."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            resp = client.post(f"{API_PREFIX}/characters", json={"name": "Empty"})
            assert resp.status_code == 200, resp.text
            empty_id = resp.json()["character"]["id"]

            resp = client.post(
                f"{API_PREFIX}/pictures/face-search",
                params={"source_character_id": empty_id},
            )
            assert resp.status_code == 422, resp.text
            assert "reference face" in resp.json()["detail"]
