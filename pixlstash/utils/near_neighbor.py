"""Near-neighbour label-disagreement kernel — the shared core of the tag-suggestion scan.

Pure NumPy, no PixlStash deps, so both the CLI scanner
(``scripts/near_neighbor_label_disagreement.py``) and the in-app scan service can call
it and never drift. Given unit-norm CLIP embeddings and a boolean "has the tag" mask, it
scores how much each image's nearest neighbours disagree with its own label.
"""

import numpy as np

EMBEDDING_DIM = 512  # CLIP ViT-B-32 image_embedding blobs are 512 float32 = 2048 bytes.
EMBEDDING_BYTES = EMBEDDING_DIM * 4


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
