"""Backend tests for picture-set locking (see docs/reviews/2026-07-picture-set-locking-plan.md).

A locked set is a hard, whole-set freeze: neither the set's own fields/membership
nor the label data of any member picture may change until it is unlocked. These
tests assert the full rejection matrix (423), the cross-set and stack-expansion
rules, bulk-delete skipping, the locked-members / metadata surfaces, and the
review backstops - with a real Server + TestClient in the style of
tests/test_picture_sets.py.
"""

import gc
import json
import os
import shutil
import sqlite3
import tempfile
import uuid

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import delete, select

from pixlstash.db_models import (
    Face,
    Picture,
    PictureSetMember,
    PictureStack,
    ReferenceFolder,
    Tag,
    make_tag_sentinel,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.server import Server
from pixlstash.services.set_lock_service import (
    locked_picture_ids,
    locked_sets_for_pictures,
)
from pixlstash.utils.near_neighbor import EMBEDDING_DIM

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    # Disable background workers so no pipeline (reference-folder scan, tagger,
    # description) races the seeded slate: these tests assert on synchronous lock
    # enforcement, and every task they exercise (scan_tag, TagTask, DescriptionTask,
    # the metadata-import route) is invoked directly. Without this, the background
    # reference-folder scan reads a sidecar and rewrites a pic's tags between the
    # seed and the assertion - an intermittent flake in a blocking-gate suite.
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


_PNG_POOL = [
    "Bad1.png",
    "Bad2.png",
    "Good1.png",
    "Good2.png",
    "Good3.png",
]


def _first_n_pictures(server, n):
    """Create ``n`` minimal Picture rows directly in the DB and return their ids.

    Deliberately NOT an upload: these tests run with background workers disabled
    and assert on seeded tag/description state, so a real upload (which needs the
    face worker and kicks off the tagger / description / reference-folder-scan
    finders) would let a background pipeline clobber the seeded slate between the
    seed and the assertion - the intermittent flake this replaces. A direct insert
    creates no work for any finder, so the slate is stable and the tests are fast.
    """

    def insert(session):
        ids = []
        for _ in range(n):
            token = uuid.uuid4().hex
            pic = Picture(
                file_path=f"/tmp/pixlstash-locking-test/{token}.png",
                format="png",
                width=64,
                height=64,
                pixel_sha=token,
            )
            session.add(pic)
            session.flush()
            ids.append(int(pic.id))
        session.commit()
        return ids

    return server.vault.db.run_task(insert)


def _create_set(client, name):
    resp = client.post("/picture_sets", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["picture_set"]["id"]


def _add_member(client, set_id, pic_id):
    resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
    assert resp.status_code == 200, resp.text


def _add_member_directly(server, set_id, pic_id):
    """Insert ONE ``PictureSetMember`` row, without touching the stack.

    Deliberately bypasses ``POST /picture_sets/{id}/members/{picture_id}``: sets
    are stack-atomic, so that route expands to every member of the picture's
    stack and a "set whose only member is this picture" is unreachable through
    it for anything stacked. The through-stack-only state (a picture that
    *shares a stack with* a locked-set member without being one) is exactly what
    the detach guards exist for, so the tests that assert on it have to seed it
    the same way :func:`_seed_stack_directly` seeds ``stack_id``.
    """

    def insert(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=int(pic_id)))
        session.commit()

    server.vault.db.run_task(insert)


def _set_locked(client, set_id, locked):
    resp = client.patch(f"/picture_sets/{set_id}", json={"locked": locked})
    assert resp.status_code == 200, resp.text


def _force_variable_limit(server, limit=999):
    """Lower every new vault connection to SQLite's historical bind limit."""
    engine = server.vault.db._engine

    def set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)

    event.listen(engine, "connect", set_limit)
    engine.dispose()


def test_large_lock_lookups_survive_sqlite_variable_ceiling():
    """Regression for #694: reference scans can submit over 100k picture ids."""
    temp_dir, client, server = _setup()
    try:
        picture_ids = _first_n_pictures(server, 1001)
        _seed_stack_directly(server, picture_ids[:2])
        set_id = _create_set(client, "LargeLookupFreeze")
        _add_member(client, set_id, picture_ids[0])
        _add_member(client, set_id, picture_ids[2])
        _set_locked(client, set_id, True)
        _force_variable_limit(server)

        frozen, detail = server.vault.db.run_task(
            lambda session: (
                locked_picture_ids(session, picture_ids),
                locked_sets_for_pictures(session, picture_ids),
            )
        )

        assert frozen == set(picture_ids[:3])
        assert set(detail) == set(picture_ids[:3])
        assert {item["id"] for item in detail[picture_ids[1]]} == {set_id}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_large_project_and_stack_routes_survive_sqlite_variable_ceiling():
    """Expanded bulk mutations must not rebind the full id list downstream."""
    temp_dir, client, server = _setup()
    try:
        picture_ids = _first_n_pictures(server, 1001)
        _force_variable_limit(server)

        project_response = client.patch(
            "/pictures/project",
            json={"picture_ids": picture_ids, "project_id": None, "mode": "set"},
        )
        assert project_response.status_code == 200, project_response.text
        assert project_response.json()["missing_ids"] == []

        stack_response = client.post("/stacks", json={"picture_ids": picture_ids})
        assert stack_response.status_code == 200, stack_response.text
        assert set(stack_response.json()["picture_ids"]) == set(picture_ids)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _seed_tag(server, pic_id, tag):
    def insert(session):
        session.add(Tag(picture_id=pic_id, tag=tag))
        session.commit()

    server.vault.db.run_task(insert)


def _seed_suggestion(
    server,
    picture_id,
    tag,
    direction,
    *,
    twin_picture_id=None,
    status="PENDING",
    score=1.0,
):
    def insert(session):
        s = TagSuggestion(
            picture_id=picture_id,
            twin_picture_id=twin_picture_id,
            tag=tag,
            direction=direction,
            source="near_neighbor",
            score=score,
            status=status,
            reason="near-twin disagrees",
        )
        session.add(s)
        session.commit()
        session.refresh(s)
        return s.id

    return server.vault.db.run_task(insert)


# ---------------------------------------------------------------------------
# 1. Lock/unlock round-trip + serialization
# ---------------------------------------------------------------------------


def test_lock_flag_roundtrips_and_serializes():
    temp_dir, client, server = _setup()
    try:
        set_id = _create_set(client, "Freezer")

        # Default unlocked, present in list + read.
        listing = client.get("/picture_sets").json()
        row = next(s for s in listing if s["id"] == set_id)
        assert row["locked"] is False

        read = client.get(f"/picture_sets/{set_id}?info=true").json()
        assert read["locked"] is False

        _set_locked(client, set_id, True)
        assert (
            next(s for s in client.get("/picture_sets").json() if s["id"] == set_id)[
                "locked"
            ]
            is True
        )
        assert client.get(f"/picture_sets/{set_id}?info=true").json()["locked"] is True

        _set_locked(client, set_id, False)
        assert client.get(f"/picture_sets/{set_id}?info=true").json()["locked"] is False
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 2. Locked-set rejection matrix (field edits, delete, membership) + unlock
# ---------------------------------------------------------------------------


