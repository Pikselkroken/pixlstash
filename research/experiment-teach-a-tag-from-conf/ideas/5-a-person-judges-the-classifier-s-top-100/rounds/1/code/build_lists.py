"""Idea 5: build the lists a person judges, one per tag.

    python build_lists.py DATA_DIR LISTS_DIR EVIDENCE_DIR [--tags jewelry,teeth,...]
    python build_lists.py --selftest

DATA_DIR holds labels.csv and embeddings.npz from idea 4's export_data.py (owner's data,
kept outside the repository). Per tag, with a fixed seed:

  train      100 verdicts drawn at random from the tag's human verdicts
  candidates every picture in the library with NO verdict on this tag
  logreg     logistic regression on the 100 verdicts (idea 4's fit, C by inner CV)
  vote       the shipped near-neighbour vote over the whole library, a picture counting
             as tagged only if it is a training POS (idea 4's "teach from verdicts" setting)
  vote_judged the same vote with neighbours drawn only from the 100 training verdicts
             (POS 1, NEG 0): follow-up 4.1

Each method's top 100 candidates go into one shuffled list per tag (LISTS_DIR/<tag>.csv:
picture_id plus which methods picked it). LISTS_DIR holds picture ids from the owner's
library, so it is kept outside the repository; the lists are reproducible from the seed.
EVIDENCE_DIR gets aggregate numbers only: overlaps, ties, near-twin shares.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

import numpy as np
from near_neighbor import knn_disagreement_with_neighbors
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

K = 12
N_TRAIN = 100
TOP = 100
CS = (0.001, 0.01, 0.1, 1.0)
TWIN = 0.95  # cosine to the nearest training picture at or above this = near-twin
SEED = 5
METHODS = ("logreg", "vote", "vote_judged")
DEFAULT_TAGS = "jewelry,teeth,smile,hand,malformed hand"


def unit(x):
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (x / n).astype(np.float32)


def fit_logreg(x, y, seed):
    """Idea 4's fit: C by stratified CV on the training verdicts alone, then refit."""
    folds = min(3, int(y.sum()), int((1 - y).sum()))
    best_c = 0.01
    if folds >= 2:
        cv = StratifiedKFold(folds, shuffle=True, random_state=seed)
        scores = {}
        for c in CS:
            m = make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000))
            p = cross_val_predict(m, x, y, cv=cv, method="decision_function")
            scores[c] = average_precision_score(y, p)
        best_c = max(scores, key=scores.get)
    m = make_pipeline(StandardScaler(), LogisticRegression(C=best_c, max_iter=2000))
    return m.fit(x, y), best_c


def judged_vote(train_x, train_y, test_x):
    k = min(K, len(train_y))
    sims = test_x @ train_x.T
    top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    w = np.clip(np.take_along_axis(sims, top, 1), 0.0, None)
    ws = w.sum(1)
    return np.where(ws > 0, (w * train_y[top]).sum(1) / np.where(ws > 0, ws, 1), 0.0)


def top_k(score, rng, k=TOP):
    """Indices of the k highest scores, ties broken at random (as the app's order is
    arbitrary among equal scores)."""
    return np.lexsort((rng.random(len(score)), -score))[:k]


