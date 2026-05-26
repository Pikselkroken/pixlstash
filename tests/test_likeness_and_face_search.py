import tempfile
import os
from fastapi.testclient import TestClient
from sqlmodel import select, func
from pixlstash.server import Server
from pixlstash.db_models.picture import Picture
from tests.utils import (
    upload_pictures_and_wait,
    wait_for_faces,
    poll_until_zero,
    API_PREFIX,
)
from tests.test_server import random_images


def _wait_for_image_embeddings(server, picture_ids, timeout_s=120):
    """Block until all given pictures have a stored image_embedding."""
    id_set = list(picture_ids)

    def _count_missing(session):
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.id.in_(id_set))
            .where(Picture.image_embedding.is_(None))
        ).one()
        return result[0] if isinstance(result, tuple) else (result or 0)

    poll_until_zero(server, _count_missing, "image embeddings", timeout_s=timeout_s)


def test_likeness_search_basic():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            images = [
                ("file", ("img1.png", random_images[0], "image/png")),
                ("file", ("img2.png", random_images[1], "image/png")),
            ]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            picture_ids = [r["picture_id"] for r in import_status["results"]]

            # Wait for CLIP image embeddings to be computed before querying.
            _wait_for_image_embeddings(server, picture_ids)

            # POST to likeness-search
            resp = client.post(
                f"{API_PREFIX}/pictures/likeness-search"
                f"?source_picture_ids={picture_ids[0]}&top_n=10&threshold=0.01"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert isinstance(data, list)
            # Should include the source image itself
            ids = [r["picture_id"] for r in data]
            assert picture_ids[0] in ids


def test_face_search_basic():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            response = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert response.status_code == 200

            images = [
                ("file", ("img1.png", random_images[0], "image/png")),
                ("file", ("img2.png", random_images[1], "image/png")),
            ]
            import_status = upload_pictures_and_wait(client, images)
            assert import_status["status"] == "completed"
            picture_ids = [r["picture_id"] for r in import_status["results"]]

            # Poll until face extraction has had a chance to run (may be empty for
            # random-noise images, which is a valid outcome).
            all_faces = wait_for_faces(client, picture_ids[0], timeout_s=60)
            # Exclude sentinel records (face_index == -1), which have no embedding.
            faces = [f for f in all_faces if f.get("face_index", 0) != -1]
            if not faces:
                # No real faces detected in random noise images — nothing to assert
                return
            face_id = faces[0]["id"]

            # POST to face-search using the stored face embedding
            resp = client.post(
                f"{API_PREFIX}/pictures/face-search?source_face_id={face_id}&top_n=10"
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert isinstance(data, list)
            assert data
