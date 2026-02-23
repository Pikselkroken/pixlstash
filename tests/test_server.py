import numpy as np
import logging
import shutil
import os
import random
import tempfile
import time
import tomllib

import gc
import psutil
import tracemalloc
import collections

from PIL import Image
from fastapi.testclient import TestClient
from io import BytesIO
from urllib.parse import quote

from pixlvault.db_models.picture import Picture
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils
from pixlvault.worker_registry import WorkerType
from pixlvault.server import Server
from tests.utils import upload_pictures_and_wait

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


def test_esmeralda_vault_character_and_logo():
    """Test that Esmeralda Vault exists and that the Logo is not associated with any character."""

    tracemalloc.start()
    log_resources("START test_esmeralda_vault_character_and_logo")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # This triggers _import_default_data
        with Server(server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)

            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            # First access with the token
            response = client.get("/protected")
            assert response.status_code == 200
            assert response.json()["message"] == "You are authenticated!"

            pics = server.vault.db.run_task(lambda s: s.query(Picture).all())
            assert len(pics) > 0, "No pictures found in vault"

            logging.info(
                f"Found {len(pics)} pictures in vault, starting facial features processing"
            )

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
            img_resp = client.get(f"/pictures/{pics[0]['id']}.png")
            assert img_resp.status_code == 200
            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                logo_bytes = f.read()
            # Compare the full file
            assert img_resp.content == logo_bytes, (
                "Esmeralda Vault's picture does not match Logo.png"
            )
    gc.collect()
    log_resources("END test_esmeralda_vault_character_and_logo")


def test_create_and_get_default_character():
    """Test creating and fetching the default character 'Esmeralda'."""
    log_resources("START test_create_and_get_default_character")

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

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
            logger.info("Created character: {}".format(data["character"]))
            char_id = data["character"]["id"]
            assert data["character"]["name"] == char_name
            assert data["character"]["description"] == char_desc

            # Fetch Esmeralda by id
            resp2 = client.get(f"/characters/{char_id}")
            assert resp2.status_code == 200
            char = resp2.json()
            logger.info("List object?? " + str(char))
            assert char["id"] == char_id
            assert char["name"] == char_name
            assert char["description"] == char_desc
    gc.collect()
    log_resources("END test_create_and_get_default_character")


