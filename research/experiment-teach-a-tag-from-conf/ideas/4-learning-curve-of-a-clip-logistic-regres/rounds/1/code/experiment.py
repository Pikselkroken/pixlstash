"""Idea 4: learning curve of a CLIP logistic regression against the near-neighbour vote.

    python experiment.py DATA_DIR EVIDENCE_DIR [--tags 10] [--repeats 20]
    python experiment.py --selftest

DATA_DIR holds labels.csv and embeddings.npz from export_data.py. Per tag, each repeat
shuffles that tag's verdicts, keeps everything after the first 200 as the test set
(identical for n = 50, 100, 200) and trains on the first n. Methods, all fitted on the
same n verdicts and scored on the same held-out verdicts:

  logreg      logistic regression on unit-norm CLIP embeddings, standardised, C picked
              by stratified CV inside the n training verdicts only
  vote        the shipped near-neighbour vote (near_neighbor.py, copied unchanged from
              pixlstash/utils): similarity-weighted share of the k=12 nearest pictures in
              the whole pool that carry the tag, where "carries the tag" = a training POS
  vote_judged the same vote, but neighbours drawn only from the n training verdicts
              (POS = 1, NEG = 0), so it uses the rejects too
  random      chance, i.e. the held-out positive rate

Ties are broken at random. Metrics: average precision (AP) and precision among the top
TOPK held-out pictures.
"""

import argparse
import csv
import os
import sys
import zlib
from collections import defaultdict

import matplotlib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from near_neighbor import knn_disagreement_with_neighbors

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be chosen first)

K = 12  # scan_tag's default k
NS = (50, 100, 200)
TOPK = 50
MIN_TEST = 100
CS = (0.001, 0.01, 0.1, 1.0)
METHODS = ("logreg", "vote", "vote_judged", "random")


def unit(x):
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (x / n).astype(np.float32)


def pool_neighbours(pool_emb, query_rows, block=2048):
    """Top-K neighbours (index into pool, cosine) of each query row, self excluded.

    The vote's neighbours depend only on the embeddings, never the labels, so they are
    computed once and every draw reuses them; `selftest` checks this matches the kernel.
    """
    idx = np.empty((len(query_rows), K), np.int64)
    sim = np.empty((len(query_rows), K), np.float32)
    for s in range(0, len(query_rows), block):
        q = query_rows[s : s + block]
        sims = pool_emb[q] @ pool_emb.T
        sims[np.arange(len(q)), q] = -np.inf
        top = np.argpartition(-sims, K - 1, axis=1)[:, :K]
        idx[s : s + len(q)] = top
        sim[s : s + len(q)] = np.take_along_axis(sims, top, 1)
    return idx, sim


def vote_score(nbr_idx, nbr_sim, has_tag):
    w = np.clip(nbr_sim, 0.0, None)
    ws = w.sum(1)
    return np.where(ws > 0, (w * has_tag[nbr_idx]).sum(1) / np.where(ws > 0, ws, 1), 0.0)


def judged_vote(train_x, train_y, test_x):
    k = min(K, len(train_y))
    sims = test_x @ train_x.T
    top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
    w = np.clip(np.take_along_axis(sims, top, 1), 0.0, None)
    ws = w.sum(1)
    return np.where(ws > 0, (w * train_y[top]).sum(1) / np.where(ws > 0, ws, 1), 0.0)


def fit_logreg(x, y, seed):
    """Pick C by stratified CV on the training verdicts alone, then refit on all of them."""
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


def ranked_metrics(y, score, rng, topk):
    order = np.lexsort((rng.random(len(y)), -score))  # descending score, random ties
    return average_precision_score(y, score + rng.random(len(y)) * 1e-9), y[order[:topk]].mean()


