import logging
import os
import tempfile

import gc
from time import time

from fastapi.testclient import TestClient

from pixlvault.db_models.face_character_likeness import FaceCharacterLikeness
from pixlvault.db_models.face_likeness import FaceLikeness
from pixlvault.db_models.picture_likeness import PictureLikeness
from pixlvault.db_models.picture import Picture
from pixlvault.pixl_logging import get_logger
from pixlvault.worker_registry import WorkerType
from pixlvault.server import Server

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

            server.vault.start_workers(
                {
                    WorkerType.QUALITY,
                }
            )

            # Upload all images as new pictures
            picture_ids = []
            face_futures = []
            picture_likeness_futures = []
            id_to_filename = {}
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    r = client.post("/pictures", files=files)
                assert r.status_code == 200
                resp = r.json()
                assert resp["results"][0]["status"] == "success"
                id_to_filename[resp["results"][0]["picture_id"]] = fname
                picture_ids.append(resp["results"][0]["picture_id"])
                face_futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACE, Picture, picture_ids[-1], "faces"
                    )
                )
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
                    WorkerType.FACE,
                }
            )

            # Wait for facial features to be processed
            all_face_ids = set()
            for idx, future in enumerate(face_futures):
                pid, _ = future.result(timeout=120)
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

            face_likeness_futures = []
            for face_id1 in all_face_ids:
                for face_id2 in all_face_ids:
                    if face_id2 > face_id1:
                        face_likeness_futures.append(
                            (
                                face_id1,
                                face_id2,
                                server.vault.get_worker_future(
                                    WorkerType.FACE_LIKENESS,
                                    FaceLikeness,
                                    (face_id1, face_id2),
                                    "pair",
                                ),
                            )
                        )

            server.vault.start_workers({WorkerType.FACE_LIKENESS})

            logger.info("Waiting for likeness to be processed...")

            likeness_pairs = []
            for pid1, pid2, future in picture_likeness_futures:
                logger.info("Waiting for picture likeness pair : (%s, %s)", pid1, pid2)
                result = future.result(timeout=60)
                assert result is not None, "LikenessWorker timed out"
                likeness_pairs.append(result)
                logger.info("Picture likeness computed: %s", result)

            logger.info("Waiting for facial likeness to be processed...")
            face_likeness_pairs = []
            for face_id1, face_id2, future in face_likeness_futures:
                logger.debug(
                    "Waiting for facial likeness pair: (%s, %s)", face_id1, face_id2
                )
                result = future.result(timeout=60)
                assert result is not None, "FaceLikenessWorker timed out"
                face_likeness_pairs.append(result)

            assert (
                len(likeness_pairs) == (len(picture_ids) * (len(picture_ids) - 1)) // 2
            ), "Not all picture likeness pairs were computed."
            assert (
                len(face_likeness_pairs)
                == (len(all_face_ids) * (len(all_face_ids) - 1)) // 2
            ), "Not all face likeness pairs were computed."

            # Log DB contents for likeness and face likeness
            likeness_rows = server.vault.db.run_task(PictureLikeness.find)
            face_likeness_rows = server.vault.db.run_task(
                lambda session: session.exec(
                    FaceLikeness.__table__.select().order_by(
                        FaceLikeness.likeness.desc()
                    )
                ).all()
            )
            logger.info(
                f"PictureLikeness table rows: {[{'a': r.picture_id_a, 'b': r.picture_id_b, 'likeness': r.likeness} for r in likeness_rows]}"
            )
            logger.info(
                f"FaceLikeness table rows: {[{'a': r.face_id_a, 'b': r.face_id_b, 'likeness': r.likeness} for r in face_likeness_rows]}"
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

            # Build a dict of dicts for likeness values
            likeness_table = {pid: {} for pid in pic_ids}
            for stack in stacks_data.get("stacks", []):
                matrix = stack.get("likeness_matrix", {})
                for key, score in matrix.items():
                    id_a, id_b = map(int, key.split("|", 1))
                    likeness_table.setdefault(id_a, {})[id_b] = score
                    likeness_table.setdefault(id_b, {})[id_a] = score
            # Log as a text table using descriptions
            header = [" "] + [id_to_filename[pid] for pid in pic_ids]
            rows = []
            for pid_a in pic_ids:
                row = [id_to_filename[pid_a]]
                for pid_b in pic_ids:
                    if pid_a == pid_b:
                        row.append("--")
                    else:
                        val = likeness_table.get(pid_a, {}).get(pid_b)
                        row.append(f"{val:.3f}" if val is not None else "")
                rows.append(row)
            # Format as aligned text
            col_widths = [
                max(len(str(cell)) for cell in col) for col in zip(*([header] + rows))
            ]

            def fmt_row(row):
                return " | ".join(
                    str(cell).ljust(w) for cell, w in zip(row, col_widths)
                )

            table_str = "\n".join([fmt_row(header)] + [fmt_row(r) for r in rows])
            logger.info(
                "\nPicture-to-picture likeness table (from /pictures/stacks):\n%s",
                table_str,
            )

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

            # Upload all images as new pictures
            picture_ids = []
            face_futures = []
            id_to_filename = {}
            for fname in image_files:
                with open(os.path.join(src_dir, fname), "rb") as f:
                    files = [("file", (fname, f.read(), "image/png"))]
                    r = client.post("/pictures", files=files)
                assert r.status_code == 200
                resp = r.json()
                assert resp["results"][0]["status"] == "success"
                id_to_filename[resp["results"][0]["picture_id"]] = fname
                picture_ids.append(resp["results"][0]["picture_id"])
                face_futures.append(
                    server.vault.get_worker_future(
                        WorkerType.FACE, Picture, picture_ids[-1], "faces"
                    )
                )

            server.vault.start_workers(
                {
                    WorkerType.FACE,
                }
            )

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
            for idx, future in enumerate(face_futures):
                pid, faces = future.result(timeout=120)
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
            server.vault.start_workers(
                {WorkerType.FACE_LIKENESS, WorkerType.FACE_CHARACTER_LIKENESS}
            )

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
