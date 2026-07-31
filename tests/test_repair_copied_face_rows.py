"""The one-off repair for face rows copied onto a differently-sized picture.

``scripts/repair_copied_face_rows.py`` deletes detections that were inherited
from a source picture and never remapped onto the output's canvas. It is
destructive and driven by a heuristic, so the heuristic is worth pinning.

The case that matters most is the third test. A first attempt at this looked for
a bbox *identical* to the original's and missed every real instance in the
library it was written for: a 178x218 reference crop with box
``[0, 11, 178, 218]`` produced copies carrying ``[25, 27, 153, 218]``, which is
neither the original's numbers nor a valid box for an 896x1440 canvas. The test
comparing against the correct *rescale* is what catches that.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from repair_copied_face_rows import find_affected  # noqa: E402


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE picture (
            id INTEGER PRIMARY KEY, width INTEGER, height INTEGER, created_at TEXT
        );
        CREATE TABLE face (
            id INTEGER PRIMARY KEY, picture_id INTEGER, bbox TEXT,
            features BLOB, character_id INTEGER
        );
        """
    )
    return conn


def _picture(conn, pic_id, width, height, created):
    conn.execute(
        "INSERT INTO picture (id, width, height, created_at) VALUES (?,?,?,?)",
        (pic_id, width, height, created),
    )


def _face(conn, face_id, pic_id, bbox, embedding, character_id=None):
    conn.execute(
        "INSERT INTO face (id, picture_id, bbox, features, character_id) "
        "VALUES (?,?,?,?,?)",
        (
            face_id,
            pic_id,
            json.dumps(bbox),
            np.asarray(embedding, dtype=np.float32).tobytes(),
            character_id,
        ),
    )


def test_independent_detections_are_left_alone():
    """Different embeddings means both were really detected. Nothing to repair."""
    conn = _db()
    _picture(conn, 1, 178, 218, "2026-01-01")
    _picture(conn, 2, 896, 1440, "2026-04-01")
    _face(conn, 10, 1, [0, 11, 178, 218], [1, 0, 0, 0])
    _face(conn, 11, 2, [300, 200, 600, 700], [0, 1, 0, 0])

    picture_ids, copied = find_affected(conn)
    assert picture_ids == set()
    assert copied == []


def test_a_correctly_rescaled_copy_is_kept():
    """A copy whose box was scaled to the new canvas is right, so it stays.

    Right box, right person: deleting it would cost a detection pass and a
    character assignment for no gain.
    """
    conn = _db()
    _picture(conn, 1, 100, 200, "2026-01-01")
    _picture(conn, 2, 200, 400, "2026-04-01")  # exactly 2x
    _face(conn, 10, 1, [10, 20, 50, 80], [1, 0, 0, 0])
    _face(conn, 11, 2, [20, 40, 100, 160], [1, 0, 0, 0])  # the same box, doubled

    picture_ids, copied = find_affected(conn)
    assert picture_ids == set(), "a correctly remapped copy was flagged for deletion"
    assert copied == []


def test_a_copy_that_was_never_remapped_is_flagged():
    """The real-world shape: a box valid only on the source's canvas.

    The copy carries neither the original's numbers nor a rescale of them, and
    on an 896x1440 canvas it covers 1.9% of the picture in the top-left corner.
    A rule keyed on "identical bbox" misses this entirely.
    """
    conn = _db()
    _picture(conn, 87, 178, 218, "2026-01-18 08:41:41")
    _picture(conn, 35230, 896, 1440, "2026-04-23 06:44:46")
    _face(conn, 92, 87, [0, 11, 178, 218], [1, 0, 0, 0])
    _face(conn, 54176, 35230, [25, 27, 153, 218], [1, 0, 0, 0])

    picture_ids, copied = find_affected(conn)
    assert picture_ids == {35230}, "the mis-placed copy was not detected"
    assert [entry[0] for entry in copied] == [54176]
    assert 87 not in picture_ids, "the original lost its faces"


def test_the_earliest_picture_is_treated_as_the_original():
    """Direction comes from creation time, not canvas size.

    Copies run both ways here: a small reference crop feeding a large
    generation, and a large generation feeding a larger upscale. Picking the
    smallest canvas as the original gets the second case backwards and would
    clear the picture that actually owns the detection.
    """
    conn = _db()
    _picture(conn, 100, 1920, 1280, "2026-03-09")  # earliest, but the LARGEST
    _picture(conn, 200, 896, 1440, "2026-04-23")
    _face(conn, 10, 100, [64, 20, 128, 84], [1, 0, 0, 0])
    _face(conn, 11, 200, [64, 20, 128, 84], [1, 0, 0, 0])

    picture_ids, _copied = find_affected(conn)
    assert picture_ids == {200}
    assert 100 not in picture_ids, "the earliest picture was treated as the copy"


def test_same_size_duplicates_are_left_alone():
    """Two imports of the same image share an embedding legitimately.

    Same canvas means the box is correct on both, so there is nothing wrong to
    repair -- and these carry character assignments worth keeping.
    """
    conn = _db()
    _picture(conn, 1, 1920, 1280, "2026-03-09")
    _picture(conn, 2, 1920, 1280, "2026-03-10")
    _face(conn, 10, 1, [100, 100, 300, 400], [1, 0, 0, 0], character_id=15)
    _face(conn, 11, 2, [100, 100, 300, 400], [1, 0, 0, 0], character_id=15)

    picture_ids, copied = find_affected(conn)
    assert picture_ids == set()
    assert copied == []


@pytest.mark.parametrize("bad_bbox", ["not json", "[1, 2]", "null"])
def test_an_unparseable_bbox_on_a_copy_is_flagged(bad_bbox):
    """A copy whose box cannot even be read cannot be verified, so it goes."""
    conn = _db()
    _picture(conn, 1, 100, 200, "2026-01-01")
    _picture(conn, 2, 200, 400, "2026-04-01")
    _face(conn, 10, 1, [10, 20, 50, 80], [1, 0, 0, 0])
    conn.execute(
        "INSERT INTO face (id, picture_id, bbox, features, character_id) "
        "VALUES (?,?,?,?,?)",
        (11, 2, bad_bbox, np.asarray([1, 0, 0, 0], dtype=np.float32).tobytes(), None),
    )

    picture_ids, _copied = find_affected(conn)
    assert picture_ids == {2}
