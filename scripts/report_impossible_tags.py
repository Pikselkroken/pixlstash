"""Dry-run report: what would the "Impossible tags" grid filters surface and clear?

Validates the impossible-tag plan (docs/reviews/2026-06-impossible-tags-review-plan.md)
against the real vault BEFORE the endpoints/UI are built. Read-only: opens the vault in
SQLite read-only mode and never writes. Shares the exact classifier the scan service uses
(pixlstash.utils.service.person_tags) so the report can't drift from what would ship.

It prints, for the chosen scope:
  * base rates — person-tagged pictures, and how many have no detected face,
  * per named-filter counts — People-tags-on-"no humans" / Face-tags-no-face /
    People-tags-on-an-object (pictures and tags each would flag),
  * a histogram of which person-tags occur on no-face pictures (refine PERSON_TAGS), and
  * samples to spot-check each signal.

Usage (from the repo root, using the project venv):
    .venv/bin/python scripts/report_impossible_tags.py
    .venv/bin/python scripts/report_impossible_tags.py --db vault.db --out tmp/impossible.csv
"""

import argparse
import csv
import os
import sqlite3
import sys
from collections import Counter

# Share the taxonomy + classifier with the in-app scan service so they can't drift.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pixlstash.utils.service.person_tags import (  # noqa: E402
    OBJECT_META_TAGS,
    PERSON_TAGS,
    SOURCE_NO_FACE,
    SOURCE_NO_HUMANS,
    SOURCE_OBJECT,
    is_face_requiring,
    plan_strips,
)

_PERSON_TAGS_LOWER = sorted(PERSON_TAGS)
# Per-candidate we load person-tags AND the object meta-tags (evidence the classifier
# needs); the candidate requirement itself is "carries a person-tag".
_LOAD_TAGS_LOWER = sorted(PERSON_TAGS | OBJECT_META_TAGS)

# Map each suggestion source to its user-facing filter name, for the report.
_FILTER_NAMES = {
    SOURCE_NO_HUMANS: 'People tags on "no humans"',
    SOURCE_NO_FACE: "Face tags, no face",
    SOURCE_OBJECT: "People tags on an object",
}


def resolve_db(path: str | None) -> str:
    """Resolve the vault path, following the common ./vault.db symlink."""
    if path:
        return os.path.abspath(path)
    for candidate in ("vault.db", os.path.expanduser("~/Projects/pixlstash/vault.db")):
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    sys.exit("ERROR: no --db given and no vault.db found in cwd.")


def resolve_project_id(conn, project: str | None) -> int | None:
    """Resolve a --project name to its id (None = whole vault)."""
    if not project:
        return None
    row = conn.execute(
        "SELECT id FROM project WHERE name = ? COLLATE NOCASE", (project,)
    ).fetchone()
    if row is None:
        names = [r[0] for r in conn.execute("SELECT name FROM project ORDER BY id")]
        sys.exit(f"ERROR: no project named {project!r}. Known projects: {names}")
    return int(row[0])


def _ph(values) -> str:
    return ",".join("?" * len(values))


def load_candidates(conn, project_id: int | None):
    """Return [(picture_id, description, file_path, [tags])] for candidates.

    A candidate is a non-deleted picture with no real detected face that carries at
    least one person-tag. ``tags`` includes its person-tags and any object meta-tags.
    """
    proj = " AND p.project_id = ?" if project_id is not None else ""
    person_ph = _ph(_PERSON_TAGS_LOWER)
    load_ph = _ph(_LOAD_TAGS_LOWER)

    cand_sql = (
        "SELECT p.id, p.description, p.file_path FROM picture p "
        "WHERE p.deleted = 0" + proj + " "
        "AND NOT EXISTS (SELECT 1 FROM face f "
        "                WHERE f.picture_id = p.id AND f.face_index != -1) "
        f"AND EXISTS (SELECT 1 FROM tag t "
        f"            WHERE t.picture_id = p.id AND lower(t.tag) IN ({person_ph}))"
    )
    cand_params: list = list(_PERSON_TAGS_LOWER)
    if project_id is not None:
        cand_params.append(project_id)
    cand_rows = conn.execute(cand_sql, cand_params).fetchall()

    tag_sql = (
        f"SELECT t.picture_id, t.tag FROM tag t WHERE lower(t.tag) IN ({load_ph}) "
        "AND EXISTS (SELECT 1 FROM picture p "
        "            WHERE p.id = t.picture_id AND p.deleted = 0" + proj + " "
        "            AND NOT EXISTS (SELECT 1 FROM face f "
        "                            WHERE f.picture_id = p.id AND f.face_index != -1))"
    )
    tag_params: list = list(_LOAD_TAGS_LOWER)
    if project_id is not None:
        tag_params.append(project_id)
    tags_by_pic: dict[int, list[str]] = {}
    for pic_id, tag in conn.execute(tag_sql, tag_params):
        tags_by_pic.setdefault(pic_id, []).append(tag)

    return [
        (pid, desc, file_path, tags_by_pic.get(pid, []))
        for pid, desc, file_path in cand_rows
    ]


