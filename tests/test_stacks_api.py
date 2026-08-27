"""Tests for the Stacks API: create, read, reorder, and auto-delete on last removal."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, PictureStack
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")


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
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _upload_picture(client, filename="Bad1.png"):
    img_path = os.path.join(PICTURES_DIR, filename)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(
            client, [("file", (filename, f, "image/png"))]
        )
    assert result["status"] == "completed"
    return result["results"][0]["picture_id"]


def test_create_stack_and_list_members():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post(
            "/stacks",
            json={"picture_ids": [pic_id1, pic_id2], "name": "TestStack"},
        )
        assert resp.status_code == 200
        stack_id = resp.json()["id"]
        assert stack_id

        resp = client.get(f"/stacks/{stack_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data["picture_ids"]) == {pic_id1, pic_id2}
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_stack_pictures_in_order():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post("/stacks", json={"picture_ids": [pic_id1, pic_id2]})
        assert resp.status_code == 200
        stack_id = resp.json()["id"]

        resp = client.get(f"/stacks/{stack_id}/pictures")
        assert resp.status_code == 200
        pics = resp.json()
        assert isinstance(pics, list)
        assert len(pics) == 2
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stack_member_reorder():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post("/stacks", json={"picture_ids": [pic_id1, pic_id2]})
        stack_id = resp.json()["id"]

        # Move pic_id1 to position 1 (last)
        resp = client.patch(
            f"/stacks/{stack_id}/members/{pic_id1}",
            json={"position": 1},
        )
        assert resp.status_code == 200
        ordered_ids = resp.json()["picture_ids"]
        assert ordered_ids.index(pic_id1) == 1
        assert ordered_ids.index(pic_id2) == 0
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_remove_last_member_auto_deletes_stack():
    temp_dir, client, server = _setup()
    try:
        pic_id1 = _upload_picture(client, "Bad1.png")
        pic_id2 = _upload_picture(client, "Bad2.png")

        resp = client.post("/stacks", json={"picture_ids": [pic_id1, pic_id2]})
        stack_id = resp.json()["id"]

        # Remove both members - stack should be auto-deleted when only 1 remains
        resp = client.request(
            "DELETE",
            f"/api/v1/stacks/{stack_id}/members",
            json={"picture_ids": [pic_id1, pic_id2]},
        )
        assert resp.status_code == 200
        assert resp.json().get("stack_id") is None

        resp = client.get(f"/stacks/{stack_id}")
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_stack_not_found_returns_404():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/stacks/99999")
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


# ── DELETE /stacks/{id}/members: what it leaves behind ───────────────────────
#
# Both tests below insert their pictures directly with background workers off:
# they assert exact ``stack_position`` values and need one member soft-deleted,
# and an import worker rewriting positions underneath would make either
# assertion meaningless.


def _setup_direct():
    """A logged-in client on a server with background workers disabled."""
    temp_dir = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _make_stack_directly(server, count, deleted_positions=()):
    """Insert one stack of *count* members at positions ``0..count-1``."""

    def insert(session):
        stack = PictureStack(name=None)
        session.add(stack)
        session.flush()
        picture_ids = []
        for position in range(count):
            picture = Picture(
                file_path=f"/vault/members_{int(stack.id)}_{position}.png",
                format="png",
                width=100,
                height=100,
                size_bytes=100,
                stack_id=int(stack.id),
                stack_position=position,
                deleted=position in set(deleted_positions),
            )
            session.add(picture)
            session.flush()
            picture_ids.append(int(picture.id))
        session.commit()
        return int(stack.id), picture_ids

    return server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)


def _stack_state(server, picture_ids):
    """``{picture_id: (stack_id, stack_position)}`` for the given pictures."""

    def read(session):
        rows = session.exec(
            select(Picture.id, Picture.stack_id, Picture.stack_position).where(
                Picture.id.in_(picture_ids)
            )
        ).all()
        return {int(pid): (sid, pos) for pid, sid, pos in rows}

    return server.vault.db.run_task(read, priority=DBPriority.IMMEDIATE)


def _delete_members(client, stack_id, picture_ids):
    # httpx's `delete` carries no body, so the request is issued the long way.
    # `client.request` is not the conftest-patched wrapper, hence the full path.
    return client.request(
        "DELETE",
        f"/api/v1/stacks/{stack_id}/members",
        json={"picture_ids": picture_ids},
    )


def test_removing_a_member_clears_its_stack_position():
    """A detached picture keeps no position: ``(None, None)``, never ``(None, 2)``.

    A ``stack_position`` without a ``stack_id`` is meaningless state that any
    later ordering query can still read. The dissolve branch of this same
    handler and the mixed-stack split path both already clear both fields; this
    branch did not.
    """
    temp_dir, client, server = _setup_direct()
    try:
        stack_id, picture_ids = _make_stack_directly(server, 3)
        assert _stack_state(server, picture_ids)[picture_ids[2]] == (stack_id, 2)

        resp = _delete_members(client, stack_id, [picture_ids[2]])
        assert resp.status_code == 200, resp.text

        state = _stack_state(server, picture_ids)
        assert state[picture_ids[2]] == (None, None), (
            "the detached picture must lose its position along with its stack"
        )
        # Over-blocking guard: the survivors keep the stack and are compacted.
        assert state[picture_ids[0]] == (stack_id, 0)
        assert state[picture_ids[1]] == (stack_id, 1)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_scrapheaped_member_does_not_keep_a_one_live_member_stack_alive():
    """Dissolution is based on visible membership, then detaches every row."""
    temp_dir, client, server = _setup_direct()
    try:
        stack_id, picture_ids = _make_stack_directly(server, 3, deleted_positions={2})

        resp = _delete_members(client, stack_id, [picture_ids[0]])
        assert resp.status_code == 200, resp.text
        assert resp.json().get("stack_id") is None
        assert all(
            state == (None, None)
            for state in _stack_state(server, picture_ids).values()
        )
        assert client.get(f"/stacks/{stack_id}").status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_removing_a_member_records_the_scrapheaped_members_so_undo_restores_them():
    """The undo snapshot has to cover the soft-deleted members too.

    Removing a member renumbers the stack via ``normalize_stack_positions``,
    which renumbers **every** row pointing at the stack, soft-deleted ones
    included. Snapshot only the live members and the scrapheaped member's
    ``stack_position`` moves outside the operation, so undo cannot put it back:
    the picture returns from the Scrapheap in the wrong place, or (on the
    dissolve branch) with no stack at all. ``mixed_stack_service._apply_removal``
    passes ``include_deleted=True`` for this exact hazard.

    Fixture: four members, the second scrapheaped. Removing the leader leaves
    the two live members plus the deleted one, and normalization ranks live
    before deleted, so the scrapheaped member is pushed from position 1 to 2.
    """
    temp_dir, client, server = _setup_direct()
    try:
        stack_id, picture_ids = _make_stack_directly(server, 4, deleted_positions={1})
        before = _stack_state(server, picture_ids)
        assert before[picture_ids[1]] == (stack_id, 1)

        resp = _delete_members(client, stack_id, [picture_ids[0]])
        assert resp.status_code == 200, resp.text

        after = _stack_state(server, picture_ids)
        assert after[picture_ids[0]] == (None, None)
        assert after[picture_ids[1]] == (stack_id, 2), (
            "the scrapheaped member is renumbered by a removal nobody named it "
            "in; that is precisely why it has to be in the snapshot"
        )

        assert client.post("/operations/undo", json={}).status_code == 200
        assert _stack_state(server, picture_ids) == before, (
            "one undo must restore every member's stack id AND position, "
            "including the members that are in the Scrapheap"
        )
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()
