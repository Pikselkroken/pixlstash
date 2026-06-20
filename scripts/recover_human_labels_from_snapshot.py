"""Recover human POS/NEG labels by diffing the live vault against an older snapshot.

The label ledger (label_state/label_source on tag_prediction) only started capturing
human decisions going forward, so tags you hand-edited *before* it shipped are invisible.
But every snapshot is a full SQLite copy of the vault, so the tags you added/removed in a
recent window are recoverable: diff a snapshot from before the window against the live DB.

Per your rule: in this window, any anomaly tag **added** is a human POS and any **removed**
is a human NEG. This writes those to the live DB's tag_prediction ledger with the same
semantics as ``record_human_label`` (upsert, synthetic 'manual' row if missing, snapshot
the adjudicated model_version/confidence, set status). Anomaly-vocabulary tags only, so
content tags never pollute the prediction store. Scoped to the PixlTagger project by
default (``--project``) — edits to pictures outside your tagging project are ignored;
pass ``--all-projects`` to diff the whole vault. Dry-run by default — pass --apply to write.

CAVEAT — background tagger churn: the tagger re-tags a picture by deleting and re-inserting
all its Tag rows, so if it re-ran on a picture *inside the window*, that churn looks like
human adds/removes. Run with the tagger paused for the cleanest result, eyeball the dry-run
sample, and/or use --exclude-tagger-touched to drop pairs the tagger demonstrably wrote a
prediction for inside the window.

Examples:
    # Dry run, baseline = newest snapshot at least 2 days old (your home machine):
    python scripts/recover_human_labels_from_snapshot.py /path/to/vault.db

    # Apply, explicit baseline snapshot id, drop tagger-churned pairs:
    python scripts/recover_human_labels_from_snapshot.py /path/to/vault.db \
        --snapshot-id 41 --exclude-tagger-touched --apply
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

# Make `pixlstash` importable when run as `python scripts/<this>.py` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from pixlstash.utils.service.label_ledger import ANOMALY_LABELS
except Exception:  # pragma: no cover - allow running outside an installed env
    ANOMALY_LABELS = None


def _parse_dt(value: str) -> datetime:
    """Parse a snapshot.created_at string (ISO, with or without microseconds)."""
    value = value.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Last resort: fromisoformat handles most remaining shapes.
    return datetime.fromisoformat(value)


def _pick_baseline(conn: sqlite3.Connection, days: float, snapshot_id):
    """Return (relative_path, created_at) for the baseline snapshot to diff against."""
    cur = conn.cursor()
    if snapshot_id is not None:
        row = cur.execute(
            "SELECT relative_path, created_at FROM snapshot WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        if not row:
            raise SystemExit(f"No snapshot with id={snapshot_id}")
        return row[0], _parse_dt(row[1])

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = cur.execute(
        "SELECT id, relative_path, created_at FROM snapshot ORDER BY created_at DESC"
    ).fetchall()
    if not rows:
        raise SystemExit(
            "No snapshots found in this vault — cannot recover. (Snapshots are created "
            "under the GFS retention policy; check GET /snapshots.)"
        )
    for sid, rel, created in rows:
        if _parse_dt(created) <= cutoff:
            print(f"Baseline snapshot: id={sid}  created_at={created}  ({rel})")
            return rel, _parse_dt(created)
    raise SystemExit(
        f"No snapshot older than {days} days exists; the oldest is "
        f"{rows[-1][2]}. Pass --days smaller or --snapshot-id explicitly."
    )


def _is_anomaly(tag: str) -> bool:
    if ANOMALY_LABELS is None:
        return True  # no vocab available: don't silently drop everything
    return bool(tag) and tag.strip().lower() in ANOMALY_LABELS


def _resolve_project_id(conn: sqlite3.Connection, name: str) -> int:
    """Resolve a project NAME to its id in the live DB, or exit with a helpful error.

    We deliberately do NOT silently fall back to the whole vault on a bad name — the
    caller asked to scope to a specific project, so a typo should stop, not widen.
    """
    row = conn.execute("SELECT id FROM project WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row[0])
    available = [r[0] for r in conn.execute("SELECT name FROM project ORDER BY name")]
    raise SystemExit(
        f"No project named {name!r}. Available projects: {available}. "
        "Pass --project <name>, or --all-projects to diff the whole vault."
    )


def _diff(conn: sqlite3.Connection, project_id: int | None = None):
    """Return (added, removed) lists of (picture_id, tag) for live-vs-baseline, anomaly only.

    added   = present live, absent in baseline, picture still exists → human POS
    removed = present baseline, absent live, picture still exists    → human NEG

    When *project_id* is given, only pictures that are members of that project (the
    canonical ``pictureprojectmember`` M-M table in the live DB) count — edits to pictures
    outside your tagging project are ignored. ``None`` diffs the whole vault.
    """
    cur = conn.cursor()
    # Optional project scope. {pic} is filled with each query's picture-id column; the ?
    # binds project_id. Empty string (whole vault) formats to nothing.
    proj = (
        " AND EXISTS (SELECT 1 FROM pictureprojectmember m "
        "WHERE m.picture_id = {pic} AND m.project_id = ?)"
        if project_id is not None
        else ""
    )
    params = (project_id,) if project_id is not None else ()
    added = [
        (pid, tag)
        for pid, tag in cur.execute(
            "SELECT t.picture_id, t.tag FROM tag t "
            "JOIN picture p ON p.id = t.picture_id "
            "WHERE t.tag IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM base.tag b "
            "  WHERE b.picture_id = t.picture_id AND b.tag = t.tag)"
            + proj.format(pic="t.picture_id"),
            params,
        ).fetchall()
        if _is_anomaly(tag)
    ]
    removed = [
        (pid, tag)
        for pid, tag in cur.execute(
            "SELECT b.picture_id, b.tag FROM base.tag b "
            "JOIN picture p ON p.id = b.picture_id "  # picture still exists live
            "WHERE b.tag IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM tag t "
            "  WHERE t.picture_id = b.picture_id AND t.tag = b.tag)"
            + proj.format(pic="b.picture_id"),
            params,
        ).fetchall()
        if _is_anomaly(tag)
    ]
    return added, removed


def _tagger_touched(conn: sqlite3.Connection, baseline_at: datetime) -> set:
    """(picture_id, tag) pairs the tagger wrote a prediction for inside the window."""
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT picture_id, tag FROM tag_prediction "
        "WHERE model_version IS NOT NULL AND model_version != 'manual' "
        "  AND predicted_at IS NOT NULL AND predicted_at >= ?",
        (baseline_at.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()
    return {(pid, tag) for pid, tag in rows}


def _record(conn: sqlite3.Connection, picture_id: int, tag: str, state: str) -> None:
    """Upsert one human label on tag_prediction — mirrors record_human_label()."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    status = "CONFIRMED" if state == "POS" else "REJECTED"
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, model_version, confidence FROM tag_prediction "
        "WHERE picture_id = ? AND tag = ?",
        (picture_id, tag),
    ).fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO tag_prediction "
            "(picture_id, tag, confidence, model_version, status, predicted_at, "
            " label_state, label_source, labeled_at) "
            "VALUES (?, ?, ?, 'manual', ?, ?, ?, 'human', ?)",
            (
                picture_id,
                tag,
                1.0 if state == "POS" else 0.0,
                status,
                now,
                state,
                now,
            ),
        )
        return
    pred_id, model_version, confidence = row
    # Snapshot what the human adjudicated only when it was a real tagger prediction.
    if model_version and model_version != "manual":
        cur.execute(
            "UPDATE tag_prediction SET status = ?, label_state = ?, label_source = 'human', "
            "labeled_at = ?, label_model_version = ?, label_confidence = ? WHERE id = ?",
            (status, state, now, model_version, confidence, pred_id),
        )
    else:
        cur.execute(
            "UPDATE tag_prediction SET status = ?, label_state = ?, label_source = 'human', "
            "labeled_at = ? WHERE id = ?",
            (status, state, now, pred_id),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", help="Path to the live vault SQLite DB")
    parser.add_argument(
        "--vault-root",
        default=None,
        help="Vault root that snapshot relative_paths resolve against "
        "(default: the directory containing db_path)",
    )
    parser.add_argument(
        "--days",
        type=float,
        default=2.0,
        help="Window: baseline = newest snapshot at least this many days old (default 2)",
    )
    parser.add_argument(
        "--snapshot-id",
        type=int,
        default=None,
        help="Force a specific baseline snapshot",
    )
    parser.add_argument(
        "--snapshot-path",
        default=None,
        help="Diff against this .sqlite file directly (skips snapshot-table lookup)",
    )
    parser.add_argument(
        "--project",
        default="PixlTagger",
        help="Scope the diff to pictures in this project (default: PixlTagger). Only "
        "changes to its pictures are recovered; everything else is ignored.",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="Diff the whole vault instead of scoping to --project.",
    )
    parser.add_argument(
        "--exclude-tagger-touched",
        action="store_true",
        help="Drop pairs the background tagger wrote a prediction for inside the window "
        "(guards against tagger re-tag churn being misread as human edits)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write changes (default: dry run)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        raise SystemExit(f"Database not found: {args.db_path}")
    if ANOMALY_LABELS is None:
        print(
            "WARNING: could not import pixlstash ANOMALY_LABELS — proceeding WITHOUT the "
            "anomaly-vocabulary filter (every changed tag will be recorded)."
        )

    vault_root = args.vault_root or os.path.dirname(os.path.abspath(args.db_path))

    conn = sqlite3.connect(args.db_path)
    try:
        # Resolve the project scope first so a bad name fails before any decompress/attach.
        project_id = None
        if args.all_projects:
            print("Scope: whole vault (--all-projects).")
        else:
            project_id = _resolve_project_id(conn, args.project)
            print(
                f"Scope: project {args.project!r} (id={project_id}) — pictures outside it "
                "are ignored."
            )
        if args.snapshot_path:
            abs_snapshot = args.snapshot_path
            baseline_at = datetime.utcnow() - timedelta(days=args.days)
            print(f"Baseline snapshot (explicit path): {abs_snapshot}")
        else:
            rel, baseline_at = _pick_baseline(conn, args.days, args.snapshot_id)
            abs_snapshot = os.path.join(vault_root, rel)
        if not os.path.exists(abs_snapshot):
            raise SystemExit(
                f"Snapshot file not found: {abs_snapshot}\n"
                "Pass --vault-root or --snapshot-path."
            )

        print(
            f"Window: human edits since {baseline_at:%Y-%m-%d %H:%M} "
            f"(~{args.days:g} days back). Widen --days to capture more, but stop before "
            "your last bulk auto-tagging run or that churn will be misread as human edits."
        )

        conn.execute("ATTACH DATABASE ? AS base", (abs_snapshot,))
        added, removed = _diff(conn, project_id)

        if args.exclude_tagger_touched:
            touched = _tagger_touched(conn, baseline_at)
            before = len(added) + len(removed)
            added = [p for p in added if p not in touched]
            removed = [p for p in removed if p not in touched]
            dropped = before - (len(added) + len(removed))
            print(f"Excluded {dropped} tagger-touched pair(s).")

        print(f"\nAnomaly tags ADDED in window  → human POS: {len(added)}")
        print(f"Anomaly tags REMOVED in window → human NEG: {len(removed)}")
        for label, pairs in (("POS", added), ("NEG", removed)):
            for pid, tag in pairs[:10]:
                print(f"  {label}  picture {pid}  {tag!r}")
            if len(pairs) > 10:
                print(f"  … and {len(pairs) - 10} more")

        if not args.apply:
            print("\nDRY RUN — no changes written. Re-run with --apply to commit.")
            return

        for pid, tag in added:
            _record(conn, pid, tag, "POS")
        for pid, tag in removed:
            _record(conn, pid, tag, "NEG")
        conn.commit()
        print(f"\nApplied {len(added)} POS + {len(removed)} NEG human labels.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