def test_upload_existing_picture():
    """Test uploading an existing picture."""

    log_resources("START test_upload_existing_picture")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            server.vault.start_workers({WorkerType.FACE})
            client = TestClient(server.api)
            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            # Create a new picture
            img_bytes = random_images[0]
            images = [("file", ("master.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            picture_id_1 = import_status["results"][0]["picture_id"]

            # Fetch the picture and check it
            fetch_r1 = client.get(f"/pictures/{picture_id_1}/metadata")
            assert 200 == fetch_r1.status_code, "Error: " + fetch_r1.text
            fetched_picture = fetch_r1.json()
            assert fetched_picture["id"] == picture_id_1

            # Upload a new file
            img_bytes2 = random_images[1]
            files2 = [("file", ("iteration2.png", img_bytes2, "image/png"))]
            import_status_2 = upload_pictures_and_wait(client, files2)
            assert import_status_2["status"] == "completed"
            assert import_status_2["results"][0]["status"] == "success"
            picture_id_2 = import_status_2["results"][0]["picture_id"]

            # Fetch the new picture and check association
            fetch_r2 = client.get(f"/pictures/{picture_id_2}/metadata")
            assert 200 == fetch_r2.status_code, "Error: " + fetch_r2.text
            fetched_picture_2 = fetch_r2.json()
            logger.info(f"Fetched picture 2 metadata: {fetched_picture_2}")
            assert fetched_picture_2["id"] == picture_id_2

            # Upload the first picture again. Should report duplicate
            files3 = [("file", ("random_name.png", img_bytes, "image/png"))]
            import_status_3 = upload_pictures_and_wait(client, files3)
            assert import_status_3["status"] == "completed"
            assert import_status_3["results"][0]["status"] == "duplicate"

            image_bytes3 = random_images[2]
            # Upload two pictures at once, one existing and one new
            files4 = [
                files2[0],
                ("file", ("random_name2.png", image_bytes3, "image/png")),
            ]
            import_status_4 = upload_pictures_and_wait(client, files4)
            assert import_status_4["status"] == "completed"
            for i, result in enumerate(import_status_4["results"]):
                if i == 0:
                    assert result["status"] == "duplicate"  # Existing picture
                else:
                    assert result["status"] == "success"  # New picture

    gc.collect()
    log_resources("END test_upload_existing_picture")


def test_favicon():
    """Test /favicon.ico endpoint returns 200 and PNG content."""
    log_resources("START test_favicon")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)
            resp = client.get("/favicon.ico")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/vnd.microsoft.icon"
            assert resp.content[:4] == b"\x00\x00\x01\x00"  # ICO file signature
    gc.collect()
    log_resources("END test_favicon")


def test_characters_summary():
    """Test /characters/summary endpoint returns 200 and valid structure."""
    log_resources("START test_characters_summary")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        with Server(server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)
            server.vault.start_workers({WorkerType.FACE})

            # Get a valid token
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            # Get Esmeralda Vault character ID
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
                    import_status = upload_pictures_and_wait(client, files)
                assert import_status["status"] == "completed"
                assert import_status["results"][0]["status"] == "success"
                picture_ids.append(import_status["results"][0]["picture_id"])

            # Wait for facial features to be processed and associate Esmeralda Vault with largest face in each picture
            for pid in picture_ids:
                faces_resp = client.get(f"/pictures/{pid}/faces")
                assert faces_resp.status_code == 200
                faces_data = faces_resp.json().get("faces", [])
                if not faces_data:
                    continue

                def face_area(face):
                    bbox = face.get("bbox")
                    if bbox and len(bbox) == 4:
                        return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                    return 0

                largest_face = max(faces_data, key=face_area)
                face_id = largest_face.get("id")
                assert face_id is not None
                assoc_resp = client.post(
                    f"/characters/{esmeralda_id}/faces",
                    json={"face_ids": [face_id]},
                )
                assert assoc_resp.status_code == 200, (
                    f"Failed to associate face {face_id} with Esmeralda Vault: {assoc_resp.text}"
                )
                assoc_data = assoc_resp.json()
                assert assoc_data["status"] == "success"

                # Query the character-face association to verify
                check_assoc_resp = client.get(f"/characters/{esmeralda_id}/faces")
                assert check_assoc_resp.status_code == 200, (
                    f"Failed to fetch faces for character {esmeralda_id} after association"
                )
                faces_data = check_assoc_resp.json().get("faces", [])
                face_ids = [f.get("id") for f in faces_data]
                assert face_id in face_ids, (
                    f"Face ID {face_id} not found in Esmeralda Vault character association: {face_ids}"
                )
                logging.debug(
                    f"Verified Esmeralda Vault character association for face {face_id}"
                )

            # Call /characters/summary and check count
            summary_resp = client.get(f"/characters/{str(esmeralda_id)}/summary")
            assert summary_resp.status_code == 200
            summary = summary_resp.json()
            # Accept dict or list, but check count
            if isinstance(summary, dict):
                count = summary.get("image_count")
            elif isinstance(summary, list):
                count = len(summary)
            else:
                count = None
            assert count is not None and count >= len(picture_ids), (
                f"Expected at least {len(picture_ids)} pictures for Esmeralda Vault, got {count}"
            )
    gc.collect()
    log_resources("END test_characters_summary")


def test_pictures_stacks():
    """Test /pictures/stacks endpoint returns 200 and valid structure."""
    log_resources("START test_pictures_stacks")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)

            server.vault.start_workers({WorkerType.FACE})

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            resp = client.get("/pictures/stacks")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
    gc.collect()
    log_resources("END test_pictures_stacks")


def test_pictures_thumbnails():
    """Test /pictures/thumbnails endpoint returns 200 and valid structure."""
    log_resources("START test_pictures_thumbnails")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)

            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200
            # Send empty payload for basic test
            resp = client.post("/pictures/thumbnails", json={"ids": []})
            assert resp.status_code == 200
            assert isinstance(resp.json(), dict)
    gc.collect()
    log_resources("END test_pictures_thumbnails")


