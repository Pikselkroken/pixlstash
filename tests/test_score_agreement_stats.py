"""Tests for the smart-score vs user-score agreement matrix in /pictures/stats.

Two layers:

* the rank coefficient on its own, against hand-computable contingency tables —
  tau-b is the part where a sign error or a wrong tie correction would be
  invisible in a rendered heatmap;
* the endpoint, which has to place pictures in the right cells, treat score 0 and
  NULL alike as unrated, and — the load-bearing one — NOT apply the score /
  smart-score-bucket filters to itself, because a widget whose own click
  collapses it to a single cell is a dead end.
"""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.db_models import Picture
from pixlstash.server import Server
from pixlstash.utils.service.picture_stats import (
    AGREEMENT_MIN_PAIRS,
    _kendall_tau_b,
    clear_stats_cache,
)
from tests.utils import upload_pictures_and_wait

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")


# ── The coefficient ────────────────────────────────────────────────────────


def test_tau_b_perfect_concordance_is_one():
    """A strictly diagonal table means every untied pair agrees."""
    matrix = [[5, 0, 0], [0, 5, 0], [0, 0, 5]]
    assert _kendall_tau_b(matrix) == 1.0


def test_tau_b_perfect_discordance_is_minus_one():
    """The anti-diagonal is the same strength of relationship, inverted."""
    matrix = [[0, 0, 5], [0, 5, 0], [5, 0, 0]]
    assert _kendall_tau_b(matrix) == -1.0


def test_tau_b_is_zero_for_a_uniform_table():
    """Equal counts everywhere: concordant and discordant pairs cancel exactly."""
    matrix = [[4, 4, 4], [4, 4, 4], [4, 4, 4]]
    assert _kendall_tau_b(matrix) == 0.0


def test_tau_b_matches_a_hand_computed_table():
    """A small asymmetric table, checked against the tau-b definition by hand.

    matrix = [[2, 1],
              [1, 3]]
    Concordant: the 2 cell pairs with the 3 cell (2*3=6). Discordant: the 1 cell
    at (0,1) pairs with the 1 cell at (1,0) (1*1=1). n=7, all_pairs=21,
    row ties = 3 + 6 = 9, col ties = 3 + 6 = 9, so tau-b = 5 / sqrt(12*12) = 5/12.
    """
    assert _kendall_tau_b([[2, 1], [1, 3]]) == 5 / 12


def test_tau_b_is_none_when_a_variable_is_constant():
    """One populated row means the user rated everything the same.

    There is no pair that differs on that axis, so the coefficient is undefined
    rather than 0 — reporting 0 would claim "no relationship" from no evidence.
    """
    assert _kendall_tau_b([[3, 4, 5], [0, 0, 0], [0, 0, 0]]) is None


def test_tau_b_is_none_below_two_observations():
    assert _kendall_tau_b([[1, 0], [0, 0]]) is None
    assert _kendall_tau_b([[0, 0], [0, 0]]) is None


# ── The endpoint ───────────────────────────────────────────────────────────


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _upload_picture(client, filename):
    img_path = os.path.join(PICTURES_DIR, filename)
    with open(img_path, "rb") as f:
        result = upload_pictures_and_wait(
            client, [("file", (filename, f, "image/png"))]
        )
    assert result["status"] == "completed"
    return (result.get("results") or [])[0]["picture_id"]


def _set_scores(server, picture_id, score, smart_score):
    def apply(session):
        pic = session.get(Picture, picture_id)
        assert pic is not None
        pic.score = score
        pic.smart_score = smart_score
        session.add(pic)
        session.commit()

    server.vault.db.run_task(apply)


def _agreement(client, query=""):
    clear_stats_cache()
    resp = client.get(f"/pictures/stats?include=picture{query}")
    assert resp.status_code == 200
    return resp.json()["score_agreement"]


def _cell(agreement, score, bucket):
    for cell in agreement["cells"]:
        if cell["score"] == score and cell["bucket"] == bucket:
            return cell["count"]
    raise AssertionError(f"no cell for score={score} bucket={bucket}")


