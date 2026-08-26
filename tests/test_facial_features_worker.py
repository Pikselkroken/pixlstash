from PIL import Image
import io

import gc
import json
import logging
import sys
import time
import tempfile
import os
from types import SimpleNamespace

from pixlstash.inference.vram_budget import VramBudget

import cv2
import numpy as np
import pytest

from pixlstash.db_models import Character, Face, Picture
from sqlmodel import select
from pixlstash.server import Server
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.insightface_model_utils import DEFAULT_MODEL_PACK
from pixlstash.utils.insightface_batched import BatchedFaceRunner


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
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(server_config_path) as server:
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
                        TaskType.FACE_EXTRACTION, Picture, pic.id, "faces"
                    )
                )

            # Wait for all face detection futures to complete
            results = [future.result(timeout=60) for future in futures]
            assert all(results), "Not all pictures were processed in time"

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
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(server_config_path) as server:
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
                        TaskType.FACE_EXTRACTION, Picture, pic.id, "faces"
                    )
                )

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

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            response = client.get(f"/characters/{char.id}/thumbnail")
            assert response.status_code == 200, (
                f"Thumbnail endpoint failed: {response.status_code}"
            )
            assert response.headers["content-type"] == "image/png"

            # Load the image from response
            thumb_img = Image.open(io.BytesIO(response.content))
            # Get the best face and crop from the database
            picture_ids = {
                face.picture_id for face in char.faces if face.picture_id is not None
            }

            def fetch_picture_scores(session, ids):
                if not ids:
                    return {}
                rows = session.exec(
                    select(Picture.id, Picture.score).where(Picture.id.in_(ids))
                ).all()
                return {pid: (score or 0) for pid, score in rows}

            score_by_picture_id = server.vault.db.run_task(
                fetch_picture_scores, picture_ids
            )
            best_face = max(
                char.faces,
                key=lambda face: (
                    score_by_picture_id.get(face.picture_id, 0),
                    face.id or 0,
                ),
            )
            # Query the picture for this face (avoid DetachedInstanceError)
            best_pic = server.vault.db.run_task(
                lambda session: session.get(Picture, best_face.picture_id)
            )
            from pixlstash.utils.image_processing.face_utils import FaceUtils

            bbox = best_face.bbox
            logger.info(
                f"Cropping bbox: {bbox} from picture {best_pic.file_path} with description {best_pic.description}"
            )
            crop_img = FaceUtils.crop_face_bbox_exact(
                os.path.join(server.vault.image_root, best_pic.file_path), bbox
            )
            # Save both images for manual inspection
            outdir = os.path.join(
                os.path.dirname(__file__), "..", "tmp", "face_thumbnails"
            )
            os.makedirs(outdir, exist_ok=True)
            thumb_img.save(os.path.join(outdir, f"character_{char.id}_endpoint.png"))
            crop_img.save(os.path.join(outdir, f"character_{char.id}_dbcrop.png"))


