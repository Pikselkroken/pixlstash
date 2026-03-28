import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils


def build_session(
    base_url: str,
    timeout: float,
    token: Optional[str],
    verify_tls: bool,
) -> requests.Session:
    session = requests.Session()
    session.verify = verify_tls
    if not token:
        return session

    login_resp = session.post(
        f"{base_url}/login",
        json={"token": token},
        timeout=timeout,
    )
    login_resp.raise_for_status()
    return session


def fetch_smart_score_rows(
    session: requests.Session,
    base_url: str,
    timeout: float,
    limit: int | None,
) -> tuple[list, int, int]:
    summary = session.get(f"{base_url}/characters/ALL/summary", timeout=timeout)
    summary.raise_for_status()
    db_total = int(summary.json().get("image_count") or 0)

    query_limit = limit if limit is not None else max(20000, db_total + 2000)
    resp = session.get(
        f"{base_url}/pictures",
        params={
            "fields": "grid",
            "sort": "SMART_SCORE",
            "descending": "true",
            "offset": 0,
            "limit": query_limit,
        },
        timeout=max(timeout, 60.0),
    )
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected /pictures payload: {type(rows)!r}")
    return rows, db_total, query_limit


def _decode_vec(blob) -> Optional[np.ndarray]:
    if blob is None:
        return None
    if isinstance(blob, memoryview):
        blob = bytes(blob)
    if isinstance(blob, bytearray):
        blob = bytes(blob)
    if not isinstance(blob, (bytes, bytearray)):
        return None
    try:
        arr = np.frombuffer(blob, dtype=np.float32)
    except Exception:
        return None
    if arr.ndim != 1 or arr.size == 0:
        return None
    return arr.copy()


