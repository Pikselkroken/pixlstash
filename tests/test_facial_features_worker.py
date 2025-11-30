import gc
import logging
import sys
import time
import tempfile
import os

from pixlvault.db_models import Face, Picture
from pixlvault.server import Server
from pixlvault.logging import get_logger
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


def test_facial_features_worker_tagging():
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
                print(
                    "Scheduling watch for picture %s with description %s"
                    % (pic.file_path, pic.description)
                )
                futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACIAL_FEATURES, Picture, pic.id, "faces"
                    )
                )

            server.vault.start_workers({WorkerType.FACIAL_FEATURES})

            # Wait for the worker to complete processing all pictures
            results = [future.result(timeout=60) for future in futures]
            assert all(results), "Not all pictures were processed in time"

            # Check face counts for TaggerTest*.png
            pics = server.vault.db.run_task(lambda session: Picture.find(session))

            assert len(pics) > 0, "No pictures found in vault"

            for pic in pics:
                if pic.description and pic.description.startswith("TaggerTest"):
                    print(
                        "Checking picture %s with description %s"
                        % (pic.file_path, pic.description)
                    )
                    faces = server.vault.db.run_task(
                        lambda session: Face.find(session, picture_id=pic.id)
                    )
                    # Check face count as before
                    if "Multi" in os.path.basename(pic.description):
                        assert 2 <= len(faces) <= 3, (
                            f"{pic.description} should have 2 or 3 faces, found {len(faces)}"
                        )
                        print(
                            "Picture %s has %d faces as expected"
                            % (pic.description, len(faces))
                        )
                    else:
                        assert len(faces) == 1, (
                            f"{pic.description} should have 1 face, found {len(faces)}"
                        )
                        print(
                            "Picture %s has %d faces as expected"
                            % (pic.description, len(faces))
                        )
                    # New: Check that each face has a non-empty face_bbox
                    for face in faces:
                        assert face.bbox not in (None, "", "null"), (
                            f"Face bbox missing for {pic.description} face_index={face.face_index}"
                        )
                        print(
                            "%s face_index=%s has bbox: %s",
                            pic.description,
                            face.face_index,
                            face.bbox,
                        )
            server.vault.stop_workers({WorkerType.FACIAL_FEATURES})
    gc.collect()
