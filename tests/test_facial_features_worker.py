from PIL import Image
import io

import gc
import logging
import sys
import time
import tempfile
import os

from pixlvault.db_models import Character, Face, Picture
from pixlvault.server import Server
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import WorkerType


logger = get_logger(__name__)

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logging.info("Debug info")


def wait_for_worker_completion(worker, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        if not worker.is_alive() or not worker.is_busy():
            return True
        time.sleep(0.5)
    return False


def test_facial_features():
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        config_path = os.path.join(temp_dir, "config.json")
        config = Server.create_config(
            default_device="cpu",
            image_roots=[image_root],
            selected_image_root=image_root,
        )
        with open(config_path, "w") as f:
            import json

            f.write(json.dumps(config, indent=2))
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path, server_config_path) as server:
            server.vault.import_default_data(add_tagger_test_images=True)

            # Check face counts for TaggerTest*.png
            pics = server.vault.db.run_task(lambda session: Picture.find(session))

            futures = []
            for pic in pics:
                logger.info(
                    "Scheduling watch for picture %s with description %s"
                    % (pic.file_path, pic.description)
                )
                futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACE, Picture, pic.id, "faces"
                    )
                )

            server.vault.start_workers({WorkerType.FACE})
            # Wait for all face detection futures to complete
            results = [future.result(timeout=60) for future in futures]
            assert all(results), "Not all pictures were processed in time"
            server.vault.stop_workers({WorkerType.FACE})

            # Now run assertions as before
            pics = server.vault.db.run_task(lambda session: Picture.find(session))
            assert len(pics) > 0, "No pictures found in vault"
            for pic in pics:
                if pic.description and pic.description.startswith("TaggerTest"):
                    faces = server.vault.db.run_task(
                        lambda session: Face.find(session, picture_id=pic.id)
                    )
                    # Check face count as before
                    if "Multi" in os.path.basename(pic.description):
                        assert 2 <= len(faces) <= 3, (
                            f"{pic.description} should have 2 or 3 faces, found {len(faces)}"
                        )
                        logger.info(
                            "Picture %s has %d faces as expected"
                            % (pic.description, len(faces))
                        )
                    else:
                        assert len(faces) == 1, (
                            f"{pic.description} should have 1 face, found {len(faces)}"
                        )
                        logger.info(
                            "Picture %s has %d faces as expected"
                            % (pic.description, len(faces))
                        )
                    # New: Check that each face has a non-empty face_bbox
                    for face in faces:
                        assert face.bbox not in (None, "", "null"), (
                            f"Face bbox missing for {pic.description} face_index={face.face_index}"
                        )
                        logger.info(
                            f"{pic.description} face_index={face.face_index} has bbox: {face.bbox}"
                        )
                        assert face.face_index >= 0, (
                            f"Face index should be non-negative for {pic.description} face_index={face.face_index}"
                        )

                        assert face.features is not None, (
                            f"Face features missing for {pic.description} face_index={face.face_index}"
                        )
                        logger.info(
                            f"{pic.description} face_index={face.face_index} has features: {face.features is not None}"
                        )
    gc.collect()


def test_character_thumbnail_endpoint():
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        config_path = os.path.join(temp_dir, "config.json")
        config = Server.create_config(
            default_device="cpu",
            image_roots=[image_root],
            selected_image_root=image_root,
        )
        with open(config_path, "w") as f:
            import json

            f.write(json.dumps(config, indent=2))
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path, server_config_path) as server:
            server.vault.import_default_data(add_tagger_test_images=True)

            # Check face counts for TaggerTest*.png
            pics = server.vault.db.run_task(lambda session: Picture.find(session))

            futures = []
            for pic in pics:
                logger.info(
                    "Scheduling watch for picture %s with description %s"
                    % (pic.file_path, pic.description)
                )
                futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACE, Picture, pic.id, "faces"
                    )
                )

            server.vault.start_workers({WorkerType.FACE})
            # Wait for all face detection futures to complete
            results = [future.result(timeout=60) for future in futures]
            assert len(results) == len(futures), (
                "Not all pictures were processed in time"
            )

            # Assign the default character to the largest face in each picture
            chars = server.vault.db.run_task(lambda session: Character.find(session))
            char = chars[0]
            pics = server.vault.db.run_task(lambda session: Picture.find(session))
            for pic in pics:
                faces = server.vault.db.run_task(
                    lambda session: Face.find(session, picture_id=pic.id)
                )
                if not faces:
                    continue
                # Find the largest face by area
                largest_face = max(
                    faces, key=lambda f: (f.width or 0) * (f.height or 0)
                )

                def assign_char(session, face_id, char_id):
                    face = session.get(Face, face_id)
                    face.character_id = char_id
                    session.add(face)
                    session.commit()

                server.vault.db.run_task(assign_char, largest_face.id, char.id)

            # Now get the character with faces
            chars = server.vault.db.run_task(
                lambda session: Character.find(session, select_fields=["faces"])
            )
            char = None
            for c in chars:
                if c.faces:
                    char = c
                    break
            assert char is not None, "No character with faces found"

            # Get the thumbnail via the endpoint
            from fastapi.testclient import TestClient

            client = TestClient(server.api)
            response = client.get(f"/characters/{char.id}/thumbnail")
            assert response.status_code == 200, (
                f"Thumbnail endpoint failed: {response.status_code}"
            )
            assert response.headers["content-type"] == "image/png"

            # Load the image from response
            thumb_img = Image.open(io.BytesIO(response.content))
            # Get the best face and crop from the database
            best_face = sorted(
                char.faces, key=lambda f: (f.likeness or 0), reverse=True
            )[0]
            # Query the picture for this face (avoid DetachedInstanceError)
            best_pic = server.vault.db.run_task(
                lambda session: session.get(Picture, best_face.picture_id)
            )
            from pixlvault.picture_utils import PictureUtils

            bbox = best_face.bbox
            crop_img = PictureUtils.crop_face_bbox_exact(best_pic.file_path, bbox)
            assert crop_img.size == thumb_img.size, (
                f"Thumbnail size {thumb_img.size} does not match crop size {crop_img.size}"
            )
            # Save both images for manual inspection
            outdir = os.path.join(
                os.path.dirname(__file__), "..", "tmp", "face_thumbnails"
            )
            os.makedirs(outdir, exist_ok=True)
            thumb_img.save(os.path.join(outdir, f"character_{char.id}_endpoint.png"))
            crop_img.save(os.path.join(outdir, f"character_{char.id}_dbcrop.png"))
