"""Remove face rows that were copied onto a differently-sized picture.

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

**This is deliberately not an Alembic migration.** The detection below is a
heuristic over embeddings, and deleting face rows costs any character assignment
made on them. That is a judgement call about one library's data, not a schema
change every vault should have applied to it unattended. Run it yourself, read
the report, then pass --apply.

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

It deletes **every** face row on an affected picture, not only the copied ones:
``MissingFaceExtractionFinder`` selects on ``~Picture.faces.any()``, so a
picture that keeps even one face row is never re-detected and would be left with
a partial, wrong face set. Once the rows are gone the finder re-detects those
pictures the next time the server runs.

Usage:
    python scripts/repair_copied_face_rows.py [path/to/vault.db]
    python scripts/repair_copied_face_rows.py [path/to/vault.db] --apply

Reports and does nothing without --apply. Take a backup first; there is no undo.
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
    rows = conn.execute(
        """
        SELECT f.id, f.picture_id, f.bbox, f.features, f.character_id,
               p.width, p.height, p.created_at
        FROM face f
        JOIN picture p ON p.id = f.picture_id
        WHERE f.features IS NOT NULL AND f.bbox IS NOT NULL
        """
    ).fetchall()

    by_embedding = defaultdict(list)
    for face_id, pic_id, bbox, features, char_id, width, height, created in rows:
        if not width or not height:
            continue
        key = hashlib.sha1(features).digest()
        by_embedding[key].append(
            (face_id, pic_id, bbox, char_id, width, height, created or "")
        )

    copied = []
    for group in by_embedding.values():
        if len(group) < 2:
            continue
        if len({(entry[4], entry[5]) for entry in group}) < 2:
            # Same embedding on same-sized pictures is a duplicate import, not a
            # cross-canvas copy. Leave it alone.
            continue
        # The earliest picture is the original; the rest inherited its face.
        # Creation time rather than canvas size, because copies run both ways
        # (a small reference crop feeding a large generation, and a large
        # generation feeding a larger upscale).
        original = min(group, key=lambda entry: (entry[6], entry[1]))
        origin_box = _parse_bbox(original[2])
        for entry in group:
            if entry is original:
                continue
            if (entry[4], entry[5]) == (original[4], original[5]):
                continue
            actual = _parse_bbox(entry[2])
            if actual is None or origin_box is None:
                copied.append(entry[:6])
                continue
            scale_x = entry[4] / original[4]
            scale_y = entry[5] / original[5]
            expected = [
                origin_box[0] * scale_x,
                origin_box[1] * scale_y,
                origin_box[2] * scale_x,
                origin_box[3] * scale_y,
            ]
            if _iou(actual, expected) < MIN_IOU:
                copied.append(entry[:6])

    return {int(entry[1]) for entry in copied}, copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", nargs="?", default="vault.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without it the script only reports.",
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
        print(f"face rows that will be gone : {total_on_pictures}")
        print(
            "   (every row on those pictures, not just the copies: the extraction "
            "finder only picks up a picture with no faces at all)"
        )
        print(f"character assignments lost  : {len(assigned)}")
        for face_id, pic_id, bbox, char_id, width, height in assigned:
            print(
                f"   face={face_id} picture={pic_id} ({width}x{height}) "
                f"character_id={char_id} bbox={bbox}"
            )

        if not args.apply:
            print("\nReport only. Re-run with --apply to delete (back up first).")
            return

        conn.execute(
            f"DELETE FROM face WHERE picture_id IN ({placeholders})",
            ids,
        )
        conn.commit()
        print(
            f"\nDeleted {total_on_pictures} face row(s) across {len(picture_ids)} "
            "picture(s). Face extraction will re-detect them on the next server run."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
