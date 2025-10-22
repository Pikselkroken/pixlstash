import numpy as np
import os
import pytest
import random
import tempfile
import time
import tomllib

import gc

from PIL import Image
from fastapi.testclient import TestClient
from pixelurgy_vault.server import Server
from io import BytesIO

TEST_SIZE = 10 if os.getenv("GITHUB_ACTIONS") == "true" else 50
random_images = []
total_bytes = 0
for i in range(TEST_SIZE):
    arr = np.random.randint(0, 256, (1024, 1024, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()
    random_images.append(img_bytes)
    total_bytes += len(img_bytes)


def get_project_version():
    pyproject_path = os.path.join(os.path.dirname(__file__), "../pyproject.toml")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def test_esmeralda_vault_character_and_logo():
    """Test that EsmeraldaVault exists and her picture matches Logo.png exactly."""

    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        # This triggers _import_default_data
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        # Find EsmeraldaVault character (by name)
        resp = client.get("/characters")
        assert resp.status_code == 200
        chars = resp.json()
        print("DEBUG: /characters returned:", chars)
        esmeralda = None
        for c in chars:
            if c.get("name") == "EsmeraldaVault":
                esmeralda = c
                break
        assert esmeralda is not None, "EsmeraldaVault character not found"
        char_id = esmeralda["id"]

        # Find picture for EsmeraldaVault
        resp2 = client.get(f"/pictures?character_id={char_id}&info=true")
        assert resp2.status_code == 200
        pics = resp2.json()
        assert pics, "No picture found for EsmeraldaVault"
        pic_id = pics[0]["id"] if isinstance(pics[0], dict) else pics[0]["ids"][0]

        # Fetch the master iteration image
        img_resp = client.get(f"/pictures/{pic_id}")
        assert img_resp.status_code == 200
        logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
        with open(logo_path, "rb") as f:
            logo_bytes = f.read()
        # Compare the full file
        assert img_resp.content == logo_bytes, (
            "EsmeraldaVault's picture does not match Logo.png"
        )
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


def test_create_and_get_default_character():
    """Test creating and fetching the default character 'Esmeralda'."""
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        # Create Esmeralda
        char_id = "esmeralda"
        char_name = "Esmeralda"
        char_desc = "Default vault character"
        resp = client.post(
            "/characters",
            json={"id": char_id, "name": char_name, "description": char_desc},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["character"]["id"] == char_id
        assert data["character"]["name"] == char_name
        assert data["character"]["description"] == char_desc

        # Fetch Esmeralda by id
        resp2 = client.get(f"/characters/{char_id}")
        assert resp2.status_code == 200
        char = resp2.json()
        assert char["id"] == char_id
        assert char["name"] == char_name
        assert char["description"] == char_desc
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


def test_upload_iteration_to_existing_picture():
    """Test uploading additional iterations to an existing picture."""

    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        # Create a new picture with a master iteration
        img_bytes = random_images[0]
        files = {"image": ("master.png", img_bytes, "image/png")}
        data = {
            "character_id": "testchar",
            "description": "original master",
            "tags": "[]",
        }
        r = client.post("/pictures", files=files, data=data)
        assert r.status_code == 200
        resp = r.json()
        assert resp["results"][0]["status"] == "success"
        picture_id = resp["results"][0]["picture_id"]

        # Upload a new iteration to the same picture
        img_bytes2 = random_images[1]
        files2 = {"file": ("iteration2.png", img_bytes2, "image/png")}
        data2 = {"picture_id": picture_id}
        r2 = client.post("/iterations/", files=files2, data=data2)
        assert r2.status_code == 200
        resp2 = r2.json()
        assert resp2["status"] == "success"
        iteration_id = resp2["iteration_id"]

        # Fetch the new iteration and check association
        r3 = client.get(f"/iterations/{iteration_id}")
        assert r3.status_code == 200
        it = r3.json()
        assert it["picture_id"] == picture_id
        assert it["id"] == iteration_id

        # Upload a third iteration with transform metadata
        img_bytes3 = random_images[2]
        files3 = {"file": ("iteration3.png", img_bytes3, "image/png")}
        data3 = {"picture_id": picture_id, "transform_metadata": '{"filter":"blur"}'}
        r4 = client.post("/iterations/", files=files3, data=data3)
        assert r4.status_code == 200
        resp4 = r4.json()
        assert resp4["status"] == "success"
        iteration_id3 = resp4["iteration_id"]
        r5 = client.get(f"/iterations/{iteration_id3}")
        assert r5.status_code == 200
        it3 = r5.json()
        assert it3["picture_id"] == picture_id
        assert it3["transform_metadata"] == '{"filter":"blur"}'
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


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
        assert "results" in resp
        assert resp["results"][0]["status"] == "success"
        assert resp["results"][0]["picture_id"]
        assert resp["results"][0]["iteration_id"]
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


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
        data = {
            "file_path": tmp_path,
            "character_id": "test",
            "description": "altered pixel path",
            "tags": "[]",
        }
        r = client.post("/pictures", data=data)
        os.remove(tmp_path)
        assert r.status_code == 200
        resp = r.json()
        assert "results" in resp
        assert resp["results"][0]["status"] == "success"
        assert resp["results"][0]["picture_id"]
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


def test_read_root():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)
        response = client.get("/")
        assert response.status_code == 200
        expected_version = get_project_version()
        assert response.json() == {
            "message": "Pixelurgy Vault REST API",
            "version": expected_version,
        }
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


def test_benchmark_add_images_by_binary_upload():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        start = time.time()
        ids = []
        for i, img_bytes in enumerate(random_images):
            files = {"image": (f"image_{i:04d}.png", img_bytes, "image/png")}
            data = {
                "character_id": "bench",
                "description": f"benchmark image {i}",
                "tags": "[]",
            }
            r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 200
            resp = r.json()
            assert "results" in resp
            assert resp["results"][0]["status"] == "success"
            ids.append(resp["results"][0]["picture_id"])
        end = time.time()
        print(
            f"Upload Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
        )

        # Read back and check a few images
        random_indices = random.sample(range(TEST_SIZE), 3)
        for check_idx in random_indices:
            pic_id = ids[check_idx]
            img_resp = client.get(f"/pictures/{pic_id}")
            assert img_resp.status_code == 200
            assert img_resp.content[:1024] == random_images[check_idx][:1024]
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()

def test_benchmark_add_images_by_path():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)
        image_paths = []
        total_bytes = 0
        for i, img in enumerate(random_images):
            img_path = os.path.join(temp_dir, f"image_{i:04d}.png")
            with open(img_path, "wb") as f:
                f.write(img)
            image_paths.append(img_path)
            total_bytes += os.path.getsize(img_path)
        start = time.time()

        ids = []
        for i, img_path in enumerate(image_paths):
            data = {
                "file_path": img_path,
                "character_id": "bench",
                "description": f"benchmark image {i}",
                "tags": "[]",
            }
            r = client.post("/pictures", data=data)
            assert r.status_code == 200
            resp = r.json()
            assert "results" in resp
            assert resp["results"][0]["status"] == "success"
            ids.append(resp["results"][0]["picture_id"])
        end = time.time()
        print(
            f"Single Image Path Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
        )

        # Read back and check a few images
        random_indices = random.sample(range(TEST_SIZE), 3)
        for check_idx in random_indices:
            pic_id = ids[check_idx]
            img_resp = client.get(f"/pictures/{pic_id}")
            assert img_resp.status_code == 200
            with open(image_paths[check_idx], "rb") as f:
                image = f.read()
            assert img_resp.content[:1024] == image[:1024]
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


def test_benchmark_add_images_by_directory():
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)
        image_path = os.path.join(temp_dir, "image_dir")
        os.makedirs(image_path, exist_ok=True)
        total_bytes = 0
        for i, img in enumerate(random_images):
            img_path = os.path.join(image_path, f"image_{i:04d}.png")
            with open(img_path, "wb") as f:
                f.write(img)
            total_bytes += os.path.getsize(img_path)
        start = time.time()
        data = {
            "file_path": image_path,
            "character_id": "bench",
            "description": "benchmark images from directory",
            "tags": "[]",
        }
        r = client.post("/pictures", data=data)
        assert r.status_code == 200
        resp = r.json()
        assert "results" in resp
        file_to_picid = {}
        for result in resp["results"]:
            assert result["status"] == "success"
            assert result["picture_id"]
            # Extract file name from result["file"] if present, else assign sequentially
            file_name = os.path.basename(result.get("file", ""))
            file_to_picid[file_name] = result["picture_id"]
        end = time.time()
        print(
            f"Path Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
        )

        # Read back and check a few images
        random_indices = random.sample(range(TEST_SIZE), 3)
        for check_idx in random_indices:
            file_name = f"image_{check_idx:04d}.png"
            pic_id = file_to_picid[file_name]
            img_resp = client.get(f"/pictures/{pic_id}")
            assert img_resp.status_code == 200
            with open(os.path.join(image_path, file_name), "rb") as f:
                image = f.read()
            assert img_resp.content[:1024] == image[:1024]
        server.vault.close()  # Ensure cleanup before temp_dir is deleted
    gc.collect()