def build_tag(tag, verdicts, ids, emb, row_of, seed):
    """Return ({method: candidate rows in its top 100}, stats) for one tag."""
    rng = np.random.default_rng(seed)
    pids = np.array(sorted(verdicts))
    pids = pids[np.isin(pids, ids)]
    tr_pids = rng.permutation(pids)[:N_TRAIN]
    tr_rows = np.array([row_of[p] for p in tr_pids])
    ytr = np.array([verdicts[p] for p in tr_pids], np.float32)
    judged_rows = np.array([row_of[p] for p in pids])
    cand = np.setdiff1d(np.arange(len(ids)), judged_rows)

    model, c = fit_logreg(emb[tr_rows], ytr.astype(int), seed)
    s_log = model.decision_function(emb[cand])

    has = np.zeros(len(ids), bool)
    has[tr_rows[ytr == 1]] = True
    pos_frac, *_ = knn_disagreement_with_neighbors(emb, has, K)
    s_vote = pos_frac[cand]

    s_judged = judged_vote(emb[tr_rows], ytr, emb[cand])

    picks = {
        "logreg": cand[top_k(s_log, rng)],
        "vote": cand[top_k(s_vote, rng)],
        "vote_judged": cand[top_k(s_judged, rng)],
    }
    nearest_train = (emb[cand] @ emb[tr_rows].T).max(1)
    near = dict(zip(cand, nearest_train))
    stats = {
        "tag": tag,
        "verdicts": len(pids),
        "train_pos": int(ytr.sum()),
        "train_neg": int(len(ytr) - ytr.sum()),
        "logreg_C": c,
        "candidates": len(cand),
        "vote_scored_above_0": int((s_vote > 0).sum()),
        "vote_judged_scored_above_0": int((s_judged > 0).sum()),
        "vote_top100_with_score_0": max(0, TOP - int((s_vote > 0).sum())),
    }
    for m in METHODS:
        stats[f"{m}_near_twins_in_top100"] = int(sum(near[r] >= TWIN for r in picks[m]))
        stats[f"{m}_median_cos_to_train"] = round(float(np.median([near[r] for r in picks[m]])), 3)
    for i, a in enumerate(METHODS):
        for b in METHODS[i + 1 :]:
            stats[f"overlap_{a}_{b}"] = len(set(picks[a]) & set(picks[b]))
    union = set().union(*map(set, picks.values()))
    stats["distinct_pictures_to_judge"] = len(union)
    return picks, near, stats


def load(data_dir):
    with open(os.path.join(data_dir, "labels.csv")) as f:
        labels = [(int(r["picture_id"]), r["tag"], r["label_state"]) for r in csv.DictReader(f)]
    z = np.load(os.path.join(data_dir, "embeddings.npz"))
    return labels, z["ids"], unit(z["emb"])


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def run(labels, ids, emb, tags, lists_dir, evidence_dir):
    by_tag = defaultdict(dict)
    for p, t, s in labels:
        if s in ("POS", "NEG"):
            by_tag[t][p] = 1 if s == "POS" else 0
    row_of = {int(p): i for i, p in enumerate(ids)}
    os.makedirs(lists_dir, exist_ok=True)
    all_stats = []
    for ti, tag in enumerate(tags):
        picks, near, stats = build_tag(tag, by_tag[tag], ids, emb, row_of, SEED + ti)
        all_stats.append(stats)
        rows = sorted(set().union(*map(set, picks.values())))
        order = np.random.default_rng(SEED + ti).permutation(len(rows))
        out = [
            {
                "picture_id": int(ids[rows[i]]),
                **{m: int(rows[i] in set(picks[m])) for m in METHODS},
                "cos_to_nearest_train": round(float(near[rows[i]]), 4),
            }
            for i in order
        ]
        write_csv(os.path.join(lists_dir, tag.replace(" ", "_") + ".csv"), out)
        print(stats, file=sys.stderr)
    write_csv(os.path.join(evidence_dir, "lists.csv"), all_stats)
    return all_stats


def selftest():
    """On a synthetic library where a linear direction defines the tag, every method's
    picks avoid judged pictures and logreg's top 100 is mostly true positives."""
    rng = np.random.default_rng(0)
    direction = unit(rng.normal(size=(1, 512)))[0]
    raw = rng.normal(size=(3000, 512))
    y = (raw @ direction > 1.5).astype(int)
    raw += 4 * y[:, None] * direction
    emb = unit(raw)
    ids = np.arange(1, 3001)
    judged = rng.choice(3000, 400, replace=False)
    verdicts = {int(ids[r]): int(y[r]) for r in judged}
    row_of = {int(p): i for i, p in enumerate(ids)}
    picks, _, stats = build_tag("synthetic", verdicts, ids, emb, row_of, 1)
    for m in METHODS:
        assert not set(picks[m]) & set(judged), m
        assert len(picks[m]) == TOP, m
    prec = y[picks["logreg"]].mean()
    assert prec > 2 * y.mean(), (prec, y.mean())
    print("selftest ok", {m: float(y[picks[m]].mean()) for m in METHODS}, "base", y.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?")
    ap.add_argument("lists_dir", nargs="?")
    ap.add_argument("evidence_dir", nargs="?")
    ap.add_argument("--tags", default=DEFAULT_TAGS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    labels, ids, emb = load(a.data_dir)
    run(labels, ids, emb, a.tags.split(","), a.lists_dir, a.evidence_dir)


if __name__ == "__main__":
    main()
