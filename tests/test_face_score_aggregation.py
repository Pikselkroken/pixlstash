"""A picture's face-likeness score is the MAX over its faces, never a sum.

A photo of two twins is not twice as similar to one of them as a photo of a
single twin is. Every place that reduces per-face scores to a per-picture score
therefore takes the maximum, and these tests pin that so a future refactor
cannot quietly swap in a sum, a mean, or a count-weighted blend:

- ``_score_best_faces`` (POST /pictures/face-search)
- ``find_pictures_by_character_likeness`` (the Python likeness sort)
- ``find_pictures_by_character_likeness_sql`` (``func.max`` GROUP BY picture_id)
- ``count_pictures_by_character_likeness`` (COUNT DISTINCT picture_id)

The residual effect max cannot remove is that N faces give N chances at a high
score, so a crowd photo is marginally more findable than a portrait. Measured on
a real 45k-face library that is small (mean best score 0.116 at one face vs
0.172 at 33+, and many-face pictures were slightly UNDER-represented among
>=0.7 hits), so it is left alone deliberately rather than normalised away.
"""

from __future__ import annotations

import numpy as np

from pixlstash.routes.pictures._face_search import _score_best_faces


def _unit(vec) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    return arr / max(float(np.linalg.norm(arr)), 1e-8)


def test_two_matching_faces_score_the_same_as_one():
    """The twins case, stated directly.

    One picture holds a single face matching the query; the other holds two
    faces that each match it just as well. The second must not outrank the
    first, and must not exceed the similarity of a single face.
    """
    query = _unit([1, 0, 0, 0])
    twin = _unit([0.9, 0.436, 0, 0])  # cosine 0.9 with the query

    pic_ids, scores, face_ids, _ = _score_best_faces(
        [query],
        [
            (1, [(10, twin)]),
            (2, [(20, twin), (21, twin)]),
        ],
        "max",
    )

    by_pic = dict(zip(pic_ids, scores))
    assert by_pic[1] == by_pic[2], (
        "a picture of two twins scored differently from a picture of one twin"
    )
    assert by_pic[2] <= 1.0
    assert (
        by_pic[2] == np.float32(np.dot(twin, query)).astype(np.float32)
        or abs(float(by_pic[2]) - float(np.dot(twin, query))) < 1e-5
    ), "the score is not simply the best face's similarity"
    assert set(face_ids) <= {10, 20, 21}


def test_many_weak_faces_never_beat_one_strong_face():
    """Twenty poor matches must not add up to one good match.

    This is the shape the bug would take if the reduction ever became a sum or a
    count-weighted average: a crowd photo containing nobody in particular would
    outrank a portrait of the person being searched for.
    """
    query = _unit([1, 0, 0, 0])
    strong = _unit([0.95, 0.312, 0, 0])
    weak = _unit([0.2, 0.98, 0, 0])

    pic_ids, scores, _face_ids, _ = _score_best_faces(
        [query],
        [
            (1, [(100, strong)]),
            (2, [(200 + i, weak) for i in range(20)]),
        ],
        "max",
    )

    by_pic = dict(zip(pic_ids, scores))
    assert by_pic[1] > by_pic[2], "a crowd of weak matches outranked a strong match"
    assert by_pic[2] < 0.5, f"20 weak faces accumulated into {by_pic[2]}"


def test_the_winning_face_is_the_one_reported():
    """The picture's score and its named face_id come from the same detection.

    A bulk character assignment writes to that face row, so a score taken from
    one face and an id taken from another would tag the wrong person.
    """
    query = _unit([1, 0, 0, 0])
    good = _unit([0.97, 0.243, 0, 0])
    bad = _unit([0, 1, 0, 0])

    pic_ids, scores, face_ids, per_query = _score_best_faces(
        [query],
        [(7, [(70, bad), (71, good)])],
        "max",
    )

    assert pic_ids == [7]
    assert face_ids == [71], "the non-matching face was named as the winner"
    assert abs(float(scores[0]) - float(np.dot(good, query))) < 1e-5
    # reference_likeness travels from the same winning face.
    assert abs(float(per_query[0][0]) - float(np.dot(good, query))) < 1e-5


def test_sql_and_python_likeness_paths_both_reduce_with_max():
    """Guard the two sort implementations against drifting apart.

    They are separate code paths over the same data, so a change to one is easy
    to make without the other. Asserted by reading the source rather than by
    running a query, because the SQL path needs a live scalar function
    registration and this is a statement about the aggregation choice.
    """
    from pathlib import Path

    src = Path("pixlstash/scoring/character_likeness.py").read_text()
    assert "max(picture_likeness_map[pic_id], likeness)" in src, (
        "the Python likeness sort no longer reduces per-picture scores with max"
    )
    assert "func.max(" in src and "group_by(Face.picture_id)" in src, (
        "the SQL likeness sort no longer reduces per-picture scores with MAX"
    )
    assert "func.count(func.distinct(Face.picture_id))" in src, (
        "the likeness count no longer counts DISTINCT pictures, so a picture "
        "with several faces would be counted several times"
    )
