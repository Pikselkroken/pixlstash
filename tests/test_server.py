import time
import os
import tempfile
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from pixelurgy_vault.server import Server
import random


def test_post_logo_altered_pixel_upload():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)
        logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
        img = Image.open(logo_path).convert("RGBA")
        arr = np.array(img)
        arr[0, 0] = [255, 0, 0, 255]  # Red pixel
        altered_img = Image.fromarray(arr)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            altered_img.save(tmp.name)
            tmp_path = tmp.name
        with open(tmp_path, "rb") as f:
            files = {"image": ("altered_logo.png", f, "image/png")}
            data = {
                "character_id": "test",
                "description": "altered pixel",
                "tags": "[]",
            }
            r = client.post("/pictures", files=files, data=data)
        os.remove(tmp_path)
        assert r.status_code == 200
        resp = r.json()
        assert resp.get("status") == "success"
        assert resp.get("id")
        assert resp.get("file_path")


def test_benchmark_add_images_by_path():
    TEST_SIZE = 50
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server()
        server.vault.db_path = vault_path
        server.vault.image_root = image_root
        client = TestClient(server.app)
        image_paths = []
        total_bytes = 0
        for i in range(TEST_SIZE):
            arr = np.random.randint(0, 256, (1024, 1024, 3), dtype=np.uint8)
            img = Image.fromarray(arr)
            img_path = os.path.join(temp_dir, f"image_{i:04d}.png")
            img.save(img_path)
            image_paths.append(img_path)
            total_bytes += os.path.getsize(img_path)
        start = time.time()
        for i, img_path in enumerate(image_paths):
            json_data = {
                "file_path": img_path,
                "character_id": "bench",
                "title": f"benchmark image {i}",
                "description": f"benchmark image {i}",
                "tags": [],
            }
            r = client.post("/pictures", data=json_data)
            assert r.status_code == 200
            resp = r.json()
            assert resp.get("status") == "success"
            assert resp.get("id")
            assert resp.get("file_path")
        end = time.time()
        print(
            f"Path Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
        )

        # Read back and check a few images
        random_indices = random.sample(range(TEST_SIZE), 3)
        for check_idx in random_indices:
            title = f"benchmark image {check_idx}"
            # Query metadata to get id
            resp = client.get(f"/pictures?description={title}")
            assert resp.status_code == 200
            results = resp.json()
            assert len(results) > 0
            pic_id = results[0]["id"]
            # Fetch image by id
            img_resp = client.get(f"/pictures/{pic_id}")
            assert img_resp.status_code == 200
            # Compare first 1024 bytes for speed
            with open(image_paths[check_idx], "rb") as f:
                image = f.read()
            assert img_resp.content[:1024] == image[:1024]


def test_benchmark_add_images_by_binary_upload():
    TEST_SIZE = 50
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server()
        server.vault.db_path = vault_path
        server.vault.image_root = image_root
        client = TestClient(server.app)

        from io import BytesIO

        images = []
        total_bytes = 0
        for i in range(TEST_SIZE):
            arr = np.random.randint(0, 256, (1024, 1024, 3), dtype=np.uint8)
            img = Image.fromarray(arr)
            buf = BytesIO()
            img.save(buf, format="PNG")
            img_bytes = buf.getvalue()
            images.append(img_bytes)
            total_bytes += len(img_bytes)
        start = time.time()
        for i, img_bytes in enumerate(images):
            files = {"image": (f"image_{i:04d}.png", img_bytes, "image/png")}
            data = {
                "character_id": "bench",
                "description": f"benchmark image {i}",
                "tags": "[]",
            }
            r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 200
            resp = r.json()
            assert resp.get("status") == "success"
            assert resp.get("id")
            assert resp.get("file_path")
        end = time.time()
        print(
            f"Path Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
        )

        # Read back and check a few images
        random_indices = random.sample(range(TEST_SIZE), 3)
        for check_idx in random_indices:
            title = f"benchmark image {check_idx}"
            # Query metadata to get id
            resp = client.get(f"/pictures?description={title}")
            assert resp.status_code == 200
            results = resp.json()
            assert len(results) > 0
            pic_id = results[0]["id"]
            # Fetch image by id
            img_resp = client.get(f"/pictures/{pic_id}")
            assert img_resp.status_code == 200
            # Compare first 1024 bytes for speed
            assert img_resp.content[:1024] == images[check_idx][:1024]


def test_post_logo_altered_pixel_path():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)
        logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
        img = Image.open(logo_path).convert("RGBA")
        arr = np.array(img)
        arr[0, 1] = [0, 255, 0, 255]  # Green pixel
        altered_img = Image.fromarray(arr)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            altered_img.save(tmp.name)
            tmp_path = tmp.name
        json_data = {
            "file_path": tmp_path,
            "character_id": "test",
            "description": "altered pixel path",
            "tags": [],
        }
        r = client.post("/pictures", data=json_data)
        os.remove(tmp_path)
        assert r.status_code == 200
        resp = r.json()
        assert resp.get("status") == "success"
        assert resp.get("id")
        assert resp.get("file_path")


def test_read_root():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {
            "message": "Pixelurgy Vault REST API",
            "version": "0.1.0",
        }