def test_locked_set_rejects_field_edits_and_delete():
    temp_dir, client, server = _setup()
    try:
        set_id = _create_set(client, "Frozen")
        _set_locked(client, set_id, True)

        # Read the current icon/color so the edit payloads below are a genuine
        # change (a PATCH that echoes the CURRENT value is a no-op and allowed -
        # sets are auto-assigned an icon/color on creation).
        current = client.get(f"/picture_sets/{set_id}?info=true").json()
        new_icon = "mdi-heart" if current.get("set_icon") != "mdi-heart" else "mdi-star"
        new_color = "#000000" if current.get("set_color") != "#000000" else "#ffffff"

        for payload in (
            {"name": "Renamed"},
            {"description": "new"},
            {"set_icon": new_icon},
            {"set_color": new_color},
        ):
            resp = client.patch(f"/picture_sets/{set_id}", json=payload)
            assert resp.status_code == 423, (payload, resp.text)
            assert resp.json()["detail"]["code"] == "set_locked"

        # Delete is refused too (unlock first).
        resp = client.delete(f"/picture_sets/{set_id}")
        assert resp.status_code == 423
        assert resp.json()["detail"]["code"] == "set_locked"

        # A no-op PATCH that only echoes the CURRENT values back (no effective
        # change) is allowed even while locked.
        resp = client.patch(f"/picture_sets/{set_id}", json={"name": "Frozen"})
        assert resp.status_code == 200, resp.text

        # Unlock-only PATCH succeeds; then edits and delete work again.
        _set_locked(client, set_id, False)
        assert (
            client.patch(f"/picture_sets/{set_id}", json={"name": "Thawed"}).status_code
            == 200
        )
        delete_resp = client.delete(f"/picture_sets/{set_id}")
        assert delete_resp.status_code == 200, delete_resp.text
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_locked_set_rejects_membership_mutations():
    temp_dir, client, server = _setup()
    try:
        pics = _first_n_pictures(server, 2)
        set_id = _create_set(client, "MemberFreeze")
        _add_member(client, set_id, pics[0])
        _set_locked(client, set_id, True)

        # single add
        r = client.post(f"/picture_sets/{set_id}/members/{pics[1]}")
        assert r.status_code == 423 and r.json()["detail"]["code"] == "set_locked"
        # single remove
        r = client.delete(f"/picture_sets/{set_id}/members/{pics[0]}")
        assert r.status_code == 423 and r.json()["detail"]["code"] == "set_locked"
        # bulk add
        r = client.post(
            f"/picture_sets/{set_id}/members", json={"picture_ids": [pics[1]]}
        )
        assert r.status_code == 423 and r.json()["detail"]["code"] == "set_locked"
        # bulk replace
        r = client.put(
            f"/picture_sets/{set_id}/members", json={"picture_ids": [pics[1]]}
        )
        assert r.status_code == 423 and r.json()["detail"]["code"] == "set_locked"

        # Membership is unchanged, and unlocking restores mutability.
        assert client.get(f"/picture_sets/{set_id}/members").json()["picture_ids"] == [
            pics[0]
        ]
        _set_locked(client, set_id, False)
        assert (
            client.post(f"/picture_sets/{set_id}/members/{pics[1]}").status_code == 200
        )
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 3. Cross-set rule: a picture in locked A + unlocked B
# ---------------------------------------------------------------------------


def test_cross_set_picture_label_freeze_and_membership_allowed():
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)
        set_a = _create_set(client, "LockedA")
        set_b = _create_set(client, "UnlockedB")
        _add_member(client, set_a, pic)
        _seed_tag(server, pic, "malformed hand")
        _set_locked(client, set_a, True)

        def _assert_locked(resp):
            assert resp.status_code == 423, resp.text
            body = resp.json()["detail"]
            assert body["code"] == "pictures_locked"
            assert any(s["name"] == "LockedA" for s in body["sets"])
            assert pic in body["picture_ids"]

        # Label data is frozen everywhere the picture appears.
        _assert_locked(client.post(f"/pictures/{pic}/tags", json={"tag": "new tag"}))
        _assert_locked(
            client.post(
                f"/pictures/{pic}/tags/remove_all", json={"tag": "malformed hand"}
            )
        )
        _assert_locked(client.delete(f"/pictures/{pic}/tags"))
        _assert_locked(client.patch(f"/pictures/{pic}", json={"description": "x"}))
        _assert_locked(client.patch(f"/pictures/{pic}", json={"score": 5}))
        # PATCH /pictures/{id} with a `tags` key (the DetachedInstance 500 is now
        # fixed) is frozen on a locked picture via the _replace_tags guard.
        _assert_locked(client.patch(f"/pictures/{pic}", json={"tags": ["x"]}))
        _assert_locked(client.delete(f"/pictures/{pic}"))

        # But membership of that same picture in an UNLOCKED set is allowed.
        add_resp = client.post(f"/picture_sets/{set_b}/members/{pic}")
        assert add_resp.status_code == 200, add_resp.text
        remove_resp = client.delete(f"/picture_sets/{set_b}/members/{pic}")
        assert remove_resp.status_code == 200, remove_resp.text

        # Unlocking A restores every label edit.
        _set_locked(client, set_a, False)
        assert (
            client.patch(f"/pictures/{pic}", json={"description": "ok"}).status_code
            == 200
        )
        assert client.patch(f"/pictures/{pic}", json={"score": 3}).status_code == 200
        assert (
            client.post(f"/pictures/{pic}/tags", json={"tag": "fine"}).status_code
            == 200
        )
        # PATCH tags succeeds once unlocked (regression: it used to 500 for all).
        assert (
            client.patch(f"/pictures/{pic}", json={"tags": ["fine2"]}).status_code
            == 200
        )
        delete_resp = client.delete(f"/pictures/{pic}")
        assert delete_resp.status_code == 200, delete_resp.text
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 4. Stack expansion: a locked sibling blocks the whole op post-expansion
# ---------------------------------------------------------------------------


def test_stack_sibling_in_locked_set_blocks_label_edit():
    temp_dir, client, server = _setup()
    try:
        pic_a, pic_b = _first_n_pictures(server, 2)
        assert (
            client.post("/stacks", json={"picture_ids": [pic_a, pic_b]}).status_code
            == 200
        )

        set_id = _create_set(client, "StackFreeze")
        # Sets are stack-atomic: adding one member makes the whole stack a member.
        _add_member(client, set_id, pic_a)
        _set_locked(client, set_id, True)

        # Editing / deleting EITHER stacked picture is blocked, naming the set.
        for target in (pic_a, pic_b):
            resp = client.patch(f"/pictures/{target}", json={"score": 4})
            assert resp.status_code == 423, (target, resp.text)
            assert resp.json()["detail"]["code"] == "pictures_locked"
            resp = client.delete(f"/pictures/{target}")
            assert resp.status_code == 423, (target, resp.text)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 5. Bulk delete skips locked ids and reports them
# ---------------------------------------------------------------------------


