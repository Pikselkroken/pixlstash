"""
End-to-end pipeline test.

Uploads every image from the pictures/ directory and waits for all automatic
background tasks to complete, then asserts that each expected field is
populated on every picture.

Tasks covered:
    - FaceExtractionTask     (FACE_EXTRACTION) → Face records exist per picture
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
import math
import os
import tempfile
import time

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlmodel import func, select

from pixlstash.db_models import Face, Picture, Quality
from pixlstash.db_models.picture_likeness import PictureLikeness
from pixlstash.pixl_logging import get_logger
from pixlstash.server import Server
from pixlstash.tasks.likeness_task import LikenessTask
from pixlstash.tasks.quality_task import QualityTask
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.likeness.likeness_parameter_utils import LikenessParameterUtils
from tests.utils import upload_pictures_and_wait, poll_until_zero

logger = get_logger(__name__)

_PICTURES_DIR = os.path.join(os.path.dirname(__file__), "../pictures")
_SCORES_FILE = os.path.join(_PICTURES_DIR, "scores.txt")
_TASK_TIMEOUT_S = 180
_API_PREFIX = "/api/v1"


def _poll_until_zero(server, count_fn, label, timeout_s=_TASK_TIMEOUT_S, interval=0.5):
    poll_until_zero(server, count_fn, label, timeout_s=timeout_s, interval=interval)


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


def _parse_reference_scores(scores_file: str) -> dict[str, int]:
    result = {}
    with open(scores_file, "r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            parts = text.split()
            if len(parts) != 2:
                continue
            filename, score_text = parts
            try:
                result[filename] = int(score_text)
            except ValueError:
                continue
    return result


def _pearson_corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    den = math.sqrt(den_x * den_y)
    if den <= 0.0:
        return 0.0
    return float(num / den)


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            orig_idx = indexed[k][0]
            ranks[orig_idx] = avg_rank
        i = j
    return ranks


def _spearman_corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    rank_x = _average_ranks(xs)
    rank_y = _average_ranks(ys)
    return _pearson_corr(rank_x, rank_y)


def _format_ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    if not headers:
        return ""
    widths = [len(str(header)) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    def make_row(cells: list[str]) -> str:
        return (
            "| "
            + " | ".join(str(cell).ljust(widths[idx]) for idx, cell in enumerate(cells))
            + " |"
        )

    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    lines = [sep, make_row(headers), sep]
    for row in rows:
        lines.append(make_row(row))
    lines.append(sep)
    return "\n".join(lines)


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
                f"{_API_PREFIX}/login",
                json={"username": "testuser", "password": "testpassword"},
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
                    TaskType.FACE_EXTRACTION, Picture, pid, "faces"
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
                LikenessParameterUtils.count_pending_parameters,
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


def test_smart_score_correlates_with_reference_scores():
    """Verify smart score correlates strongly with reference human scores."""

    image_files = sorted(
        f
        for f in os.listdir(_PICTURES_DIR)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )
    assert image_files, f"No test images found in {_PICTURES_DIR}"

    reference_scores = _parse_reference_scores(_SCORES_FILE)
    assert reference_scores, f"No reference scores parsed from {_SCORES_FILE}"

    source_sha_to_score = {}
    source_sha_to_filename = {}
    for filename, score in reference_scores.items():
        file_path = os.path.join(_PICTURES_DIR, filename)
        if not os.path.exists(file_path):
            continue
        with open(file_path, "rb") as handle:
            sha = ImageUtils.calculate_hash_from_bytes(handle.read())
            source_sha_to_score[sha] = score
            source_sha_to_filename[sha] = filename

    assert source_sha_to_score, "No source hashes with scores could be constructed"

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")

        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login = client.post(
                f"{_API_PREFIX}/login",
                json={"username": "testuser", "password": "testpassword"},
            )
            assert login.status_code == 200

            files = []
            for fname in image_files:
                with open(os.path.join(_PICTURES_DIR, fname), "rb") as handle:
                    files.append(("file", (fname, handle.read(), "image/png")))

            import_status = upload_pictures_and_wait(client, files, timeout_s=120)
            assert import_status["status"] == "completed", (
                f"Batch import failed: {import_status}"
            )

            picture_ids = []
            for result in import_status.get("results", []):
                if result.get("status") in {"success", "duplicate"}:
                    picture_ids.append(result["picture_id"])
            picture_ids = sorted(set(picture_ids))
            assert picture_ids, "No picture ids from import"

            emb_futures = {
                pid: server.vault.get_worker_future(
                    TaskType.IMAGE_EMBEDDING, Picture, pid, "image_embedding"
                )
                for pid in picture_ids
            }
            for future in emb_futures.values():
                future.result(timeout=_TASK_TIMEOUT_S)

            # Quality metrics (sharpness, edge_density, luminance_entropy,
            # text_score) are used by the smart-score formula.  Wait until
            # every imported picture has a quality row before querying.
            _poll_until_zero(
                server, QualityTask.count_missing_quality, "picture quality"
            )

            def fetch_imported_picture_shas(session):
                pics = session.exec(
                    select(Picture).where(Picture.id.in_(picture_ids))
                ).all()
                return [
                    {
                        "id": pic.id,
                        "pixel_sha": pic.pixel_sha,
                        "imported_file": os.path.basename(pic.file_path or ""),
                    }
                    for pic in pics
                    if pic.id is not None and pic.pixel_sha
                ]

            imported_rows = server.vault.db.run_immediate_read_task(
                fetch_imported_picture_shas
            )
            assert imported_rows, "Could not fetch imported pictures for score mapping"

            expected_score_by_picture_id = {}
            expected_source_name_by_picture_id = {}
            imported_name_by_picture_id = {}
            for row in imported_rows:
                score = source_sha_to_score.get(row["pixel_sha"])
                if score is not None:
                    expected_score_by_picture_id[row["id"]] = int(score)
                    expected_source_name_by_picture_id[row["id"]] = (
                        source_sha_to_filename.get(row["pixel_sha"], "")
                    )
                    imported_name_by_picture_id[row["id"]] = row.get(
                        "imported_file", ""
                    )

            assert expected_score_by_picture_id, (
                "No imported pictures matched scores.txt via content hash"
            )

            for pic_id, score in expected_score_by_picture_id.items():
                patch_resp = client.patch(
                    f"{_API_PREFIX}/pictures/{pic_id}", json={"score": score}
                )
                assert patch_resp.status_code == 200, patch_resp.text

            # Smart scores may have been pre-computed before user scores were
            # set (no good/bad anchors yet).  Reset them so the background
            # worker recomputes with the now-available user anchors.
            def _reset_smart_scores(session, ids):
                session.execute(
                    update(Picture).where(Picture.id.in_(ids)).values(smart_score=None)
                )
                session.commit()

            server.vault.db.run_task(_reset_smart_scores, picture_ids)

            _poll_until_zero(server, SmartScoreTask.count_remaining, "smart scores")

            smart_resp = client.get(
                f"{_API_PREFIX}/pictures",
                params={
                    "sort": "SMART_SCORE",
                    "descending": "true",
                    "offset": 0,
                    "limit": 10000,
                },
            )
            assert smart_resp.status_code == 200, smart_resp.text

            smart_results = smart_resp.json() or []
            smart_score_by_id = {
                int(row.get("id")): float(row.get("smartScore"))
                for row in smart_results
                if row.get("id") is not None and row.get("smartScore") is not None
            }

            common_ids = [
                pid
                for pid in expected_score_by_picture_id.keys()
                if pid in smart_score_by_id
            ]
            assert len(common_ids) >= 8, (
                f"Too few points for correlation check: {len(common_ids)}"
            )

            expected_values = [
                float(expected_score_by_picture_id[pid]) for pid in common_ids
            ]
            smart_values = [float(smart_score_by_id[pid]) for pid in common_ids]

            pearson = _pearson_corr(expected_values, smart_values)
            spearman = _spearman_corr(expected_values, smart_values)

            scored_rows = []
            for pid in common_ids:
                scored_rows.append(
                    [
                        str(pid),
                        expected_source_name_by_picture_id.get(pid, ""),
                        imported_name_by_picture_id.get(pid, ""),
                        f"{expected_score_by_picture_id[pid]:.2f}",
                        f"{smart_score_by_id[pid]:.4f}",
                    ]
                )

            scored_rows.sort(key=lambda row: (row[1], row[0]))

            score_table = _format_ascii_table(
                ["Picture ID", "Source File", "Imported File", "Expected", "Smart"],
                scored_rows,
            )
            coeff_table = _format_ascii_table(
                ["Coefficient", "Value"],
                [
                    ["Pearson", f"{pearson:.4f}"],
                    ["Spearman", f"{spearman:.4f}"],
                    ["Sample Size", str(len(common_ids))],
                ],
            )

            logger.info("Smart score vs expected table:\n%s", score_table)
            logger.info("Smart score correlation coefficients:\n%s", coeff_table)

            logger.info(
                "Smart score correlation: n=%d pearson=%.4f spearman=%.4f",
                len(common_ids),
                pearson,
                spearman,
            )

            threshold = 0.70
            assert max(pearson, spearman) >= threshold, (
                "Smart score correlation too low: "
                f"pearson={pearson:.4f}, spearman={spearman:.4f}, "
                f"required>={threshold:.2f}"
            )

    gc.collect()
