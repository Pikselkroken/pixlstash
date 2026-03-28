import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PENALIZED_TAGS = [
    "incorrect reflection",
    "fused fingers",
    "malformed eye",
    "bad anatomy",
    "extra digit",
    "missing digit",
    "extra limb",
    "missing limb",
    "malformed hand",
    "malformed teeth",
    "missing nipples",
    "malformed nipples",
    "waxy skin",
    "flux chin",
    "silicone breasts",
    "malformed foot",
    "missing toe",
    "extra toe",
    "fused toes",
    "pixelated",
]


def parse_previous_rate(report_path: Path) -> float | None:
    if not report_path.exists():
        return None
    for line in report_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("anomaly_tag_rate_percent:"):
            continue
        try:
            return float(line.split(":", 1)[1].strip().replace(",", "."))
        except Exception:
            return None
    return None


def snapshot_anomaly_tag_rate(
    db_path: Path,
    output_dir: Path,
    previous_report: Path | None = None,
) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM picture WHERE deleted = 0")
        non_deleted_total = int(cur.fetchone()[0])

        placeholders = ",".join(["?"] * len(PENALIZED_TAGS))
        cur.execute(
            f"""
            SELECT COUNT(DISTINCT t.picture_id)
            FROM tag t
            JOIN picture p ON p.id = t.picture_id
            WHERE p.deleted = 0 AND t.tag IN ({placeholders})
            """,
            PENALIZED_TAGS,
        )
        anomaly_tagged = int(cur.fetchone()[0])
    finally:
        conn.close()

    anomaly_rate = (
        (anomaly_tagged / non_deleted_total) * 100.0 if non_deleted_total else 0.0
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"anomaly_tag_rate_snapshot_{ts}.txt"

    lines = [
        "PixlStash anomaly-tag rate snapshot",
        f"timestamp_utc: {datetime.now(timezone.utc).isoformat()}",
        f"anomaly_tag_definition: DEFAULT_SMART_SCORE_PENALIZED_TAGS ({len(PENALIZED_TAGS)} tags)",
        f"non_deleted_pictures_total: {non_deleted_total}",
        f"pictures_with_at_least_one_anomaly_tag: {anomaly_tagged}",
        f"anomaly_tag_rate_percent: {anomaly_rate:.6f}",
        "anomaly_tags:",
    ]
    lines.extend([f"- {tag}" for tag in PENALIZED_TAGS])

    if previous_report is not None:
        previous_rate = parse_previous_rate(previous_report)
        if previous_rate is not None:
            lines.extend(
                [
                    "",
                    "[comparison_vs_previous_snapshot]",
                    f"previous_snapshot: {previous_report.as_posix()}",
                    f"previous_anomaly_tag_rate_percent: {previous_rate:.6f}",
                    f"delta_percent_points: {anomaly_rate - previous_rate:+.6f}",
                ]
            )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output_path.as_posix())
    print(
        f"non_deleted_total={non_deleted_total} anomaly_tagged={anomaly_tagged} "
        f"anomaly_rate_percent={anomaly_rate:.6f}"
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot current anomaly-tag rate from vault.db."
    )
    parser.add_argument(
        "--db-path",
        default="vault.db",
        help="Path to vault.db (default: vault.db)",
    )
    parser.add_argument(
        "--output-dir",
        default="tmp/reports",
        help="Directory for snapshot report (default: tmp/reports)",
    )
    parser.add_argument(
        "--previous-report",
        default=None,
        help="Optional previous anomaly snapshot report for delta comparison.",
    )
    args = parser.parse_args()

    snapshot_anomaly_tag_rate(
        db_path=Path(os.path.expanduser(args.db_path)),
        output_dir=Path(os.path.expanduser(args.output_dir)),
        previous_report=(
            Path(os.path.expanduser(args.previous_report))
            if args.previous_report
            else None
        ),
    )


if __name__ == "__main__":
    main()