def count_person_tagged(conn, project_id: int | None) -> int:
    """Total non-deleted pictures carrying any person-tag, regardless of face."""
    proj = " AND p.project_id = ?" if project_id is not None else ""
    ph = _ph(_PERSON_TAGS_LOWER)
    sql = (
        "SELECT COUNT(*) FROM picture p WHERE p.deleted = 0" + proj + " "
        f"AND EXISTS (SELECT 1 FROM tag t "
        f"            WHERE t.picture_id = p.id AND lower(t.tag) IN ({ph}))"
    )
    params: list = list(_PERSON_TAGS_LOWER)
    if project_id is not None:
        params.append(project_id)
    return int(conn.execute(sql, params).fetchone()[0])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=None, help="Path to vault.db (default ./vault.db).")
    ap.add_argument(
        "--project",
        default="",
        help="Scope to this project name (default: whole vault, since person-tags live "
        "across the library, not the PixlTagger anomaly-training project). Pass a name "
        "to narrow.",
    )
    ap.add_argument("--out", default=None, help="Optional CSV of the per-picture plan.")
    args = ap.parse_args()

    db_path = resolve_db(args.db)
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        project_id = resolve_project_id(conn, args.project or None)
        person_tagged = count_person_tagged(conn, project_id)
        candidates = load_candidates(conn, project_id)
    finally:
        conn.close()

    scope = f"project {args.project!r}" if (args.project or "") else "whole vault"
    print(f"Vault:  {db_path}")
    print(f"Scope:  {scope}")
    print(f"Person-tagged pictures:        {person_tagged}")
    print(f"  of those, no detected face:  {len(candidates)}  (the candidate set)")

    if not candidates:
        print("\nNo candidates — nothing to strip.")
        return

    pics_per_source: Counter = Counter()
    tags_per_source: Counter = Counter()
    tag_histogram: Counter = Counter()
    rows_out: list[dict] = []
    samples: dict[str, list] = {
        SOURCE_NO_HUMANS: [],
        SOURCE_NO_FACE: [],
        SOURCE_OBJECT: [],
    }

    for pid, desc, file_path, tags in candidates:
        tag_histogram.update(t.strip().lower() for t in tags)
        plan = plan_strips(desc, tags)
        source = plan["source"]
        flag = sorted(plan["flag"])
        if not source or not flag:
            continue
        pics_per_source[source] += 1
        tags_per_source[source] += len(flag)
        rows_out.append(
            {
                "picture_id": pid,
                "source": source,
                "score": plan["score"],
                "flagged_tags": "|".join(flag),
                "description": (desc or "").replace("\n", " ")[:200],
                "file_path": file_path or "",
            }
        )
        if len(samples[source]) < 12:
            samples[source].append((pid, desc, flag))

    print("\nPer named-filter (what the grid would surface / clear):")
    for source in (SOURCE_NO_HUMANS, SOURCE_NO_FACE, SOURCE_OBJECT):
        print(
            f"  {_FILTER_NAMES[source]:<28} ({source}): "
            f"{pics_per_source[source]} pictures, {tags_per_source[source]} tags"
        )
    print(
        f"  TOTAL: {sum(pics_per_source.values())} pictures, "
        f"{sum(tags_per_source.values())} tags"
    )

    print("\nPerson-tags seen on no-face candidates (refine PERSON_TAGS from this):")
    for tag, n in tag_histogram.most_common(40):
        mark = "  (face-requiring)" if is_face_requiring(tag) else ""
        print(f"  {n:>6}  {tag}{mark}")

    for source in (SOURCE_NO_HUMANS, SOURCE_NO_FACE, SOURCE_OBJECT):
        if not samples[source]:
            continue
        print(f"\nSample — {_FILTER_NAMES[source]}:")
        for pid, desc, flag in samples[source]:
            snippet = (desc or "").replace("\n", " ")[:80]
            print(f"  #{pid}  {snippet!r}  → strip {flag}")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "picture_id",
                    "source",
                    "score",
                    "flagged_tags",
                    "description",
                    "file_path",
                ],
            )
            w.writeheader()
            w.writerows(rows_out)
        print(f"\nWrote {len(rows_out)} rows to {args.out}")


if __name__ == "__main__":
    main()
