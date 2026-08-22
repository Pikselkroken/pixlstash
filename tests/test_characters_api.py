"""Tests for the Characters API: create, update, delete, and reference pictures."""

import gc
import glob
import hashlib
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.db_models import Face, Picture
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


def _import_two_pictures(client):
    """Import two DISTINCT images and return their picture ids."""
    candidates = sorted(
        glob.glob(os.path.join(os.path.dirname(__file__), "..", "pictures", "*.png"))
        + glob.glob(os.path.join(os.path.dirname(__file__), "..", "pictures", "*.jpg"))
    )
    assert len(candidates) >= 2, "Need two test images in pictures/"
    ids = []
    for path in candidates[:2]:
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        with open(path, "rb") as image_file:
            import_resp = upload_pictures_and_wait(
                client, [("file", (os.path.basename(path), image_file, mime))]
            )
        ids.append(import_resp["results"][0]["picture_id"])
    return ids


def _link_face_with_bbox(server, pic_id, char_id):
    """Assign a face with a REAL bbox, which the thumbnail crop needs.

    ``_link_face`` above writes ``"0,0,10,10"``, which ``Face.bbox`` cannot
    parse (it is JSON) — fine for the assignment tests that use it, useless for
    a render.

    ``face_index`` sits above anything detection produces: import runs the face
    detector, and index 0 is whichever face it found, so writing there races the
    import for the (picture, frame, index) unique constraint.
    """

    def _add(session):
        session.add(
            Face(
                picture_id=pic_id,
                frame_index=0,
                face_index=99,
                character_id=char_id,
                bbox=[0, 0, 32, 32],
            )
        )
        session.commit()

    server.vault.db.run_task(_add)


def _thumbnail_bytes(client, char_id):
    """The rendered thumbnail itself.

    The BYTES, not the render's cache metadata: that metadata records the
    selection query's winner, which is not always the picture the route ends up
    cropping, so asserting on it passes with the whole pin implementation
    deleted. The image is the only thing the user sees and the only thing worth
    asserting on.
    """
    resp = client.get(f"/characters/{char_id}/thumbnail")
    assert resp.status_code == 200, resp.text
    return hashlib.sha256(resp.content).hexdigest()