def fetch_scores_from_db(db_path: Path) -> np.ndarray:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT image_embedding, score
            FROM picture
            WHERE score >= 4
              AND image_embedding IS NOT NULL
              AND deleted = 0
            ORDER BY score DESC, created_at DESC
            LIMIT 200
            """
        )
        good_rows = cur.fetchall()

        cur.execute(
            """
            SELECT image_embedding, score
            FROM picture
            WHERE score <= 1
              AND score > 0
              AND image_embedding IS NOT NULL
              AND deleted = 0
            ORDER BY score ASC, created_at DESC
            LIMIT 200
            """
        )
        bad_rows = cur.fetchall()

        cur.execute(
            """
            SELECT p.id,
                   p.image_embedding,
                   p.aesthetic_score,
                   p.width,
                   p.height,
                   q.sharpness,
                   q.edge_density,
                   q.luminance_entropy,
                   q.text_score
            FROM picture p
            LEFT JOIN quality q
              ON q.picture_id = p.id
             AND q.face_id IS NULL
            WHERE p.deleted = 0
              AND p.image_embedding IS NOT NULL
            """
        )
        cand_rows = cur.fetchall()
    finally:
        conn.close()

    good_list = []
    for emb, score in good_rows:
        vec = _decode_vec(emb)
        if vec is None:
            continue
        good_list.append({"embedding": vec, "score": score})

    bad_list = []
    for emb, score in bad_rows:
        vec = _decode_vec(emb)
        if vec is None:
            continue
        bad_list.append({"embedding": vec, "score": score})

    cand_list = []
    for row in cand_rows:
        (
            pid,
            emb,
            aest,
            width,
            height,
            sharpness,
            edge_density,
            luminance_entropy,
            text_score,
        ) = row
        vec = _decode_vec(emb)
        if vec is None:
            continue
        cand_list.append(
            {
                "id": pid,
                "embedding": vec,
                "aesthetic_score": aest,
                "penalised_tag_count": 0,
                "width": width,
                "height": height,
                "sharpness": sharpness,
                "edge_density": edge_density,
                "luminance_entropy": luminance_entropy,
                "text_score": text_score,
            }
        )

    if not cand_list:
        raise RuntimeError("No candidates found for DB-based smart score calculation")

    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
        cand_list,
        good_list,
        bad_list,
    )
    return np.asarray(scores, dtype=np.float64)


def extract_scores(rows: list) -> np.ndarray:
    scores: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        val = row.get("smartScore", row.get("smart_score"))
        if val is None:
            continue
        try:
            scores.append(float(val))
        except Exception:
            continue
    arr = np.array(scores, dtype=np.float64)
    if arr.size == 0:
        raise RuntimeError("No smart scores found in response")
    return arr


def generate_plot_and_report(
    *,
    base_url: str,
    output_plot_dir: Path,
    output_report_dir: Path,
    timeout: float,
    limit: int | None,
    title: str,
    token: Optional[str],
    source: str,
    db_path: Optional[Path],
    verify_tls: bool,
) -> tuple[Path, Path]:
    output_plot_dir.mkdir(parents=True, exist_ok=True)
    output_report_dir.mkdir(parents=True, exist_ok=True)

    if source == "db":
        if db_path is None:
            raise ValueError("--db-path is required when --source=db")
        arr = fetch_scores_from_db(db_path)
        rows = []
        db_total = int(arr.size)
        query_limit = int(arr.size)
    else:
        session = build_session(base_url, timeout, token, verify_tls)
        rows, db_total, query_limit = fetch_smart_score_rows(
            session,
            base_url,
            timeout,
            limit,
        )
        arr = extract_scores(rows)

    mean = float(np.mean(arr))
    std = float(np.std(arr))
    median = float(np.median(arr))
    q1 = float(np.quantile(arr, 0.25))
    q3 = float(np.quantile(arr, 0.75))
    min_v = float(np.min(arr))
    max_v = float(np.max(arr))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plot_path = output_plot_dir / f"smart_score_distribution_{ts}.png"
    report_path = output_report_dir / f"smart_score_distribution_{ts}.txt"

    plt.figure(figsize=(11, 6.5))
    plt.hist(arr, bins=50)
    plt.axvline(mean, linestyle="--", linewidth=2, label=f"mean={mean:.4f}")
    plt.axvline(
        mean - std, linestyle=":", linewidth=2, label=f"-1sigma={mean - std:.4f}"
    )
    plt.axvline(
        mean + std, linestyle=":", linewidth=2, label=f"+1sigma={mean + std:.4f}"
    )
    plt.title(title)
    plt.xlabel("Smart Score")
    plt.ylabel("Count")
    plt.legend()
    plt.tight_layout()
    plt.savefig(plot_path, dpi=140)
    plt.close()

    lines = [
        "PixlStash smart score distribution",
        f"timestamp_utc: {datetime.now(timezone.utc).isoformat()}",
        f"api_base: {base_url}",
        f"source: {source}",
        f"db_total_from_summary: {db_total}",
        f"query_limit_used: {query_limit}",
        f"rows_returned: {len(rows)}",
        f"scores_count: {arr.size}",
        f"mean: {mean:.6f}",
        f"std_dev: {std:.6f}",
        f"median: {median:.6f}",
        f"q1: {q1:.6f}",
        f"q3: {q3:.6f}",
        f"min: {min_v:.6f}",
        f"max: {max_v:.6f}",
        f"plot: {plot_path.as_posix()}",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(report_path.as_posix())
    print(plot_path.as_posix())
    print(
        json.dumps(
            {
                "db_total": db_total,
                "rows_returned": len(rows),
                "scores_count": int(arr.size),
                "mean": mean,
                "std_dev": std,
                "min": min_v,
                "max": max_v,
            },
            indent=2,
        )
    )
    return report_path, plot_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch SMART_SCORE-sorted grid data and generate distribution plot/report."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:9537",
        help="PixlStash API base URL (default: http://127.0.0.1:9537)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional explicit picture query limit.",
    )
    parser.add_argument(
        "--plot-dir",
        default="tmp/plots",
        help="Output directory for the PNG plot (default: tmp/plots)",
    )
    parser.add_argument(
        "--report-dir",
        default="tmp/reports",
        help="Output directory for the TXT report (default: tmp/reports)",
    )
    parser.add_argument(
        "--title",
        default="Smart Score Distribution",
        help="Plot title.",
    )
    parser.add_argument(
        "--source",
        choices=["api", "db"],
        default="api",
        help="Data source: api (default) or db.",
    )
    parser.add_argument(
        "--db-path",
        default="vault.db",
        help="Path to vault.db when --source=db (default: vault.db)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (useful for local self-signed HTTPS).",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Optional auth token for /login before fetching data.",
    )
    args = parser.parse_args()

    generate_plot_and_report(
        base_url=args.base_url.rstrip("/"),
        output_plot_dir=Path(os.path.expanduser(args.plot_dir)),
        output_report_dir=Path(os.path.expanduser(args.report_dir)),
        timeout=float(args.timeout),
        limit=args.limit,
        title=args.title,
        token=(str(args.token).strip() if args.token else None),
        source=args.source,
        db_path=(Path(os.path.expanduser(args.db_path)) if args.db_path else None),
        verify_tls=not args.insecure,
    )


if __name__ == "__main__":
    main()
