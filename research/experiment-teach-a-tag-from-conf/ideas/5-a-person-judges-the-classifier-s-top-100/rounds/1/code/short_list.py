"""Idea 5, second game: a short list for one tag, after the person found 260-290 cards
per tag too long and full of repeats.

    python short_list.py DATA_DIR LISTS_DIR [--tag jewelry] [--per-method 30]

Same training draw as build_lists.py for the tag (same seed). Each method's ranked top
100 is walked in order, skipping any picture within cosine REPEAT of one that method
already kept, until it has --per-method pictures. --per-method random unjudged pictures
are added as a baseline (the confirm rate of a pick at random). Writes the shuffled list
to LISTS_DIR/<tag>.csv in the format review.py reads, with a "random" column.
"""

import argparse
import os

import numpy as np
from build_lists import DEFAULT_TAGS, METHODS, SEED, build_tag, load, write_csv

REPEAT = 0.90


def distinct(rows, emb, n):
    kept = []
    for r in rows:
        if not kept or (emb[kept] @ emb[r]).max() < REPEAT:
            kept.append(r)
        if len(kept) == n:
            break
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("lists_dir")
    ap.add_argument("--tag", default="jewelry")
    ap.add_argument("--per-method", type=int, default=30)
    a = ap.parse_args()
    labels, ids, emb = load(a.data_dir)
    seed = SEED + DEFAULT_TAGS.split(",").index(a.tag)
    verdicts = {p: int(s == "POS") for p, t, s in labels if t == a.tag and s in ("POS", "NEG")}
    row_of = {int(p): i for i, p in enumerate(ids)}
    picks, near, _ = build_tag(a.tag, verdicts, ids, emb, row_of, seed)
    short = {m: distinct(picks[m], emb, a.per_method) for m in METHODS}
    taken = set().union(*map(set, short.values()))
    rng = np.random.default_rng(seed + 1000)
    pool = [r for r in near if r not in taken]  # `near` is keyed by every candidate row
    short["random"] = list(rng.choice(pool, a.per_method, replace=False))
    rows = sorted(set().union(*map(set, short.values())))
    cols = (*METHODS, "random")
    out = [
        {
            "picture_id": int(ids[r]),
            **{m: int(r in set(short[m])) for m in cols},
            "cos_to_nearest_train": round(float(near[r]), 4),
        }
        for r in rng.permutation(rows)
    ]
    os.makedirs(a.lists_dir, exist_ok=True)
    write_csv(os.path.join(a.lists_dir, a.tag.replace(" ", "_") + ".csv"), out)
    print(f"{a.tag}: {len(out)} cards", {m: len(short[m]) for m in cols})


if __name__ == "__main__":
    main()
