"""Report face rows that were copied onto generated pictures.

Plugin and ComfyUI I2I outputs used to inherit their source picture's face rows
without always remapping the bounding box. A bbox is pixel coordinates, so on a
larger canvas the source's numbers collapse toward the top-left corner and
capture no face at all. The plugin path also copied ``features``, so each copy
carried the source's embedding: a single picture could accumulate dozens of
different people's identities (81 observed in one library) and then match nearly
every face search on merit, because every one of those embeddings really is a
real face of a real person.

Both copy paths now fail closed and copy nothing they cannot place
(``image_plugins/service.py`` and ``comfyui_service._copy_face_assignments``),
so this script is a one-off repair for rows written before that fix.

**This is deliberately report-only.** Detection remains partly heuristic, and
deleting face rows costs character assignments and manually drawn boxes. Safe
repair requires loading the real output image and running the configured face
model so the server can reconcile real detections. A standalone SQLite script
cannot do that, so ``--apply`` fails loudly instead of deleting guessed rows.

Detection works in two steps.

First, group faces by their ``features`` blob. Two independent detections never
produce byte-identical float32 embeddings, so a blob shared across pictures of
*different* sizes means every member but the earliest is a copy.

Second, ask whether each copy's bbox was remapped correctly. The original's box
scaled by the canvas ratio is where the face should be; a copy whose actual box
overlaps that by less than ``MIN_IOU`` was never remapped and is pointing at the
wrong region. Copies that were rescaled properly are left alone -- right box,
right person, no reason to touch them.

Comparing against the *rescale* rather than against the original's raw numbers
matters: the boxes that started this were not verbatim copies. A 178x218
reference crop with box ``[0, 11, 178, 218]`` produced copies carrying
``[25, 27, 153, 218]`` on 896x1440 and 1280x1920 canvases -- different numbers,
still describing a region only a ~178x218 canvas has, and covering 1.9% of the
picture in the top-left corner.

``Picture.source_picture_id`` generation provenance additionally catches the
cases embedding grouping cannot: same-size generated copies and historical rows
with ``features IS NULL``. These are reported for real whole-picture
re-extraction; provenance does not make blind deletion safe.

Usage:
    python scripts/repair_copied_face_rows.py [path/to/vault.db]
    python scripts/repair_copied_face_rows.py [path/to/vault.db] --apply

Always reports only. ``--apply`` exits with an explanation and changes nothing.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict

# A copy whose box overlaps the correct rescale by at least this much was
# remapped properly and is left alone. Generous on purpose: the failure mode
# being repaired misses by an order of magnitude, not by rounding.
MIN_IOU = 0.5


def _iou(a, b) -> float:
    """Intersection-over-union of two ``[x1, y1, x2, y2]`` boxes."""
    if not a or not b or len(a) != 4 or len(b) != 4:
        return 0.0
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _parse_bbox(raw):
    try:
        box = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        return [float(v) for v in box]
    except (TypeError, ValueError):
        return None


def find_affected(conn: sqlite3.Connection):
    """Return ``(picture_ids, copied_rows)`` for badly-placed copied faces.

    Args:
        conn: An open connection to the vault database.

    Returns:
        A ``(set_of_picture_ids, list_of_row_tuples)`` pair, where each row
        tuple is ``(face_id, picture_id, bbox, character_id, width, height)``.
    """
    picture_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(picture)").fetchall()
    }
    face_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(face)").fetchall()
    }
    source_expr = (
        "p.source_picture_id" if "source_picture_id" in picture_columns else "NULL"
    )
    frame_expr = "f.frame_index" if "frame_index" in face_columns else "0"
    index_expr = "f.face_index" if "face_index" in face_columns else "0"
    rows = conn.execute(
        f"""
        SELECT f.id, f.picture_id, f.bbox, f.features, f.character_id,
               p.width, p.height, p.created_at, {source_expr},
               {frame_expr}, {index_expr}
        FROM face f
        JOIN picture p ON p.id = f.picture_id
        WHERE f.bbox IS NOT NULL
        """
    ).fetchall()

    by_embedding = defaultdict(list)
    for row in rows:
        features = row[3]
        if not row[5] or not row[6] or features is None:
            continue
        by_embedding[hashlib.sha1(features).digest()].append(row)

    copied = []
    copied_face_ids: set[int] = set()

    def add_report(entry) -> None:
        if int(entry[0]) in copied_face_ids:
            return
        copied_face_ids.add(int(entry[0]))
        copied.append((entry[0], entry[1], entry[2], entry[4], entry[5], entry[6]))

    for group in by_embedding.values():
        if len(group) < 2:
            continue
        if len({(entry[5], entry[6]) for entry in group}) < 2:
            # Without generation provenance, same-size byte-identical rows can
            # be legitimate duplicate imports. The provenance pass below handles
            # generated copies without widening this heuristic.
            continue
        original = min(group, key=lambda entry: (entry[7] or "", entry[1]))
        origin_box = _parse_bbox(original[2])
        for entry in group:
            if entry is original:
                continue
            if (entry[5], entry[6]) == (original[5], original[6]):
                continue
            actual = _parse_bbox(entry[2])
            if actual is None or origin_box is None:
                add_report(entry)
                continue
            scale_x = entry[5] / original[5]
            scale_y = entry[6] / original[6]
            expected = [
                origin_box[0] * scale_x,
                origin_box[1] * scale_y,
                origin_box[2] * scale_x,
                origin_box[3] * scale_y,
            ]
            if _iou(actual, expected) < MIN_IOU:
                add_report(entry)

    # Provenance catches same-size generated copies and old copied manual rows
    # with NULL features. These are re-extraction candidates, not deletion
    # targets: only inference over the target image can establish its real faces.
    faces_by_picture: dict[int, list[tuple]] = defaultdict(list)
    for row in rows:
        faces_by_picture[int(row[1])].append(row)
    for entry in rows:
        source_picture_id = entry[8]
        if source_picture_id is None:
            continue
        for source in faces_by_picture.get(int(source_picture_id), []):
            same_slot = (entry[9], entry[10]) == (source[9], source[10])
            same_features = (
                entry[3] is not None and source[3] is not None and entry[3] == source[3]
            )
            same_null_box = (
                entry[3] is None
                and source[3] is None
                and _parse_bbox(entry[2]) == _parse_bbox(source[2])
            )
            if same_slot and (same_features or same_null_box):
                add_report(entry)
                break

    copied.sort(key=lambda entry: (int(entry[1]), int(entry[0])))
    return {int(entry[1]) for entry in copied}, copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", nargs="?", default="vault.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Refused: safe repair requires real model re-extraction.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.db_path):
        print(f"Error: database not found at {args.db_path}")
        sys.exit(1)

    conn = sqlite3.connect(args.db_path)
    try:
        picture_ids, copied = find_affected(conn)
        if not picture_ids:
            print("No copied face rows found. Nothing to do.")
            return

        placeholders = ",".join("?" for _ in picture_ids)
        ids = sorted(picture_ids)
        total_on_pictures = conn.execute(
            f"SELECT COUNT(*) FROM face WHERE picture_id IN ({placeholders})", ids
        ).fetchone()[0]
        assigned = [entry for entry in copied if entry[3] is not None]

        print(f"copied face rows found      : {len(copied)}")
        print(f"pictures affected           : {len(picture_ids)}")
        print(f"face rows needing review    : {total_on_pictures}")
        print(
            "   (re-extract each whole picture with the configured face model; "
            "do not delete only the reported rows)"
        )
        print(f"assigned copied candidates  : {len(assigned)}")
        for face_id, pic_id, bbox, char_id, width, height in assigned:
            print(
                f"   face={face_id} picture={pic_id} ({width}x{height}) "
                f"character_id={char_id} bbox={bbox}"
            )

        if not args.apply:
            print("\nReport only. No rows were changed.")
            return

        print(
            "\nRefusing --apply: safe repair requires real face re-extraction "
            "and assignment reconciliation in the running server; this SQLite "
            "script cannot prove which legitimate/manual rows to preserve.",
            file=sys.stderr,
        )
        sys.exit(2)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
