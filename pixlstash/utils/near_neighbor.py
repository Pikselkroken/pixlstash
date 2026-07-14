"""Near-neighbour label-disagreement kernel — the shared core of the tag-suggestion scan.

Pure NumPy, no PixlStash deps, so both the CLI scanner
(``scripts/near_neighbor_label_disagreement.py``) and the in-app scan service can call
it and never drift. Given unit-norm CLIP embeddings and a boolean "has the tag" mask, it
scores how much each image's nearest neighbours disagree with its own label.
"""

import numpy as np

EMBEDDING_DIM = 512  # CLIP ViT-B-32 image_embedding blobs are 512 float32 = 2048 bytes.
EMBEDDING_BYTES = EMBEDDING_DIM * 4


def dedupe_by_pair(rows: list[dict]) -> list[dict]:
    """Collapse rows describing the same unordered {picture, twin} pair, keeping the
    highest-scoring one.

    A pair where a tagged image and its untagged twin disagree yields two suspects — a
    "remove" for the tagged one and an "add" for the untagged one — which are the *same*
    review (the queue card is symmetric over the pair). Keeping one per pair avoids
    showing it twice. Each row must have ``picture_id``, ``twin_picture_id`` and ``score``.
    """
    best: dict[frozenset, dict] = {}
    for r in rows:
        key = frozenset((r["picture_id"], r.get("twin_picture_id")))
        cur = best.get(key)
        if cur is None or r["score"] > cur["score"]:
            best[key] = r
    return list(best.values())


def knn_disagreement(emb: np.ndarray, has_tag: np.ndarray, k: int, block: int = 1024):
    """For every image, the similarity-weighted positive fraction among its k nearest
    neighbours, plus its most-similar opposite-labelled neighbour ("twin").

    Args:
        emb: (n, dim) unit-norm embeddings (cosine == dot product).
        has_tag: (n,) bool mask — whether each image carries the (merged) tag.
        k: neighbours per image.
        block: rows per similarity block (memory/speed trade-off).

    Returns arrays aligned to the input rows:
        pos_frac  – sim-weighted fraction of neighbours carrying the tag
        twin_idx  – row index of nearest opposite-labelled neighbour (-1 if none)
        twin_sim  – cosine similarity of that twin (0.0 if none)
    """
    n = emb.shape[0]
    pos_frac = np.zeros(n, dtype=np.float32)
    twin_idx = np.full(n, -1, dtype=np.int64)
    twin_sim = np.zeros(n, dtype=np.float32)
    if n <= 1:
        return pos_frac, twin_idx, twin_sim
    k = min(k, n - 1)  # can't have more neighbours than other images
    y = has_tag.astype(np.float32)

    for start in range(0, n, block):
        end = min(start + block, n)
        sims = emb[start:end] @ emb.T  # (rows, n) cosine similarities
        # Exclude self-matches before ranking.
        for local, glob in enumerate(range(start, end)):
            sims[local, glob] = -np.inf

        # Top-k neighbours per row.
        top = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]  # (rows, k), unsorted
        for local in range(end - start):
            glob = start + local
            nbr = top[local]
            w = sims[local, nbr]
            w = np.clip(w, 0.0, None)  # ignore negative-sim neighbours in the vote
            wsum = w.sum()
            yn = y[nbr]
            pos_frac[glob] = float((w * yn).sum() / wsum) if wsum > 0 else 0.0

            # Nearest opposite-labelled neighbour ("twin").
            opp = nbr[has_tag[nbr] != has_tag[glob]]
            if opp.size:
                opp_sims = sims[local, opp]
                best = int(opp[np.argmax(opp_sims)])
                twin_idx[glob] = best
                twin_sim[glob] = float(sims[local, best])

    return pos_frac, twin_idx, twin_sim


def _popcount_u64(values: np.ndarray) -> np.ndarray:
    """Population count (number of set bits) per element of a uint64 array.

    Vectorised: view the (n,) uint64 array as (n, 8) uint8, unpack to bits, sum.
    Avoids a Python loop over the n rows.
    """
    as_bytes = values.astype(np.uint64).view(np.uint8).reshape(values.shape[0], 8)
    return np.unpackbits(as_bytes, axis=1).sum(axis=1).astype(np.int64)


def hamming_distance(a: int, b: int) -> int:
    """Hamming distance (number of differing bits) between two integers."""
    return int(int(a ^ b).bit_count())


def nearest_opposite_by_hamming(
    phash_ints: np.ndarray,
    valid_mask: np.ndarray,
    has_tag: np.ndarray,
    i: int,
    max_hamming: int,
    twin_sim: np.ndarray | None = None,
) -> int:
    """Index of the opposite-labelled near-duplicate of row ``i`` by dhash Hamming distance.

    Picks the opposite-labelled picture whose 64-bit perceptual hash (dhash) is closest to
    row ``i``'s, among those within ``max_hamming`` bits. This is the "same image, altered"
    signal: an oversaturated or watermarked copy of a picture lands a handful of bits away
    from its original, so the original is the obvious thing to compare against in review.

    Args:
        phash_ints: (n,) array of 64-bit dhash values (any integer dtype; read as uint64).
        valid_mask: (n,) bool — rows with a parseable phash. Invalid rows are ignored.
        has_tag: (n,) bool — whether each row carries the (merged) tag.
        i: row to find a near-duplicate for. Must itself have a valid phash.
        max_hamming: inclusive Hamming-distance threshold (~<=8 bits ≈ near-identical).
        twin_sim: optional (n,) CLIP cosine array used only to break ties (higher wins).

    Returns:
        Row index of the closest opposite-labelled in-threshold near-duplicate, or ``-1``
        when none exists. Ties on Hamming distance are broken by higher ``twin_sim`` when
        provided, otherwise by lower row index (deterministic).
    """
    n = phash_ints.shape[0]
    if n == 0 or not valid_mask[i]:
        return -1

    ref = np.uint64(phash_ints[i])
    dist = _popcount_u64(np.bitwise_xor(phash_ints.astype(np.uint64), ref))

    # Eligible: valid phash, opposite label, within threshold, and not row i itself.
    eligible = valid_mask & (has_tag != has_tag[i]) & (dist <= max_hamming)
    eligible[i] = False
    if not eligible.any():
        return -1

    cand = np.flatnonzero(eligible)
    cand_dist = dist[cand]
    best_dist = cand_dist.min()
    tied = cand[cand_dist == best_dist]
    if tied.size == 1:
        return int(tied[0])
    if twin_sim is not None:
        # Break ties by higher CLIP cosine; np.argmax keeps the first (lowest index) max.
        return int(tied[int(np.argmax(twin_sim[tied]))])
    return int(tied.min())
