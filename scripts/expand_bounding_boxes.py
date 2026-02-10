import argparse
import json
import os
import sqlite3
from typing import Dict, Optional, Tuple

import cv2

from pixlvault.picture_utils import PictureUtils


DEFAULT_SCALE = 1.75


def expand_bbox(bbox, frame_w, frame_h, scale):
    if bbox is None or len(bbox) != 4:
        return None
    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    w = max(1.0, x2 - x1)
    h = max(1.0, y2 - y1)
    half_w = (w * scale) / 2.0
    half_h = (h * scale) / 2.0
    ex1 = int(max(0, min(frame_w - 1, round(cx - half_w))))
    ey1 = int(max(0, min(frame_h - 1, round(cy - half_h))))
    ex2 = int(max(0, min(frame_w, round(cx + half_w))))
    ey2 = int(max(0, min(frame_h, round(cy + half_h))))
    if ex2 <= ex1 or ey2 <= ey1:
        return None
    return [ex1, ey1, ex2, ey2]


def get_media_size(
    file_path: str, size_cache: Dict[str, Optional[Tuple[int, int]]]
) -> Optional[Tuple[int, int]]:
    if file_path in size_cache:
        return size_cache[file_path]

    if not os.path.exists(file_path):
        size_cache[file_path] = None
        return None

    ext = os.path.splitext(file_path)[1].lower()
    size: Optional[Tuple[int, int]] = None
    if ext in [".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"]:
        cap = cv2.VideoCapture(file_path)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if width <= 0 or height <= 0:
            ret, frame = cap.read()
            if ret and frame is not None:
                height, width = frame.shape[:2]
        cap.release()
        if width > 0 and height > 0:
            size = (width, height)
    else:
        img = cv2.imread(file_path)
        if img is not None:
            height, width = img.shape[:2]
            if width > 0 and height > 0:
                size = (width, height)

    size_cache[file_path] = size
    return size


def resolve_path(image_root: str, rel_path: Optional[str]) -> Optional[str]:
    if not rel_path:
        return None
    return PictureUtils.resolve_picture_path(image_root, rel_path)


def expand_table_bboxes(
    cursor: sqlite3.Cursor,
    table: str,
    image_root: str,
    scale: float,
    size_cache: Dict[str, Optional[Tuple[int, int]]],
) -> int:
    cursor.execute(
        f"""
        SELECT {table}.id, {table}.picture_id, {table}.bbox, picture.file_path
        FROM {table}
        JOIN picture ON picture.id = {table}.picture_id
        WHERE {table}.bbox IS NOT NULL
        """
    )
    rows = cursor.fetchall()
    updated = 0

    for row_id, picture_id, bbox_json, rel_path in rows:
        if bbox_json is None:
            continue
        try:
            bbox = json.loads(bbox_json)
        except Exception:
            continue
        full_path = resolve_path(image_root, rel_path)
        if not full_path:
            continue
        size = get_media_size(full_path, size_cache)
        if not size:
            continue
        frame_w, frame_h = size
        expanded = expand_bbox(bbox, frame_w, frame_h, scale)
        if expanded is None:
            continue
        if expanded == bbox:
            continue
        cursor.execute(
            f"UPDATE {table} SET bbox = ? WHERE id = ?",
            (json.dumps(expanded), row_id),
        )
        updated += 1

    return updated


def expand_all_bounding_boxes(db_path: str, image_root: str, scale: float) -> None:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")

    size_cache: Dict[str, Optional[Tuple[int, int]]] = {}

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        face_updates = expand_table_bboxes(
            cursor, "face", image_root, scale, size_cache
        )
        hand_updates = expand_table_bboxes(
            cursor, "hand", image_root, scale, size_cache
        )
        conn.commit()
        print(f"Updated {face_updates} face bboxes and {hand_updates} hand bboxes.")
    except Exception as exc:
        conn.rollback()
        raise exc
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand face/hand bounding boxes and clamp to image borders."
    )
    parser.add_argument("db_path", help="Path to vault.db")
    parser.add_argument(
        "--image-root",
        default=None,
        help="Image root directory (defaults to db directory)",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_SCALE,
        help=f"Expansion scale (default {DEFAULT_SCALE})",
    )
    args = parser.parse_args()

    image_root = args.image_root or os.path.dirname(os.path.abspath(args.db_path))
    expand_all_bounding_boxes(args.db_path, image_root, args.scale)


if __name__ == "__main__":
    main()