def test_reference_picture_workflow():
    """Test adding and retrieving reference images for a character."""
    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        # Create a character
        char_id = "testchar-123"
        resp = client.post(
            "/characters",
            json={
                "id": char_id,
                "name": "Test Character",
                "description": "For reference image test",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["character"]["id"] == char_id

        # Create a dummy image
        img = Image.new("RGB", (32, 32), color=(123, 222, 111))
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        img_bytes = buf.read()

        # Add reference picture
        resp2 = client.post(
            "/characters/reference_pictures",
            data={
                "character_id": char_id,
                "description": "Reference image test",
                "tags": '["ref", "test"]',
            },
            files={"image": ("ref.png", img_bytes, "image/png")},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert "picture_id" in data
        assert "iteration_id" in data
        assert data["description"] == "Reference image test"
        assert data["tags"] == ["ref", "test"]

        # Retrieve reference pictures
        resp3 = client.get(f"/characters/reference_pictures/{char_id}")
        assert resp3.status_code == 200
        ref_data = resp3.json()["reference_pictures"]
        assert len(ref_data) == 1
        ref_pic = ref_data[0]
        assert ref_pic["picture_id"] == data["picture_id"]
        assert ref_pic["iteration_id"] == data["iteration_id"]
        assert ref_pic["description"] == "Reference image test"
        assert ref_pic["tags"] == ["ref", "test"]
        server.vault.close()
    gc.collect()


def test_tagger_worker_adds_tags():
    """Test that uploading TaggerTest.png results in tags being added by the tag worker."""
    import shutil

    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        # Copy TaggerTest.png into temp dir
        src_img = os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png")
        dest_img = os.path.join(image_root, "TaggerTest.png")
        shutil.copyfile(src_img, dest_img)
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        # Upload TaggerTest.png as a new picture
        with open(dest_img, "rb") as f:
            files = {"image": ("TaggerTest.png", f.read(), "image/png")}
            data = {
                "character_id": "testchar",
                "description": "tagger test",
                "tags": "[]",
            }
            r = client.post("/pictures", files=files, data=data)
        assert r.status_code == 200
        resp = r.json()
        assert resp["results"][0]["status"] == "success"
        picture_id = resp["results"][0]["picture_id"]

        # Wait for tag worker to process
        found_tags = None
        for _ in range(20):
            time.sleep(0.5)
            get_resp = client.get(f"/pictures/{picture_id}?info=true")
            assert get_resp.status_code == 200
            pic_info = get_resp.json()
            found_tags = pic_info.get("tags", [])
            if found_tags:
                break
        assert found_tags, (
            "Tagger worker did not add tags to TaggerTest.png after waiting."
        )
        print(f"Tags for TaggerTest.png: {found_tags}")
        server.vault.close()
    gc.collect()


def test_semantic_search_on_all_pictures():
    """Test: Add all images from pictures folder, wait for tagging, perform semantic search, print results, assert count."""
    import shutil

    with tempfile.TemporaryDirectory() as temp_dir:
        vault_path = os.path.join(temp_dir, "vault.db")
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        # Copy all images from pictures folder
        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        for fname in image_files:
            shutil.copyfile(
                os.path.join(src_dir, fname), os.path.join(image_root, fname)
            )
        server = Server(vault_db_path=vault_path, image_root=image_root)
        client = TestClient(server.app)

        # Upload all images as new pictures
        picture_ids = []
        for fname in image_files:
            with open(os.path.join(image_root, fname), "rb") as f:
                files = {"image": (fname, f.read(), "image/png")}
                data = {"character_id": "Esmeralda", "description": fname, "tags": "[]"}
                r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 200
            resp = r.json()
            assert resp["results"][0]["status"] == "success"
            picture_ids.append(resp["results"][0]["picture_id"])

        # Wait for all pictures to be tagged (embeddings generated)
        for pid in picture_ids:
            for _ in range(30):
                time.sleep(0.5)
                get_resp = client.get(f"/pictures/{pid}?info=true")
                assert get_resp.status_code == 200
                pic_info = get_resp.json()
                # Embedding is present if semantic search will work
                if pic_info.get("has_embedding"):
                    break
            else:
                assert False, f"Picture {pid} did not get embedding after waiting."

        # Perform semantic search
        search_texts = [
            "It was a bright rainy day but Esmeralda needed to get out and get some fresh air, so she dressed for the weather, brought an umbrella and walked out into the countryside.",
            "Esmeralda smiles as she sits across me in the cafe wearing her grey sweater. The sunlight filters through the window of the empty cafe",
            "It was a bright winter morning, and Esmeralda decided to go for a walk in the snow-covered park, admiring the glistening trees and the crisp air. She was glad to have her scarf and her warm coat to keep her cozy.",
            "Esmeralda spent hours in her garden wearing overalls tending to her grass and bushes. It made her smile.",
            "Do I look like a man? Esmeralda asked, raising an eyebrow as she posed with her business suit.",
            "She sat down on the park bench and considered her predicament. A quiet sadness came over her.",
        ]

        for search_text in search_texts:
            search_resp = client.post(
                "/pictures/search", json={"query": search_text, "threshold": 0.45}
            )
            assert search_resp.status_code == 200
            results = search_resp.json()
            print("Semantic search results:")
            for pic in results:
                print(pic)
            assert 1 <= len(results), (
                f"Expected at least one results, got {len(results)} for the text '{search_text}'"
            )
        server.vault.close()
    gc.collect()