def test_bulk_delete_skips_locked_and_reports():
    temp_dir, client, server = _setup()
    try:
        locked_pic, free_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "BulkFreeze")
        _add_member(client, set_id, locked_pic)
        _set_locked(client, set_id, True)

        # conftest's path normalizer wraps get/post/patch/delete but not
        # client.request, so the API prefix is spelled out here (mirrors
        # tests/test_picture_mutation_scope.py's bulk-delete calls).
        resp = client.request(
            "DELETE",
            "/api/v1/pictures",
            json={"picture_ids": [locked_pic, free_pic]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted_count"] == 1
        assert body["skipped_locked"] == [locked_pic]

        # The free picture is gone; the locked one is untouched.
        assert client.get(f"/pictures/{free_pic}/metadata").status_code in (200, 404)
        meta = client.get(f"/pictures/{locked_pic}/metadata").json()
        assert meta["id"] == locked_pic
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 6. Locked-members endpoint + metadata locked_by_sets
# ---------------------------------------------------------------------------


def test_locked_members_endpoint_and_metadata():
    temp_dir, client, server = _setup()
    try:
        pic_locked, pic_free = _first_n_pictures(server, 2)
        locked_set = _create_set(client, "MembersFrozen")
        open_set = _create_set(client, "MembersOpen")
        _add_member(client, locked_set, pic_locked)
        _add_member(client, open_set, pic_free)

        # Nothing locked yet.
        assert client.get("/picture_sets/locked-members").json()["sets"] == []

        _set_locked(client, locked_set, True)
        payload = client.get("/picture_sets/locked-members").json()
        assert len(payload["sets"]) == 1
        entry = payload["sets"][0]
        assert entry["id"] == locked_set
        assert entry["name"] == "MembersFrozen"
        assert entry["picture_ids"] == [pic_locked]

        # metadata surfaces the locking set for the frozen picture only.
        locked_meta = client.get(f"/pictures/{pic_locked}/metadata").json()
        assert locked_meta["locked_by_sets"] == [
            {"id": locked_set, "name": "MembersFrozen"}
        ]
        # `locked` is the authoritative frozen-ness flag and tracks the names.
        assert locked_meta["locked"] is True
        free_meta = client.get(f"/pictures/{pic_free}/metadata").json()
        assert free_meta["locked_by_sets"] == []
        assert free_meta["locked"] is False
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_metadata_hides_locked_set_names_from_out_of_scope_token():
    """A share token learns a picture is frozen, never the private set's name.

    ``enforce_picture_scope`` authorizes the *picture*; it says nothing about the
    related entities named in its payload. A set-scoped READ token can hold a
    picture that is also a member of some other, locked set it cannot enumerate
    via ``GET /picture_sets`` - and set names are user-authored and routinely
    carry client / project / subject identifiers.

    Both directions: the out-of-scope name is withheld (while ``locked`` still
    tells the UI to disable its editing controls), and an in-scope locked set is
    still named, to the token and to the owner alike.
    """
    temp_dir, client, server = _setup()
    try:
        pic = _first_n_pictures(server, 1)[0]
        share_set = _create_set(client, "Shared")
        secret_set = _create_set(client, "SECRET-Client-Q3")
        _add_member(client, share_set, pic)
        _add_member(client, secret_set, pic)
        _set_locked(client, secret_set, True)

        token = client.post(
            "/users/me/token",
            json={
                "description": "set read",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": share_set,
            },
        ).json()["token"]
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}

        # The token may read the picture (it is in its share) …
        scoped = bearer.get(f"/pictures/{pic}/metadata", headers=headers)
        assert scoped.status_code == 200, scoped.text
        body = scoped.json()
        # … and must learn that it is frozen, so the UI disables the right
        # controls …
        assert body["locked"] is True
        # … but not the name or the id of the set doing the freezing.
        assert body["locked_by_sets"] == []
        assert "SECRET-Client-Q3" not in scoped.text
        assert str(secret_set) not in [str(s.get("id")) for s in body["locked_by_sets"]]
        # Cross-check the premise: the token genuinely cannot enumerate that set.
        visible = bearer.get("/picture_sets", headers=headers).json()
        assert [s["name"] for s in visible] == ["Shared"]

        # Owner is unaffected - the name is still served (no over-blocking).
        owner_body = client.get(f"/pictures/{pic}/metadata").json()
        assert owner_body["locked"] is True
        assert owner_body["locked_by_sets"] == [
            {"id": secret_set, "name": "SECRET-Client-Q3"}
        ]

        # And a set the token CAN see is still named to it: locking the shared
        # set must surface it, or we have simply over-blocked.
        _set_locked(client, share_set, True)
        scoped_body = bearer.get(f"/pictures/{pic}/metadata", headers=headers).json()
        assert scoped_body["locked"] is True
        assert scoped_body["locked_by_sets"] == [{"id": share_set, "name": "Shared"}], (
            "an in-scope locked set must still be named"
        )
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 7. Review backstops: creation blocked; decisions on locked suspects blocked
# ---------------------------------------------------------------------------


def test_review_creation_blocked_on_locked_set():
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)
        set_id = _create_set(client, "ReviewFreeze")
        _add_member(client, set_id, pic)
        _seed_tag(server, pic, "malformed hand")
        _set_locked(client, set_id, True)

        resp = client.post("/reviews", json={"tag": "malformed hand", "set_id": set_id})
        assert resp.status_code == 423, resp.text

        resp = client.get(
            "/reviews/preview", params={"tag": "malformed hand", "set_id": set_id}
        )
        assert resp.status_code == 423, resp.text
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_review_decisions_blocked_on_locked_suspect():
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)
        set_id = _create_set(client, "SuspectFreeze")
        _add_member(client, set_id, pic)
        _seed_tag(server, pic, "malformed hand")

        def seed_suggestion(session):
            s = TagSuggestion(
                picture_id=pic,
                tag="malformed hand",
                direction="remove",
                source="near_neighbor",
                score=1.0,
                reason="near-twin disagrees",
            )
            session.add(s)
            session.commit()
            session.refresh(s)
            return s.id

        sid = server.vault.db.run_task(seed_suggestion)
        _set_locked(client, set_id, True)

        assert client.post(f"/tag_suggestions/{sid}/accept").status_code == 423
        assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 423

        # prediction confirm/reject on a locked suspect is refused too.
        def seed_prediction(session):
            session.add(
                TagPrediction(
                    picture_id=pic,
                    tag="bad anatomy",
                    confidence=0.99,
                    model_version="testv1",
                )
            )
            session.commit()

        server.vault.db.run_task(seed_prediction)
        assert (
            client.post(
                f"/pictures/{pic}/tag_predictions/bad anatomy/confirm"
            ).status_code
            == 423
        )
        assert (
            client.post(
                f"/pictures/{pic}/tag_predictions/bad anatomy/reject"
            ).status_code
            == 423
        )

        # Unlocking restores the decision path.
        _set_locked(client, set_id, False)
        assert client.post(f"/tag_suggestions/{sid}/accept").status_code == 200
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 8. Scan excludes locked pictures as SUSPECTS but keeps them in the pool (twins)
# ---------------------------------------------------------------------------


