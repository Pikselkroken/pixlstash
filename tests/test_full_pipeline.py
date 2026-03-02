"""
End-to-end pipeline test.

Uploads every image from the pictures/ directory and waits for all automatic
background tasks to complete, then asserts that each expected field is
populated on every picture.

Tasks covered:
    - FeatureExtractionTask (FACE)       → Face records exist per picture
    - TagTask                (TAGGER)    → Picture.tags populated
    - QualityTask            (QUALITY)   → Quality record linked to picture
    - FaceQualityTask        (FACE_QUALITY) → Quality record linked to each real face
    - ImageEmbeddingTask     (IMAGE_EMBEDDING) → Picture.image_embedding populated
    - DescriptionTask        (DESCRIPTION)     → Picture.description populated
    - TextEmbeddingTask      (TEXT_EMBEDDING)  → Picture.text_embedding populated

Tasks covered:
    - LikenessParametersTask (LIKENESS_PARAMETERS) → Picture.likeness_parameters and
                                                    size_bin_index populated
    - LikenessTask           (LIKENESS)            → PictureLikeness pairs scored
                                                    and queue drained

Tasks intentionally excluded (require external setup):
    - WatchFolderImportTask — needs watch folder config
"""

import gc
import os
import tempfile
import time

from fastapi.testclient import TestClient
from sqlmodel import func, select

from pixlvault.db_models import Face, Picture, Quality
from pixlvault.db_models.picture_likeness import PictureLikeness
from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.tasks.likeness_task import LikenessTask
from pixlvault.tasks.quality_task import QualityTask
from pixlvault.tasks.task_type import TaskType
from pixlvault.utils.likeness.likeness_params import PictureLikenessParameterUtils
from tests.utils import upload_pictures_and_wait

logger = get_logger(__name__)

_PICTURES_DIR = os.path.join(os.path.dirname(__file__), "../pictures")
_TASK_TIMEOUT_S = 180


def _poll_until_zero(server, count_fn, label, timeout_s=_TASK_TIMEOUT_S, interval=0.5):
    """Poll a count function (called in a DB read task) until it returns 0."""
    start = time.time()
    while time.time() - start < timeout_s:
        remaining = server.vault.db.run_immediate_read_task(count_fn)
        if remaining == 0:
            return
        time.sleep(interval)
    raise AssertionError(
        f"Timed out after {timeout_s}s waiting for {label}: {remaining} still pending"
    )


def _poll_until_nonzero(
    server, count_fn, label, timeout_s=_TASK_TIMEOUT_S, interval=0.5
):
    """Poll a count function until it returns > 0 (task has produced output)."""
    start = time.time()
    while time.time() - start < timeout_s:
        value = server.vault.db.run_immediate_read_task(count_fn)
        if (value or 0) > 0:
            return
        time.sleep(interval)
    raise AssertionError(
        f"Timed out after {timeout_s}s waiting for {label} to produce output"
    )


