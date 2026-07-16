"""Tests for the Wave B train/eval leakage guard: PictureSplit assignment,
the write-path conflict guard, picture-set stratification, authz, and the
read-path `has_train_side_conflict` helper's contract.

See docs/reviews/tag-review-tagger-takeover-design.md §2.
"""

import gc
import io
import json
import os
import tempfile

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from pixlstash.db_models import Picture, PictureLikeness, PictureSplit
from pixlstash.services.picture_split_service import has_train_side_conflict
from pixlstash.server import Server
from pixlstash.utils.likeness.likeness_utils import LikenessUtils
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _teardown(temp_dir, server):
    server.vault.close()
    temp_dir.cleanup()
    gc.collect()


_distinct_counter = [0]


def _upload_named(client):
    _distinct_counter[0] += 1
    n = _distinct_counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"split{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _set_embedding(server, pic_id, vec):
    blob = np.asarray(vec, dtype=np.float32).tobytes()

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.image_embedding = blob
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _set_phash(server, pic_id, phash_int):
    hex_str = f"{phash_int:016x}"

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.perceptual_hash = hex_str
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _seed_likeness(server, pic_a, pic_b, likeness=0.5):
    a, b = sorted((pic_a, pic_b))

    def ins(session):
        session.add(
            PictureLikeness(
                picture_id_a=a,
                picture_id_b=b,
                likeness=likeness,
                metric="image_embedding",
            )
        )
        session.commit()

    server.vault.db.run_task(ins)


def _set_split(server, pic_id, split, component_key=None, conflict=False):
    def ins(session):
        session.add(
            PictureSplit(
                picture_id=pic_id,
                split=split,
                component_key=component_key if component_key is not None else pic_id,
                conflict=conflict,
            )
        )
        session.commit()

    server.vault.db.run_task(ins)


def _get_split(server, pic_id):
    def fetch(session):
        row = session.get(PictureSplit, pic_id)
        if row is None:
            return None
        return {
            "split": row.split,
            "component_key": row.component_key,
            "conflict": row.conflict,
            "conflict_detail": row.conflict_detail,
        }

    return server.vault.db.run_immediate_read_task(fetch)


def _add_to_set(server, pic_id, set_id):
    from pixlstash.db_models import PictureSetMember

    def ins(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=pic_id))
        session.commit()

    server.vault.db.run_task(ins)


# --------------------------------------------------------------------------- #
# (a) Component-aware assignment keeps a corroborated near-dup pair together.
# --------------------------------------------------------------------------- #


def test_assign_keeps_corroborated_pair_on_same_split():
    temp_dir, client, server = _setup()
    try:
        a = _upload_named(client)
        b = _upload_named(client)
        # Identical dhash + identical CLIP embedding: hamming=0 <= threshold,
        # cosine=1.0 >= MIN_DISPLAY_TWIN_SIM -> corroborated under the AND
        # branch regardless of the stored likeness value below.
        vec = [1.0] + [0.0] * 511
        _set_embedding(server, a, vec)
        _set_embedding(server, b, vec)
        _set_phash(server, a, 0x00FF00FF00FF00FF)
        _set_phash(server, b, 0x00FF00FF00FF00FF)
        # assign_splits_in_session sources candidate edges from the
        # PictureLikeness table (see module docstring's judgement call), so a
        # row must exist to be re-tested against the tighter corroboration bar.
        _seed_likeness(server, a, b, likeness=0.5)

        resp = client.post(f"{API}/picture_splits/assign")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["assigned"] == 2
        assert body["conflicted"] == 0

        row_a, row_b = _get_split(server, a), _get_split(server, b)
        assert row_a is not None and row_b is not None
        assert row_a["split"] == row_b["split"]
        assert row_a["split"] in ("TRAIN", "EVAL")
        assert row_a["component_key"] == row_b["component_key"] == min(a, b)
        assert not row_a["conflict"] and not row_b["conflict"]
    finally:
        _teardown(temp_dir, server)


def test_assign_unrelated_pictures_are_independent_singleton_components():
    temp_dir, client, server = _setup()
    try:
        a = _upload_named(client)
        b = _upload_named(client)
        # No PictureLikeness row at all -> no edge -> independent components.
        resp = client.post(f"{API}/picture_splits/assign")
        assert resp.status_code == 200, resp.text
        row_a, row_b = _get_split(server, a), _get_split(server, b)
        assert row_a["component_key"] == a
        assert row_b["component_key"] == b
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (b) Write-path conflict guard: a new corroborated edge between pictures
# with pre-existing opposite splits must flag both, force NEITHER, and must
# NOT silently resolve onto one side.
# --------------------------------------------------------------------------- #


