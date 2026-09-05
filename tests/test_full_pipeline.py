"""
End-to-end pipeline test.

Uploads every image from the pictures/ directory and waits for all automatic
background tasks to complete, then asserts that each expected field is
populated on every picture.

Tasks covered:
    - FaceExtractionTask     (FACE_EXTRACTION) → Face records exist per picture
    - TagTask                (TAGGER)    → Picture.tags populated
    - QualityTask            (QUALITY)   → Quality record linked to picture
    - ImageEmbeddingTask     (IMAGE_EMBEDDING) → Picture.image_embedding populated
    - DescriptionTask        (DESCRIPTION)     → Picture.description populated
    - TextEmbeddingTask      (TEXT_EMBEDDING)  → Picture.text_embedding populated

Tasks covered:
    - LikenessParametersTask (LIKENESS_PARAMETERS) → Picture.likeness_parameters and
                                                    size_bin_index populated
    - LikenessTask           (LIKENESS)            → PictureLikeness pairs scored
                                                    and queue drained

Tasks intentionally excluded (require external setup):
    - WatchFolderImportTask - needs watch folder config
"""

import gc
import math
import os
import random
import tempfile
import time

import numpy as np
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
from pixlstash.tasks.tag_task import TagTask
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.likeness.likeness_parameter_utils import LikenessParameterUtils
from tests.utils import upload_pictures_and_wait, poll_until_zero

logger = get_logger(__name__)

_PICTURES_DIR = os.path.join(os.path.dirname(__file__), "../pictures")
_SCORES_FILE = os.path.join(_PICTURES_DIR, "scores.txt")
_TASK_TIMEOUT_S = 180
_API_PREFIX = "/api/v1"

# Cosine similarity at or above which two fixture pictures count as near-duplicates and
# are held out together (see _near_duplicate_groups).  The fixture separates cleanly at
# this value: the three genuine duplicate pairs sit at 0.998-1.000 and the next-closest
# unrelated pair is at 0.833, so anything in roughly [0.9, 0.99] produces identical
# folds.
_NEAR_DUPLICATE_COSINE = 0.95

# Floor for the one-sided 95% bootstrap lower bound on Kendall tau-b between the smart
# score and the reference labels in pictures/scores.txt.
#
# THIS BAR IS DELIBERATELY LOW.  It records where smart-score quality actually is, not
# where it should be.  Do not "restore" the old 0.70.
#
# History, so the next person does not repeat it: this test used to assert
# max(pearson, spearman) >= 0.70 and had a recorded baseline of 0.853.  That number was
# never valid.  The test writes the reference labels it is grading into Picture.score,
# and Picture.score is exactly what the scorer selects its good (>= 4) and bad (== 1)
# anchors from, so 9 of the 18 pictures were their own anchor at cosine 1.0 and the
# measurement was largely the labels correlated with themselves.  The apparent "drop"
# during 1.8.0 was not a quality regression; it was the anchor term being de-weighted,
# which shrank the leak.  The test now scores each picture with its own label (and its
# near-duplicates' labels) removed from the anchor pool, so the number it reports is a
# real held-out measurement and is much lower than the leaked one ever was.
#
# The statistic changed with it.  `max(pearson, spearman)` gave the assertion two
# independent shots at the bar; Pearson assumes a linear relationship between a bounded,
# non-linearly compressed score and a 5-level ordinal label, which does not hold; and at
# n=18 the sampling error on any correlation is large enough that a point estimate
# cannot support a threshold at all.  So: Kendall tau-b (tie-aware, and 31 of the 153
# label pairs are tied), asserted on the pessimistic end of a seeded bootstrap rather
# than on the point estimate.
#
# Where the bar comes from.  The leak-free measurement on this fixture is
# tau-b = 0.410 with a 95% lower bound of 0.054 (n=18, 15 folds).  A jackknife shows a
# single picture's behaviour is worth up to ~0.15 of that bound at n=18, so the bar has
# to sit *below* the noise band, not just below the measurement: 0.054 - 0.15 is
# negative, so a bar at 0.0 would fail when one image's tag detections move rather than
# when the scorer regresses.  Hence -0.10 -- comfortably outside one picture's leverage,
# while still meaning "the score is not inverted and has not collapsed".  That is a
# coarse floor by construction: it catches the scorer inverting, collapsing, or losing
# its anchors, not fine-grained drift.  A meaningfully tighter bar needs a bigger
# labelled fixture, not a bolder number -- at n=18 there is no threshold between -0.10
# and 0.41 that a single image cannot cross on its own.
#
# Raising this bar is real work on the scorer -- chiefly anomaly-detector precision, and
# how much a single unstable detection is allowed to move a picture -- not a matter of
# re-tuning weights until the number looks better.  Re-weighting was swept and cannot
# reach 0.70.  Changing the bar upward is only legitimate alongside a scoring change
# that earns it.
# Note this file is in DEFERRED_FROM_GATE (tests/test_ci_shards.py), so this assertion
# does not block a PR: its only CI home is the release-prep sweep, which is
# informational.  That is how it stayed red at 0.55-vs-0.70 for a whole release cycle.
_MIN_TAU_B_LOWER_BOUND = -0.10


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