def test_scan_excludes_locked_pictures_from_suspects_only():
    """A locked picture must never be surfaced as a suspect, yet must stay in the
    scan pool so it can still serve as a twin/neighbour guide.

    Driven through the confidence-fallback path (no ground truth for the tag) so
    the assertion is deterministic without running CLIP: both pictures carry a
    high-confidence prediction for the tag and no Tag row, so both would be "add"
    suspects - but the locked one is dropped from the suspect list while remaining
    counted in ``scanned`` (proof it stayed in the pool).
    """
    from pixlstash.services import tag_scan_service

    temp_dir, client, server = _setup()
    try:
        pic_locked, pic_free = _first_n_pictures(server, 2)
        set_id = _create_set(client, "ScanFreeze")
        _add_member(client, set_id, pic_locked)

        # Give both pictures a valid image embedding so they enter the scan pool,
        # and a high-confidence prediction (no Tag row) so the fallback proposes
        # them as "add" suspects.
        def seed(session):
            rng = np.random.default_rng(0)
            for pid in (pic_locked, pic_free):
                pic = session.get(Picture, pid)
                pic.image_embedding = (
                    rng.standard_normal(EMBEDDING_DIM).astype(np.float32).tobytes()
                )
                session.add(pic)
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="freeze tag",
                        confidence=0.99,
                        model_version="testv1",
                    )
                )
            session.commit()

        server.vault.db.run_task(seed)

        _set_locked(client, set_id, True)

        result = tag_scan_service.scan_tag(server.vault, "freeze tag")
        # The locked picture is still in the pool (scanned counts it) ...
        assert result["scanned"] >= 2

        def _suspect_ids(session):
            return set(
                session.exec(
                    select(TagSuggestion.picture_id).where(
                        TagSuggestion.tag == "freeze tag"
                    )
                ).all()
            )

        suspect_ids = server.vault.db.run_immediate_read_task(_suspect_ids)
        # ... but only the unlocked picture is a suspect.
        assert pic_free in suspect_ids
        assert pic_locked not in suspect_ids
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 9. CSO coverage-audit gaps: every other label/curation mutation path
# ---------------------------------------------------------------------------


