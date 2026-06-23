"""Unit tests for the pure near-neighbour kernel helpers in
``pixlstash.utils.near_neighbor``.

These build their inputs directly (no DB, no models), so they isolate the perceptual-hash
near-duplicate twin selection that the tag scan uses to prefer an altered copy of a picture
over the CLIP-nearest opposite-labelled neighbour.
"""

import numpy as np

from pixlstash.utils.near_neighbor import (
    hamming_distance,
    nearest_opposite_by_hamming,
)


def test_hamming_distance_basic():
    assert hamming_distance(0, 0) == 0
    assert hamming_distance(0b1011, 0b1110) == 2
    # Full 64-bit range round-trips (max unsigned 64-bit value).
    assert hamming_distance(0, (1 << 64) - 1) == 64


def test_returns_closest_opposite_in_threshold():
    """Picks the opposite-labelled near-dup with the SMALLEST Hamming distance."""
    base = 0b1010_1010
    phash = np.array(
        [
            base,  # i = 0 (the suspect)
            base ^ 0b0000_0011,  # j=1: 2 bits away, opposite label  <- expected
            base ^ 0b0000_0111,  # j=2: 3 bits away, opposite label
            base,  # j=3: 0 bits away but SAME label (ignored)
        ],
        dtype=np.uint64,
    )
    valid = np.ones(4, dtype=bool)
    has_tag = np.array([True, False, False, True], dtype=bool)

    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == 1


def test_returns_minus_one_when_only_same_labelled():
    """All near-dups carry the SAME label → no genuine disagreement → -1."""
    base = 0b1111_0000
    phash = np.array([base, base ^ 0b1, base ^ 0b11], dtype=np.uint64)
    valid = np.ones(3, dtype=bool)
    has_tag = np.array([True, True, True], dtype=bool)

    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == -1


def test_returns_minus_one_when_all_opposite_exceed_threshold():
    """Opposite-labelled candidates exist but are all beyond max_hamming → -1."""
    base = 0
    phash = np.array(
        [
            base,
            base ^ 0b1111_1111_1111,  # 12 bits away, opposite label
            base ^ 0b1111_1111_1111_1,  # 13 bits away, opposite label
        ],
        dtype=np.uint64,
    )
    valid = np.ones(3, dtype=bool)
    has_tag = np.array([True, False, False], dtype=bool)

    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == -1
    # Loosening the threshold past 12 bits finds the closer one.
    j2 = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=12)
    assert j2 == 1


def test_invalid_phash_rows_are_ignored_via_valid_mask():
    """Rows flagged invalid (no/garbled phash) never become the twin, even at distance 0."""
    base = 0b0101_0101
    phash = np.array([base, base, base ^ 0b11], dtype=np.uint64)
    valid = np.array([True, False, True], dtype=bool)  # row 1 invalid
    has_tag = np.array([True, False, False], dtype=bool)

    # Row 1 is distance 0 and opposite-labelled, but invalid → must be skipped for row 2.
    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == 2


def test_returns_minus_one_when_suspect_phash_invalid():
    """If the suspect itself has no valid phash, there is nothing to match against."""
    phash = np.array([0, 1], dtype=np.uint64)
    valid = np.array([False, True], dtype=bool)
    has_tag = np.array([True, False], dtype=bool)

    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == -1


def test_tie_broken_by_higher_clip_sim_then_lower_index():
    """Equal Hamming distance → higher CLIP cosine wins; absent sims → lower index."""
    base = 0
    phash = np.array(
        [
            base,
            base ^ 0b1,  # j=1: 1 bit away
            base ^ 0b10,  # j=2: 1 bit away (tie)
        ],
        dtype=np.uint64,
    )
    valid = np.ones(3, dtype=bool)
    has_tag = np.array([True, False, False], dtype=bool)

    # No tie-breaker similarity → deterministic lowest index.
    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == 1

    # With similarity, the higher-cosine candidate (index 2) wins the tie.
    twin_sim = np.array([0.0, 0.10, 0.90], dtype=np.float32)
    j2 = nearest_opposite_by_hamming(
        phash, valid, has_tag, i=0, max_hamming=8, twin_sim=twin_sim
    )
    assert j2 == 2


def test_full_64bit_range_no_overflow():
    """uint64 dhash values near the top of the range XOR/popcount correctly."""
    top = (1 << 64) - 1
    phash = np.array([top, top ^ 0b111, top], dtype=np.uint64)
    valid = np.ones(3, dtype=bool)
    has_tag = np.array([True, False, True], dtype=bool)

    j = nearest_opposite_by_hamming(phash, valid, has_tag, i=0, max_hamming=8)
    assert j == 1