def test_pictures_export():
    """Test /pictures/export endpoint returns 200 and zip content."""
    import zipfile

    log_resources("START test_pictures_export")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path) as server:
            server.vault.import_default_data(add_tagger_test_images=True)
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            resp = client.get("/pictures/export")
            assert resp.status_code == 200, f"Error: {resp.text}"
            assert resp.headers["content-type"] == "application/json"

            task_id = resp.json().get("task_id")
            assert task_id, "Missing task_id in export response"

            status_payload = None
            timeout_s = 10
            start = time.time()
            while time.time() - start < timeout_s:
                status_resp = client.get(
                    "/pictures/export/status", params={"task_id": task_id}
                )
                assert status_resp.status_code == 200, f"Error: {status_resp.text}"
                status_payload = status_resp.json()
                if status_payload.get("status") == "completed":
                    break
                if status_payload.get("status") == "failed":
                    raise AssertionError("Export task failed")
                time.sleep(0.1)

            assert status_payload, "Missing export status payload"
            assert status_payload.get("status") == "completed", (
                f"Export task did not complete in {timeout_s}s"
            )

            download_url = status_payload.get("download_url")
            assert download_url, "Missing download_url in export status"

            download_resp = client.get(download_url)
            assert download_resp.status_code == 200, f"Error: {download_resp.text}"
            assert download_resp.content[:2] == b"PK"  # ZIP file signature
            logger.info(
                "Exported pictures zip size: {} bytes".format(
                    len(download_resp.content)
                )
            )

            # Extract zip and compare SHA, file size, format, width, height
            with zipfile.ZipFile(BytesIO(download_resp.content)) as zf:
                zip_names = set(zf.namelist())
                image_names = [
                    name for name in zip_names if not name.lower().endswith(".txt")
                ]
                # Get expected metadata from the database
                pictures = server.vault.db.run_task(Picture.find)

                assert len(pictures) == len(image_names), (
                    f"Expected {len(pictures)} pictures in export, found {len(image_names)} in zip"
                )
                logger.info("Found {} images in export zip".format(len(image_names)))
                for fname in image_names:
                    found = False
                    data = None
                    with zf.open(fname) as f:
                        data = f.read()
                        sha = PictureUtils.calculate_hash_from_bytes(data)

                    # For file in the zip find a matching picture by SHA
                    for pic in pictures:
                        if sha == pic.pixel_sha:
                            found = True
                            # Compare file size
                            assert len(data) == pic.size_bytes, (
                                f"Size mismatch for {fname}: {len(data)} != {pic.size_bytes}"
                            )
                            # Compare format, width, height
                            img = Image.open(BytesIO(data))
                            assert img.format.lower() == (pic.format or "").lower(), (
                                f"Format mismatch for {fname}: {img.format} != {pic.format}"
                            )
                            assert img.width == pic.width, (
                                f"Width mismatch for {fname}: {img.width} != {pic.width}"
                            )
                            assert img.height == pic.height, (
                                f"Height mismatch for {fname}: {img.height} != {pic.height}"
                            )
                            break
                    assert found, (
                        f"No database picture matches exported SHA for picture {fname}"
                    )
    gc.collect()
    log_resources("END test_pictures_export")


