import logging
import os
import tempfile

import gc
from time import time

from fastapi.testclient import TestClient

from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness
from pixlvault.db_models.picture_likeness import PictureLikeness
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import WorkerType
from pixlvault.server import Server
from tests.utils import upload_pictures_and_wait

logger = get_logger(__name__)


def test_picture_stacking():
    """Test: Add all images from pictures folder, wait for tagging, perform semantic search, print results, assert count."""

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server_config.json")

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
            # server.vault.import_default_data()
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            server.vault.start_workers(
                {
                    WorkerType.QUALITY,
                    WorkerType.FACE,
                }
            )

            # Upload all images as new pictures
            picture_ids = []
            picture_likeness_futures = []
            id_to_filename = {}
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    import_status = upload_pictures_and_wait(client, files)
                assert import_status["status"] == "completed"
                assert import_status["results"][0]["status"] == "success"
                id_to_filename[import_status["results"][0]["picture_id"]] = fname
                picture_ids.append(import_status["results"][0]["picture_id"])
            for pid1 in picture_ids:
                for pid2 in picture_ids:
                    if pid2 > pid1:
                        logger.info("Queuing likeness pair: (%s, %s)", pid1, pid2)
                        picture_likeness_futures.append(
                            (
                                pid1,
                                pid2,
                                server.vault.get_worker_future(
                                    WorkerType.LIKENESS,
                                    PictureLikeness,
                                    (pid1, pid2),
                                    "pair",
                                ),
                            )
                        )

            server.vault.start_workers(
                {
                    WorkerType.LIKENESS,
                }
            )

            logger.info("Waiting for likeness to be processed...")

            likeness_pairs = []
            for pid1, pid2, future in picture_likeness_futures:
                logger.info("Waiting for picture likeness pair : (%s, %s)", pid1, pid2)
                result = future.result(timeout=60)
                assert result is not None, "LikenessWorker timed out"
                likeness_pairs.append(result)
                logger.info("Picture likeness computed: %s", result)

            assert (
                len(likeness_pairs) == (len(picture_ids) * (len(picture_ids) - 1)) // 2
            ), "Not all picture likeness pairs were computed."

            # Log DB contents for likeness and face likeness
            likeness_rows = server.vault.db.run_task(PictureLikeness.find)
            logger.info(
                f"PictureLikeness table rows: {[{'a': r.picture_id_a, 'b': r.picture_id_b, 'likeness': r.likeness} for r in likeness_rows]}"
            )

            server.vault.stop_workers()

            # --- NEW: Fetch /pictures/stacks and log likeness table ---
            response = client.get("/pictures/stacks")
            assert response.status_code == 200, (
                f"Failed to fetch /pictures/stacks: {response.text}"
            )
            stacks_data = response.json()
            logger.info("Fetched /pictures/stacks data: %s", stacks_data)
            # Build a picture-to-picture likeness table from all stacks
            pic_ids = picture_ids
            # Fetch descriptions for all picture ids
            desc_resp = client.get(
                "/pictures", params={"ids": ",".join(map(str, pic_ids))}
            )
            assert desc_resp.status_code == 200, (
                f"Failed to fetch picture descriptions: {desc_resp.text}"
            )
            desc_data = desc_resp.json()

            logger.info("Picture descriptions data: %s", desc_data)

            # Check the list of stacks. We can't really build a full table anymore since
            # we only get stacks of similar pictures, not all pairs.
            logger.info(f"Getting stack data {stacks_data}")
            stack_dict = {}
            for stack_pic in stacks_data:
                logger.info(f"Processing stack picture: {stack_pic}")
                stack_dict.setdefault(stack_pic.get("stack_index"), []).append(
                    stack_pic
                )

            for stack_index, stack_pics in stack_dict.items():
                logger.info(f"Stack index {stack_index} has pictures: {stack_pics}")

    gc.collect()