def test_reset_tags_and_description_blocked_on_locked_pic():
    """reset_tags wipes all confirmed tags; reset_description clears the frozen
    caption - both refuse on a picture in a locked set."""
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)
        set_id = _create_set(client, "ResetFreeze")
        _add_member(client, set_id, pic)
        _seed_tag(server, pic, "malformed hand")
        _set_locked(client, set_id, True)

        assert client.post(f"/pictures/{pic}/reset_tags").status_code == 423
        assert client.post(f"/pictures/{pic}/reset_description").status_code == 423
        bulk = {"picture_ids": [pic]}
        assert client.post("/pictures/reset_tags", json=bulk).status_code == 423
        assert client.post("/pictures/reset_description", json=bulk).status_code == 423

        # The confirmed tag survived the refused reset.
        tags = client.get(f"/pictures/{pic}/tags").json()["tags"]
        assert any(t["tag"] == "malformed hand" for t in tags)

        # Unlock restores both.
        _set_locked(client, set_id, False)
        assert client.post(f"/pictures/{pic}/reset_tags").status_code == 200
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_fix_twin_blocked_when_twin_locked():
    """The plan lets a locked picture appear as a read-only TWIN; fix-twin writes
    to the twin, so it must refuse when the twin is locked (suspect is free)."""
    temp_dir, client, server = _setup()
    try:
        suspect, twin = _first_n_pictures(server, 2)
        locked_set = _create_set(client, "TwinFreeze")
        _add_member(client, locked_set, twin)  # lock the TWIN, not the suspect
        _seed_tag(server, suspect, "malformed hand")
        sid = _seed_suggestion(
            server, suspect, "malformed hand", "remove", twin_picture_id=twin
        )
        _set_locked(client, locked_set, True)

        resp = client.post(f"/tag_suggestions/{sid}/fix-twin")
        assert resp.status_code == 423, resp.text

        _set_locked(client, locked_set, False)
        assert client.post(f"/tag_suggestions/{sid}/fix-twin").status_code == 200
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_swap_blocked_when_suspect_locked():
    """swap writes Tag + ledger on both suspect and twin - refuse when either is
    locked (here the suspect)."""
    temp_dir, client, server = _setup()
    try:
        suspect, twin = _first_n_pictures(server, 2)
        locked_set = _create_set(client, "SwapFreeze")
        _add_member(client, locked_set, suspect)
        _seed_tag(server, suspect, "malformed hand")
        sid = _seed_suggestion(
            server, suspect, "malformed hand", "remove", twin_picture_id=twin
        )
        _set_locked(client, locked_set, True)

        assert client.post(f"/tag_suggestions/{sid}/swap").status_code == 423
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_reopen_blocked_on_locked_suspect():
    """Reopening a decision re-adds/deletes Tag rows on the suspect/twin - refuse
    when the suspect is locked."""
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)
        set_id = _create_set(client, "ReopenFreeze")
        _add_member(client, set_id, pic)
        _seed_tag(server, pic, "malformed hand")
        sid = _seed_suggestion(
            server, pic, "malformed hand", "remove", status="ACCEPTED"
        )
        _set_locked(client, set_id, True)

        assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 423
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_accept_skips_locked_rows_and_reports():
    """A bulk accept skips (and reports) any row whose suspect or twin is frozen,
    rather than 423-ing the whole batch."""
    temp_dir, client, server = _setup()
    try:
        locked_pic, free_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "BulkAcceptFreeze")
        _add_member(client, set_id, locked_pic)
        _seed_tag(server, locked_pic, "malformed hand")
        _seed_tag(server, free_pic, "malformed hand")
        _seed_suggestion(server, locked_pic, "malformed hand", "remove")
        _seed_suggestion(server, free_pic, "malformed hand", "remove")
        _set_locked(client, set_id, True)

        resp = client.post(
            "/tag_suggestions/bulk-accept",
            json={"tag": "malformed hand", "min_combined": 0.0, "dry_run": True},
        )
        assert resp.status_code == 200, resp.text
        assert locked_pic in resp.json()["skipped_locked"]
        assert free_pic not in resp.json()["skipped_locked"]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_bulk_reopen_skips_locked_rows_and_reports():
    """A batch undo skips (and reports) rows whose suspect/twin is frozen."""
    temp_dir, client, server = _setup()
    try:
        locked_pic, free_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "BulkReopenFreeze")
        _add_member(client, set_id, locked_pic)
        _seed_tag(server, locked_pic, "malformed hand")
        _seed_tag(server, free_pic, "malformed hand")
        sid_locked = _seed_suggestion(
            server, locked_pic, "malformed hand", "remove", status="ACCEPTED"
        )
        sid_free = _seed_suggestion(
            server, free_pic, "malformed hand", "remove", status="ACCEPTED"
        )
        _set_locked(client, set_id, True)

        resp = client.post(
            "/tag_suggestions/bulk-reopen", json={"ids": [sid_locked, sid_free]}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert locked_pic in body["skipped_locked"]
        assert body["count"] == 1  # only the free row reopened
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_reference_folder_metadata_import_skips_locked():
    """The sidecar-import route deletes+rewrites confirmed tags on EXISTING
    pictures; a locked picture is skipped (its frozen tags are preserved)."""
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)

        # Build a reference folder on disk with a tags sidecar that WOULD overwrite.
        folder_dir = os.path.join(temp_dir.name, "refs")
        os.makedirs(folder_dir, exist_ok=True)
        img_path = os.path.join(folder_dir, "target.png")
        shutil.copy(os.path.join(PICTURES_DIR, _PNG_POOL[0]), img_path)
        with open(os.path.join(folder_dir, "target_tags.txt"), "w") as f:
            f.write("sidecar-new-a, sidecar-new-b")

        def wire(session):
            rf = ReferenceFolder(folder=folder_dir, tags_suffix="_tags.txt")
            session.add(rf)
            session.commit()
            session.refresh(rf)
            db_pic = session.get(Picture, pic)
            db_pic.file_path = img_path
            db_pic.reference_folder_id = rf.id
            session.add(db_pic)
            # Replace any import sentinel with a confirmed tag we expect to survive.
            session.exec(delete(Tag).where(Tag.picture_id == pic))
            session.add(Tag(picture_id=pic, tag="frozen-keep"))
            session.commit()
            return rf.id

        folder_id = server.vault.db.run_task(wire)

        set_id = _create_set(client, "ImportFreeze")
        _add_member(client, set_id, pic)
        _set_locked(client, set_id, True)

        resp = client.post(
            f"/reference-folders/{folder_id}/metadata/import", json={"types": ["tags"]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["skipped_count"] >= 1

        # The locked picture kept its frozen tag; the sidecar did NOT overwrite it.
        tags = {t["tag"] for t in client.get(f"/pictures/{pic}/tags").json()["tags"]}
        assert tags == {"frozen-keep"}, tags
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_tagger_preserves_locked_confirmed_tags():
    """The background tagger's confirmed-Tag rewrite (TagTask._add_tags_bulk) must
    never overwrite a locked picture's tags, even if a retag sentinel is present."""
    from pixlstash.tasks.tag_task import TagTask

    temp_dir, client, server = _setup()
    try:
        locked_pic, free_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "TaggerFreeze")
        _add_member(client, set_id, locked_pic)

        def seed(session):
            for pid in (locked_pic, free_pic):
                session.exec(delete(Tag).where(Tag.picture_id == pid))
                session.add(Tag(picture_id=pid, tag="original"))
            session.commit()

        server.vault.db.run_task(seed)
        _set_locked(client, set_id, True)

        # Simulate a fresh tagger pass proposing a totally different tag set for both.
        def run_write(session):
            return TagTask._add_tags_bulk(
                session,
                [
                    {"pic_id": locked_pic, "tags": ["tagger-new"]},
                    {"pic_id": free_pic, "tags": ["tagger-new"]},
                ],
            )

        server.vault.db.run_task(run_write)

        locked_tags = {
            t["tag"] for t in client.get(f"/pictures/{locked_pic}/tags").json()["tags"]
        }
        free_tags = {
            t["tag"] for t in client.get(f"/pictures/{free_pic}/tags").json()["tags"]
        }
        # Locked picture's confirmed tags are untouched; the free one is rewritten.
        assert locked_tags == {"original"}, locked_tags
        assert free_tags == {"tagger-new"}, free_tags
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 10. Description freeze (rule 3): character reassignment + machine regeneration
# ---------------------------------------------------------------------------


def test_character_reassignment_preserves_locked_pic_description():
    """Reassigning/renaming a character invalidates its pictures' embeddings, but a
    locked picture's description (rule 3) must survive - the reassignment itself
    (rule 4) still succeeds."""
    temp_dir, client, server = _setup()
    try:
        (pic,) = _first_n_pictures(server, 1)
        char = client.post("/characters", json={"name": "Alice"}).json()["character"]
        char_id = char["id"]

        def seed(session):
            p = session.get(Picture, pic)
            p.description = "keepme-frozen"
            session.add(p)
            session.add(Face(picture_id=pic, character_id=char_id))
            session.commit()

        server.vault.db.run_task(seed)

        set_id = _create_set(client, "CharFreeze")
        _add_member(client, set_id, pic)
        _set_locked(client, set_id, True)

        # Rename the character -> triggers alter_char's embedding/description clear.
        resp = client.patch(f"/characters/{char_id}", json={"name": "Alice2"})
        assert resp.status_code == 200, resp.text
        # The rename succeeded ...
        assert client.get(f"/characters/{char_id}").json()["name"] == "Alice2"

        # ... but the locked picture kept its frozen description.
        desc = server.vault.db.run_immediate_read_task(
            lambda s: s.get(Picture, pic).description
        )
        assert desc == "keepme-frozen"
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_description_regeneration_skips_locked_pic():
    """The description finder never re-queues a locked pic, and the description
    task never persists a machine caption onto one - while a free pic regenerates."""
    import types

    from pixlstash.tasks.description_task import DescriptionTask
    from pixlstash.tasks.missing_description_finder import MissingDescriptionFinder

    temp_dir, client, server = _setup()
    try:
        locked_pic, free_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "DescFreeze")
        _add_member(client, set_id, locked_pic)

        def seed(session):
            lp = session.get(Picture, locked_pic)
            lp.description = "frozen-desc"
            session.add(lp)
            fp = session.get(Picture, free_pic)
            fp.description = None  # eligible for (re)description
            session.add(fp)
            session.commit()

        server.vault.db.run_task(seed)
        _set_locked(client, set_id, True)

        # (1) Finder exclusion: a locked pic is never queued for description work.
        finder_ids = set(
            p.id
            for p in server.vault.db.run_immediate_read_task(
                lambda s: MissingDescriptionFinder._fetch_missing_descriptions(s, 100)
            )
        )
        assert free_pic in finder_ids
        assert locked_pic not in finder_ids

        # (2) Write-side: even if a task is handed both, the locked pic is skipped.
        class _StubWorkflow:
            def generate_batch(self, pictures, engine_override=None, stop_event=None):
                return {p.id: "regenerated-caption" for p in pictures}

            def estimate_vram_mb(self, n, plugin_name=None):
                return 0

        pics = [
            types.SimpleNamespace(id=locked_pic, description="x"),
            types.SimpleNamespace(id=free_pic, description="x"),
        ]
        task = DescriptionTask(server.vault.db, _StubWorkflow(), pics)
        task._run_task()

        descs = server.vault.db.run_immediate_read_task(
            lambda s: (
                s.get(Picture, locked_pic).description,
                s.get(Picture, free_pic).description,
            )
        )
        assert descs[0] == "frozen-desc"  # locked: untouched
        assert descs[1] == "regenerated-caption"  # free: regenerated
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_legacy_suggestion_queue_withholds_locked_pending_rows():
    """The legacy GET /tag_suggestions list withholds still-PENDING suspects
    frozen by a locked set (every action on them 423s), but keeps already-decided
    rows listed as the audit record - same rule as the review queue."""
    temp_dir, client, server = _setup()
    try:
        locked_pic, free_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "LegacyQueueFreeze")
        _add_member(client, set_id, locked_pic)
        _seed_tag(server, locked_pic, "malformed hand")
        _seed_tag(server, free_pic, "malformed hand")
        _seed_suggestion(server, locked_pic, "malformed hand", "remove")
        _seed_suggestion(server, free_pic, "malformed hand", "remove")

        # Both listed while unlocked.
        rows = client.get("/tag_suggestions", params={"tag": "malformed hand"}).json()
        assert {r["picture_id"] for r in rows} == {locked_pic, free_pic}

        _set_locked(client, set_id, True)

        # The frozen one is withheld; the free one is untouched (no over-blocking).
        rows = client.get("/tag_suggestions", params={"tag": "malformed hand"}).json()
        assert [r["picture_id"] for r in rows] == [free_pic]

        # A row decided before the lock stays listed under its decided status.
        decided_id = _seed_suggestion(
            server, locked_pic, "bad anatomy", "remove", status="DISMISSED"
        )
        decided = client.get(
            "/tag_suggestions", params={"tag": "bad anatomy", "status": "DISMISSED"}
        ).json()
        assert [r["id"] for r in decided] == [decided_id]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 14. Finder selection uses the SAME locked definition as the write guards