def test_write_path_conflict_guard_fires_on_new_edge_discovery():
    temp_dir, client, server = _setup()
    try:
        a = _upload_named(client)
        b = _upload_named(client)
        _set_split(server, a, "TRAIN", component_key=a)
        _set_split(server, b, "EVAL", component_key=b)

        # Corroborated (dhash hamming 0, cosine 1.0): the AND-branch fires
        # even though the stored likeness itself is below MISMATCH_LIKENESS_THRESHOLD.
        vec = [0.0, 1.0] + [0.0] * 510
        _set_embedding(server, a, vec)
        _set_embedding(server, b, vec)
        _set_phash(server, a, 0x1234)
        _set_phash(server, b, 0x1234)

        pair_a, pair_b = sorted((a, b))
        new_edge = PictureLikeness(
            picture_id_a=pair_a,
            picture_id_b=pair_b,
            likeness=0.5,
            metric="image_embedding",
        )

        # Exercise the exact integration point: LikenessUtils.write_results,
        # the PictureLikenessQueue consumer this guard is hooked into.
        server.vault.db.run_task(LikenessUtils.write_results, [new_edge], 200)

        row_a, row_b = _get_split(server, a), _get_split(server, b)
        assert row_a["conflict"] is True
        assert row_b["conflict"] is True
        assert row_a["split"] == "NEITHER"
        assert row_b["split"] == "NEITHER"
        assert row_a["conflict_detail"]
        assert (
            "TRAIN" in row_a["conflict_detail"] and "EVAL" in row_a["conflict_detail"]
        )

        # Both sides land NEITHER -- not silently resolved onto one side.
        assert {row_a["split"], row_b["split"]} == {"NEITHER"}
    finally:
        _teardown(temp_dir, server)


def test_write_path_conflict_guard_is_noop_for_uncorroborated_edge():
    """A PictureLikeness row that doesn't clear the corroboration bar must
    not flag a conflict, even between pictures with opposite splits."""
    temp_dir, client, server = _setup()
    try:
        a = _upload_named(client)
        b = _upload_named(client)
        _set_split(server, a, "TRAIN", component_key=a)
        _set_split(server, b, "EVAL", component_key=b)

        # Far apart dhash and low likeness -> not corroborated.
        _set_phash(server, a, 0x0000000000000000)
        _set_phash(server, b, 0xFFFFFFFFFFFFFFFF)  # hamming 64
        vec_a = [1.0] + [0.0] * 511
        vec_b = [0.0] * 511 + [1.0]  # orthogonal -> cosine 0.0
        _set_embedding(server, a, vec_a)
        _set_embedding(server, b, vec_b)

        pair_a, pair_b = sorted((a, b))
        weak_edge = PictureLikeness(
            picture_id_a=pair_a,
            picture_id_b=pair_b,
            likeness=0.5,
            metric="image_embedding",
        )
        server.vault.db.run_task(LikenessUtils.write_results, [weak_edge], 200)

        row_a, row_b = _get_split(server, a), _get_split(server, b)
        assert row_a["conflict"] is False
        assert row_b["conflict"] is False
        assert row_a["split"] == "TRAIN"
        assert row_b["split"] == "EVAL"
    finally:
        _teardown(temp_dir, server)