def run(labels, ids, emb, n_tags, repeats, evidence_dir):
    emb = unit(emb)
    row_of = {int(p): i for i, p in enumerate(ids)}
    by_tag = defaultdict(dict)
    for pid, tag, state in labels:
        if pid in row_of:
            by_tag[tag][pid] = 1 if state == "POS" else 0
    ranked = sorted(by_tag, key=lambda t: -len(by_tag[t]))
    tags = [
        t for t in ranked
        if len(by_tag[t]) >= NS[0] + MIN_TEST
        and min(sum(by_tag[t].values()), len(by_tag[t]) - sum(by_tag[t].values())) >= 10
    ][:n_tags]
    print(f"{len(by_tag)} tags with verdicts; testing {len(tags)}", file=sys.stderr)

    rows, tag_rows = [], []
    for tag in tags:
        pids = np.array(sorted(by_tag[tag]))
        y_all = np.array([by_tag[tag][p] for p in pids])
        r_all = np.array([row_of[p] for p in pids])
        nbr_idx, nbr_sim = pool_neighbours(emb, r_all)
        tag_rows.append({"tag": tag, "verdicts": len(pids), "pos": int(y_all.sum()),
                         "neg": int(len(pids) - y_all.sum())})
        n_train_max = min(max(NS), len(pids) - MIN_TEST)
        for rep in range(repeats):
            rng = np.random.default_rng([rep, zlib.crc32(tag.encode())])
            perm = rng.permutation(len(pids))
            test, head = perm[n_train_max:], perm[:n_train_max]
            yt = y_all[test]
            if yt.sum() == 0 or yt.sum() == len(yt):
                continue
            topk = min(TOPK, len(test) // 2)
            for n in NS:
                if n > n_train_max:
                    continue
                tr = head[:n]
                ytr = y_all[tr]
                if ytr.sum() < 2 or ytr.sum() > n - 2:
                    continue  # too few of one class to fit or vote; recorded as missing
                model, c = fit_logreg(emb[r_all[tr]], ytr, rep)
                has_tag = np.zeros(len(emb), np.float32)
                has_tag[r_all[tr][ytr == 1]] = 1.0
                scores = {
                    "logreg": model.decision_function(emb[r_all[test]]),
                    "vote": vote_score(nbr_idx[test], nbr_sim[test], has_tag),
                    "vote_judged": judged_vote(emb[r_all[tr]], ytr.astype(np.float32),
                                               emb[r_all[test]]),
                    "random": rng.random(len(test)),
                }
                for meth, s in scores.items():
                    ap, prec = ranked_metrics(yt, s, rng, topk)
                    rows.append({"tag": tag, "n": n, "repeat": rep, "method": meth,
                                 "ap": ap, "prec_top": prec, "topk": topk,
                                 "test_size": len(test), "test_pos": int(yt.sum()),
                                 "train_pos": int(ytr.sum()), "C": c,
                                 "nonzero": float((s > 0).mean())})
        print(f"  {tag}: {len(pids)} verdicts done", file=sys.stderr)

    os.makedirs(evidence_dir, exist_ok=True)
    write_csv(os.path.join(evidence_dir, "runs.csv"), rows)
    write_csv(os.path.join(evidence_dir, "tags.csv"), tag_rows)
    summary = summarise(rows)
    write_csv(os.path.join(evidence_dir, "summary.csv"), summary)
    plot(summary, tags, evidence_dir)
    plot_gap(summary, evidence_dir)
    return summary


def plot_gap(summary, evidence_dir, n=100):
    """Logreg minus each vote, precision in the top held-out pictures, at n verdicts."""
    rows = sorted((r for r in summary if r["n"] == n),
                  key=lambda r: r["diff_vs_vote_prec_top_mean"])
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7, 0.32 * len(rows) + 1.2))
    for off, base, colour, label in ((-0.17, "vote", "#d1495b", "vs shipped vote"),
                                     (0.17, "vote_judged", "#edae49",
                                      "vs vote among judged pictures only")):
        ax.errorbar([r[f"diff_vs_{base}_prec_top_mean"] for r in rows], y + off,
                    xerr=[r[f"diff_vs_{base}_prec_top_sd"] for r in rows],
                    fmt="o", ms=4, color=colour, capsize=2, lw=1, label=label)
    ax.axvline(0, color="#444", lw=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{r['tag']} ({r['test_pos_rate']:.0%} pos)" for r in rows],
                       fontsize=8)
    ax.set_xlabel(f"logistic regression minus vote, precision in top held-out\n"
                  f"{n} verdicts, mean ± 1 SD of the paired difference", fontsize=8)
    ax.legend(fontsize=7, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(evidence_dir, f"gap_at_{n}.png"), dpi=110)
    plt.close(fig)


