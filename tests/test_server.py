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
from io import BytesIO
from urllib.parse import quote

from pixlvault.db_models.picture import Picture
from pixlvault.logging import get_logger
from pixlvault.worker_registry import WorkerType
from pixlvault.server import Server

logger = get_logger(__name__)

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
    """Test that Esmeralda Vault exists and that the Logo is not associated with any character."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # This triggers _import_default_data
        with Server(config_path, server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            pics = server.vault.db.run_task(lambda s: s.query(Picture).all())
            assert len(pics) > 0, "No pictures found in vault"

            logging.info(
                f"Found {len(pics)} pictures in vault, starting facial features processing"
            )
            futures = []
            for pic in pics:
                future = server.vault.get_worker_future(
                    WorkerType.FACIAL_FEATURES, Picture, pic.id, "faces"
                )
                futures.append(future)

            server.vault.start_workers({WorkerType.FACIAL_FEATURES})

            for future in futures:
                result_id = future.result(timeout=60)
                logging.info(f"Processed picture ID: {result_id}")

            # Find Esmeralda Vault character (by name)
            resp = client.get("/characters")
            assert resp.status_code == 200
            chars = resp.json()
            assert len(chars) > 0, "No characters found in vault"
            esmeralda = None
            for c in chars:
                if c.get("name") == "Esmeralda Vault":
                    esmeralda = c
                    break
            assert esmeralda is not None, "Esmeralda Vault character not found"
            char_id = esmeralda["id"]
            logging.info(f"Found Esmeralda Vault character with ID: {char_id}")

            # Find all pictures, then filter by character association (robust to int/str id)
            resp2 = client.get("/pictures")
            assert resp2.status_code == 200
            pics = resp2.json()
            assert len(pics) > 0, "No pictures found in vault"
            pic_id = None
            for pic in pics:
                char_resp = client.get(f"/pictures/{pic['id']}/metadata")
                if char_resp.status_code == 200:
                    pic_info = char_resp.json()
                    char_ids = [str(cid) for cid in pic_info.get("character_ids", [])]
                    if str(char_id) in char_ids:
                        pic_id = pic["id"]
                        break

            # In the end the logo simply doesn't have any face and so no character association
            assert pic_id is None, (
                f"Logo picture should not be associated with any character (char_id={char_id})"
            )

            # Fetch the  picture form id
            img_resp = client.get(f"/pictures/{pics[0]['id']}")
            assert img_resp.status_code == 200
            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                logo_bytes = f.read()
            # Compare the full file
            assert img_resp.content == logo_bytes, (
                "Esmeralda Vault's picture does not match Logo.png"
            )
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
            r = client.post("/pictures", files=images)

            assert 200 == r.status_code, "Error: " + r.text
            resp = r.json()
            assert resp["results"][0]["status"] == "success"
            picture_id_1 = resp["results"][0]["picture_id"]

            # Fetch the picture and check it
            fetch_r1 = client.get(f"/pictures/{picture_id_1}/metadata")
            assert 200 == fetch_r1.status_code, "Error: " + fetch_r1.text
            fetched_picture = fetch_r1.json()
            assert fetched_picture["id"] == picture_id_1

            # Upload a new file
            img_bytes2 = random_images[1]
            files2 = [("file", ("iteration2.png", img_bytes2, "image/png"))]
            r2 = client.post("/pictures", files=files2)
            assert 200 == r2.status_code, "Error: " + r2.text
            resp2 = r2.json()
            assert resp2["results"][0]["status"] == "success"
            picture_id_2 = resp2["results"][0]["picture_id"]

            # Fetch the new picture and check association
            fetch_r2 = client.get(f"/pictures/{picture_id_2}/metadata")
            assert 200 == fetch_r2.status_code, "Error: " + fetch_r2.text
            fetched_picture_2 = fetch_r2.json()
            assert fetched_picture_2["id"] == picture_id_2

            # Upload the first picture again. Should get a 400
            files3 = [("file", ("random_name.png", img_bytes, "image/png"))]
            r3 = client.post("/pictures", files=files3)
            assert 400 == r3.status_code

            image_bytes3 = random_images[2]
            # Upload two pictures at once, one existing and one new
            files4 = [
                files2[0],
                ("file", ("random_name2.png", image_bytes3, "image/png")),
            ]
            r4 = client.post("/pictures", files=files4)
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
            r = client.post("/pictures", files=files)
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
            r = client.post("/pictures", files=files)
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
                "message": "PixlVault REST API",
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

            r = client.post("/pictures", files=files)
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


def test_semantic_search():
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
            config_path=config_path,
            server_config_path=server_config_path,
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
            face_futures = []
            embeddings_futures = []
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    r = client.post("/pictures", files=files)
                assert r.status_code == 200
                resp = r.json()
                assert resp["results"][0]["status"] == "success"
                picture_ids.append(resp["results"][0]["picture_id"])
                face_futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACIAL_FEATURES, Picture, picture_ids[-1], "faces"
                    )
                )
                embeddings_futures.append(
                    server.vault.get_worker_future(
                        WorkerType.TEXT_EMBEDDING,
                        Picture,
                        picture_ids[-1],
                        "text_embedding",
                    )
                )

            server.vault.start_workers(
                {
                    WorkerType.DESCRIPTION,
                    WorkerType.TAGGER,
                    WorkerType.TEXT_EMBEDDING,
                    WorkerType.FACIAL_FEATURES,
                }
            )

            # Wait for facial features to be processed and associate Esmeralda Vault with largest face in each picture
            for idx, future in enumerate(face_futures):
                result_id = future.result(timeout=120)
                logging.debug(f"Facial features processed for picture ID: {result_id}")
                pid = picture_ids[idx]
                # Fetch faces for this picture
                faces_resp = client.get(f"/pictures/{pid}/faces")
                assert faces_resp.status_code == 200, (
                    f"Failed to get picture info for {pid}"
                )
                logging.debug(
                    f"Received face data for picture ID {pid}: {faces_resp.json().get('faces', [])}"
                )
                faces_data = faces_resp.json().get("faces", [])
                logging.debug(f"Picture ID {pid} has {len(faces_data)} faces detected")
                if not faces_data:
                    continue  # No faces detected

                # Find largest face by area
                def face_area(face):
                    bbox = face.get("bbox")
                    if bbox and len(bbox) == 4:
                        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    return 0

                largest_face = max(faces_data, key=face_area)
                face_id = largest_face.get("id")
                assert face_id is not None, (
                    f"No face id found for largest face in picture {pid}"
                )
                # Associate Esmeralda Vault with this face
                assoc_resp = client.post(
                    f"/faces/{face_id}/character",
                    json={"character_id": esmeralda_id},
                )
                assert assoc_resp.status_code == 200, (
                    f"Failed to associate face {face_id} with Esmeralda Vault: {assoc_resp.text}"
                )
                assoc_data = assoc_resp.json()
                assert assoc_data["status"] == "success"
                logging.debug(
                    f"Associated face ID {face_id} in picture {pid} with Esmeralda Vault character ID {esmeralda_id}"
                )

            # Wait for all text embeddings to be processed
            for future in embeddings_futures:
                result_id = future.result(timeout=60)
                logging.debug(f"Text embedding processed for picture ID: {result_id}")

            # Inspect embeddings for each picture after embedding futures complete
            for pid in picture_ids:
                meta_resp = client.get(f"/pictures/{pid}/text_embedding")
                assert meta_resp.status_code == 200
                meta = meta_resp.json()
                embedding_b64 = meta.get("text_embedding")
                if embedding_b64:
                    import base64
                    import numpy as np

                    emb_bytes = base64.b64decode(embedding_b64)
                    emb = np.frombuffer(emb_bytes, dtype=np.float32)
                    print(
                        f"Picture {pid} embedding: shape={emb.shape}, norm={np.linalg.norm(emb):.4f}, sample={emb[:5]}"
                    )
                else:
                    print(f"Picture {pid} has no embedding!")

            # Perform semantic search
            search_texts = [
                "It was a bright rainy day but Esmeralda needed to get out and get some fresh air, so she dressed for the weather, brought an umbrella and walked out into the countryside.",
                "Esmeralda smiles as she sits across me in the cafe wearing her grey sweater. The sunlight filters through the window of the empty cafe",
                "It was a bright winter morning, and Esmeralda decided to go for a walk in the woods. The snow had fallen the night before, and she enjoyed the glistening trees and the crisp air. She was glad to have her scarf and her warm coat to keep her cozy.",
                "Esmeralda spent hours in her garden tending to her grass and bushes wearing her dungarees. The greenery made her smile. Especially when the sky was blue",
                "Do I look like a man? Esmeralda asked, raising an eyebrow as she posed with her grey business suit, complete with shirt, jacket and tie.",
                "Esmeralda sat down on the wooden park bench and considered her predicament. She was in serious trouble.",
            ]

            for search_text in search_texts:
                search_resp = client.get(
                    f"/pictures?query={quote(search_text)}&sort=search_likeness%20desc&threshold=0.3"
                )
                assert search_resp.status_code == 200
                results = search_resp.json()

                assert 1 <= len(results), (
                    f"Expected at least one results, got {len(results)} for the text '{search_text}'"
                )
                print("===== Semantic Search Result =====")
                print(f"Search text:\n{search_text}\n")
                print(f"Number of results: {len(results)}\n")
                for r in results:
                    print(f"Match: {r['description']}")
                    print(f"Similarity: {r['likeness_score']:.4f}.")
    gc.collect()