def _kendall_tau_b(xs: list[float], ys: list[float]) -> float:
    """Kendall's tau-b between two sequences, tie-corrected in both variables.

    tau-b is the right coefficient here: the reference labels are a 5-level ordinal
    scale with heavy ties (18 labels over 5 levels leaves 31 of the 153 pairs tied on
    the label side), and the smart score is a bounded, non-linearly compressed value,
    so neither Pearson's linearity assumption nor tau-a's tie-blind denominator holds.
    tau-b answers exactly the question the feature is judged on: given two pictures, how
    often does the score order them the way the human did?

    Args:
        xs: First sequence (e.g. reference labels).
        ys: Second sequence (e.g. smart scores), same length as ``xs``.

    Returns:
        tau-b in [-1, 1], or 0.0 when it is undefined (fewer than two points, or one
        sequence entirely tied).
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return 0.0
    concordant_minus_discordant = 0
    tied_x_pairs = 0
    tied_y_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx = xs[i] - xs[j]
            dy = ys[i] - ys[j]
            if dx == 0 and dy == 0:
                tied_x_pairs += 1
                tied_y_pairs += 1
            elif dx == 0:
                tied_x_pairs += 1
            elif dy == 0:
                tied_y_pairs += 1
            elif (dx > 0) == (dy > 0):
                concordant_minus_discordant += 1
            else:
                concordant_minus_discordant -= 1
    total_pairs = n * (n - 1) // 2
    denominator = math.sqrt((total_pairs - tied_x_pairs) * (total_pairs - tied_y_pairs))
    if denominator <= 0.0:
        return 0.0
    return float(concordant_minus_discordant / denominator)


def _bootstrap_tau_b_lower_bound(
    xs: list[float],
    ys: list[float],
    resamples: int = 4000,
    confidence: float = 0.95,
    seed: int = 20260726,
) -> float:
    """One-sided lower confidence bound on tau-b from a percentile bootstrap.

    The point estimate of a correlation at n=18 is far too noisy to assert on directly
    (a 95% interval around it spans most of the usable range), so the assertion is made
    against this lower bound instead: "even the pessimistic end of the sampling
    distribution is still above the floor". Resampling is paired (a bootstrap draw
    picks whole (label, score) observations) and seeded, so the bound is exactly
    reproducible run to run and the test cannot flake on bootstrap noise alone.

    Args:
        xs: Reference labels.
        ys: Smart scores, paired element-wise with ``xs``.
        resamples: Number of bootstrap resamples.
        confidence: One-sided confidence level; 0.95 returns the 5th percentile.
        seed: Fixed RNG seed, so the returned bound is deterministic.

    Returns:
        The lower confidence bound on tau-b.
    """
    n = len(xs)
    if n != len(ys) or n < 2:
        return 0.0
    rng = random.Random(seed)
    taus = []
    for _ in range(resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        taus.append(_kendall_tau_b([xs[i] for i in idx], [ys[i] for i in idx]))
    taus.sort()
    position = (1.0 - confidence) * (len(taus) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(taus) - 1)
    weight = position - low
    return float(taus[low] * (1.0 - weight) + taus[high] * weight)


def _near_duplicate_groups(
    embeddings_by_id: dict[int, "np.ndarray"], threshold: float
) -> list[list[int]]:
    """Group picture ids into single-linkage clusters of near-duplicate embeddings.

    Leave-one-out over individual pictures is not enough to remove label leakage from
    this fixture: ``Changed{1,2,3}.png`` and ``Reference{1,2,3}.png`` are pairwise
    near-identical (cosine 0.998-1.000) and carry identical labels, so holding out one
    of a pair still leaves its twin in the anchor set at cosine ~1.0: the same leak,
    one step removed. Holding out the whole cluster closes it.

    Args:
        embeddings_by_id: Picture id to its L2-normalisable image embedding vector.
        threshold: Cosine similarity at or above which two pictures are treated as
            near-duplicates and forced into the same held-out group.

    Returns:
        Groups of picture ids, each group sorted, groups ordered by first id.
    """
    ids = sorted(embeddings_by_id)
    parent = {pid: pid for pid in ids}

    def find(pid):
        while parent[pid] != pid:
            parent[pid] = parent[parent[pid]]
            pid = parent[pid]
        return pid

    def union(a, b):
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    normalised = {}
    for pid in ids:
        vec = np.asarray(embeddings_by_id[pid], dtype=np.float64)
        norm = float(np.linalg.norm(vec))
        normalised[pid] = vec / norm if norm > 0 else vec

    for i, pid_a in enumerate(ids):
        for pid_b in ids[i + 1 :]:
            if float(np.dot(normalised[pid_a], normalised[pid_b])) >= threshold:
                union(pid_a, pid_b)

    groups: dict[int, list[int]] = {}
    for pid in ids:
        groups.setdefault(find(pid), []).append(pid)
    return [sorted(group) for group in sorted(groups.values(), key=min)]


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

            # Enable the PixlStash tagger (all tag plugins default to disabled).
            resp = client.patch(
                f"{_API_PREFIX}/users/me/config",
                json={
                    "tagger_settings": {
                        "plugins": {
                            "pixlstash_tagger": {"enabled": True},
                        }
                    }
                },
            )
            assert resp.status_code == 200, f"Failed to enable tagger: {resp.text}"

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
            # Wait for face extraction
            # ------------------------------------------------------------------ #
            for pid, future in face_futures.items():
                future.result(timeout=_TASK_TIMEOUT_S)
            logger.info("Face extraction complete for all %d pictures.", n)

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
            # Assertions - fetch all data in a single session
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
                    # relationship to get the picture-level quality row.
                    quality = session.exec(
                        select(Quality).where(
                            Quality.picture_id == pic.id,
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
                    "[%s] %s - tags=%d, desc=%s, img_emb=%s, txt_emb=%s, "
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
    """Measure held-out agreement between the smart score and reference human scores.

    Runs leave-one-group-out cross-validation over ``pictures/scores.txt``: every
    picture is scored with its own reference label, and its near-duplicates' labels,
    removed from the scorer's anchor pool, so no picture can be graded against a label
    it was seeded from.  Asserts on the lower end of a bootstrap interval around
    Kendall tau-b; see ``_MIN_TAU_B_LOWER_BOUND`` for why the bar is where it is.
    """

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

            # Tags (e.g. "bad anatomy") feed the penalised-tag penalty in the
            # smart-score formula.  Wait until all pictures are tagged so the
            # penalty is applied correctly when smart scores are computed.
            _poll_until_zero(server, TagTask.count_missing_tags, "tags")

            def fetch_imported_picture_rows(session):
                pics = session.exec(
                    select(Picture).where(Picture.id.in_(picture_ids))
                ).all()
                rows = []
                for pic in pics:
                    if pic.id is None or not pic.pixel_sha:
                        continue
                    blob = pic.image_embedding
                    embedding = None
                    if blob is not None:
                        if isinstance(blob, (memoryview, bytearray)):
                            blob = bytes(blob)
                        embedding = np.frombuffer(blob, dtype=np.float32).copy()
                    rows.append(
                        {
                            "id": pic.id,
                            "pixel_sha": pic.pixel_sha,
                            "imported_file": os.path.basename(pic.file_path or ""),
                            "image_embedding": embedding,
                        }
                    )
                return rows

            imported_rows = server.vault.db.run_immediate_read_task(
                fetch_imported_picture_rows
            )
            assert imported_rows, "Could not fetch imported pictures for score mapping"

            expected_score_by_picture_id = {}
            expected_source_name_by_picture_id = {}
            imported_name_by_picture_id = {}
            embedding_by_picture_id = {}
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
                    if row["image_embedding"] is not None:
                        embedding_by_picture_id[row["id"]] = row["image_embedding"]

            assert expected_score_by_picture_id, (
                "No imported pictures matched scores.txt via content hash"
            )
            assert len(embedding_by_picture_id) == len(expected_score_by_picture_id), (
                "Some scored pictures have no image embedding; cannot build "
                "leak-free evaluation folds"
            )

            # --- Leave-one-group-out evaluation ------------------------------
            #
            # The scorer seeds its anchors from *user scores*: good anchors are
            # pictures with score >= 4, bad anchors are pictures with score == 1
            # (pixlstash/scoring/smart_score.py, fetch_smart_score_data).  The
            # reference labels this test grades against are written to exactly
            # that column, so scoring all 18 pictures in one pass makes 9 of them
            # their own anchor at cosine 1.0 and the test grades the labels
            # against themselves.  That leak, not model quality, is what
            # produced the 0.85 correlation this test used to record.
            #
            # So each picture is scored with its own label (and its near
            # duplicates' labels) removed from the anchor pool, and only the
            # held-out scores are collected.  What is measured is then the real
            # question: does the smart score predict a rating it has not seen?
            groups = _near_duplicate_groups(
                embedding_by_picture_id, _NEAR_DUPLICATE_COSINE
            )
            logger.info(
                "Leave-one-group-out folds (%d groups, cosine >= %.2f): %s",
                len(groups),
                _NEAR_DUPLICATE_COSINE,
                [
                    [expected_source_name_by_picture_id.get(pid, str(pid)) for pid in g]
                    for g in groups
                ],
            )

            def _set_scores_and_invalidate(session, scores_by_id, invalidate_ids):
                """Write the fold's anchor labels and clear the held-out scores.

                Written directly rather than through ``PATCH /pictures/{id}`` because
                this is evaluation scaffolding, not a user flow, and the endpoint is
                actively unsuitable here: a score change that crosses an anchor
                boundary schedules a *background* ``smart_score = NULL`` sweep over the
                whole library at LOW priority, which can land after this fold has
                already polled its rescore to completion and blank the value the fold
                is about to read.  A single synchronous write swaps all 18 labels and
                invalidates exactly the held-out rows, with no reset in flight.  The
                column written is byte-for-byte what the endpoint persists.

                The single commit is load-bearing: the rescore is triggered by
                ``smart_score IS NULL``, so putting the label swap and the invalidation
                in one transaction means the scoring task cannot observe a held-out row
                as scorable while its own label is still sitting in the anchor pool.
                """
                for score_value, ids_for_score in scores_by_id.items():
                    session.execute(
                        update(Picture)
                        .where(Picture.id.in_(ids_for_score))
                        .values(score=score_value)
                    )
                session.execute(
                    update(Picture)
                    .where(Picture.id.in_(invalidate_ids))
                    .values(smart_score=None)
                )
                session.commit()

            held_out_smart_score_by_id = {}
            for group in groups:
                held_out = set(group)
                ids_by_score = {}
                for pic_id, label in expected_score_by_picture_id.items():
                    # 0 == unrated: excluded from both the good (>= 4) and the
                    # bad (0 < score <= 1) anchor queries.
                    fold_score = 0 if pic_id in held_out else label
                    ids_by_score.setdefault(fold_score, []).append(pic_id)

                server.vault.db.run_task(
                    _set_scores_and_invalidate, ids_by_score, list(group)
                )
                _poll_until_zero(server, SmartScoreTask.count_remaining, "smart scores")

                def fetch_group_scores(session, ids):
                    return {
                        row[0]: row[1]
                        for row in session.exec(
                            select(Picture.id, Picture.smart_score).where(
                                Picture.id.in_(ids)
                            )
                        ).all()
                    }

                fold_scores = server.vault.db.run_immediate_read_task(
                    fetch_group_scores, group
                )
                for pic_id in group:
                    value = fold_scores.get(pic_id)
                    assert value is not None, (
                        f"No held-out smart score for picture {pic_id} "
                        f"({expected_source_name_by_picture_id.get(pic_id, '')})"
                    )
                    held_out_smart_score_by_id[pic_id] = float(value)

            common_ids = sorted(held_out_smart_score_by_id)
            assert len(common_ids) >= 8, (
                f"Too few points for correlation check: {len(common_ids)}"
            )

            expected_values = [
                float(expected_score_by_picture_id[pid]) for pid in common_ids
            ]
            smart_values = [
                float(held_out_smart_score_by_id[pid]) for pid in common_ids
            ]

            tau_b = _kendall_tau_b(expected_values, smart_values)
            tau_b_lower = _bootstrap_tau_b_lower_bound(expected_values, smart_values)
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
                        f"{held_out_smart_score_by_id[pid]:.4f}",
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
                    ["Kendall tau-b", f"{tau_b:.4f}"],
                    ["tau-b 95% lower bound", f"{tau_b_lower:.4f}"],
                    ["Pearson (diagnostic)", f"{pearson:.4f}"],
                    ["Spearman (diagnostic)", f"{spearman:.4f}"],
                    ["Sample Size", str(len(common_ids))],
                    ["Folds", str(len(groups))],
                ],
            )

            logger.info("Held-out smart score vs expected table:\n%s", score_table)
            logger.info("Held-out smart score correlation:\n%s", coeff_table)

            logger.info(
                "Smart score leave-one-group-out correlation: n=%d folds=%d "
                "tau_b=%.4f tau_b_lower=%.4f pearson=%.4f spearman=%.4f",
                len(common_ids),
                len(groups),
                tau_b,
                tau_b_lower,
                pearson,
                spearman,
            )

            assert tau_b_lower >= _MIN_TAU_B_LOWER_BOUND, (
                "Smart score agreement with reference labels regressed: "
                f"tau_b={tau_b:.4f}, 95% lower bound={tau_b_lower:.4f}, "
                f"required>={_MIN_TAU_B_LOWER_BOUND:.2f} "
                f"(n={len(common_ids)}, {len(groups)} leave-one-group-out folds)"
            )

    gc.collect()
