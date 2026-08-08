"""Tests for the Characters API: create, update, delete, and reference pictures."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.db_models import Face
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


def test_create_character():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "Alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        char = data["character"]
        assert char["name"] == "Alice"
        assert char["id"] is not None
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_character_by_id():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "Bob"})
        char_id = resp.json()["character"]["id"]

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == char_id
        assert resp.json()["name"] == "Bob"
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_patch_character_name_and_description():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "Charlie"})
        char_id = resp.json()["character"]["id"]

        resp = client.patch(
            f"/characters/{char_id}",
            json={"name": "Charles", "description": "Updated description"},
        )
        assert resp.status_code == 200

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Charles"
        assert resp.json()["description"] == "Updated description"

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Charles"
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_delete_character_returns_success():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "DeleteMe"})
        char_id = resp.json()["character"]["id"]

        resp = client.delete(f"/characters/{char_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted_id"] == char_id

        resp = client.get(f"/characters/{char_id}")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            assert resp.json() is None
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_character_reference_pictures_empty_without_faces():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/characters", json={"name": "NoFaces"})
        char_id = resp.json()["character"]["id"]

        resp = client.get(f"/characters/{char_id}/reference_pictures")
        assert resp.status_code == 200
        assert resp.json()["reference_picture_ids"] == []
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_delete_nonexistent_character_returns_404():
    temp_dir, client, server = _setup()
    try:
        resp = client.delete("/characters/99999")
        assert resp.status_code == 404
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_characters_filtered_by_numeric_project_id():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "CharFilter Project"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.post(
            "/characters", json={"name": "InProject", "project_id": project_id}
        )
        assert resp.status_code == 200

        resp = client.post("/characters", json={"name": "NotInProject"})
        assert resp.status_code == 200

        resp = client.get(f"/characters?project_id={project_id}")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "InProject" in names
        assert "NotInProject" not in names
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_characters_filtered_by_unassigned():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "UnassignedFilter Project"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.post(
            "/characters", json={"name": "Assigned", "project_id": project_id}
        )
        assert resp.status_code == 200

        resp = client.post("/characters", json={"name": "Unassigned"})
        assert resp.status_code == 200

        resp = client.get("/characters?project_id=UNASSIGNED")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "Unassigned" in names
        assert "Assigned" not in names
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_characters_invalid_project_id_returns_400():
    temp_dir, client, server = _setup()
    try:
        resp = client.get("/characters?project_id=not-a-number")
        assert resp.status_code == 400
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_character_scoped_token_may_not_filter_by_project():
    """Issue #708 F3 — this test previously pinned the vulnerability.

    It used to assert that a character-scoped token filtering
    ``/characters?project_id=<id>`` got its character back, and that the same
    request with ``UNASSIGNED`` did not. Comparing those two answers *is* the
    disclosure: it tells a token that is 403'd on ``GET /projects/{id}`` that the
    project exists and that its character is filed under it. Both requests are
    now refused by the authz gate (``enforce_project_filter_scope``), which is
    the behaviour this test pins instead. The unfiltered listing, which is what
    the share UI actually issues, must keep working — over-blocking would be its
    own regression.
    """
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/projects", json={"name": "ScopedTokenProject"})
        assert resp.status_code == 200
        project_id = resp.json()["id"]

        resp = client.post(
            "/characters", json={"name": "CharInProject", "project_id": project_id}
        )
        assert resp.status_code == 200
        char_id = resp.json()["character"]["id"]

        resp = client.post(
            "/users/me/token",
            json={
                "description": "char token",
                "scope": "READ",
                "resource_type": "character",
                "resource_id": char_id,
            },
        )
        assert resp.status_code == 200
        char_token = resp.json()["token"]

        token_client = TestClient(server.api)
        headers = {"Authorization": f"Bearer {char_token}"}

        # Naming the project it is filed under, the UNASSIGNED sentinel, and a
        # project id that does not exist all get the same 403 — so the refusal
        # itself answers nothing.
        for probe in (str(project_id), "UNASSIGNED", "99999999"):
            resp = token_client.get(f"/characters?project_id={probe}", headers=headers)
            assert resp.status_code == 403, (
                f"project_id={probe} must be refused for a character token; got "
                f"{resp.status_code} {resp.text}"
            )

        # In-scope direction: without the filter the token still reads its own
        # character, and learns no project ids from the payload.
        resp = token_client.get("/characters", headers=headers)
        assert resp.status_code == 200, resp.text
        listed = {c["id"]: c for c in resp.json()}
        assert char_id in listed
        assert listed[char_id]["project_ids"] == []
        assert listed[char_id]["project_id"] is None

        # The owner is never restricted by any of it.
        resp = client.get(f"/characters?project_id={project_id}")
        assert resp.status_code == 200, resp.text
        assert char_id in [c["id"] for c in resp.json()]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def _import_one_picture(client):
    import glob

    candidates = glob.glob(
        os.path.join(os.path.dirname(__file__), "..", "pictures", "*.png")
    ) + glob.glob(os.path.join(os.path.dirname(__file__), "..", "pictures", "*.jpg"))
    assert candidates, "No test images found in pictures/ directory"
    path = candidates[0]
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as image_file:
        import_resp = upload_pictures_and_wait(
            client, [("file", (os.path.basename(path), image_file, mime))]
        )
    return import_resp["results"][0]["picture_id"]


def _link_face(server, pic_id, char_id, face_index=0):
    def _add(session):
        session.add(
            Face(
                picture_id=pic_id,
                frame_index=0,
                face_index=face_index,
                character_id=char_id,
                bbox_="0,0,10,10",
            )
        )
        session.commit()

    server.vault.db.run_task(_add)


def _project_picture_ids(client, project_id):
    resp = client.get("/pictures", params={"project_id": str(project_id)})
    assert resp.status_code == 200
    return {row.get("id") for row in resp.json()}


def test_moving_character_to_new_project_disassociates_pictures_from_old():
    """Moving a character out of project A removes the character's pictures from
    A (the project it was dragged out of) and into the new project B."""
    temp_dir, client, server = _setup()
    try:
        project_a = client.post("/projects", json={"name": "Char Move A"}).json()["id"]
        project_b = client.post("/projects", json={"name": "Char Move B"}).json()["id"]

        pic_id = _import_one_picture(client)
        char_id = client.post("/characters", json={"name": "Mover"}).json()[
            "character"
        ]["id"]
        _link_face(server, pic_id, char_id)

        # Assigning the character to A cascades the picture into A.
        assert (
            client.patch(
                f"/characters/{char_id}", json={"project_id": project_a}
            ).status_code
            == 200
        )
        assert pic_id in _project_picture_ids(client, project_a)

        # Moving the character to B should pull the picture out of A.
        assert (
            client.patch(
                f"/characters/{char_id}", json={"project_id": project_b}
            ).status_code
            == 200
        )
        assert pic_id in _project_picture_ids(client, project_b)
        assert pic_id not in _project_picture_ids(client, project_a)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_moving_character_keeps_pictures_shared_with_another_character_in_old_project():
    """A picture also containing a second character still in project A is kept in
    A when the first character is moved out — only orphaned pictures leave."""
    temp_dir, client, server = _setup()
    try:
        project_a = client.post("/projects", json={"name": "Shared Char A"}).json()[
            "id"
        ]
        project_b = client.post("/projects", json={"name": "Shared Char B"}).json()[
            "id"
        ]

        pic_id = _import_one_picture(client)
        char_one = client.post("/characters", json={"name": "First"}).json()[
            "character"
        ]["id"]
        char_two = client.post("/characters", json={"name": "Second"}).json()[
            "character"
        ]["id"]
        _link_face(server, pic_id, char_one, face_index=0)
        _link_face(server, pic_id, char_two, face_index=1)

        for char_id in (char_one, char_two):
            assert (
                client.patch(
                    f"/characters/{char_id}", json={"project_id": project_a}
                ).status_code
                == 200
            )
        assert pic_id in _project_picture_ids(client, project_a)

        # Move only the first character to B.
        assert (
            client.patch(
                f"/characters/{char_one}", json={"project_id": project_b}
            ).status_code
            == 200
        )

        # Added to B, but retained in A because the second character anchors it.
        assert pic_id in _project_picture_ids(client, project_b)
        assert pic_id in _project_picture_ids(client, project_a)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_batch_character_membership_returns_assignments():
    """POST /characters/membership must return a 200 with the picture's character
    assignment. Regression: the handler built character_assignments with integer
    keys while CharacterMembershipResponse declares dict[str, list[int]]; pydantic
    v2 rejected the int keys, the response 500'd, and the AddToCharacter menu
    received no membership data (every character shown unchecked)."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _import_one_picture(client)
        char_id = client.post("/characters", json={"name": "Member"}).json()[
            "character"
        ]["id"]
        _link_face(server, pic_id, char_id)

        resp = client.post("/characters/membership", json={"picture_ids": [pic_id]})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["character_assignments"] == {str(char_id): [pic_id]}
        assert data["pictures_with_faces"] == [pic_id]
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()