def summarise(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["tag"], r["n"], r["method"])].append(r)
    paired = defaultdict(dict)
    for r in rows:
        paired[(r["tag"], r["n"], r["repeat"])][r["method"]] = r
    out = []
    for tag, n in sorted({(t, n) for t, n, _ in groups}):
        reps = [p for (t, nn, _), p in paired.items() if t == tag and nn == n]
        row = {"tag": tag, "n": n, "repeats": len(reps)}
        for m in METHODS:
            for metric in ("ap", "prec_top"):
                v = np.array([r[metric] for r in groups[(tag, n, m)]])
                row[f"{m}_{metric}_mean"] = round(v.mean(), 4)
                row[f"{m}_{metric}_sd"] = round(v.std(ddof=1), 4) if len(v) > 1 else 0.0
        for base in ("vote", "vote_judged"):
            for metric in ("ap", "prec_top"):
                d = np.array([p["logreg"][metric] - p[base][metric] for p in reps])
                row[f"diff_vs_{base}_{metric}_mean"] = round(d.mean(), 4)
                row[f"diff_vs_{base}_{metric}_sd"] = round(d.std(ddof=1), 4)
                row[f"wins_vs_{base}_{metric}"] = f"{int((d > 0).sum())}/{len(d)}"
                lo = row[f"logreg_{metric}_mean"] - row[f"logreg_{metric}_sd"]
                hi = row[f"{base}_{metric}_mean"] + row[f"{base}_{metric}_sd"]
                row[f"clear_vs_{base}_{metric}"] = bool(lo > hi)
        row["vote_nonzero_share"] = round(np.mean([p["vote"]["nonzero"] for p in reps]), 4)
        row["test_size"] = reps[0]["logreg"]["test_size"]
        row["topk"] = reps[0]["logreg"]["topk"]
        row["test_pos_rate"] = round(np.mean([p["logreg"]["test_pos"] / p["logreg"]["test_size"]
                                              for p in reps]), 4)
        out.append(row)
    return out


def plot(summary, tags, evidence_dir):
    colours = {"logreg": "#1b6ca8", "vote": "#d1495b", "vote_judged": "#edae49",
               "random": "#8a8a8a"}
    for metric, label in (("ap", "average precision"), ("prec_top", "precision in top held-out")):
        cols = min(4, len(tags))
        nrows = -(-len(tags) // cols)
        fig, axes = plt.subplots(nrows, cols, figsize=(3.4 * cols, 2.8 * nrows),
                                 squeeze=False, sharex=True)
        for ax, tag in zip(axes.flat, tags):
            srows = [r for r in summary if r["tag"] == tag]
            for m in METHODS:
                x = [r["n"] for r in srows]
                mu = np.array([r[f"{m}_{metric}_mean"] for r in srows])
                sd = np.array([r[f"{m}_{metric}_sd"] for r in srows])
                ax.plot(x, mu, "o-", color=colours[m], label=m, lw=1.5, ms=3)
                ax.fill_between(x, mu - sd, mu + sd, color=colours[m], alpha=0.15, lw=0)
            ax.set_title(tag, fontsize=9)
            ax.set_xticks(list(NS))
            ax.set_ylim(0, 1)
            ax.tick_params(labelsize=7)
        for ax in axes.flat[len(tags):]:
            ax.axis("off")
        axes.flat[0].legend(fontsize=7, loc="lower right")
        fig.supxlabel("verdicts used for fitting", fontsize=9)
        fig.supylabel(f"{label} (mean ± 1 SD over repeats)", fontsize=9)
        fig.tight_layout()
        fig.savefig(os.path.join(evidence_dir, f"learning_curve_{metric}.png"), dpi=110)
        plt.close(fig)


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def load(data_dir):
    with open(os.path.join(data_dir, "labels.csv")) as f:
        labels = [(int(r["picture_id"]), r["tag"], r["label_state"]) for r in csv.DictReader(f)]
    z = np.load(os.path.join(data_dir, "embeddings.npz"))
    return labels, z["ids"], z["emb"]


def selftest():
    """The precomputed-neighbour vote equals the shipped kernel; logreg beats chance on
    a separable synthetic tag."""
    rng = np.random.default_rng(0)
    emb = unit(rng.normal(size=(600, 512)))
    has = rng.random(600) < 0.2
    pos_frac, *_ = knn_disagreement_with_neighbors(emb, has, K)
    idx, sim = pool_neighbours(emb, np.arange(600))
    mine = vote_score(idx, sim, has.astype(np.float32))
    assert np.allclose(mine, pos_frac, atol=1e-5), np.abs(mine - pos_frac).max()

    direction = unit(rng.normal(size=(1, 512)))[0]
    raw = rng.normal(size=(1500, 512))
    y = (raw @ direction > 0.8).astype(int)
    raw += 6 * y[:, None] * direction  # a tag that a linear boundary can find
    labels = [(i, "synthetic", "POS" if y[i] else "NEG") for i in range(400)]
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        summary = run(labels, np.arange(1500), raw, 1, 4, d)
    r100 = next(r for r in summary if r["n"] == 100)
    assert r100["logreg_ap_mean"] > r100["random_ap_mean"] + 0.2, r100
    print("selftest ok", {k: v for k, v in r100.items() if k.endswith("ap_mean")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?")
    ap.add_argument("evidence_dir", nargs="?")
    ap.add_argument("--tags", type=int, default=10)
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    labels, ids, emb = load(a.data_dir)
    run(labels, ids, emb, a.tags, a.repeats, a.evidence_dir)


if __name__ == "__main__":
    main()