# ---------------------------------------------------------------------------


def _seed_stack_directly(server, picture_ids):
    """Put *picture_ids* in one stack by writing ``stack_id`` straight to the DB.

    Deliberately bypasses ``POST /stacks``: that route now reconciles set
    membership across the stack (and refuses to grow a locked set), so it cannot
    produce the state under test here - a picture that *shares a stack with* a
    locked-set member without being a member itself. That state exists in any
    database written before the lock guards landed, and is still reachable
    through the restore path, so the finders must handle it.
    """

    def seed(session):
        stack = PictureStack(name="seeded")
        session.add(stack)
        session.flush()
        for pos, pid in enumerate(picture_ids):
            pic = session.get(Picture, pid)
            pic.stack_id = stack.id
            pic.stack_position = pos
            session.add(pic)
        session.commit()
        return int(stack.id)

    return server.vault.db.run_task(seed)


def test_finders_exclude_stack_sibling_of_locked_member():
    """Regression: an unbounded GPU re-queue loop.

    Both finders used a narrow ``PictureSetMember -> PictureSet.locked`` join with
    no stack arm, while their write guards (``locked_picture_ids``) expand to the
    whole stack. A picture sharing a stack with a locked-set member was therefore
    selected, ran full tagging/captioning inference, had its write skipped, kept
    its sentinel, and was selected again on the next sweep - forever.

    Asserts both directions: the stack sibling is NOT selected, and an unrelated
    unlocked picture still IS (over-blocking would be its own regression).
    """
    from pixlstash.tasks.missing_description_finder import MissingDescriptionFinder
    from pixlstash.tasks.missing_tag_finder import MissingTagFinder

    temp_dir, client, server = _setup()
    try:
        member_pic, sibling_pic, free_pic = _first_n_pictures(server, 3)

        # member_pic and sibling_pic share a stack; only member_pic is in the set.
        _seed_stack_directly(server, [member_pic, sibling_pic])
        set_id = _create_set(client, "LoopFreeze")
        _add_member_directly(server, set_id, member_pic)
        _set_locked(client, set_id, True)

        # Every picture carries pending work for both finders, and the face
        # stage the tag finder now waits for per picture has already run.
        def seed_work(session):
            for pid in (member_pic, sibling_pic, free_pic):
                session.add(Tag(picture_id=pid, tag=make_tag_sentinel()))
                session.add(Face(picture_id=pid, face_index=-1))
                pic = session.get(Picture, pid)
                pic.description = None
                session.add(pic)
            session.commit()

        server.vault.db.run_task(seed_work)

        tag_ids = {
            p.id
            for p in server.vault.db.run_immediate_read_task(
                lambda s: MissingTagFinder._fetch_missing_tags(s, 100)
            )
        }
        desc_ids = {
            p.id
            for p in server.vault.db.run_immediate_read_task(
                lambda s: MissingDescriptionFinder._fetch_missing_descriptions(s, 100)
            )
        }

        for name, selected in (("tag", tag_ids), ("description", desc_ids)):
            # The loop case: neither the member nor its stack sibling is queued.
            assert member_pic not in selected, name
            assert sibling_pic not in selected, name
            # The over-blocking case: an unrelated picture is still queued.
            assert free_pic in selected, name
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# 15. Membership-mutation paths respect the lock
# ---------------------------------------------------------------------------


def _set_member_ids(server, set_id):
    return server.vault.db.run_immediate_read_task(
        lambda s: {
            int(pid)
            for pid in s.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == set_id
                )
            ).all()
        }
    )