def test_post_logo_identical_upload():
    log_resources("START test_post_logo_identical_upload")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            server.vault.import_default_data()
            server.vault.start_workers({WorkerType.FACE})
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            logo_path = os.path.join(os.path.dirname(__file__), "../Logo.png")
            with open(logo_path, "rb") as f:
                img_bytes = f.read()
                files = [("file", ("identical_logo.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "duplicate"
    gc.collect()
    log_resources("END test_post_logo_identical_upload")


def test_post_logo_altered_pixel_upload():
    log_resources("START test_post_logo_altered_pixel_upload")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            server.vault.start_workers({WorkerType.FACE})
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

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
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            assert import_status["results"][0]["picture_id"]
            os.remove(tmp_path)
    gc.collect()
    log_resources("END test_post_logo_altered_pixel_upload")


def test_read_version():
    log_resources("START test_read_version")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.get("/version")
            assert response.status_code == 200
            expected_version = get_project_version()
            assert response.json() == {
                "message": "PixlVault REST API",
                "version": expected_version,
            }
    gc.collect()
    log_resources("END test_read_version")


def test_benchmark_add_images_by_binary_upload():
    log_resources("START test_benchmark_add_images_by_binary_upload")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            server.vault.start_workers({WorkerType.FACE})
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            start = time.time()
            ids = []
            files = []
            for i, img_bytes in enumerate(random_images):
                file = ("file", (f"image_{i:04d}.png", img_bytes, "image/png"))
                files.append(file)

            import_status = upload_pictures_and_wait(client, files, timeout_s=60)
            end = time.time()

            assert import_status["status"] == "completed"
            assert len(import_status["results"]) == TEST_SIZE
            for result in import_status["results"]:
                assert result["status"] == "success"
                ids.append(result["picture_id"])

            print(
                f"Upload Benchmark: Added {TEST_SIZE} images in {end - start:.2f} seconds or {total_bytes / (end - start) / 1024 / 1024:.2f} MB/s"
            )

            # Read back and check a few images
            random_indices = random.sample(range(TEST_SIZE), 3)
            for check_idx in random_indices:
                pic_id = ids[check_idx]
                img_resp = client.get(f"/pictures/{pic_id}.png")
                assert img_resp.status_code == 200
                assert img_resp.content[:1024] == random_images[check_idx][:1024]
    gc.collect()
    log_resources("END test_benchmark_add_images_by_binary_upload")


def test_semantic_search():
    """Test: Add all images from pictures folder, wait for tagging, perform semantic search, print results, assert count."""

    log_resources("START test_semantic_search")
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")

        src_dir = os.path.join(os.path.dirname(__file__), "../pictures")
        image_files = [
            f
            for f in os.listdir(src_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        ]

        with Server(server_config_path=server_config_path) as server:
            server.vault.import_default_data()
            client = TestClient(server.api)
            server.vault.start_workers({WorkerType.FACE})

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Get Esmeralda's character ID
            resp = client.get("/characters")
            assert resp.status_code == 200
            chars = resp.json()
            esmeralda_id = None
            barbara_id = None
            barry_id = None
            cassandra_id = None
            for c in chars:
                if c.get("name") == "Esmeralda Vault":
                    esmeralda_id = c["id"]
                elif c.get("name") == "Barbara Vault":
                    barbara_id = c["id"]
                elif c.get("name") == "Barry Vault":
                    barry_id = c["id"]
                elif c.get("name") == "Cassandra Vault":
                    cassandra_id = c["id"]

            assert esmeralda_id is not None, "Esmeralda Vault character not found"
            assert barbara_id is not None, "Barbara Vault character not found"
            assert barry_id is not None, "Barry Vault character not found"
            assert cassandra_id is not None, "Cassandra Vault character not found"

            # Upload all images as new pictures
            picture_ids = []
            embeddings_futures = []
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    import_status = upload_pictures_and_wait(client, files)
                assert import_status["status"] == "completed"
                assert import_status["results"][0]["status"] == "success"
                picture_ids.append(import_status["results"][0]["picture_id"])
                embeddings_futures.append(
                    server.vault.get_worker_future(
                        WorkerType.TEXT_EMBEDDING,
                        Picture,
                        picture_ids[-1],
                        "text_embedding",
                    )
                )

            tag_futures = [
                server.vault.get_worker_future(
                    WorkerType.TAGGER,
                    Picture,
                    pic_id,
                    "tags",
                )
                for pic_id in picture_ids
            ]
            description_futures = [
                server.vault.get_worker_future(
                    WorkerType.DESCRIPTION,
                    Picture,
                    pic_id,
                    "description",
                )
                for pic_id in picture_ids
            ]

            server.vault.start_workers(
                {
                    WorkerType.TAGGER,
                    WorkerType.DESCRIPTION,
                    WorkerType.SMART_SCORE_SCRAPHEAP,
                }
            )

            def wait_for_imported_at(timeout_s=60, poll_interval=0.5):
                start = time.time()
                pending = set(picture_ids)
                while pending and (time.time() - start) < timeout_s:
                    completed = set()
                    for pid in pending:
                        meta_resp = client.get(f"/pictures/{pid}/metadata")
                        if meta_resp.status_code != 200:
                            continue
                        meta = meta_resp.json()
                        if meta.get("imported_at"):
                            completed.add(pid)
                    pending -= completed
                    if pending:
                        time.sleep(poll_interval)
                assert not pending, (
                    "Timed out waiting for imported_at for picture ids: "
                    f"{sorted(pending)}"
                )

            wait_for_imported_at()

            # Wait for facial features to be processed and associate Esmeralda Vault with largest face in each picture
            for pid in picture_ids:
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

                # Order faces left to right
                faces_ordered = sorted(
                    faces_data, key=lambda f: f.get("bbox", [0, 0, 0, 0])[0]
                )
                if len(faces_ordered) == 1:
                    face_id = faces_ordered[0].get("id")
                    assert face_id is not None, (
                        f"No face id found for largest face in picture {pid}"
                    )
                    # Associate Esmeralda Vault with this face
                    assoc_resp = client.post(
                        f"/characters/{esmeralda_id}/faces",
                        json={"face_ids": [face_id]},
                    )
                    assert assoc_resp.status_code == 200, (
                        f"Failed to associate face {face_id} with Esmeralda Vault: {assoc_resp.text}"
                    )
                    assoc_data = assoc_resp.json()
                    assert assoc_data["status"] == "success"
                    logging.debug(
                        f"Associated face ID {face_id} in picture {pid} with Esmeralda Vault character ID {esmeralda_id}"
                    )

                    # Query the character-face association to verify
                    check_assoc_resp = client.get(f"/characters/{esmeralda_id}/faces")
                    assert check_assoc_resp.status_code == 200, (
                        f"Failed to fetch faces for character {esmeralda_id} after association due to {check_assoc_resp.text}"
                    )
                    faces_data = check_assoc_resp.json().get("faces", [])
                    assert len(faces_data) > 0, (
                        f"No faces found for character {esmeralda_id} after association"
                    )
                    face_ids = [f.get("id") for f in faces_data]
                    assert face_id in face_ids, (
                        f"Face ID {face_id} not found in Esmeralda Vault character association: {face_ids} and {faces_data}"
                    )
                    logging.debug(
                        f"Verified Esmeralda Vault character association for face {face_id}"
                    )
                elif len(faces_ordered) >= 3:
                    # Associate Barbara, Barry, Cassandra with left, center, right faces
                    face_ids = [
                        faces_ordered[0].get("id"),
                        faces_ordered[len(faces_ordered) // 2].get("id"),
                        faces_ordered[-1].get("id"),
                    ]
                    char_ids = [barbara_id, barry_id, cassandra_id]
                    for face_id, char_id in zip(face_ids, char_ids):
                        assert face_id is not None, (
                            f"No face id found for face in picture {pid} for character {char_id}"
                        )
                        assoc_resp = client.post(
                            f"/characters/{char_id}/faces",
                            json={"face_ids": [face_id]},
                        )
                        assert assoc_resp.status_code == 200, (
                            f"Failed to associate face {face_id} with character {char_id}: {assoc_resp.text}"
                        )
                        assoc_data = assoc_resp.json()
                        assert assoc_data["status"] == "success"
                        logging.debug(
                            f"Associated face ID {face_id} in picture {pid} with character ID {char_id}"
                        )

            for future in description_futures:
                future.result(timeout=120)

            for future in tag_futures:
                future.result(timeout=120)

            server.vault.start_workers(
                {
                    WorkerType.TEXT_EMBEDDING,
                }
            )

            # Wait for all text embeddings to be processed
            for future in embeddings_futures:
                result_id = future.result(timeout=80)
                logging.debug(f"Text embedding processed for picture ID: {result_id}")

            def wait_for_semantic_ready(timeout_s=80, poll_interval=0.5):
                start = time.time()
                pending = set(picture_ids)
                while pending and (time.time() - start) < timeout_s:
                    completed = set()
                    for pid in pending:
                        meta_resp = client.get(f"/pictures/{pid}/metadata")
                        if meta_resp.status_code != 200:
                            continue
                        meta = meta_resp.json()
                        if not meta.get("description"):
                            continue
                        embed_resp = client.get(f"/pictures/{pid}/text_embedding")
                        if embed_resp.status_code != 200:
                            continue
                        if embed_resp.json().get("text_embedding") is None:
                            continue
                        completed.add(pid)
                    pending -= completed
                    if pending:
                        time.sleep(poll_interval)
                assert not pending, (
                    f"Timed out waiting for semantic readiness for picture ids: {sorted(pending)}"
                )

            wait_for_semantic_ready()

            server.vault.stop_workers(
                {
                    WorkerType.TAGGER,
                    WorkerType.DESCRIPTION,
                    WorkerType.TEXT_EMBEDDING,
                }
            )

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
                    f"/pictures/search?query={quote(search_text)}&threshold=0.4"
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
    log_resources("END test_semantic_search")
