#!/usr/bin/env python3
"""Seed byte-identical duplicates into the e2e vault and detect them.

The duplicate queue is the one e2e surface the harness cannot reach through the
UI or the API, for two reasons that are both properties of the test backend
rather than of the feature:

1. **Neither import path can add a duplicate.** ``POST /pictures/import``
   refuses outright without the face-extraction worker, and a reference folder's
   initial scan is driven by the work planner. Both are off, because
   ``serve_e2e_backend.py`` boots with ``disable_background_workers: true``.
2. **A duplicate group only exists after a scan.** ``GET /dedup/groups`` reads
   persisted ``DedupGroup`` rows; the rows are written by ``DedupScanTask``,
   which the same disabled work planner never runs. ``POST /dedup/scan`` writes
   a ``pending`` row and nothing ever picks it up.

Enabling the workers for the whole suite is not the answer: they would start
face extraction, quality scoring and captioning over the entire fixture on every
e2e run. So this script does the two things the disabled workers would have
done, and nothing else:

* copies a handful of fixture images to new files and inserts ``picture`` rows
  for them, which is exactly what makes them byte-identical duplicates (the
  ``pixel_sha`` is a hash of the bytes, and tier 1 groups on
  ``(pixel_sha, size_bytes)``);
* runs the **real** ``DedupScanTask.run_scan_in_session`` against the same
  database, so the groups, signatures, covers and evidence the spec then drives
  are produced by production code rather than fabricated here.

It writes only to the throwaway work directory ``serve_e2e_backend.py`` creates,
never to the committed ``test-data/`` fixture, and it prints a JSON summary on
stdout for the spec to read.

The suite shares one mutable backend, so the same script also **undoes** its own
work: ``--cleanup`` removes the seeded pictures and every row that hangs off
them, unstacks and drops the stacks the verdicts created, and clears the dedup
tables. The spec runs it after its last case, because a set left counting a
picture that no longer exists is how a duplicate run breaks the export and
tag-health specs that come after it.

Usage::

    python seed_dedup_fixture.py [--groups 3] [--copies 2]
    python seed_dedup_fixture.py --cleanup
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DATA = Path(os.environ.get("PIXLSTASH_E2E_DATA") or (REPO_ROOT / "test-data"))
# Mirrors serve_e2e_backend.WORK_DIR exactly; the two must not drift.
WORK_DIR = Path(tempfile.gettempdir()) / f"pixlstash-e2e-{SRC_DATA.name}"
IMAGE_ROOT = WORK_DIR / "images"
DB_PATH = IMAGE_ROOT / "vault.db"

# The marker every seeded row carries, so a re-run replaces its own rows instead
# of stacking a second set of duplicates on top of the first.
SEED_PREFIX = "e2e-dedup-seed-"


def _source_pictures(conn: sqlite3.Connection, wanted: int) -> list[sqlite3.Row]:
    """Pick fixture pictures that can carry a duplicate.

    Only unstacked, undeleted rows with a hash and a file that is actually on
    disk: a group whose members do not resolve to real images would render as
    broken thumbnails and the spec would be asserting on a placeholder.
    """
    rows = conn.execute(
        "SELECT * FROM picture "
        "WHERE deleted = 0 AND stack_id IS NULL AND pixel_sha IS NOT NULL "
        "AND file_path IS NOT NULL AND file_path NOT LIKE ? "
        "ORDER BY id",
        (f"%{SEED_PREFIX}%",),
    ).fetchall()
    picked = []
    for row in rows:
        if _on_disk(row["file_path"]).is_file():
            picked.append(row)
        if len(picked) >= wanted:
            break
    return picked


def _on_disk(file_path: str) -> Path:
    """Resolve a stored ``picture.file_path`` to a real file.

    Managed pictures store a bare file name relative to the image root; a
    reference-folder picture stores an absolute path. Both have to resolve, or
    the seed silently skips every managed row in the fixture.
    """
    path = Path(file_path)
    return path if path.is_absolute() else IMAGE_ROOT / path


# Every table that hangs off a picture. A seeded row acquires children the
# moment a verdict runs the metadata union (tags and set / project membership
# are copied onto every member), so removing the picture without these would
# leave a set counting a picture that no longer exists - which is exactly how a
# dedup run breaks the export and tag-health specs that run after it.
_PICTURE_CHILD_TABLES = (
    "dedupgroupmember",
    "detection",
    "face",
    "guest_score",
    "picturelikenessqueue",
    "pictureprojectmember",
    "picturesetmember",
    "quality",
    "tag",
    "tag_prediction",
    "tag_suggestion",
)

# The dedup state a run leaves behind. Cleared wholesale: verdict memory is
# permanent by design, so a leftover row would leave the next run's queue empty.
_DEDUP_TABLES = ("dedupgroupmember", "dedupgroup", "dedupverdict", "dedupscan")


def _clear_previous_seed(conn: sqlite3.Connection) -> int:
    """Remove every trace of an earlier run and return how many pictures went.

    The suite shares one mutable backend, so this is not merely a convenience
    for re-running the seed: it is what keeps the duplicate queue's specs
    reversible for the specs that run after them. It restores the pictures the
    verdicts stacked, drops the stack rows those verdicts created, and clears
    the dedup tables.
    """
    seeded = conn.execute(
        "SELECT id, file_path, stack_id FROM picture WHERE file_path LIKE ?",
        (f"%{SEED_PREFIX}%",),
    ).fetchall()
    for table in _DEDUP_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError as exc:
            print(f"[seed-dedup] skip clearing {table}: {exc}", file=sys.stderr)
    if not seeded:
        conn.commit()
        return 0

    ids = [int(row["id"]) for row in seeded]
    stack_ids = {int(row["stack_id"]) for row in seeded if row["stack_id"]}
    placeholders = ",".join("?" * len(ids))
    for table in _PICTURE_CHILD_TABLES:
        try:
            conn.execute(
                f"DELETE FROM {table} WHERE picture_id IN ({placeholders})", ids
            )
        except sqlite3.OperationalError as exc:
            # A table the fixture does not carry is not an error worth aborting
            # a cleanup for, but it must not pass unremarked either.
            print(f"[seed-dedup] skip cleaning {table}: {exc}", file=sys.stderr)
    conn.execute(f"DELETE FROM picture WHERE id IN ({placeholders})", ids)
    for row in seeded:
        path = _on_disk(row["file_path"])
        if path.is_file():
            path.unlink()

    # A stack a verdict created held nothing but seeded copies and the fixture
    # picture they were cloned from, and that picture was unstacked when the
    # seed picked it. Unstack it again and drop the stack row, or the stacks
    # spec finds a stack the fixture never had.
    if stack_ids:
        stack_places = ",".join("?" * len(stack_ids))
        conn.execute(
            "UPDATE picture SET stack_id = NULL, stack_position = NULL "
            f"WHERE stack_id IN ({stack_places})",
            list(stack_ids),
        )
        try:
            conn.execute(
                f"DELETE FROM picturestack WHERE id IN ({stack_places})",
                list(stack_ids),
            )
        except sqlite3.OperationalError as exc:
            print(f"[seed-dedup] skip deleting stacks: {exc}", file=sys.stderr)
    conn.commit()
    return len(ids)


def _insert_copies(
    conn: sqlite3.Connection, source: sqlite3.Row, copies: int, index: int
) -> list[int]:
    """Copy one picture's file `copies` times and insert a row per copy.

    Every column is carried over from the source except the identity ones, so
    the copy is byte-identical **and** row-identical where it matters: the same
    ``pixel_sha`` and ``size_bytes`` are what tier 1 groups on.
    """
    columns = [row[1] for row in conn.execute("PRAGMA table_info(picture)")]
    carried = [c for c in columns if c not in {"id", "file_path", "stack_id"}]
    source_path = _on_disk(source["file_path"])
    stored_absolute = Path(source["file_path"]).is_absolute()
    new_ids: list[int] = []
    for copy_index in range(copies):
        name = f"{SEED_PREFIX}{index}-{copy_index}{source_path.suffix}"
        target = source_path.with_name(name)
        shutil.copyfile(source_path, target)
        # Store the path the same way the source did, or the server resolves it
        # against the image root twice and serves a 404 for every thumbnail.
        stored = str(target) if stored_absolute else name
        values = [source[c] for c in carried]
        cursor = conn.execute(
            f"INSERT INTO picture (file_path, {', '.join(carried)}) "
            f"VALUES ({', '.join('?' * (len(carried) + 1))})",
            [stored, *values],
        )
        new_ids.append(int(cursor.lastrowid))
    conn.commit()
    return new_ids


def _run_scan() -> dict:
    """Run the real scan task against the seeded database.

    Imported lazily and only here: this is the one part of the script that needs
    the backend package, and a caller that only wants the rows should not pay for
    importing half the server to get them.
    """
    from sqlmodel import Session, create_engine, select

    from pixlstash.db_models.dedup import SCAN_PENDING, DedupGroup, DedupScan
    from pixlstash.services.dedup_tier_service import DedupScope, TierPolicy
    from pixlstash.tasks.dedup_scan_task import DedupScanTask

    policy = TierPolicy()
    scope = DedupScope()
    engine = create_engine(f"sqlite:///{DB_PATH}")
    with Session(engine) as session:
        row = session.exec(
            select(DedupScan).where(DedupScan.scope_key == scope.key)
        ).first()
        if row is None:
            row = DedupScan(scope_key=scope.key)
        row.scope_type = scope.scope_type.value
        row.scope_id = scope.scope_id
        row.tiers = json.dumps([tier.value for tier in policy.tiers])
        row.threshold = float(policy.threshold)
        row.status = SCAN_PENDING
        row.error = None
        session.add(row)
        session.commit()
        session.refresh(row)
        summary = DedupScanTask.run_scan_in_session(session, int(row.id))
        signatures = session.exec(
            select(DedupGroup.signature)
            .where(DedupGroup.resolved.is_(False))
            .order_by(DedupGroup.confidence.desc(), DedupGroup.id.asc())
        ).all()
    return {"scan": summary, "signatures": [str(s) for s in signatures]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--groups", type=int, default=3, help="How many duplicate groups to seed."
    )
    parser.add_argument(
        "--copies",
        type=int,
        default=2,
        help="Extra copies per group. 2 gives a three-member group.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=(
            "Remove the seeded pictures, the stacks the verdicts created and "
            "all dedup state, then exit. The spec runs this after its last "
            "case so the shared backend is handed on unchanged."
        ),
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(
            f"[seed-dedup] ERROR: no e2e vault at {DB_PATH}. "
            "Start the e2e backend first (Playwright's webServer does).",
            file=sys.stderr,
        )
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        removed = _clear_previous_seed(conn)
        if args.cleanup:
            print(json.dumps({"removed_pictures": removed}))
            return 0
        sources = _source_pictures(conn, args.groups)
        if len(sources) < args.groups:
            print(
                f"[seed-dedup] ERROR: only {len(sources)} usable fixture pictures "
                f"for {args.groups} groups.",
                file=sys.stderr,
            )
            return 1
        seeded = []
        for index, source in enumerate(sources):
            copies = _insert_copies(conn, source, args.copies, index)
            seeded.append(
                {"source_picture_id": int(source["id"]), "copy_picture_ids": copies}
            )
    finally:
        conn.close()

    result = _run_scan()
    result["seeded"] = seeded
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