def test_character_likeness():
    """
    Test: Add all images from pictures folder. Create a character. Assign some reference pictures to character.
    List pictures by character likeness and verify that unassigned pictures are ordered by likeness to character.
    """

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server_config.json")

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
            # server.vault.import_default_data()
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            server.vault.start_workers(
                {
                    WorkerType.FACE,
                }
            )

            # Upload all images as new pictures
            picture_ids = []
            id_to_filename = {}
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    import_status = upload_pictures_and_wait(client, files)
                assert import_status["status"] == "completed"
                assert import_status["results"][0]["status"] == "success"
                id_to_filename[import_status["results"][0]["picture_id"]] = fname
                picture_ids.append(import_status["results"][0]["picture_id"])

            # Create a character
            char_name = "Test Character"
            char_resp = client.post("/characters", json={"name": char_name})
            assert char_resp.status_code == 200, (
                f"Failed to create character: {char_resp.text}"
            )
            char_id = (
                char_resp.json()["id"]
                if "id" in char_resp.json()
                else char_resp.json().get("character", {}).get("id")
            )

            # Assemble reference pictures
            reference_picture_ids = []
            for id, filename in id_to_filename.items():
                if filename.startswith("Reference"):
                    reference_picture_ids.append(id)

            # Assign the reference pictures to the character's reference set using POST /picture_sets/{id}/members/{picture_id}
            # First, get the character summary to retrieve the reference_picture_set_id
            summary_resp = client.get(f"/characters/{char_id}/summary")
            assert summary_resp.status_code == 200, (
                f"Failed to get character summary: {summary_resp.text}"
            )
            reference_picture_set_id = summary_resp.json().get(
                "reference_picture_set_id"
            )
            assert reference_picture_set_id, (
                f"Character summary did not return reference_picture_set_id: {summary_resp.json()}"
            )
            for ref_pid in reference_picture_ids:
                add_resp = client.post(
                    f"/picture_sets/{reference_picture_set_id}/members/{ref_pid}"
                )
                assert add_resp.status_code == 200, (
                    f"Failed to add picture {ref_pid} to reference set {reference_picture_set_id}: {add_resp.text}"
                )

            all_face_ids = set()
            for pid in picture_ids:
                logging.debug(f"Facial features processed for picture ID: {pid}")

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

                for face in faces_data:
                    all_face_ids.add(face["id"])

            # Assign the faces in the reference pictures to the character
            ref_face_ids = []
            for ref_pid in reference_picture_ids:
                faces_resp = client.get(f"/pictures/{ref_pid}/faces")
                assert faces_resp.status_code == 200, (
                    f"Failed to get faces for {ref_pid}"
                )
                for face in faces_resp.json().get("faces", []):
                    ref_face_ids.append(face["id"])
            if ref_face_ids:
                assign_resp = client.post(
                    f"/characters/{char_id}/faces", json={"face_ids": ref_face_ids}
                )
                assert assign_resp.status_code == 200, (
                    f"Failed to assign faces to character: {assign_resp.text}"
                )
                logger.info(
                    f"Assigned {len(ref_face_ids)} faces from reference pictures to character {char_id}"
                )

            face_character_likeness_futures = []
            for face_id in all_face_ids:
                face_character_likeness_futures.append(
                    (
                        char_id,
                        face_id,
                        server.vault.get_worker_future(
                            WorkerType.FACE_CHARACTER_LIKENESS,
                            FaceCharacterLikeness,
                            (char_id, face_id),
                            "pair",
                        ),
                    )
                )

            # Start the FaceCharacterLikenessWorker
            server.vault.start_workers({WorkerType.FACE_CHARACTER_LIKENESS})

            logger.info("Waiting for facial likeness to be processed...")
            face_character_likeness_pairs = []
            # Debug logging for worker futures
            logger.debug("FaceCharacterLikeness futures:")
            for char_id, face_id, future in face_character_likeness_futures:
                logger.debug(
                    f"Future for pair (char_id={char_id}, face_id={face_id}): {future}"
                )

            # Debug logging before waiting for futures
            logger.debug(
                "Waiting for FaceCharacterLikenessWorker futures to complete..."
            )
            for char_id, face_id, future in face_character_likeness_futures:
                logger.info(
                    "Waiting for facial likeness pair: (%s, %s)", char_id, face_id
                )
                result = future.result(timeout=240)
                assert result is not None, "FaceCharacterLikenessWorker timed out"
                face_character_likeness_pairs.append(result)

            assert len(face_character_likeness_pairs) == len(all_face_ids), (
                "Not all face character likeness pairs were computed."
            )

            server.vault.stop_workers()

            # Call the GET /pictures endpoint with sort=character_likeness and character_id=<character_id>
            start = time()
            pics_resp = client.get(
                "/pictures",
                params={
                    "sort": "CHARACTER_LIKENESS",
                    "reference_character_id": char_id,
                },
            )
            end = time()
            assert pics_resp.status_code == 200, (
                f"Failed to get pictures by character likeness: {pics_resp.text}"
            )
            logger.info(
                f"Fetched pictures sorted by character likeness in {end - start:.2f} seconds"
            )
            pics = pics_resp.json()
            # If response is wrapped in {"pictures": [...]}, unwrap
            if isinstance(pics, dict) and "pictures" in pics:
                pics = pics["pictures"]

            assert pics, (
                "No pictures returned from /pictures sorted by character likeness"
            )

            # Debug logging for fetched pictures
            logger.debug("Fetched pictures:")
            for picture in pics:
                logger.debug(
                    f"Picture: {picture['id']}, Likeness: {picture.get('character_likeness')}"
                )

            # Print the ordered list of pictures with their likeness scores
            logger.info("\nOrdered pictures by character likeness:")
            for pic in pics:
                fname = id_to_filename.get(pic["id"], pic["id"])
                score = (
                    pic.get("character_likeness")
                    or pic.get("likeness_score")
                    or pic.get("score")
                )
                logger.info(f"{fname}: {score}")

            # Verify that unassigned pictures are returned ordered by likeness to the character
            # Reference pictures should be at the top (or have max score), others should be sorted by likeness
            unassigned = [pic for pic in pics if pic["id"] not in reference_picture_ids]
            likeness_scores = [
                pic.get("character_likeness")
                or pic.get("likeness_score")
                or pic.get("score")
                for pic in unassigned
            ]
            likeness_scores = [s for s in likeness_scores if s is not None]
            assert likeness_scores, "No likeness scores found for unassigned pictures"
            assert likeness_scores == sorted(likeness_scores, reverse=True), (
                "Unassigned pictures are not ordered by likeness to the character"
            )