def _write_video(path, frames, size=(64, 48)):
    """Write ``frames`` (BGR arrays or solid ``(b, g, r)`` tuples) as an mp4v clip."""
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
    if not writer.isOpened():
        pytest.skip("no OpenCV video encoder available in this environment")
    for frame in frames:
        if isinstance(frame, tuple):
            frame = np.full((size[1], size[0], 3), frame, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def _seek_frames_like_before(path):
    """The frame selection the batch loop used to do inline, seek by seek."""
    cap = cv2.VideoCapture(path)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    ret, frame = cap.read()
    if ret and frame is not None:
        frames.append((0, frame))
    step = max(1, frame_count // 3)
    for frame_index in range(step, frame_count, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ret, frame = cap.read()
        if ret and frame is not None:
            frames.append((frame_index, frame))
    cap.release()
    return frames


def test_video_preload_picks_the_frames_the_batch_loop_seeked():
    """Preloaded video frames are exactly frame 0 and the 1/3-mark frames."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Each clip's blue channel is (index // step) * 60, so a frame's colour
        # says which third it came from and a wrong index is visible.
        clips = {}
        for name, frame_count in (
            ("a.mp4", 30),
            ("b.mp4", 31),
            ("c.mp4", 29),
            ("d.mp4", 7),
        ):
            step = max(1, frame_count // 3)
            _write_video(
                os.path.join(temp_dir, name),
                [((i // step) * 60, 0, 0) for i in range(frame_count)],
            )
            clips[name] = (frame_count, step)
        pictures = [
            SimpleNamespace(id=i + 1, file_path=name) for i, name in enumerate(clips)
        ]
        task = FaceExtractionTask(SimpleNamespace(image_root=temp_dir), None, pictures)

        task._preload_images()

        assert set(task._preloaded_images) == {
            os.path.join(temp_dir, name) for name in clips
        }
        for name, (frame_count, step) in clips.items():
            path = os.path.join(temp_dir, name)
            frames, inv_scale = task._preloaded_images[path]
            assert inv_scale == 1.0
            expected_indices = [0] + list(range(step, frame_count, step))
            assert [i for i, _ in frames] == expected_indices, name
            for (index, frame), (ref_index, ref_frame) in zip(
                frames, _seek_frames_like_before(path)
            ):
                assert index == ref_index
                assert np.array_equal(frame, ref_frame), (name, index)
                # The colour proves the decoder handed back frame ``index``.
                assert abs(float(frame[..., 0].mean()) - (index // step) * 60) < 8, (
                    name,
                    index,
                )


def test_video_face_rows_identical_with_and_without_preload():
    """A preloaded batch writes the same Face rows as the synchronous path.

    One clip has a face at frame 0 and a different face at frame 20 with a
    blank third in between; the other is blank throughout and must get exactly
    one sentinel row on both paths.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")
        # No planner: it would run its own FaceExtractionTask over these rows and
        # flip ``_has_faces`` under the second run.
        with open(server_config_path, "w") as fh:
            fh.write(json.dumps({"port": 8000, "disable_background_workers": True}))
        engine = SimpleNamespace(
            insightface_model_pack=DEFAULT_MODEL_PACK,
            force_cpu=bool(Server.DEFAULT_FORCE_CPU),
            keep_models_in_memory=True,
            # The InsightFace init bounds each ORT arena from the engine's
            # budget; a real engine always has one.
            vram_budget=VramBudget("cuda" if not Server.DEFAULT_FORCE_CPU else "cpu"),
        )
        with Server(server_config_path) as server:
            image_root = server.vault.image_root
            os.makedirs(image_root, exist_ok=True)
            src_dir = os.path.join(os.path.dirname(__file__), "..", "pictures")
            size = (576, 448)
            face_a = cv2.resize(
                cv2.imread(os.path.join(src_dir, "TaggerTest.png")), size
            )
            face_b = cv2.resize(
                cv2.imread(os.path.join(src_dir, "TaggerTest3.png")), size
            )
            blank = (128, 128, 128)
            _write_video(
                os.path.join(image_root, "faces.mp4"),
                [face_a] * 10 + [blank] * 10 + [face_b] * 10,
                size,
            )
            _write_video(os.path.join(image_root, "blank.mp4"), [blank] * 30, size)
            # A clip wider than INFERENCE_MAX_SIDE with the face pasted at a
            # known place, so the source-space bbox can be checked.
            big_size = (1280, 720)
            region = (800, 300, 1056, 500)  # x0, y0, x1, y1 in source pixels
            big = np.full((big_size[1], big_size[0], 3), blank, dtype=np.uint8)
            big[region[1] : region[3], region[0] : region[2]] = cv2.resize(
                face_a, (region[2] - region[0], region[3] - region[1])
            )
            _write_video(os.path.join(image_root, "big.mp4"), [big] * 30, big_size)

            def add_pictures(session):
                pics = [
                    Picture(file_path="faces.mp4"),
                    Picture(file_path="blank.mp4"),
                    Picture(file_path="big.mp4"),
                ]
                for pic in pics:
                    session.add(pic)
                session.commit()
                for pic in pics:
                    session.refresh(pic)
                return pics

            pictures = server.vault.db.run_task(add_pictures)
            faces_id, blank_id, big_id = (p.id for p in pictures)

            def rows(task):
                _updates, bulk_faces, _crops = task._extract_features(pictures)
                return sorted(
                    (
                        (
                            f.picture_id,
                            f.frame_index,
                            f.face_index,
                            None if f.bbox is None else [round(v, 1) for v in f.bbox],
                            None
                            if f.features is None
                            else np.frombuffer(f.features, dtype="float32"),
                        )
                        for f in bulk_faces
                    ),
                    key=lambda r: (r[0], r[1], r[2]),
                )

            preloaded_task = FaceExtractionTask(server.vault.db, engine, pictures)
            preloaded_task.on_queued()
            preloaded_task._wait_for_preload()
            assert set(preloaded_task._preloaded_images) == {
                os.path.join(image_root, "faces.mp4"),
                os.path.join(image_root, "blank.mp4"),
                os.path.join(image_root, "big.mp4"),
            }
            big_frames, big_inv_scale = preloaded_task._preloaded_images[
                os.path.join(image_root, "big.mp4")
            ]
            assert big_inv_scale == 1280 / FaceExtractionTask.INFERENCE_MAX_SIDE
            assert all(
                max(f.shape[:2]) <= FaceExtractionTask.INFERENCE_MAX_SIDE
                for _, f in big_frames
            )
            preloaded_rows = rows(preloaded_task)

            sync_task = FaceExtractionTask(server.vault.db, engine, pictures)
            assert sync_task._preloaded_images == {}
            sync_rows = rows(sync_task)

            assert [r[:4] for r in preloaded_rows] == [r[:4] for r in sync_rows]
            for pre, sync in zip(preloaded_rows, sync_rows):
                if pre[4] is None:
                    assert sync[4] is None
                else:
                    assert np.allclose(pre[4], sync[4], atol=1e-4)

            # Identity: one face at frame 0, one at frame 20, nothing at 10.
            assert [r[:2] for r in preloaded_rows if r[0] == faces_id] == [
                (faces_id, 0),
                (faces_id, 20),
            ]
            # The blank clip gets exactly the sentinel row.
            assert [r[1:4] for r in preloaded_rows if r[0] == blank_id] == [
                (0, -1, None)
            ]

            # The big clip's bboxes are written in SOURCE pixels: one face per
            # sampled frame, centred on the pasted region, expanded by 1.25.
            big_rows = [r for r in preloaded_rows if r[0] == big_id]
            assert [r[1] for r in big_rows] == [0, 10, 20]
            for _pid, _frame, _idx, bbox, _emb in big_rows:
                cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                assert region[0] < cx < region[2] and region[1] < cy < region[3], bbox
                assert bbox[2] - bbox[0] < 1.3 * (region[2] - region[0]) + 8, bbox
                assert bbox[3] - bbox[1] < 1.3 * (region[3] - region[1]) + 8, bbox

            # And each frame's faces match the old one-frame-at-a-time call. The
            # clip's frames now share one recogniser call, so the embedding is
            # batch-dependent at float-noise level (~1e-3 on CUDA); compare by
            # cosine, which is how embeddings are consumed.
            runner = BatchedFaceRunner(sync_task._insightface_app)
            frames, _ = preloaded_task._preloaded_images[
                os.path.join(image_root, "faces.mp4")
            ]
            for index, frame in frames:
                per_frame = runner.run_batch([frame])[0]
                stored = [
                    r for r in preloaded_rows if r[0] == faces_id and r[1] == index
                ]
                assert len(per_frame) == len(stored), index
                for face, row in zip(per_frame, stored):
                    cosine = float(np.dot(face.embedding, row[4])) / (
                        np.linalg.norm(face.embedding) * np.linalg.norm(row[4])
                    )
                    assert cosine > 0.999, (index, cosine)
    gc.collect()
