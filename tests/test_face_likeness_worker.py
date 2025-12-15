import json
import tempfile
import os
import time

from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
from pixlvault.face_likeness_worker import FaceLikenessWorker
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.face import Face
from pixlvault.db_models.face_likeness import FaceLikeness

logger = get_logger(__name__)


def test_face_likeness_worker():
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
            f.write(json.dumps(config, indent=2))
        server_config_path = os.path.join(temp_dir, "server-config.json")
        print("Launching server for face likeness worker test...")
        with Server(config_path, server_config_path) as server:
            print("Server started for face likeness worker test.")
            server.vault.import_default_data(add_tagger_test_images=True)

            pics = server.vault.db.run_task(lambda session: Picture.find(session))

            face_futures = []
            for pic in pics:
                print(
                    "Scheduling watch for picture %s with description %s"
                    % (pic.file_path, pic.description)
                )
                if pic.description and pic.description.startswith("Tagger"):
                    face_futures.append(
                        server.vault.get_worker_future(
                            WorkerType.FACE, Picture, pic.id, "faces"
                        )
                    )

            server.vault.start_workers({WorkerType.FACE})
            timeout = time.time() + 60

            all_face_ids = []

            for face_future in face_futures:
                pic_id, face_ids = face_future.result(timeout=timeout - time.time())
                print(
                    f"Face extraction completed for picture ID {pic_id}, extracted face IDs: {face_ids}"
                )
                all_face_ids.extend(face_ids)

            server.vault.stop_workers({WorkerType.FACE})

            all_faces = server.vault.db.run_task(Face.find)
            for face in all_faces:
                assert face.features is not None, f"Face ID {face.id} has no features"

            pairs = []
            ids = sorted(all_face_ids)
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    pairs.append((a, b))

            # Get future objects for each pair
            futures = {}
            for a, b in pairs:
                future = server.vault.get_worker_future(
                    WorkerType.FACE_LIKENESS, FaceLikeness, (a, b), "pair"
                )
                futures[(a, b)] = future

            logger.info(f"Queued {len(futures)} face likeness pairs for processing.")
            server.vault.start_workers({WorkerType.FACE_LIKENESS})

            # Wait for all futures to complete
            timeout = time.time() + 60
            valid_results = 0
            for key, future in futures.items():
                logger.info(f"Waiting for face likeness result for pair {key}...")
                pair, result = future.result(timeout=timeout - time.time())
                assert pair == key
                if result is not None:
                    assert result >= FaceLikenessWorker.MIN_THRESHOLD
                    valid_results += 1
            server.vault.stop_workers({WorkerType.FACE_LIKENESS})

            # Check that all face likeness results are present
            likeness_results = server.vault.db.run_task(
                lambda session: session.exec(
                    FaceLikeness.__table__.select().order_by(
                        FaceLikeness.likeness.desc()
                    )
                ).all()
            )
            result_pairs = set((r.face_id_a, r.face_id_b) for r in likeness_results)
            assert valid_results == len(likeness_results), (
                "Not all face likeness pairs were processed"
            )

            # Print table of face likeness scores with picture descriptions and face indices
            faces = server.vault.db.run_task(
                lambda session: session.exec(Face.__table__.select()).all()
            )
            face_map = {face.id: face for face in faces}
            pic_map = {
                pic.id: pic
                for pic in server.vault.db.run_task(
                    lambda session: Picture.find(
                        session, select_fields=Picture.metadata_fields()
                    )
                )
            }
            logger.info("\nFace Likeness Table:")
            logger.info(
                f"{'Pic Desc A':<30} {'FaceIdx A':<8} | {'Pic Desc B':<30} {'FaceIdx B':<8} | {'Likeness':<10}"
            )
            logger.info("-" * 100)
            for r in likeness_results:
                face_a = face_map.get(r.face_id_a)
                face_b = face_map.get(r.face_id_b)
                desc_a = (
                    pic_map.get(face_a.picture_id).description
                    if face_a and face_a.picture_id in pic_map
                    else "?"
                )
                desc_b = (
                    pic_map.get(face_b.picture_id).description
                    if face_b and face_b.picture_id in pic_map
                    else "?"
                )
                idx_a = face_a.face_index if face_a else "?"
                idx_b = face_b.face_index if face_b else "?"
                logger.info(
                    f"{desc_a:<30} {idx_a!s:<8} | {desc_b:<30} {idx_b!s:<8} | {r.likeness:<10.4f}"
                )
            # Log all the entries that were below the threshold
            for a, b in pairs:
                if (a, b) not in result_pairs:
                    face_a = face_map.get(a)
                    face_b = face_map.get(b)
                    desc_a = (
                        pic_map.get(face_a.picture_id).description
                        if face_a and face_a.picture_id in pic_map
                        else "?"
                    )
                    desc_b = (
                        pic_map.get(face_b.picture_id).description
                        if face_b and face_b.picture_id in pic_map
                        else "?"
                    )
                    idx_a = face_a.face_index if face_a else "?"
                    idx_b = face_b.face_index if face_b else "?"
                    logger.info(
                        f"{desc_a:<30} {idx_a!s:<8} | {desc_b:<30} {idx_b!s:<8} | {'Below Threshold':<10}"
                    )