def test_pinned_thumbnail_picture_overrides_the_automatic_choice():
    """A pinned reference picture decides the crop, and clearing it restores the
    automatic pick. Both directions, because a pin that cannot be undone is its
    own defect."""
    temp_dir, client, server = _setup()
    try:
        first_pic, second_pic = _import_two_pictures(client)
        char_id = client.post("/characters", json={"name": "Pinned"}).json()[
            "character"
        ]["id"]
        _link_face_with_bbox(server, first_pic, char_id)
        _link_face_with_bbox(server, second_pic, char_id)

        # Score them apart rather than relying on the id tie-break: a scoring
        # sweep landing mid-test would otherwise decide which picture the
        # automatic path picks, and the test would flake instead of failing.
        automatic, pinned = max(first_pic, second_pic), min(first_pic, second_pic)
        resp = client.post(
            "/pictures/apply-scores",
            json={"scores": {str(automatic): 5, str(pinned): 1}},
        )
        assert resp.status_code == 200
        automatic_thumbnail = _thumbnail_bytes(client, char_id)

        # Pin each of the two in turn and compare the RENDERED bytes. The two
        # crops come from different source images, so equal bytes mean the pin
        # did not decide the render.
        #
        # This order is deliberate: `automatic` is the picture the selection
        # query names (and therefore what the cache metadata already held), so
        # pinning it first is the case where a cache keyed on that id alone
        # would hit and keep serving the previous crop. Under that bug both
        # pins render the same picture and the assertion below fails.
        assert (
            client.patch(
                f"/characters/{char_id}", json={"thumbnail_picture_id": automatic}
            ).json()["character"]["thumbnail_picture_id"]
            == automatic
        )
        pinned_to_automatic = _thumbnail_bytes(client, char_id)

        resp = client.patch(
            f"/characters/{char_id}", json={"thumbnail_picture_id": pinned}
        )
        assert resp.status_code == 200
        assert resp.json()["character"]["thumbnail_picture_id"] == pinned
        pinned_thumbnail = _thumbnail_bytes(client, char_id)
        assert pinned_thumbnail != pinned_to_automatic

        # And the pin is stable: going back to the first one renders the first
        # one again rather than whatever was cached last.
        assert (
            client.patch(
                f"/characters/{char_id}", json={"thumbnail_picture_id": automatic}
            ).status_code
            == 200
        )
        assert _thumbnail_bytes(client, char_id) == pinned_to_automatic

        assert (
            client.patch(
                f"/characters/{char_id}", json={"thumbnail_picture_id": pinned}
            ).status_code
            == 200
        )

        # The list endpoint carries it too — that is where the editor reads the
        # current pin from when it opens.
        listed = [c for c in client.get("/characters").json() if c["id"] == char_id]
        assert listed[0]["thumbnail_picture_id"] == pinned

        # null clears it, and an untouched PATCH leaves it alone.
        assert (
            client.patch(f"/characters/{char_id}", json={"name": "Pinned"}).status_code
            == 200
        )
        assert client.get(f"/characters/{char_id}").json()["thumbnail_picture_id"] == (
            pinned
        )
        resp = client.patch(
            f"/characters/{char_id}", json={"thumbnail_picture_id": None}
        )
        assert resp.status_code == 200
        assert _thumbnail_bytes(client, char_id) == automatic_thumbnail
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pinning_a_thumbnail_does_not_invalidate_derived_picture_fields():
    """The pin is not identity data.

    Renaming a person nulls the description and text embedding of every picture
    they appear in, because their name is baked into both. Choosing which
    existing crop to show is not that, and letting it share the flag deleted
    hand-written descriptions and queued a library-wide re-derive on one click.
    """
    temp_dir, client, server = _setup()
    try:
        pic_id = _import_one_picture(client)
        char_id = client.post("/characters", json={"name": "Keeper"}).json()[
            "character"
        ]["id"]
        _link_face_with_bbox(server, pic_id, char_id)

        def _seed(session):
            picture = session.get(Picture, pic_id)
            picture.description = "a description somebody typed"
            picture.text_embedding = b"embedding-bytes"
            session.add(picture)
            session.commit()

        server.vault.db.run_task(_seed)

        assert (
            client.patch(
                f"/characters/{char_id}", json={"thumbnail_picture_id": pic_id}
            ).status_code
            == 200
        )

        def _read(session):
            picture = session.get(Picture, pic_id)
            return picture.description, picture.text_embedding is not None

        description, has_embedding = server.vault.db.run_immediate_read_task(_read)
        assert description == "a description somebody typed"
        assert has_embedding is True

        # The control: a RENAME still throws both away, so the assertion above
        # is about the pin and not about a wipe that stopped working.
        assert (
            client.patch(f"/characters/{char_id}", json={"name": "Renamed"}).status_code
            == 200
        )
        description, has_embedding = server.vault.db.run_immediate_read_task(_read)
        assert description is None
        assert has_embedding is False
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_pinning_a_picture_without_this_persons_face_is_rejected():
    temp_dir, client, server = _setup()
    try:
        first_pic, second_pic = _import_two_pictures(client)
        char_id = client.post("/characters", json={"name": "NoFace"}).json()[
            "character"
        ]["id"]
        _link_face_with_bbox(server, first_pic, char_id)

        resp = client.patch(
            f"/characters/{char_id}", json={"thumbnail_picture_id": second_pic}
        )
        assert resp.status_code == 400
        resp = client.patch(
            f"/characters/{char_id}", json={"thumbnail_picture_id": "not-an-id"}
        )
        assert resp.status_code == 400

        # A scrapheaped picture keeps its faces, so it passes a face-only check
        # — and the renderer would still skip it, leaving a pin nothing can
        # honour. Refused for the same reason and with the same status.
        assert client.delete(f"/pictures/{first_pic}").status_code == 200
        resp = client.patch(
            f"/characters/{char_id}", json={"thumbnail_picture_id": first_pic}
        )
        assert resp.status_code == 400
        # And nothing was written: the person still has no pin.
        assert (
            client.get(f"/characters/{char_id}").json()["thumbnail_picture_id"] is None
        )
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()
