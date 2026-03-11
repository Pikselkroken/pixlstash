import argparse
import math
import random
import time
from collections import defaultdict
from pathlib import Path
import sqlite3
import sys
import statistics
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from typing import Optional
from pixlstash.likeness_worker import LikenessWorker
from sqlmodel import select
from sqlalchemy import func

from pixlstash.database import DBPriority, VaultDatabase
from pixlstash.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlstash.db_models.face import Face
from pixlstash.db_models.quality import Quality
from pixlstash.likeness_parameter_worker import (
    LikenessParameterWorker,
    PICTURE_PARAM_FIELDS,
    QUALITY_PARAM_FIELDS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def compute_likeness_parameters(
    db_path: str,
    batch_size: int,
    scan_limit: int,
    max_batches: int | None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE picture
            SET size_bin_index = NULL,
                likeness_parameters = NULL
            """
        )
        conn.commit()
        print("Cleared existing likeness parameters and size bins.")
    finally:
        conn.close()

    db = VaultDatabase(db_path)
    worker = LikenessParameterWorker(db, None, None)

    total_batches = 0
    total_images = 0
    param_batches = defaultdict(int)
    param_images = defaultdict(int)

    start_time = time.perf_counter()
    total_picture_count = db.run_immediate_read_task(
        lambda session: session.exec(
            select(func.count()).select_from(Picture).where(Picture.deleted.is_(False))
        ).one()
    )
    if isinstance(total_picture_count, tuple):
        total_picture_count = total_picture_count[0]
    effective_scan_limit = max(scan_limit, int(total_picture_count or 0))

    try:
        while True:
            work = db.run_task(
                LikenessParameterWorker._find_next_work,
                batch_size,
                effective_scan_limit,
                priority=DBPriority.LOW,
            )
            if not work:
                break

            param, size_bin, payload = work
            if param == LikenessParameter.SIZE_BIN:
                width, height, ids = payload
                size_bin_index = worker._size_bin_index(width, height)
                db.run_task(
                    LikenessParameterWorker._update_size_bin,
                    ids,
                    size_bin_index,
                    len(LikenessParameter),
                    priority=DBPriority.LOW,
                )
                updated_count = len(ids)
            else:
                ids, _remaining_in_bin = payload
                if param in QUALITY_PARAM_FIELDS:
                    quality_by_id = worker._fetch_quality_for_ids(ids)
                    db.run_task(
                        LikenessParameterWorker._update_quality_values,
                        ids,
                        quality_by_id,
                        len(LikenessParameter),
                        priority=DBPriority.LOW,
                    )
                elif param in PICTURE_PARAM_FIELDS:
                    picture_by_id, picture_updates = (
                        worker._fetch_picture_params_for_ids(ids)
                    )
                    if picture_updates:
                        db.run_task(
                            LikenessParameterWorker._update_picture_metadata,
                            picture_updates,
                            priority=DBPriority.LOW,
                        )
                    db.run_task(
                        LikenessParameterWorker._update_picture_values,
                        ids,
                        picture_by_id,
                        len(LikenessParameter),
                        priority=DBPriority.LOW,
                    )
                else:
                    values = [LIKENESS_PARAMETER_SENTINEL for _ in ids]
                    db.run_task(
                        LikenessParameterWorker._update_parameter_values,
                        ids,
                        int(param),
                        values,
                        len(LikenessParameter),
                        priority=DBPriority.LOW,
                    )
                updated_count = len(ids)

            total_batches += 1
            total_images += updated_count
            param_batches[param.name] += 1
            param_images[param.name] += updated_count

            if max_batches is not None and total_batches >= max_batches:
                break

    finally:
        elapsed = time.perf_counter() - start_time
        db.close()

    images_per_sec = total_images / elapsed if elapsed > 0 else 0.0
    print("Likeness parameter computation complete.")
    print(f"Total batches: {total_batches}")
    print(f"Total images updated: {total_images}")
    print(f"Elapsed time (s): {elapsed:.2f}")
    print(f"Images/sec: {images_per_sec:.2f}")
    print("Per-parameter batches/images:")
    for param_name in sorted(param_images.keys()):
        print(
            f"  {param_name}: batches={param_batches[param_name]} images={param_images[param_name]}"
        )

    def _count_missing_parameters(db_path: str) -> dict[str, int]:
        db = VaultDatabase(db_path)
        try:

            def fetch_params(session):
                return session.exec(
                    select(Picture.id, Picture.likeness_parameters)
                    .where(Picture.deleted.is_(False))
                    .order_by(Picture.id)
                ).all()

            rows = db.run_immediate_read_task(fetch_params)
        finally:
            db.close()

        missing_counts = {param.name: 0 for param in LikenessParameter}
        for _, blob in rows:
            vec = LikenessParameterWorker._decode_parameters(
                blob, len(LikenessParameter)
            )
            for param in LikenessParameter:
                if vec[int(param)] == LIKENESS_PARAMETER_SENTINEL:
                    missing_counts[param.name] += 1
        return missing_counts

    missing_counts = _count_missing_parameters(db_path)
    print("Missing parameter counts (sentinel values):")
    for param_name in sorted(missing_counts.keys()):
        print(f"  {param_name}: missing={missing_counts[param_name]}")


def _percentile(values, pct):
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


def _run_read_benchmark(
    db: VaultDatabase, iterations: int, use_queue: bool, workers: int
):
    timings = []

    def read_task(session):
        return session.exec(
            select(
                Picture.id,
                Picture.width,
                Picture.height,
                func.count(Face.id).label("face_count"),
                func.avg(Quality.brightness).label("avg_brightness"),
            )
            .outerjoin(Face, Face.picture_id == Picture.id)
            .outerjoin(
                Quality,
                (Quality.picture_id == Picture.id) & (Quality.face_id.is_(None)),
            )
            .where(Picture.deleted.is_(False))
            .group_by(Picture.id, Picture.width, Picture.height)
            .order_by(Picture.id)
            .limit(200)
        ).all()

    def run_one():
        start = time.perf_counter()
        if use_queue:
            db.run_task(read_task, priority=DBPriority.IMMEDIATE)
        else:
            db.run_immediate_read_task(read_task)
        return time.perf_counter() - start

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_one) for _ in range(iterations)]
        for future in futures:
            timings.append(future.result())

    return timings


def _summarize(label: str, timings: list[float]) -> None:
    if not timings:
        print(f"{label}: no timings")
        return
    total = sum(timings)
    mean = statistics.mean(timings)
    p50 = _percentile(timings, 50)
    p95 = _percentile(timings, 95)
    p99 = _percentile(timings, 99)
    ops = len(timings) / total if total else 0.0
    print(
        f"{label}: count={len(timings)} avg={mean * 1000:.2f}ms "
        f"p50={p50 * 1000:.2f}ms p95={p95 * 1000:.2f}ms p99={p99 * 1000:.2f}ms "
        f"ops/s={ops:.2f}"
    )


def benchmark_worker_niceness(db_path: str) -> None:
    iterations = 800
    workers = 4

    def run_phase(with_worker: bool) -> None:
        db = VaultDatabase(db_path)
        worker = None
        try:
            if with_worker:
                worker = LikenessParameterWorker(db, None, None)
                worker.start()
                worker.notify()
                time.sleep(0.5)

            queued = _run_read_benchmark(
                db, iterations, use_queue=True, workers=workers
            )
            immediate = _run_read_benchmark(
                db, iterations, use_queue=False, workers=workers
            )

            phase_label = "with_worker" if with_worker else "baseline"
            print(f"\n=== {phase_label} ===")
            _summarize("queued_read", queued)
            _summarize("immediate_read", immediate)
        finally:
            if worker:
                worker.stop()
            db.close()

    run_phase(with_worker=False)
    run_phase(with_worker=True)


def benchmark_parameter_bucket_pairs(db_path: str) -> None:
    max_window_size = 60
    gap_percentile = 60
    date_window_fraction = 0.004
    date_max_neighbors = 30
    excluded_params = {
        LikenessParameter.PHASH_PREFIX,
        LikenessParameter.DATE,
    }
    params = [
        param
        for param in LikenessParameter
        if param != LikenessParameter.SIZE_BIN and param not in excluded_params
    ]

    db = VaultDatabase(db_path)
    try:

        def fetch_params(session):
            return session.exec(
                select(Picture.id, Picture.likeness_parameters)
                .where(Picture.deleted.is_(False))
                .order_by(Picture.id)
            ).all()

        rows = db.run_immediate_read_task(fetch_params)
    finally:
        db.close()

    ids = [int(row[0]) for row in rows]
    vectors = [
        LikenessParameterWorker._decode_parameters(row[1], len(LikenessParameter))
        for row in rows
    ]
    vectors_by_id = {pic_id: vec for pic_id, vec in zip(ids, vectors)}

    n = len(ids)
    total_pairs = (n * (n - 1)) // 2

    min_overlap = 2
    start = time.perf_counter()
    candidate_counts: dict[tuple[int, int], int] = {}

    for param in params:
        param_index = int(param)
        values = [vec[param_index] for vec in vectors]
        sorted_indices = sorted(
            range(n),
            key=lambda i: (not math.isfinite(values[i]), values[i]),
        )
        values_sorted = [values[i] for i in sorted_indices]
        diffs = []
        for idx in range(1, n):
            prev_val = values_sorted[idx - 1]
            curr_val = values_sorted[idx]
            if not math.isfinite(prev_val) or not math.isfinite(curr_val):
                continue
            diff = curr_val - prev_val
            if diff >= 0:
                diffs.append(diff)
        if diffs:
            gap_threshold = float(np.percentile(diffs, gap_percentile))
        else:
            gap_threshold = 0.0
        for position, i in enumerate(sorted_indices):
            value_a = values_sorted[position]
            if not math.isfinite(value_a):
                continue
            upper = min(position + max_window_size, n - 1)
            id_a = ids[i]
            for neighbor_pos in range(position + 1, upper + 1):
                value_b = values_sorted[neighbor_pos]
                if not math.isfinite(value_b):
                    break
                if (value_b - value_a) > gap_threshold:
                    break
                j = sorted_indices[neighbor_pos]
                id_b = ids[j]
                if id_a < id_b:
                    key = (id_a, id_b)
                else:
                    key = (id_b, id_a)
                candidate_counts[key] = candidate_counts.get(key, 0) + 1

    date_param_index = int(LikenessParameter.DATE)
    date_values = [vec[date_param_index] for vec in vectors]
    finite_dates = [val for val in date_values if math.isfinite(val)]
    date_pairs_added = 0
    if finite_dates:
        min_date = min(finite_dates)
        max_date = max(finite_dates)
        date_span = max_date - min_date
        max_gap = date_span * date_window_fraction
        if max_gap > 0:
            date_sorted_indices = sorted(
                range(n),
                key=lambda i: (not math.isfinite(date_values[i]), date_values[i]),
            )
            for position, i in enumerate(date_sorted_indices):
                date_a = date_values[i]
                if not math.isfinite(date_a):
                    continue
                id_a = ids[i]
                upper = min(position + date_max_neighbors, n - 1)
                for neighbor_pos in range(position + 1, upper + 1):
                    j = date_sorted_indices[neighbor_pos]
                    date_b = date_values[j]
                    if not math.isfinite(date_b):
                        break
                    if (date_b - date_a) > max_gap:
                        break
                    id_b = ids[j]
                    if id_a < id_b:
                        key = (id_a, id_b)
                    else:
                        key = (id_b, id_a)
                    if key not in candidate_counts:
                        date_pairs_added += 1
                    candidate_counts[key] = candidate_counts.get(key, 0) + 1

    candidate_pairs = {
        pair for pair, count in candidate_counts.items() if count >= min_overlap
    }
    elapsed = time.perf_counter() - start
    print("\n=== parameter_bucket_pairs ===")
    print(f"total_pairs: {total_pairs}")
    print(f"proposed_pairs: {len(candidate_pairs)}")
    print(f"elapsed_seconds: {elapsed:.2f}")
    if finite_dates:
        print(
            "date_pair_additions: "
            + f"added={date_pairs_added} max_gap={max_gap:.2f}s "
            + f"fraction={date_window_fraction:.4f} max_neighbors={date_max_neighbors}"
        )
    display_names = {
        LikenessParameter.PHASH_PREFIX: "PHASH_FULL",
    }
    print(
        "params_used: "
        + ", ".join(display_names.get(param, param.name) for param in params)
        + f" (max_window={max_window_size}, gap_p={gap_percentile}, min_overlap={min_overlap})"
    )

    def fetch_embeddings(session):
        return session.exec(
            select(Picture.id, Picture.image_embedding, Picture.perceptual_hash)
            .where(Picture.id.in_(ids))
            .order_by(Picture.id)
        ).all()

    db = VaultDatabase(db_path)
    try:
        emb_rows = db.run_immediate_read_task(fetch_embeddings)
    finally:
        db.close()

    embedding_by_id: dict[int, np.ndarray] = {}
    phash_by_id: dict[int, str] = {}
    for pic_id, emb_blob, phash in emb_rows:
        emb = LikenessWorker._decode_embedding(emb_blob)
        if emb is not None:
            embedding_by_id[int(pic_id)] = emb
        if phash:
            phash_by_id[int(pic_id)] = str(phash)

    rng = random.Random(1337)
    all_ids = ids[:]

    def compute_likeness(a_id: int, b_id: int) -> Optional[float]:
        emb_a = embedding_by_id.get(a_id)
        emb_b = embedding_by_id.get(b_id)
        if emb_a is None or emb_b is None or emb_a.shape != emb_b.shape:
            return None
        norm_a = np.linalg.norm(emb_a)
        norm_b = np.linalg.norm(emb_b)
        if norm_a == 0 or norm_b == 0:
            return None
        cos_sim = float((emb_a / norm_a) @ (emb_b / norm_b))
        return max(-1.0, min(1.0, cos_sim))

    def compute_phash_sim(a_id: int, b_id: int) -> Optional[float]:
        phash_a = phash_by_id.get(a_id)
        phash_b = phash_by_id.get(b_id)
        if not phash_a or not phash_b:
            return None
        return LikenessWorker._phash_similarity(phash_a, phash_b)

    start = time.perf_counter()
    proposed_scores: list[float] = []
    proposed_phash_scores: list[float] = []
    random_scores: list[float] = []
    random_phash_scores: list[float] = []
    overlap_scores: dict[int, list[float]] = {}
    phash_scores_by_pair: dict[tuple[int, int], float] = {}
    proposed_score_by_pair: dict[tuple[int, int], float] = {}
    good_thresholds = [0.9, 0.93, 0.95]
    phash_gate_threshold = 0.45
    gated_candidate_pairs: set[tuple[int, int]] = set()
    gated_out_pairs = 0
    skipped = 0

    proposed_pair_list: list[tuple[int, int, float]] = []
    random_pair_list: list[tuple[int, int, float]] = []

    for a_id, b_id in candidate_pairs:
        overlap = candidate_counts.get((a_id, b_id), min_overlap)
        phash_sim = compute_phash_sim(a_id, b_id)
        if phash_sim is not None:
            phash_scores_by_pair[(a_id, b_id)] = phash_sim
            if phash_sim < phash_gate_threshold:
                gated_out_pairs += 1
                continue
            gated_candidate_pairs.add((a_id, b_id))
            proposed_phash_scores.append(phash_sim)
        else:
            gated_out_pairs += 1
            continue
        likeness = compute_likeness(a_id, b_id)
        if likeness is None:
            skipped += 1
            continue
        proposed_scores.append(likeness)
        proposed_score_by_pair[(a_id, b_id)] = likeness
        proposed_pair_list.append((a_id, b_id, likeness))
        overlap_scores.setdefault(overlap, []).append(likeness)

        if len(all_ids) > 1:
            rand_id = a_id
            while rand_id == a_id:
                rand_id = rng.choice(all_ids)
            rand_likeness = compute_likeness(a_id, rand_id)
            if rand_likeness is not None:
                random_scores.append(rand_likeness)
                random_pair_list.append((a_id, rand_id, rand_likeness))
                rand_phash = compute_phash_sim(a_id, rand_id)
                if rand_phash is not None:
                    random_phash_scores.append(rand_phash)

    likeness_elapsed = time.perf_counter() - start
    processed_pairs = len(proposed_scores)
    per_pair_seconds = likeness_elapsed / processed_pairs if processed_pairs else 0.0
    est_total_seconds = (
        (total_pairs / processed_pairs) * likeness_elapsed if processed_pairs else 0.0
    )
    rounded_seconds = int(round(est_total_seconds))
    rounded_minutes = int(round(est_total_seconds / 60.0) * 60.0)

    print("\n=== likeness_pair_benchmark ===")
    print(f"proposed_pairs_processed: {processed_pairs}")
    print(f"proposed_pairs_skipped: {skipped}")
    print(f"phash_gate_threshold: {phash_gate_threshold:.2f}")
    print(f"phash_gate_rejected_pairs: {gated_out_pairs}")
    print(f"elapsed_seconds: {likeness_elapsed:.2f}")
    print(f"avg_seconds_per_pair: {per_pair_seconds:.8f}")
    print(f"estimated_total_pairs_seconds: {est_total_seconds:.2f}")
    if rounded_seconds:
        rounded_hours = rounded_seconds / 3600.0
        print(
            f"estimated_total_pairs_seconds_rounded: {rounded_seconds} (~{rounded_hours:.2f}h)"
        )
    if rounded_minutes and rounded_minutes >= 60:
        rounded_hours_min = rounded_minutes / 3600.0
        print(
            f"estimated_total_pairs_seconds_rounded_minutes: {rounded_minutes} (~{rounded_hours_min:.2f}h)"
        )

    def summarize_scores(label: str, values: list[float]) -> None:
        if not values:
            print(f"{label}: no scores")
            return
        mean_val = statistics.mean(values)
        p50 = _percentile(values, 50)
        p95 = _percentile(values, 95)
        print(
            f"{label}: count={len(values)} avg={mean_val:.4f} p50={p50:.4f} p95={p95:.4f}"
        )

    summarize_scores("proposed_embedding_likeness", proposed_scores)
    summarize_scores("random_embedding_likeness", random_scores)
    summarize_scores("proposed_phash_similarity", proposed_phash_scores)
    summarize_scores("random_phash_similarity", random_phash_scores)

    for low_threshold in (0.7, 0.8):
        low_count = sum(1 for score in proposed_scores if score < low_threshold)
        low_fraction = low_count / len(proposed_scores) if proposed_scores else 0.0
        print(
            f"proposed_low_likeness<{low_threshold:.2f}: {low_count} ({low_fraction:.4f})"
        )

    phash_thresholds = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9]
    if phash_scores_by_pair:
        print("phash_gate_estimates:")
        gate_start = time.perf_counter()
        for threshold in phash_thresholds:
            gated_pairs = [
                pair
                for pair, score in phash_scores_by_pair.items()
                if score >= threshold
            ]
            gated_scores = [
                proposed_score_by_pair[pair]
                for pair, score in phash_scores_by_pair.items()
                if score >= threshold and pair in proposed_score_by_pair
            ]
            gated_low_07 = sum(1 for score in gated_scores if score < 0.7)
            gated_low_08 = sum(1 for score in gated_scores if score < 0.8)
            gated_total = len(gated_scores)
            gated_low_07_frac = gated_low_07 / gated_total if gated_total else 0.0
            gated_low_08_frac = gated_low_08 / gated_total if gated_total else 0.0
            print(
                f"  phash>={threshold:.2f}: pairs={len(gated_pairs)} "
                f"low<0.70={gated_low_07_frac:.4f} low<0.80={gated_low_08_frac:.4f}"
            )
        gate_elapsed = time.perf_counter() - gate_start
        print(f"phash_gate_elapsed_seconds: {gate_elapsed:.4f}")

    for threshold in good_thresholds:
        proposed_good = sum(1 for score in proposed_scores if score >= threshold)
        proposed_precision = (
            proposed_good / len(proposed_scores) if proposed_scores else 0.0
        )
        print(f"proposed_precision@{threshold:.2f}: {proposed_precision:.4f}")

    if overlap_scores:
        print("overlap_thresholds:")
        for threshold in sorted({min_overlap, min_overlap + 1, min_overlap + 2}):
            pooled: list[float] = []
            for overlap, scores in overlap_scores.items():
                if overlap >= threshold:
                    pooled.extend(scores)
            summarize_scores(f"overlap>={threshold}", pooled)

    def rankdata(values: list[float]) -> np.ndarray:
        order = np.argsort(values)
        ranks = np.empty_like(order, dtype=np.float64)
        ranks[order] = np.arange(len(values), dtype=np.float64)
        return ranks

    def correlation_report(label: str, pairs: list[tuple[int, int, float]]) -> None:
        if not pairs:
            print(f"{label}: no pairs")
            return
        sample_size = min(20000, len(pairs))
        sampled = rng.sample(pairs, sample_size) if len(pairs) > sample_size else pairs
        results = []
        for param in params:
            diffs: list[float] = []
            likeness_vals: list[float] = []
            param_index = int(param)
            for a_id, b_id, likeness in sampled:
                vec_a = vectors_by_id.get(a_id)
                vec_b = vectors_by_id.get(b_id)
                if vec_a is None or vec_b is None:
                    continue
                if param == LikenessParameter.PHASH_PREFIX:
                    phash_sim = compute_phash_sim(a_id, b_id)
                    if phash_sim is None:
                        continue
                    diffs.append(1.0 - phash_sim)
                    likeness_vals.append(likeness)
                    continue
                val_a = float(vec_a[param_index])
                val_b = float(vec_b[param_index])
                if (
                    val_a == LIKENESS_PARAMETER_SENTINEL
                    or val_b == LIKENESS_PARAMETER_SENTINEL
                ):
                    continue
                diffs.append(abs(val_a - val_b))
                likeness_vals.append(likeness)
            if len(diffs) < 2:
                continue
            diff_arr = np.asarray(diffs, dtype=np.float64)
            like_arr = np.asarray(likeness_vals, dtype=np.float64)
            pearson = float(np.corrcoef(diff_arr, like_arr)[0, 1])
            ranks_diff = rankdata(diff_arr.tolist())
            ranks_like = rankdata(like_arr.tolist())
            spearman = float(np.corrcoef(ranks_diff, ranks_like)[0, 1])
            results.append((param.name, pearson, spearman, len(diffs)))
        results.sort(key=lambda item: abs(item[1]), reverse=True)
        print(f"{label} (all correlations):")
        for name, pearson, spearman, count in results:
            if name == LikenessParameter.PHASH_PREFIX.name:
                name = "PHASH_FULL"
            print(f"  {name}: n={count} pearson={pearson:.4f} spearman={spearman:.4f}")

    correlation_report("proposed_param_correlations", proposed_pair_list)
    correlation_report("random_param_correlations", random_pair_list)

    sample_target = 200000
    sampled_pairs: set[tuple[int, int]] = set()
    tries = 0
    max_tries = sample_target * 10
    while len(sampled_pairs) < sample_target and tries < max_tries:
        tries += 1
        a_id = rng.choice(all_ids)
        b_id = rng.choice(all_ids)
        if a_id == b_id:
            continue
        pair = (a_id, b_id) if a_id < b_id else (b_id, a_id)
        sampled_pairs.add(pair)

    sampled_scored = 0
    sampled_good_by_threshold = {threshold: 0 for threshold in good_thresholds}
    sampled_good_in_candidates = {threshold: 0 for threshold in good_thresholds}
    for a_id, b_id in sampled_pairs:
        likeness = compute_likeness(a_id, b_id)
        if likeness is None:
            continue
        sampled_scored += 1
        for threshold in good_thresholds:
            if likeness >= threshold:
                sampled_good_by_threshold[threshold] += 1
                if (a_id, b_id) in gated_candidate_pairs:
                    sampled_good_in_candidates[threshold] += 1
    print("\n=== pair_sampling_estimates ===")
    print(f"sampled_pairs: {sampled_scored}")
    for threshold in good_thresholds:
        sampled_good = sampled_good_by_threshold[threshold]
        sampled_in_candidates = sampled_good_in_candidates[threshold]
        recall_estimate = sampled_in_candidates / sampled_good if sampled_good else 0.0
        print(f"sampled_good_pairs@{threshold:.2f}: {sampled_good}")
        print(f"sampled_good_in_candidates@{threshold:.2f}: {sampled_in_candidates}")
        print(f"recall_estimate@{threshold:.2f}: {recall_estimate:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute likeness parameters for all pictures using worker logic."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--scan-limit", type=int, default=2048)
    parser.add_argument("--max-batches", type=int, default=None)
    args = parser.parse_args()

    compute_likeness_parameters(
        db_path=args.db_path,
        batch_size=args.batch_size,
        scan_limit=args.scan_limit,
        max_batches=args.max_batches,
    )
    benchmark_worker_niceness(args.db_path)
    benchmark_parameter_bucket_pairs(args.db_path)


if __name__ == "__main__":
    main()
