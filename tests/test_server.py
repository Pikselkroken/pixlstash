import numpy as np
import logging
import shutil
import os
import random
import tempfile
import time
import tomllib

import gc

from PIL import Image
from fastapi.testclient import TestClient
from pixelurgy_vault.server import Server
from io import BytesIO
from urllib.parse import quote

logging.basicConfig(level=logging.INFO)

# Monkey-patch os.remove and shutil.rmtree to log deletions

LOG_OS_REMOVES = False  # Set to True to enable logging of file deletions

if LOG_OS_REMOVES:
    original_remove = os.remove

    def logged_remove(path, *args, **kwargs):
        logging.error(f"File deleted: {path}")
        return original_remove(path, *args, **kwargs)

    os.remove = logged_remove

    original_rmtree = shutil.rmtree

    def logged_rmtree(path, *args, **kwargs):
        logging.error(f"Directory deleted: {path}")
        return original_rmtree(path, *args, **kwargs)

    shutil.rmtree = logged_rmtree

TEST_SIZE = 8 if os.getenv("GITHUB_ACTIONS") == "true" else 50
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
    """Test that Esmeralda Vault exists and her picture matches Logo.png exactly."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # This triggers _import_default_data
        with Server(config_path, server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            # Find Esmeralda Vault character (by name)
            resp = client.get("/characters")
            assert resp.status_code == 200
            chars = resp.json()
            esmeralda = None
            for c in chars:
                if c.get("name") == "Esmeralda Vault":
                    esmeralda = c
                    break
            assert esmeralda is not None, "Esmeralda Vault character not found"
            char_id = esmeralda["id"]

            # Find picture for Esmeralda Vault
            resp2 = client.get(f"/pictures?character_id={char_id}&info=true")
            assert resp2.status_code == 200
            pics = resp2.json()
            assert pics, "No picture found for Esmeralda Vault"
            pic_id = pics[0]["id"] if isinstance(pics[0], dict) else pics[0]["ids"][0]

            # Fetch the  picture form id
            img_resp = client.get(f"/pictures/{pic_id}")
            assert img_resp.status_code == 200
            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                logo_bytes = f.read()
            # Compare the full file
            assert (
                img_resp.content == logo_bytes
            ), "Esmeralda Vault's picture does not match Logo.png"
    gc.collect()


def test_create_and_get_default_character():
    """Test creating and fetching the default character 'Esmeralda'."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)

            # Create Esmeralda
            char_name = "Esmeralda"
            char_desc = "Default vault character"
            resp = client.post(
                "/characters",
                json={"name": char_name, "description": char_desc},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            char_id = data["character"]["id"]
            assert data["character"]["name"] == char_name
            assert data["character"]["description"] == char_desc

            # Fetch Esmeralda by id
            resp2 = client.get(f"/characters/{char_id}")
            assert resp2.status_code == 200
            char = resp2.json()
            assert char["id"] == char_id
            assert char["name"] == char_name
            assert char["description"] == char_desc
    gc.collect()


def test_upload_existing_picture():
    """Test uploading an existing picture."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)

            # Create a new picture
            img_bytes = random_images[0]
            images = [("file", ("master.png", img_bytes, "image/png"))]
            r = client.post(
                "/pictures", files=images, data={"character_id": "testchar"}
            )

            assert 200 == r.status_code, "Error: " + r.text
            resp = r.json()
            assert resp["results"][0]["status"] == "success"
            picture_id_1 = resp["results"][0]["picture_id"]

            # Fetch the picture and check it
            fetch_r1 = client.get(f"/pictures/{picture_id_1}?info=true")
            assert 200 == fetch_r1.status_code, "Error: " + fetch_r1.text
            fetched_picture = fetch_r1.json()
            assert fetched_picture["id"] == picture_id_1

            # Upload a new file
            img_bytes2 = random_images[1]
            files2 = [("file", ("iteration2.png", img_bytes2, "image/png"))]
            r2 = client.post(
                "/pictures", files=files2, data={"character_id": "testchar"}
            )
            assert 200 == r2.status_code, "Error: " + r2.text
            resp2 = r2.json()
            assert resp2["results"][0]["status"] == "success"
            picture_id_2 = resp2["results"][0]["picture_id"]

            # Fetch the new picture and check association
            fetch_r2 = client.get(f"/pictures/{picture_id_2}?info=true")
            assert 200 == fetch_r2.status_code, "Error: " + fetch_r2.text
            fetched_picture_2 = fetch_r2.json()
            assert fetched_picture_2["id"] == picture_id_2

            # Upload the first picture again. Should get a 400
            files3 = [("file", ("random_name.png", img_bytes, "image/png"))]
            r3 = client.post(
                "/pictures", files=files3, data={"character_id": "testchar"}
            )
            assert 400 == r3.status_code

            image_bytes3 = random_images[2]
            # Upload two pictures at once, one existing and one new
            files4 = [
                files2[0],
                ("file", ("random_name2.png", image_bytes3, "image/png")),
            ]
            r4 = client.post(
                "/pictures", files=files4, data={"character_id": "testchar"}
            )
            assert 200 == r4.status_code, "Error: " + r4.text
            for i, result in enumerate(r4.json()["results"]):
                if i == 0:
                    assert result["status"] == "duplicate"  # Existing picture
                else:
                    assert result["status"] == "success"  # New picture

    gc.collect()


def test_post_logo_identical_upload():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)
            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                img_bytes = f.read()
                files = [("file", ("identical_logo.png", img_bytes, "image/png"))]
                data = {
                    "character_id": "test",
                }
            r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 400
    gc.collect()


def test_post_logo_altered_pixel_upload():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)
            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            img = Image.open(logo_path).convert("RGBA")
            arr = np.array(img)
            arr[0, 0] = [255, 0, 0, 255]  # Red pixel
            altered_img = Image.fromarray(arr)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                altered_img.save(tmp.name)
                tmp_path = tmp.name
            img_bytes = None
            with open(tmp_path, "rb") as f:
                img_bytes = f.read()
            files = [("file", ("altered_logo.png", img_bytes, "image/png"))]
            data = {
                "character_id": "test",
            }
            r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 200
            resp = r.json()
            assert "results" in resp
            assert resp["results"][0]["status"] == "success"
            assert resp["results"][0]["picture_id"]
            os.remove(tmp_path)
    gc.collect()


def test_read_root():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)
            response = client.get("/")
            assert response.status_code == 200
            expected_version = get_project_version()
            assert response.json() == {
                "message": "Pixelurgy Vault REST API",
                "version": expected_version,
            }
    gc.collect()


def test_benchmark_add_images_by_binary_upload():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)

            start = time.time()
            ids = []
            files = []
            for i, img_bytes in enumerate(random_images):
                file = ("file", (f"image_{i:04d}.png", img_bytes, "image/png"))
                files.append(file)

            data = {
                "character_id": "bench",
            }
            r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 200
            end = time.time()

            resp = r.json()
            assert "results" in resp
            assert len(resp["results"]) == TEST_SIZE
            for result in resp["results"]:
                assert result["status"] == "success"
                ids.append(result["picture_id"])

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
    gc.collect()


def test_reference_picture_workflow():
    """Test adding and retrieving reference images for a character."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)

            # Create a character
            resp = client.post(
                "/characters",
                json={
                    "name": "Test Character",
                    "description": "For reference image test",
                },
            )
            assert resp.status_code == 200
            char_id = resp.json()["character"]["id"]

            # Create a dummy image
            img = Image.new("RGB", (32, 32), color=(123, 222, 111))
            buf = BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_bytes = buf.read()

            # Post image
            resp2 = client.post(
                "/pictures",
                data={
                    "character_id": char_id,
                },
                files=[("file", ("ref.png", img_bytes, "image/png"))],
            )
            assert resp2.status_code == 200
            data = resp2.json()

            upload_result = data["results"][0]
            assert "picture_id" in upload_result

            # Update the picture to be a reference picture
            pic_id = upload_result["picture_id"]
            resp3 = client.patch(f"/pictures/{pic_id}", params={"is_reference": "true"})
            assert resp3.status_code == 200
            assert resp3.json()["status"] == "success"

            # Retrieve reference pictures
            resp4 = client.get(f"/characters/reference_pictures/{char_id}")
            assert resp4.status_code == 200
            ref_data = resp4.json()["reference_pictures"]
            assert len(ref_data) == 1
            ref_pic = ref_data[0]
            assert ref_pic["id"] == upload_result["picture_id"]
    gc.collect()


def test_tagger_worker_adds_tags():
    """Test that uploading TaggerTest.png results in tags being added by the tag worker."""
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # Copy TaggerTest.png into temp dir
        src_img = os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png")
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            client = TestClient(server.api)

            # Upload TaggerTest.png as a new picture
            with open(src_img, "rb") as f:
                files = [("file", ("TaggerTest.png", f.read(), "image/png"))]
                data = {
                    "character_id": "testchar",
                }
                r = client.post("/pictures", files=files, data=data)
            assert r.status_code == 200
            resp = r.json()
            assert resp["results"][0]["status"] == "success"
            picture_id = resp["results"][0]["picture_id"]

            # Wait for tag worker to process
            found_tags = None
            for _ in range(60):
                time.sleep(0.5)
                get_resp = client.get(f"/pictures/{picture_id}?info=true")
                assert get_resp.status_code == 200
                pic_info = get_resp.json()
                found_tags = pic_info.get("tags", [])
                if found_tags:
                    break
            assert (
                found_tags
            ), "Tagger worker did not add tags to TaggerTest.png after waiting."
    gc.collect()


def test_semantic_search_on_all_pictures():
    """Test: Add all images from pictures folder, wait for tagging, perform semantic search, print results, assert count."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]
        with Server(
            config_path=config_path, server_config_path=server_config_path
        ) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            # Get Esmeralda's character ID
            resp = client.get("/characters")
            assert resp.status_code == 200
            chars = resp.json()
            esmeralda_id = None
            for c in chars:
                if c.get("name") == "Esmeralda Vault":
                    esmeralda_id = c["id"]
                    break
            assert esmeralda_id is not None, "Esmeralda Vault character not found"

            # Upload all images as new pictures
            picture_ids = []
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    data = {
                        "character_id": esmeralda_id,
                    }
                    r = client.post("/pictures", files=files, data=data)
                assert r.status_code == 200
                resp = r.json()
                assert resp["results"][0]["status"] == "success"
                picture_ids.append(resp["results"][0]["picture_id"])

            # Wait for all pictures to be tagged (embeddings generated)

            for _ in range(120):
                missing_embeddings = picture_ids.copy()
                if not missing_embeddings:
                    break

                for pid in missing_embeddings:
                    get_resp = client.get(f"/pictures/{pid}?info=true")
                    if not get_resp.status_code == 200:
                        continue
                    pic_info = get_resp.json()
                    embedding_b64 = pic_info.get("embedding")
                    if not embedding_b64:
                        continue
                    import base64
                    import numpy as np

                    try:
                        emb_bytes = base64.b64decode(embedding_b64)
                        emb = np.frombuffer(emb_bytes, dtype=np.float32)
                        # Check for non-empty and not all zeros
                        if emb.size == 0 or np.allclose(emb, 0):
                            print(f"Picture {pid} has empty or zero embedding: {emb}")
                            continue
                    except Exception as e:
                        print(f"Error decoding embedding for {pid}: {e}")
                        continue
                    print(
                        f"Picture {pid} has embedding of length {len(embedding_b64)} and norm {np.linalg.norm(emb):.4f}."
                    )
                    picture_ids.remove(pid)
                time.sleep(1)

            if picture_ids:
                assert (
                    False
                ), f"Pictures {picture_ids} did not get valid embedding after waiting."

            # Perform semantic search
            search_texts = [
                "It was a bright rainy day but Esmeralda needed to get out and get some fresh air, so she dressed for the weather, brought an umbrella and walked out into the countryside.",
                "Esmeralda smiles as she sits across me in the cafe wearing her grey sweater. The sunlight filters through the window of the empty cafe",
                "It was a bright winter morning, and Esmeralda decided to go for a walk in the woods. The snow had fallen the night before, and she enjoyed the glistening trees and the crisp air. She was glad to have her scarf and her warm coat to keep her cozy.",
                "Esmeralda spent hours in her garden tending to her grass and bushes wearing her dungarees. The greenery made her smile. Especially when the sky was blue",
                "Do I look like a man? Esmeralda asked, raising an eyebrow as she posed with her grey business suit, complete with shirt, jacket and tie.",
                "Esmeralda sat down on the park bench and considered her predicament. A quiet sadness came over her.",
            ]

            for search_text in search_texts:
                search_resp = client.get(
                    f"search?query={quote(search_text)}&threshold=0.6"
                )
                assert search_resp.status_code == 200
                results = search_resp.json()

                assert (
                    1 <= len(results)
                ), f"Expected at least one results, got {len(results)} for the text '{search_text}'"
                print("===== Semantic Search Result =====")
                print(f"Search text:\n{search_text}\n\n")
                print(f"Best match: {results[0]['description']}\n\n")
                print(f"Similarity: {results[0]['likeness_score']:.4f}.\n")
    gc.collect()
