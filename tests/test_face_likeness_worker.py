import json
import tempfile
import os
import time

from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
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
            for face_future in face_futures:
                result = face_future.result(timeout=timeout - time.time())

            server.vault.stop_workers({WorkerType.FACE})

            # Now, for each TaggerTest* picture, issue and wait for facial feature futures for each face
            feature_futures = []
            feature_future_info = []
            pics = server.vault.db.run_task(lambda session: Picture.find(session))
            for pic in pics:
                if pic.description and pic.description.startswith("TaggerTest"):
                    faces = server.vault.db.run_task(
                        lambda session: Face.find(session, picture_id=pic.id)
                    )
                    for face in faces:
                        if face.face_index < 0:
                            continue  # Skip sentinels
                        feature_futures.append(
                            server.vault.get_worker_future(
                                WorkerType.FACIAL_FEATURES, Face, face.id, "features"
                            )
                        )
                        feature_future_info.append(
                            (pic.description, face.face_index, face.id)
                        )

            server.vault.start_workers({WorkerType.FACIAL_FEATURES})

            logger.info("Waiting for features for the following faces:")
            for desc, idx, fid in feature_future_info:
                logger.info(f"  Picture: {desc}, face_index: {idx}, face_id: {fid}")
            # Wait for all feature futures to complete
            for (desc, idx, fid), future in zip(feature_future_info, feature_futures):
                logger.info(
                    f"#### Waiting for features: Picture '{desc}', face_index {idx}, face_id {fid}"
                )
                result = future.result(timeout=60)
                logger.info(
                    f"#### Done: Picture '{desc}', face_index {idx}, face_id {fid}, result: {result}"
                )

            server.vault.stop_workers({WorkerType.FACIAL_FEATURES})

            logger.info(
                "All faces extracted. Gathering pairs for likeness computation."
            )

            # Get all faces
            faces = server.vault.db.run_task(lambda session: Face.find(session))
            assert faces and len(faces) >= 2, (
                "No faces found in the database. Test requires at least two faces."
            )
            # Get all unique face pairs (a < b)
            pairs = []
            ids = sorted([face.id for face in faces])
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    if (
                        faces[i].features is not None
                        and faces[ids.index(b)].features is not None
                    ):
                        pairs.append((a, b))
                    else:
                        logger.warning(f"Skipping pair ({a}, {b}): missing features.")
            # Get future objects for each pair
            futures = {}
            for a, b in pairs:
                future = server.vault.get_worker_future(
                    WorkerType.FACE_LIKENESS, FaceLikeness, (a, b), "pair"
                )
                futures[(a, b)] = future
                server.vault.queue_face_likeness_pair_calculation(a, b)

            logger.info(f"Queued {len(futures)} face likeness pairs for processing.")
            server.vault.start_workers({WorkerType.FACE_LIKENESS})

            # Wait for all futures to complete
            timeout = time.time() + 60
            for key, future in futures.items():
                result = future.result(timeout=timeout - time.time())
                assert result == key
            server.vault.stop_workers({WorkerType.FACE_LIKENESS})

            # Check that all face likeness results are present
            likeness_results = server.vault.db.run_task(
                lambda session: session.exec(FaceLikeness.__table__.select()).all()
            )
            result_pairs = set((r.face_id_a, r.face_id_b) for r in likeness_results)
            assert len(likeness_results) == len(pairs), (
                "Not all face likeness pairs were processed"
            )
            for a, b in pairs:
                assert (a, b) in result_pairs, "Face likeness pair (%s, %s) missing" % (
                    a,
                    b,
                )

            # Print table of face likeness scores with picture descriptions and face indices
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