def test_full_pipeline_on_real_pictures():
    """Upload all pictures from pictures/ and verify every automatic pipeline task completes."""

    image_files = sorted(
        f
        for f in os.listdir(_PICTURES_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )
    assert image_files, f"No test images found in {_PICTURES_DIR}"

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")

        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # ------------------------------------------------------------------ #
            # Upload all pictures in a single batch so the WorkPlanner sees the
            # full set before any per-image tasks fire.
            # ------------------------------------------------------------------ #
            files = []
            for fname in image_files:
                with open(os.path.join(_PICTURES_DIR, fname), "rb") as f:
                    files.append(("file", (fname, f.read(), "image/png")))

            import_status = upload_pictures_and_wait(client, files, timeout_s=120)
            assert import_status["status"] == "completed", (
                f"Batch import failed: {import_status}"
            )
            picture_ids = []
            for result in import_status["results"]:
                assert result["status"] == "success", f"Import result failure: {result}"
                picture_ids.append(result["picture_id"])

            n = len(picture_ids)
            logger.info("Uploaded %d pictures; waiting for pipeline tasks…", n)

            # ------------------------------------------------------------------ #
            # Register first-wave futures (no prerequisites)
            # ------------------------------------------------------------------ #
            face_futures = {
                pid: server.vault.get_worker_future(
                    TaskType.FACE, Picture, pid, "faces"
                )
                for pid in picture_ids
            }
            tag_futures = {
                pid: server.vault.get_worker_future(
                    TaskType.TAGGER, Picture, pid, "tags"
                )
                for pid in picture_ids
            }
            img_emb_futures = {
                pid: server.vault.get_worker_future(
                    TaskType.IMAGE_EMBEDDING, Picture, pid, "image_embedding"
                )
                for pid in picture_ids
            }
            desc_futures = {
                pid: server.vault.get_worker_future(
                    TaskType.DESCRIPTION, Picture, pid, "description"
                )
                for pid in picture_ids
            }

            # ------------------------------------------------------------------ #
            # Wait for face extraction, then register face-quality futures
            # ------------------------------------------------------------------ #
            for pid, future in face_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Face extraction complete for all %d pictures.", n)

            real_face_ids = server.vault.db.run_immediate_read_task(
                lambda session: [
                    f.id
                    for f in session.exec(
                        select(Face).where(Face.face_index != -1)
                    ).all()
                ]
            )
            face_quality_futures = {
                fid: server.vault.get_worker_future(
                    TaskType.FACE_QUALITY, Face, fid, "quality"
                )
                for fid in real_face_ids
            }
            logger.info(
                "Registered face-quality futures for %d real faces.",
                len(real_face_ids),
            )

            # ------------------------------------------------------------------ #
            # Wait for tags and image embeddings
            # ------------------------------------------------------------------ #
            for pid, future in tag_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Tagging complete for all pictures.")

            for pid, future in img_emb_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Image embeddings complete for all pictures.")

            # ------------------------------------------------------------------ #
            # Wait for descriptions (prerequisite for text embeddings)
            # ------------------------------------------------------------------ #
            for pid, future in desc_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Descriptions complete for all pictures.")

            # ------------------------------------------------------------------ #
            # Register and wait for text embeddings
            # ------------------------------------------------------------------ #
            txt_emb_futures = {
                pid: server.vault.get_worker_future(
                    TaskType.TEXT_EMBEDDING, Picture, pid, "text_embedding"
                )
                for pid in picture_ids
            }
            for pid, future in txt_emb_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Text embeddings complete for all pictures.")

            # ------------------------------------------------------------------ #
            # Poll until picture quality reaches zero missing (no per-picture future)
            # ------------------------------------------------------------------ #
            _poll_until_zero(
                server, QualityTask.count_missing_quality, "picture quality"
            )
            logger.info("Picture quality scoring complete.")

            # ------------------------------------------------------------------ #
            # Wait for face quality
            # ------------------------------------------------------------------ #
            for fid, future in face_quality_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Face quality scoring complete for all real faces.")

            # ------------------------------------------------------------------ #
            # Poll until all likeness parameters are computed
            # (depends on quality metrics and image embeddings being ready)
            # ------------------------------------------------------------------ #
            _poll_until_zero(
                server,
                PictureLikenessParameterUtils.count_pending_parameters,
                "likeness parameters",
            )
            logger.info("Likeness parameters complete for all pictures.")

            # ------------------------------------------------------------------ #
            # Wait for LikenessTask to process the queue and produce pairs
            # (queue is seeded from within the task once parameters are ready)
            # ------------------------------------------------------------------ #
            def count_likeness_pairs(session):
                result = session.exec(
                    select(func.count()).select_from(PictureLikeness)
                ).one()
                return int(
                    result[0] if isinstance(result, (tuple, list)) else result or 0
                )

            _poll_until_nonzero(server, count_likeness_pairs, "likeness pairs")
            _poll_until_zero(server, LikenessTask.count_queue, "likeness queue")
            logger.info("LikenessTask queue drained; likeness pairs written.")

            # ------------------------------------------------------------------ #
            # Assertions — fetch all data in a single session
            # ------------------------------------------------------------------ #
            def fetch_picture_data(session):
                pics = session.exec(
                    select(Picture).where(Picture.id.in_(picture_ids))
                ).all()
                rows = []
                for pic in pics:
                    # Access relationships within the session so lazy loads succeed
                    tags = list(pic.tags)
                    # Use an explicit filtered query rather than the lazily-loaded
                    # relationship to guarantee we get the picture-level quality row
                    # (face_id IS NULL) and not a face quality row.
                    quality = session.exec(
                        select(Quality).where(
                            Quality.picture_id == pic.id,
                            Quality.face_id.is_(None),
                        )
                    ).first()
                    face_count = session.exec(
                        select(func.count())
                        .select_from(Face)
                        .where(Face.picture_id == pic.id)
                    ).one()
                    rows.append(
                        {
                            "id": pic.id,
                            "file_path": pic.file_path,
                            "image_embedding": pic.image_embedding,
                            "text_embedding": pic.text_embedding,
                            "description": pic.description,
                            "tag_count": len(tags),
                            "quality": quality,
                            "face_count": int(face_count),
                            "likeness_parameters": pic.likeness_parameters,
                            "size_bin_index": pic.size_bin_index,
                        }
                    )
                return rows

            rows = server.vault.db.run_immediate_read_task(fetch_picture_data)

            failures = []
            for row in rows:
                name = os.path.basename(row["file_path"])

                checks = {
                    "image_embedding": row["image_embedding"] is not None,
                    "description": row["description"] is not None,
                    "text_embedding": row["text_embedding"] is not None,
                    "quality record": row["quality"] is not None,
                    "face records": row["face_count"] > 0,
                    "likeness_parameters": row["likeness_parameters"] is not None,
                    "size_bin_index": row["size_bin_index"] is not None,
                }
                failed = [k for k, ok in checks.items() if not ok]
                if failed:
                    failures.append(f"{name}: missing {', '.join(failed)}")

                logger.info(
                    "[%s] %s — tags=%d, desc=%s, img_emb=%s, txt_emb=%s, "
                    "quality=%s, faces=%d, lk_params=%s, size_bin=%s",
                    "FAIL" if failed else "OK",
                    name,
                    row["tag_count"],
                    "yes" if row["description"] else "NO",
                    "yes" if row["image_embedding"] else "NO",
                    "yes" if row["text_embedding"] else "NO",
                    "yes" if row["quality"] else "NO",
                    row["face_count"],
                    "yes" if row["likeness_parameters"] is not None else "NO",
                    "yes" if row["size_bin_index"] is not None else "NO",
                )

            assert not failures, (
                f"Pipeline incomplete for {len(failures)}/{n} pictures:\n"
                + "\n".join(failures)
            )
            logger.info("All %d pictures passed full pipeline assertions.", n)

    gc.collect()
