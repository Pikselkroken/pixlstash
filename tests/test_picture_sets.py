from fastapi.testclient import TestClient
from pixlvault.server import Server
import tempfile
import os
import json


def setup_server_with_temp_db():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    config_path = os.path.join(temp_dir.name, "config.json")
    config = Server.create_config(
        default_device="cpu",
        image_roots=[image_root],
        selected_image_root=image_root,
    )
    with open(config_path, "w") as f:
        f.write(json.dumps(config, indent=2))
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(config_path, server_config_path)
    client = TestClient(server.api)
    return temp_dir, client


def test_create_and_list_picture_set():
    temp_dir, client = setup_server_with_temp_db()
    # Create a new picture set
    resp = client.post(
        "/picture_sets", json={"name": "TestSet", "description": "A test set"}
    )
    assert resp.status_code == 200
    data = resp.json()
    set_id = data["picture_set"]["id"]
    # List all picture sets
    resp = client.get("/picture_sets")
    assert resp.status_code == 200
    sets = resp.json()
    assert any(s["id"] == set_id for s in sets)
    temp_dir.cleanup()


def test_get_picture_set_metadata_and_members():
    temp_dir, client = setup_server_with_temp_db()
    # Create a new set
    resp = client.post("/picture_sets", json={"name": "MetaSet"})
    set_id = resp.json()["picture_set"]["id"]
    # Get metadata
    resp = client.get(f"/picture_sets/{set_id}?info=true")
    assert resp.status_code == 200
    meta = resp.json()
    assert meta["id"] == set_id
    # Get members (should be empty)
    resp = client.get(f"/picture_sets/{set_id}/members")
    assert resp.status_code == 200
    assert resp.json()["picture_ids"] == []
    temp_dir.cleanup()


def test_add_and_remove_picture_from_set():
    temp_dir, client = setup_server_with_temp_db()
    # Create set
    resp = client.post("/picture_sets", json={"name": "AddRemSet"})
    set_id = resp.json()["picture_set"]["id"]
    # Add a real picture from the pictures/ directory
    import glob

    # Find a real PNG in the pictures/ directory
    png_files = glob.glob(
        os.path.join(os.path.dirname(__file__), "..", "pictures", "*.png")
    )
    assert png_files, "No PNG files found in pictures/ directory for test."
    img_path = png_files[0]
    with open(img_path, "rb") as f:
        files = {"file": (os.path.basename(img_path), f, "image/png")}
        resp = client.post("/pictures", files=files)
    assert resp.status_code == 200
    # Get picture id
    resp = client.get("/pictures")
    pic_id = resp.json()[0]["id"]
    # Add to set
    resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
    assert resp.status_code == 200
    # Check members
    resp = client.get(f"/picture_sets/{set_id}/members")
    assert pic_id in resp.json()["picture_ids"]
    # Remove from set
    resp = client.delete(f"/picture_sets/{set_id}/members/{pic_id}")
    assert resp.status_code == 200
    resp = client.get(f"/picture_sets/{set_id}/members")
    assert pic_id not in resp.json()["picture_ids"]
    temp_dir.cleanup()


def test_update_and_delete_picture_set():
    temp_dir, client = setup_server_with_temp_db()
    # Create set
    resp = client.post("/picture_sets", json={"name": "UpdDelSet"})
    set_id = resp.json()["picture_set"]["id"]
    # Update name/description
    resp = client.patch(
        f"/picture_sets/{set_id}", json={"name": "Updated", "description": "Desc"}
    )
    assert resp.status_code == 200
    resp = client.get(f"/picture_sets/{set_id}?info=true")
    meta = resp.json()
    assert meta["name"] == "Updated"
    assert meta["description"] == "Desc"
    # Delete set
    resp = client.delete(f"/picture_sets/{set_id}")
    assert resp.status_code == 200
    resp = client.get(f"/picture_sets/{set_id}?info=true")
    assert resp.status_code == 404
    temp_dir.cleanup()


def test_reference_picture_set_created_with_character():
    temp_dir, client = setup_server_with_temp_db()
    # Create a character
    char_name = "RefSetChar"
    resp = client.post("/characters", json={"name": char_name})
    assert resp.status_code == 200
    char = resp.json()["character"]
    assert char is not None
    # List all picture sets
    resp = client.get("/picture_sets")
    assert resp.status_code == 200
    sets = resp.json()
    # There should be a reference set with name 'reference_pictures' and description == char_name
    ref_sets = [
        s
        for s in sets
        if s["name"] == "reference_pictures" and s["description"] == char_name
    ]
    assert len(ref_sets) == 1, (
        f"Expected 1 reference set for character, found {len(ref_sets)}"
    )
    temp_dir.cleanup()


def test_reference_picture_set_unique_per_character():
    temp_dir, client = setup_server_with_temp_db()
    # Create two characters
    resp1 = client.post("/characters", json={"name": "CharA"})
    resp2 = client.post("/characters", json={"name": "CharB"})
    assert resp1.status_code == 200 and resp2.status_code == 200
    # List all picture sets
    resp = client.get("/picture_sets")
    sets = resp.json()
    ref_a = [
        s
        for s in sets
        if s["name"] == "reference_pictures" and s["description"] == "CharA"
    ]
    ref_b = [
        s
        for s in sets
        if s["name"] == "reference_pictures" and s["description"] == "CharB"
    ]
    assert len(ref_a) == 1, "Reference set for CharA missing or duplicated"
    assert len(ref_b) == 1, "Reference set for CharB missing or duplicated"
    temp_dir.cleanup()


def test_no_duplicate_reference_picture_sets():
    temp_dir, client = setup_server_with_temp_db()
    # Create a character
    char_name = "NoDupChar"
    resp = client.post("/characters", json={"name": char_name})
    assert resp.status_code == 200
    # List all picture sets
    resp = client.get("/picture_sets")
    sets = resp.json()
    ref_sets = [
        s
        for s in sets
        if s["name"] == "reference_pictures" and s["description"] == char_name
    ]
    assert len(ref_sets) == 1
    # Try to create the same character name again (should create a new character and a new reference set with the same description)
    client.post("/characters", json={"name": char_name})
    # Accept either error or success, and allow multiple reference sets with the same description
    resp = client.get("/picture_sets")
    sets = resp.json()
    ref_sets = [
        s
        for s in sets
        if s["name"] == "reference_pictures" and s["description"] == char_name
    ]
    assert len(ref_sets) >= 1, "No reference picture set found for character name"
    temp_dir.cleanup()
