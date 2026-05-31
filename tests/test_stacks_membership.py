"""Tests for stack-atomic project & set membership.

Stacks are a single unit for grouping membership: a project/set change applied to
any member (e.g. a collapsed-stack leader) applies to every member, and a picture
joining a stack reconciles the stack to the union of its members' memberships.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models import Face, PictureSetMember
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait


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


def _import_pictures(client, names):
    """Import the given image filenames from the repo ``pictures/`` dir and
    return their new picture ids in order."""
    pictures_dir = os.path.join(os.path.dirname(__file__), "..", "pictures")
    handles = []
    files = []
    try:
        for name in names:
            path = os.path.join(pictures_dir, name)
            fh = open(path, "rb")
            handles.append(fh)
            mime = "image/png" if name.lower().endswith(".png") else "image/jpeg"
            files.append(("file", (name, fh, mime)))
        result = upload_pictures_and_wait(client, files)
    finally:
        for fh in handles:
            fh.close()
    ids = [r["picture_id"] for r in result["results"] if r.get("picture_id")]
    assert len(ids) == len(names), f"Expected {len(names)} imports, got {ids}"
    return ids


def _project_picture_ids(client, project_id):
    resp = client.get("/pictures", params={"project_id": str(project_id)})
    assert resp.status_code == 200
    return {row.get("id") for row in resp.json()}


def _stack(client, picture_ids):
    resp = client.post("/stacks", json={"picture_ids": picture_ids})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set_member_ids(server, set_id):
    def q(session):
        return [
            int(m)
            for m in session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == set_id
                )
            ).all()
        ]

    return set(server.vault.db.run_task(q))


def test_adding_collapsed_stack_to_project_adds_every_member():
    temp_dir, client, server = _setup()
    try:
        project_id = client.post("/projects", json={"name": "P"}).json()["id"]
        ids = _import_pictures(
            client, ["Reference1.png", "Reference2.png", "Reference3.png"]
        )
        _stack(client, ids)
        leader = ids[0]

        # Act on the leader id only (as a collapsed-stack action would).
        resp = client.patch(
            "/pictures/project",
            json={"picture_ids": [leader], "project_id": project_id},
        )
        assert resp.status_code == 200, resp.text

        in_project = _project_picture_ids(client, project_id)
        for pid in ids:
            assert pid in in_project, f"{pid} should be in project (atomic stack add)"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_removing_collapsed_stack_from_project_removes_every_member():
    """Bug 1: removing a collapsed stack from a project must remove all members."""
    temp_dir, client, server = _setup()
    try:
        project_id = client.post("/projects", json={"name": "P"}).json()["id"]
        ids = _import_pictures(
            client, ["Reference1.png", "Reference2.png", "Reference3.png"]
        )
        _stack(client, ids)
        leader = ids[0]

        client.patch(
            "/pictures/project", json={"picture_ids": ids, "project_id": project_id}
        )
        assert _project_picture_ids(client, project_id) >= set(ids)

        # Remove acting on the leader id only.
        resp = client.patch(
            "/pictures/project",
            json={"picture_ids": [leader], "project_id": project_id, "mode": "remove"},
        )
        assert resp.status_code == 200, resp.text

        in_project = _project_picture_ids(client, project_id)
        for pid in ids:
            assert pid not in in_project, (
                f"{pid} should be removed (atomic stack remove)"
            )
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_adding_collapsed_stack_to_set_adds_every_member():
    temp_dir, client, server = _setup()
    try:
        set_id = client.post("/picture_sets", json={"name": "S"}).json()["picture_set"][
            "id"
        ]
        ids = _import_pictures(
            client, ["Reference1.png", "Reference2.png", "Reference3.png"]
        )
        _stack(client, ids)

        # Add the leader only.
        resp = client.post(f"/picture_sets/{set_id}/members/{ids[0]}")
        assert resp.status_code == 200, resp.text

        members = _set_member_ids(server, set_id)
        assert members == set(ids), f"all members expected in set, got {members}"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_removing_collapsed_stack_from_set_removes_every_member():
    temp_dir, client, server = _setup()
    try:
        set_id = client.post("/picture_sets", json={"name": "S"}).json()["picture_set"][
            "id"
        ]
        ids = _import_pictures(
            client, ["Reference1.png", "Reference2.png", "Reference3.png"]
        )
        _stack(client, ids)
        client.post(f"/picture_sets/{set_id}/members", json={"picture_ids": ids})
        assert _set_member_ids(server, set_id) == set(ids)

        # Remove the leader only.
        resp = client.delete(f"/picture_sets/{set_id}/members/{ids[0]}")
        assert resp.status_code == 200, resp.text
        assert _set_member_ids(server, set_id) == set()
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def _add_face(server, picture_id, character_id=None):
    def add(session):
        session.add(
            Face(
                picture_id=picture_id,
                frame_index=0,
                face_index=0,
                character_id=character_id,
                bbox_="0,0,20,20",
            )
        )
        session.commit()

    server.vault.db.run_task(add)


def _face_character_ids(server, picture_ids):
    def q(session):
        return {
            int(pid): char_id
            for pid, char_id in session.exec(
                select(Face.picture_id, Face.character_id).where(
                    Face.picture_id.in_(picture_ids)
                )
            ).all()
        }

    return server.vault.db.run_task(q)


def test_dragging_stack_to_character_moves_every_member():
    """Dragging a collapsed stack (leader id only) onto a character assigns every
    member's face to that character, not just the leader's."""
    temp_dir, client, server = _setup()
    try:
        ids = _import_pictures(
            client, ["Reference1.png", "Reference2.png", "Reference3.png"]
        )
        for pid in ids:
            _add_face(server, pid)

        old_char = client.post("/characters", json={"name": "Old"}).json()["character"][
            "id"
        ]
        new_char = client.post("/characters", json={"name": "New"}).json()["character"][
            "id"
        ]

        # Start with the whole stack on the old character.
        for pid in ids:
            resp = client.post(
                f"/characters/{old_char}/faces", json={"picture_ids": [pid]}
            )
            assert resp.status_code == 200, resp.text

        _stack(client, ids)
        assert all(v == old_char for v in _face_character_ids(server, ids).values())

        # Drag the collapsed stack (leader id only) onto the new character.
        resp = client.post(
            f"/characters/{new_char}/faces", json={"picture_ids": [ids[0]]}
        )
        assert resp.status_code == 200, resp.text

        char_by_pic = _face_character_ids(server, ids)
        assert all(char_by_pic[pid] == new_char for pid in ids), (
            f"all members should move to new character, got {char_by_pic}"
        )
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_stacking_unions_project_membership():
    """A picture joining a stack reconciles the stack to the union of members'
    project memberships (so the stack is consistent again)."""
    temp_dir, client, server = _setup()
    try:
        project_id = client.post("/projects", json={"name": "P"}).json()["id"]
        ids = _import_pictures(
            client, ["Reference1.png", "Reference2.png", "Reference3.png"]
        )
        # Put only the first two in the project; the third stays out.
        client.patch(
            "/pictures/project",
            json={"picture_ids": ids[:2], "project_id": project_id},
        )

        # Stacking all three should union → all three end up in the project.
        _stack(client, ids)

        in_project = _project_picture_ids(client, project_id)
        for pid in ids:
            assert pid in in_project, f"{pid} should be in project after union"
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()