def test_stacking_refused_when_it_would_grow_a_locked_set():
    """Stacks are set-membership-atomic, so stacking a loose picture onto a
    locked-set member would add it to the locked set. That is a direct user
    request, so it fails loudly with 423 - and the locked set is unchanged."""
    temp_dir, client, server = _setup()
    try:
        member_pic, loose_pic = _first_n_pictures(server, 2)
        set_id = _create_set(client, "StackGrowFreeze")
        _add_member(client, set_id, member_pic)
        _set_locked(client, set_id, True)

        resp = client.post("/stacks", json={"picture_ids": [member_pic, loose_pic]})
        assert resp.status_code == 423, resp.text
        assert resp.json()["detail"]["code"] == "set_locked"
        assert [s["id"] for s in resp.json()["detail"]["sets"]] == [set_id]

        # The locked set did not gain a member, and no stack was created.
        assert _set_member_ids(server, set_id) == {member_pic}
        stacks = server.vault.db.run_immediate_read_task(
            lambda s: [
                pid
                for pid in s.exec(
                    select(Picture.stack_id).where(
                        Picture.id.in_([member_pic, loose_pic])
                    )
                ).all()
            ]
        )
        assert stacks == [None, None]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stacking_still_works_when_no_locked_set_would_grow():
    """Over-blocking regression: stacking is untouched for an unlocked set, and
    is still allowed when every resulting member is ALREADY in the locked set
    (that reconcile adds no row, so there is nothing to refuse)."""
    temp_dir, client, server = _setup()
    try:
        free_a, free_b, both_a, both_b = _first_n_pictures(server, 4)

        # (1) Unlocked set: stacking works normally and propagates membership.
        unlocked_id = _create_set(client, "StackUnlocked")
        _add_member(client, unlocked_id, free_a)
        resp = client.post("/stacks", json={"picture_ids": [free_a, free_b]})
        assert resp.status_code == 200, resp.text
        assert _set_member_ids(server, unlocked_id) == {free_a, free_b}

        # (2) Locked set that already contains both pictures: no row would be
        # added, so the stack is allowed.
        locked_id = _create_set(client, "StackAlreadyBoth")
        _add_member(client, locked_id, both_a)
        _add_member(client, locked_id, both_b)
        _set_locked(client, locked_id, True)
        resp = client.post("/stacks", json={"picture_ids": [both_a, both_b]})
        assert resp.status_code == 200, resp.text
        assert _set_member_ids(server, locked_id) == {both_a, both_b}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_comfyui_output_propagation_skips_locked_set():
    """A ComfyUI generation from a source picture in a locked set must not add
    its outputs to that set - but must still propagate the unlocked ones."""
    from pixlstash.routes.comfyui import _copy_set_and_project_assignments

    temp_dir, client, server = _setup()
    try:
        source_pic, output_pic = _first_n_pictures(server, 2)
        locked_id = _create_set(client, "GenLocked")
        open_id = _create_set(client, "GenOpen")
        _add_member(client, locked_id, source_pic)
        _add_member(client, open_id, source_pic)
        _set_locked(client, locked_id, True)

        _copy_set_and_project_assignments(server, source_pic, [output_pic])

        # Locked set unchanged; unlocked set still received the output.
        assert _set_member_ids(server, locked_id) == {source_pic}
        assert _set_member_ids(server, open_id) == {source_pic, output_pic}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_comfyui_view_context_assignment_skips_locked_set():
    """Generating while viewing a locked set must not drop the outputs into it."""
    from pixlstash.routes.comfyui import _assign_pictures_to_view_context

    temp_dir, client, server = _setup()
    try:
        (output_pic,) = _first_n_pictures(server, 1)
        locked_id = _create_set(client, "ViewLocked")
        _set_locked(client, locked_id, True)
        _assign_pictures_to_view_context(server, [output_pic], locked_id, None, None)
        assert _set_member_ids(server, locked_id) == set()

        # Unlocked control: the same call still assigns.
        open_id = _create_set(client, "ViewOpen")
        _assign_pictures_to_view_context(server, [output_pic], open_id, None, None)
        assert _set_member_ids(server, open_id) == {output_pic}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_image_plugin_output_propagation_skips_locked_set():
    """An upscale/edit run must not add its outputs to a locked source set."""
    from pixlstash.image_plugins.service import _propagate_output_picture_sets

    temp_dir, client, server = _setup()
    try:
        source_pic, output_pic = _first_n_pictures(server, 2)
        locked_id = _create_set(client, "PluginLocked")
        open_id = _create_set(client, "PluginOpen")
        _add_member(client, locked_id, source_pic)
        _add_member(client, open_id, source_pic)
        _set_locked(client, locked_id, True)

        _propagate_output_picture_sets(server, [source_pic], [output_pic])

        assert _set_member_ids(server, locked_id) == {source_pic}
        assert _set_member_ids(server, open_id) == {source_pic, output_pic}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ---------------------------------------------------------------------------
# Detaching a stack member: one contract, three routes
#
# A locked set freezes a stack's siblings THROUGH the stack (every picture-level
# guard runs on the stack-expanded id list), so detaching a member severs a
# freeze the lock exists to hold. Left unguarded, that is not a missing check but
# a lock ESCAPE: unstack, then delete, and a picture that answered 423 a moment
# ago is soft-deleted. `POST /dedup/mixed-stacks/{id}/split`, `POST
# /dedup/mixed-stacks/{id}/unstack` and the older `DELETE /stacks/{id}/members`
# all detach members, so all three carry the same whole-stack 423.
# ---------------------------------------------------------------------------

_API = "/api/v1"


def _stack_of(server, picture_id):
    return server.vault.db.run_immediate_read_task(
        lambda s: s.get(Picture, picture_id).stack_id
    )


def _remove_members(client, stack_id, picture_ids):
    """``DELETE /stacks/{id}/members``; needs ``request`` for a JSON body."""
    return client.request(
        "DELETE",
        f"{_API}/stacks/{stack_id}/members",
        json={"picture_ids": picture_ids},
    )


def test_locked_set_refuses_every_route_that_detaches_a_stack_member():
    """Negative direction, whole-stack, on all three detach routes.

    The picture named in each request (``sibling``) is deliberately NOT itself a
    member of the locked set: it is frozen only because it shares a stack with
    one. That is the case a per-member check would wave through, and it is the
    case that escalates.

    The membership is seeded row-by-row for that reason. ``POST
    /picture_sets/{id}/members/{picture_id}`` is stack-atomic, so adding the
    frozen picture through the API would pull the sibling into the set too and
    the test would pass against a per-named-id guard, which is the exact
    narrowing that would reintroduce the bug. The asserted membership below is
    the guard against that regression re-entering the test.
    """
    temp_dir, client, server = _setup()
    try:
        frozen, sibling = _first_n_pictures(server, 2)
        stack_id = _seed_stack_directly(server, [frozen, sibling])
        set_id = _create_set(client, "DetachFreeze")
        _add_member_directly(server, set_id, frozen)
        _set_locked(client, set_id, True)
        assert _set_member_ids(server, set_id) == {frozen}, (
            "the sibling must NOT be a member: the whole point is that it is "
            "frozen only THROUGH the stack"
        )

        responses = {
            "remove_members": _remove_members(client, stack_id, [sibling]),
            "split": client.post(
                f"/dedup/mixed-stacks/{stack_id}/split",
                json={"picture_ids": [sibling]},
            ),
            "unstack": client.post(f"/dedup/mixed-stacks/{stack_id}/unstack", json={}),
        }
        for name, resp in responses.items():
            assert resp.status_code == 423, (name, resp.status_code, resp.text)
            detail = resp.json()["detail"]
            assert detail["code"] == "pictures_locked", (name, detail)
            assert [s["id"] for s in detail["sets"]] == [set_id], (name, detail)

        # Fail-closed, not fail-late: neither member moved and the stack stands.
        assert _stack_of(server, frozen) == stack_id
        assert _stack_of(server, sibling) == stack_id
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_unlocked_stacks_still_split_unstack_and_lose_members():
    """Positive direction: over-blocking is its own regression.

    One fresh stack per route, because two of the three dissolve it. The
    pictures carry no perceptual hash, so every member is stranded and the
    mixed-stack routes have something to act on.
    """
    temp_dir, client, server = _setup()
    try:
        pics = _first_n_pictures(server, 6)
        remove_stack = _seed_stack_directly(server, pics[0:2])
        split_stack = _seed_stack_directly(server, pics[2:4])
        unstack_stack = _seed_stack_directly(server, pics[4:6])

        resp = _remove_members(client, remove_stack, [pics[0]])
        assert resp.status_code == 200, resp.text
        assert _stack_of(server, pics[0]) is None

        resp = client.post(f"/dedup/mixed-stacks/{split_stack}/split", json={})
        assert resp.status_code == 200, resp.text
        assert _stack_of(server, pics[2]) is None
        assert _stack_of(server, pics[3]) is None

        resp = client.post(f"/dedup/mixed-stacks/{unstack_stack}/unstack", json={})
        assert resp.status_code == 200, resp.text
        assert _stack_of(server, pics[4]) is None
        assert _stack_of(server, pics[5]) is None
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_detaching_cannot_be_used_to_escape_a_locked_set():
    """Regression for the two-call lock escape, proved to fail at the FIRST call.

    Before the guard: ``DELETE /pictures/{sibling}`` answered 423 because the
    freeze reached it through the stack; ``POST .../unstack`` then returned 200,
    severed the stack, and the same delete returned 200. Two calls turned a hard
    freeze into a soft delete. The chain must now break on step one, and the
    freeze must still be intact afterwards.

    Seeded row-by-row (see
    :func:`test_locked_set_refuses_every_route_that_detaches_a_stack_member`):
    only ``frozen`` is a member, so the sibling's 423 can come from nothing but
    the stack, which is the state this regression is named for.
    """
    temp_dir, client, server = _setup()
    try:
        frozen, sibling = _first_n_pictures(server, 2)
        stack_id = _seed_stack_directly(server, [frozen, sibling])
        set_id = _create_set(client, "EscapeFreeze")
        _add_member_directly(server, set_id, frozen)
        _set_locked(client, set_id, True)
        assert _set_member_ids(server, set_id) == {frozen}

        # Baseline: the freeze reaches the sibling through the stack.
        assert client.delete(f"/pictures/{sibling}").status_code == 423

        # Step 1 of the escape, by each of the three routes that used to work.
        assert (
            client.post(f"/dedup/mixed-stacks/{stack_id}/unstack", json={}).status_code
            == 423
        )
        assert (
            client.post(
                f"/dedup/mixed-stacks/{stack_id}/split", json={"picture_ids": [sibling]}
            ).status_code
            == 423
        )
        assert _remove_members(client, stack_id, [sibling]).status_code == 423

        # Step 2 is therefore never reached: the freeze is exactly as it was.
        assert _stack_of(server, sibling) == stack_id
        assert client.delete(f"/pictures/{sibling}").status_code == 423
        assert client.delete(f"/pictures/{frozen}").status_code == 423
        assert (
            server.vault.db.run_immediate_read_task(
                lambda s: s.get(Picture, sibling).deleted
            )
            is False
        )

        # Unlocking is the only way through, and it still works.
        _set_locked(client, set_id, False)
        assert (
            client.post(f"/dedup/mixed-stacks/{stack_id}/unstack", json={}).status_code
            == 200
        )
        assert _stack_of(server, sibling) is None
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _scrapheap_directly(server, picture_id):
    """Soft-delete *picture_id* straight in the DB.

    ``DELETE /pictures/{id}`` refuses a locked-set member (that is the lock
    working), so the only way to reach "a scrapheaped picture that is a member
    of a set locked afterwards" is to write it. That state is reachable in a
    real vault by scrapheaping first and locking second.
    """

    def scrapheap(session):
        picture = session.get(Picture, int(picture_id))
        picture.deleted = True
        session.add(picture)
        session.commit()

    server.vault.db.run_task(scrapheap)