def test_resolve_conflict_reassigns_whole_component_and_clears_flag():
    temp_dir, client, server = _setup()
    try:
        a = _upload_named(client)
        b = _upload_named(client)
        detail = "seeded for resolve test"
        _set_split(server, a, "NEITHER", component_key=a, conflict=True)
        _set_split(server, b, "NEITHER", component_key=a, conflict=True)

        def _detail(session):
            for pid in (a, b):
                row = session.get(PictureSplit, pid)
                row.conflict_detail = detail
                session.add(row)
            session.commit()

        server.vault.db.run_task(_detail)

        resp = client.post(f"{API}/picture_splits/{a}/resolve", json={"split": "TRAIN"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["picture_ids"]) == {a, b}
        assert body["split"] == "TRAIN"

        row_a, row_b = _get_split(server, a), _get_split(server, b)
        assert row_a["split"] == row_b["split"] == "TRAIN"
        assert not row_a["conflict"] and not row_b["conflict"]
        assert row_a["conflict_detail"] is None
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (c) 80/20-within-picture-set stratification.
# --------------------------------------------------------------------------- #


def test_stratified_assignment_hits_target_ratio_within_each_set():
    temp_dir, client, server = _setup()
    try:
        set_a = client.post(f"{API}/picture_sets", json={"name": "Set A"}).json()[
            "picture_set"
        ]["id"]
        set_b = client.post(f"{API}/picture_sets", json={"name": "Set B"}).json()[
            "picture_set"
        ]["id"]

        n = 20
        set_a_pics = [_upload_named(client) for _ in range(n)]
        set_b_pics = [_upload_named(client) for _ in range(n)]
        for pid in set_a_pics:
            _add_to_set(server, pid, set_a)
        for pid in set_b_pics:
            _add_to_set(server, pid, set_b)

        resp = client.post(f"{API}/picture_splits/assign")
        assert resp.status_code == 200, resp.text
        assert resp.json()["assigned"] == 2 * n

        for pics, label in ((set_a_pics, "A"), (set_b_pics, "B")):
            splits = [_get_split(server, pid)["split"] for pid in pics]
            train = splits.count("TRAIN")
            eva = splits.count("EVAL")
            assert train + eva == n, f"set {label}: unexpected split values {splits}"
            # Deterministic hash-ordered greedy fill -> exact round(0.8 * n).
            assert train == round(n * 0.8), f"set {label}: train={train}"
            assert eva == n - train, f"set {label}: eval={eva}"
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (d) Authz: scoped READ token is rejected on all three routes; owner works.
# --------------------------------------------------------------------------- #


def test_scoped_token_gets_403_on_all_three_routes_owner_succeeds():
    temp_dir, client, server = _setup()
    try:
        pic = _upload_named(client)
        set_id = client.post(f"{API}/picture_sets", json={"name": "Scoped set"}).json()[
            "picture_set"
        ]["id"]
        _add_to_set(server, pic, set_id)

        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set read",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_id,
            },
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}

        assert (
            bearer.post(f"{API}/picture_splits/assign", headers=headers).status_code
            == 403
        )
        assert (
            bearer.get(f"{API}/picture_splits/conflicts", headers=headers).status_code
            == 403
        )
        assert (
            bearer.post(
                f"{API}/picture_splits/{pic}/resolve",
                json={"split": "TRAIN"},
                headers=headers,
            ).status_code
            == 403
        )

        # Owner (unscoped session) succeeds on all three (resolve 404s
        # because pic has no split row yet -- still proves the authz gate
        # let it through rather than blocking it).
        assert client.post(f"{API}/picture_splits/assign").status_code == 200
        assert client.get(f"{API}/picture_splits/conflicts").status_code == 200
        owner_resolve = client.post(
            f"{API}/picture_splits/{pic}/resolve", json={"split": "TRAIN"}
        )
        assert owner_resolve.status_code == 200, owner_resolve.text
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# (e) has_train_side_conflict's contract, exercised directly.
# --------------------------------------------------------------------------- #


def test_has_train_side_conflict_contract():
    temp_dir, client, server = _setup()
    try:
        train_pic = _upload_named(client)
        eval_sibling = _upload_named(client)
        clean_eval_pic = _upload_named(client)

        # train_pic and eval_sibling share a component (one TRAIN, one is a
        # sibling not yet split but sharing the same component_key).
        _set_split(server, train_pic, "TRAIN", component_key=train_pic)
        _set_split(server, eval_sibling, "EVAL", component_key=train_pic)
        # An unrelated, cleanly-EVAL picture in its own component.
        _set_split(server, clean_eval_pic, "EVAL", component_key=clean_eval_pic)

        def _check(session):
            return has_train_side_conflict(
                session, {eval_sibling, clean_eval_pic, 999999}
            )

        flagged = server.vault.db.run_immediate_read_task(_check)
        assert flagged == {eval_sibling}

        # A picture with no PictureSplit row at all is never flagged.
        def _check_missing(session):
            return has_train_side_conflict(session, {999999})

        assert server.vault.db.run_immediate_read_task(_check_missing) == set()

        # Empty input -> empty output, no query issued.
        def _check_empty(session):
            return has_train_side_conflict(session, [])

        assert server.vault.db.run_immediate_read_task(_check_empty) == set()
    finally:
        _teardown(temp_dir, server)