def test_agreement_places_pictures_in_the_right_cells():
    """A rated picture lands in the cell for its star and its smart-score bucket."""
    temp_dir, client, server = _setup()
    try:
        low = _upload_picture(client, "Bad1.png")
        high = _upload_picture(client, "Bad2.png")
        _set_scores(server, low, 1, 1.4)  # -> row 1, bucket 1-2
        _set_scores(server, high, 5, 4.8)  # -> row 5, bucket 4-5

        agreement = _agreement(client)

        assert len(agreement["cells"]) == 20, "the grid is dense, all 20 cells"
        assert _cell(agreement, 1, "1-2") == 1
        assert _cell(agreement, 5, "4-5") == 1
        assert _cell(agreement, 5, "1-2") == 0
        assert agreement["pairs"] == 2
        assert agreement["rated"] == 2
        assert agreement["total"] == 2
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_bucket_boundaries_match_the_smart_score_histogram():
    """2.0 belongs to 2-3, not 1-2: the same half-open bucketing as the sibling chart."""
    temp_dir, client, server = _setup()
    try:
        boundary = _upload_picture(client, "Bad1.png")
        _set_scores(server, boundary, 3, 2.0)

        agreement = _agreement(client)

        assert _cell(agreement, 3, "2-3") == 1
        assert _cell(agreement, 3, "1-2") == 0
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_score_zero_and_null_are_both_unrated():
    """Neither counts as a rating, so neither is plotted or counted as rated."""
    temp_dir, client, server = _setup()
    try:
        zero = _upload_picture(client, "Bad1.png")
        null = _upload_picture(client, "Bad2.png")
        _set_scores(server, zero, 0, 3.5)
        _set_scores(server, null, None, 3.5)

        agreement = _agreement(client)

        assert agreement["rated"] == 0
        assert agreement["pairs"] == 0
        assert agreement["total"] == 2
        assert all(cell["count"] == 0 for cell in agreement["cells"])
        assert agreement["tau_b"] is None
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_rated_but_unscored_counts_as_rated_yet_is_not_plotted():
    """A rating with no smart score yet can't be placed, but the user did rate it.

    `rated` therefore exceeds `pairs`, and the coverage line stays honest instead
    of under-reporting how much the user has rated.
    """
    temp_dir, client, server = _setup()
    try:
        pending = _upload_picture(client, "Bad1.png")
        _set_scores(server, pending, 4, None)

        agreement = _agreement(client)

        assert agreement["rated"] == 1
        assert agreement["pairs"] == 0
        assert all(cell["count"] == 0 for cell in agreement["cells"])
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_tau_b_is_suppressed_below_the_minimum_pair_count():
    """Two pictures cannot support a coefficient, however tidy they look."""
    temp_dir, client, server = _setup()
    try:
        a = _upload_picture(client, "Bad1.png")
        b = _upload_picture(client, "Bad2.png")
        _set_scores(server, a, 1, 1.2)
        _set_scores(server, b, 5, 4.9)

        agreement = _agreement(client)

        assert agreement["pairs"] < AGREEMENT_MIN_PAIRS
        assert agreement["tau_b"] is None
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_matrix_ignores_the_score_filter_it_sets_itself():
    """The regression this widget lives or dies on.

    Clicking a cell sets min_score/max_score (+ smart_score_bucket). If the
    matrix honoured those, it would collapse to the clicked cell and no other
    cell would ever be reachable. Every other filter still applies.
    """
    temp_dir, client, server = _setup()
    try:
        low = _upload_picture(client, "Bad1.png")
        high = _upload_picture(client, "Bad2.png")
        _set_scores(server, low, 1, 1.4)
        _set_scores(server, high, 5, 4.8)

        filtered = _agreement(client, "&min_score=5&max_score=5")

        # The 1-star picture is still in the matrix even though the view is
        # filtered to 5-star pictures only.
        assert _cell(filtered, 1, "1-2") == 1
        assert _cell(filtered, 5, "4-5") == 1
        assert filtered["pairs"] == 2
        assert filtered["total"] == 2

        # ... and the sibling histogram, which does self-filter, still does.
        clear_stats_cache()
        resp = client.get("/pictures/stats?include=picture&min_score=5&max_score=5")
        score_dist = {
            row["label"]: row["count"] for row in resp.json()["score_distribution"]
        }
        assert score_dist["1"] == 0
        assert score_dist["5"] == 1
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_matrix_ignores_the_smart_score_bucket_filter_it_sets_itself():
    """Same contract for the column half of a cell click."""
    temp_dir, client, server = _setup()
    try:
        low = _upload_picture(client, "Bad1.png")
        high = _upload_picture(client, "Bad2.png")
        _set_scores(server, low, 1, 1.4)
        _set_scores(server, high, 5, 4.8)

        filtered = _agreement(client, "&smart_score_bucket=4-5")

        assert _cell(filtered, 1, "1-2") == 1
        assert _cell(filtered, 5, "4-5") == 1
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_matrix_still_honours_other_filters():
    """Self-exclusion is exactly three predicates wide, not a bypass of scope."""
    temp_dir, client, server = _setup()
    try:
        tagged = _upload_picture(client, "Bad1.png")
        untagged = _upload_picture(client, "Bad2.png")
        _set_scores(server, tagged, 5, 4.8)
        _set_scores(server, untagged, 1, 1.4)
        assert (
            client.post(f"/pictures/{tagged}/tags", json={"tag": "keeper"}).status_code
            == 200
        )

        agreement = _agreement(client, "&tag=keeper")

        assert _cell(agreement, 5, "4-5") == 1
        assert _cell(agreement, 1, "1-2") == 0, "the untagged picture is out of scope"
        assert agreement["total"] == 1
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_agreement_is_absent_without_the_picture_include():
    """It rides the same opt-in section as the histograms it cross-tabulates."""
    temp_dir, client, server = _setup()
    try:
        _upload_picture(client, "Bad1.png")
        clear_stats_cache()
        resp = client.get("/pictures/stats")
        assert resp.status_code == 200
        assert resp.json()["score_agreement"] == {}
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()
