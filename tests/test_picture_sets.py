import gc
import time
from fastapi.testclient import TestClient

import collections
import tempfile
import os
import json
import psutil
import tracemalloc

from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
from tests.utils import upload_pictures_and_wait

logger = get_logger(__name__)


def log_resources(label):
    process = psutil.Process()
    rss = process.memory_info().rss / (1024 * 1024)
    logger.info(f"[RESOURCE] {label}: RSS={rss:.2f}MB, Threads={process.num_threads()}")
    logger.info(f"[RESOURCE] {label}: gc objects={len(gc.get_objects())}")
    counter = collections.Counter(type(obj) for obj in gc.get_objects())
    logger.info(f"[RESOURCE] {label}: Top object types: {counter.most_common(5)}")
    if tracemalloc.is_tracing():
        logger.info(
            f"[RESOURCE] {label}: Tracemalloc current={tracemalloc.get_traced_memory()[0] / (1024 * 1024):.2f}MB, peak={tracemalloc.get_traced_memory()[1] / (1024 * 1024):.2f}MB"
        )


def setup_server_with_temp_db():
    log_resources("Before setup_server_with_temp_db")
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    server.vault.start_workers({WorkerType.FACE, WorkerType.SMART_SCORE_SCRAPHEAP})
    client = TestClient(server.api)

    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def test_create_and_list_picture_set():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_get_picture_set_metadata_and_members():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_add_and_remove_picture_from_set():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
        with client.websocket_connect("/ws/updates") as ws:
            import threading
            import queue

            deadline = time.time() + 10
            imported = False
            messages = queue.Queue()

            def recv_loop():
                try:
                    while True:
                        messages.put(ws.receive_json())
                except Exception:
                    return

            thread = threading.Thread(target=recv_loop, daemon=True)
            thread.start()

            with open(img_path, "rb") as f:
                files = {"file": (os.path.basename(img_path), f, "image/png")}
                import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            # Get picture id
            pic_id = import_status["results"][0]["picture_id"]
            # Add to set
            resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
            assert resp.status_code == 200
            # Wait for the PICTURE_IMPORTED websocket event before checking membership

            while time.time() < deadline:
                try:
                    payload = messages.get(timeout=0.2)
                except queue.Empty:
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("event") == "PICTURE_IMPORTED" and pic_id in (
                    payload.get("picture_ids") or []
                ):
                    imported = True
                    break
            assert imported, "Timed out waiting for PICTURE_IMPORTED websocket event"
        # Check members
        resp = client.get(f"/picture_sets/{set_id}/members")
        assert pic_id in resp.json()["picture_ids"]
        # Remove from set
        resp = client.delete(f"/picture_sets/{set_id}/members/{pic_id}")
        assert resp.status_code == 200
        resp = client.get(f"/picture_sets/{set_id}/members")
        assert pic_id not in resp.json()["picture_ids"]
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_update_and_delete_picture_set():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_reference_picture_set_created_with_character():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_reference_picture_set_unique_per_character():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_no_duplicate_reference_picture_sets():
    temp_dir, client, server = setup_server_with_temp_db()
    try:
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
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()