def test_a_scrapheaped_locked_member_still_freezes_its_stack_against_detach():
    """The soft-deleted arm of ``_stack_member_ids``, on all three routes.

    ``_stack_member_ids`` is deliberately unfiltered on ``deleted``, and this is
    the state that makes that load-bearing: the stack's ONLY locked-set member
    is scrapheaped, and no live member is a member of anything. Filter the
    deleted rows out of that helper and every route below flips from 423 to
    200 with the rest of the suite still green.

    It also pins the one place the stack rule and the picture-level rule
    deliberately differ, so a later "consistency" edit cannot quietly merge
    them:

    * **Picture level** - the live siblings are NOT frozen. A scrapheaped
      member projects no freeze onto them (``expand_picture_ids_to_stacks``
      drops deleted co-members, and ``locked_picture_id_subquery`` filters
      ``deleted`` on the stack-derived arm), so their label data is editable
      and ``DELETE /pictures/{sibling}`` succeeds. Nothing is frozen through
      this stack, so there is nothing for a detach to sever.
    * **Stack level** - the stack still refuses to break up, because the
      scrapheaped row is *itself* a member of the locked set and every detach
      route dissolves the stack (taking the scrapheaped rows with it) rather
      than leaving a stack of one. Detaching it is a deferred escape: restore
      it afterwards and it comes back loose, so the freeze it would have
      projected over its siblings never returns.
    """
    temp_dir, client, server = _setup()
    try:
        frozen, live_a, live_b = _first_n_pictures(server, 3)
        stack_id = _seed_stack_directly(server, [frozen, live_a, live_b])
        set_id = _create_set(client, "ScrapheapFreeze")
        _add_member_directly(server, set_id, frozen)
        _scrapheap_directly(server, frozen)
        _set_locked(client, set_id, True)
        assert _set_member_ids(server, set_id) == {frozen}

        # The state under test: no LIVE member of this stack is in any set.
        assert client.delete(f"/pictures/{live_a}").status_code == 200
        client.post("/pictures/scrapheap/restore", json={"picture_ids": [live_a]})
        assert (
            server.vault.db.run_immediate_read_task(
                lambda s: s.get(Picture, live_a).deleted
            )
            is False
        )

        # Yet all three detach routes still refuse the whole stack.
        responses = {
            "remove_members": _remove_members(client, stack_id, [live_a]),
            "split": client.post(
                f"/dedup/mixed-stacks/{stack_id}/split",
                json={"picture_ids": [live_a]},
            ),
            "unstack": client.post(f"/dedup/mixed-stacks/{stack_id}/unstack", json={}),
        }
        for name, resp in responses.items():
            assert resp.status_code == 423, (name, resp.status_code, resp.text)
            detail = resp.json()["detail"]
            assert detail["code"] == "pictures_locked", (name, detail)
            assert [s["id"] for s in detail["sets"]] == [set_id], (name, detail)
            # The scrapheaped row is named as the frozen one, which is the only
            # evidence that the guard read a deleted member row at all.
            assert detail["picture_ids"] == [frozen], (name, detail)

        # Fail-closed: nothing moved.
        for pid in (frozen, live_a, live_b):
            assert _stack_of(server, pid) == stack_id

        # Unlocking is still the way through.
        _set_locked(client, set_id, False)
        assert (
            client.post(f"/dedup/mixed-stacks/{stack_id}/unstack", json={}).status_code
            == 200
        )
        assert _stack_of(server, live_a) is None
        assert _stack_of(server, frozen) is None
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_a_scrapheaped_member_of_an_unlocked_set_never_freezes_the_stack():
    """Over-blocking regression for the arm above.

    Same shape, one difference: the set holding the scrapheaped member is not
    locked. A guard that read "has a scrapheaped member" rather than "has a
    scrapheaped member of a LOCKED set" would refuse this too, and the whole
    Mixed stacks page would stop working on any stack with a scrapheap entry.
    """
    temp_dir, client, server = _setup()
    try:
        heaped, live_a, live_b = _first_n_pictures(server, 3)
        stack_id = _seed_stack_directly(server, [heaped, live_a, live_b])
        set_id = _create_set(client, "OpenScrapheap")
        _add_member_directly(server, set_id, heaped)
        _scrapheap_directly(server, heaped)

        assert _remove_members(client, stack_id, [live_a]).status_code == 200
        assert _stack_of(server, live_a) is None
        # Only one live member remains, so the stack dissolves immediately;
        # the open set must not block that cleanup.
        assert _stack_of(server, live_b) is None
        # The dissolve takes the scrapheaped row with it too.
        assert _stack_of(server, heaped) is None
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()
