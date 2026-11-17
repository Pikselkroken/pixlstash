"""Idea 6: random unjudged pictures as down-weighted background negatives.

    python experiment.py DATA_DIR PREVALENCE_CSV EVIDENCE_DIR [--repeats 20] [--jobs 12]
    python experiment.py --selftest

DATA_DIR is idea 4's export (labels.csv, embeddings.npz from idea 4's export_data.py).
PREVALENCE_CSV (tag, library_count) is the share of the library carrying each tag, read
through the pixlstash MCP list_tags; it only splits tags into rare and common.

"Unjudged" = the pictures in the export with no human verdict on any tag (10,040 of
20,984). Each repeat splits them in half: background negatives are drawn only from the
first half, the second half (U_test) is never trained on.

Per tag and repeat, the verdicts are shuffled; everything after the first 200 is the
held-out test set (same for n = 50, 100, 200), the first n are training verdicts.
Methods, all on unit-norm CLIP embeddings, standardised:

  verdicts      logistic regression on the n verdicts (idea 4's logreg)
  bg{B}_w{w}    same, plus B random unjudged pictures labelled NEG with sample weight w
                (verdicts weigh 1). C is picked by 3-fold CV where every fold trains on
                its verdicts + all B background pictures and is scored (AP) on the
                held-out fold's verdicts only.

Metrics on the held-out verdicts (the only known truth): AP, precision in the top 50 and
top 100. Diagnostic, truth unknown: the share of the top 100 that is unjudged when the
held-out verdicts are ranked together with U_test (5,020 pictures).
"""

import argparse
import csv
import os
import sys
import zlib

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # one BLAS thread per worker process; set before numpy loads
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import matplotlib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import StandardScaler

from idea4_experiment import load, unit, write_csv

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be chosen first)

NS = (50, 100, 200)
CS = (0.001, 0.01, 0.1, 1.0)
BGS = (300, 1000)
WS = (0.1, 0.3, 1.0)
MIN_VERDICTS = 300
MIN_CLASS = 20
RARE_BELOW = 0.30  # library share carrying the tag
VARIANTS = ["verdicts"] + [f"bg{b}_w{w}" for b in BGS for w in WS]


def fit(x, y, sw, c):
    m = LogisticRegression(C=c, max_iter=3000)
    return m.fit(x, y, sample_weight=sw)


def fit_cv(xv, yv, xb, w, seed):
    """Pick C on the verdicts only; background joins every training fold, never a test fold."""
    folds = min(3, int(yv.sum()), int((1 - yv).sum()))
    xb = xb if xb is not None else np.empty((0, xv.shape[1]), np.float32)
    best_c = 0.01
    if folds >= 2:
        scores = defaultdict(list)
        for tr, te in StratifiedKFold(folds, shuffle=True, random_state=seed).split(xv, yv):
            x = np.vstack([xv[tr], xb])
            y = np.r_[yv[tr], np.zeros(len(xb))]
            sw = np.r_[np.ones(len(tr)), np.full(len(xb), w)]
            sc = StandardScaler().fit(x)
            for c in CS:
                s = fit(sc.transform(x), y, sw, c).decision_function(sc.transform(xv[te]))
                scores[c].append(average_precision_score(yv[te], s))
        best_c = max(CS, key=lambda c: np.mean(scores[c]))
    x = np.vstack([xv, xb])
    y = np.r_[yv, np.zeros(len(xb))]
    sw = np.r_[np.ones(len(yv)), np.full(len(xb), w)]
    sc = StandardScaler().fit(x)
    m = fit(sc.transform(x), y, sw, best_c)
    return (lambda q: m.decision_function(sc.transform(q))), best_c


def top_share(y, s, k, rng):
    order = np.lexsort((rng.random(len(y)), -s))
    return y[order[:k]].mean()


def run_tag(args):
    tag, pids_y, emb, unjudged, repeats = args
    pids = np.array(sorted(pids_y))
    y_all = np.array([pids_y[p] for p in pids])
    rows = []
    for rep in range(repeats):
        rng = np.random.default_rng([rep, zlib.crc32(tag.encode())])
        perm = rng.permutation(len(pids))
        n_max = min(max(NS), len(pids) - 100)
        test, head = perm[n_max:], perm[:n_max]
        yt = y_all[test]
        uperm = np.random.default_rng([rep, 6]).permutation(unjudged)  # same split for all tags
        u_bg, u_test = uperm[: len(uperm) // 2], uperm[len(uperm) // 2:]
        for n in NS:
            if n > n_max:
                continue
            tr = head[:n]
            ytr = y_all[tr]
            if ytr.sum() < 2 or ytr.sum() > n - 2:
                continue
            for v in VARIANTS:
                if v == "verdicts":
                    xb, w = None, 1.0
                else:
                    b, w = v[2:].split("_w")
                    xb, w = emb[rng.choice(u_bg, int(b), replace=False)], float(w)
                score, c = fit_cv(emb[pids[tr]], ytr, xb, w, rep)
                st = score(emb[pids[test]])
                su = score(emb[u_test])
                # held-out verdicts ranked together with U_test: how much of the top 100 is unjudged
                is_u = np.r_[np.zeros(len(st)), np.ones(len(su))]
                rows.append({
                    "tag": tag, "n": n, "repeat": rep, "method": v, "C": c,
                    "ap": average_precision_score(yt, st + rng.random(len(st)) * 1e-9),
                    "p50": top_share(yt, st, 50, rng),
                    "p100": top_share(yt, st, 100, rng),
                    "unjudged_in_top100": top_share(is_u, np.r_[st, su], 100, rng),
                    "test_size": len(test), "test_pos": int(yt.sum()),
                })
    print(f"  {tag} done", file=sys.stderr)
    return rows


def summarise(rows, prevalence):
    by = defaultdict(dict)
    for r in rows:
        by[(r["tag"], r["n"], r["repeat"])][r["method"]] = r
    out = []
    for tag, n in sorted({(t, n) for t, n, _ in by}):
        reps = [m for (t, nn, _), m in by.items() if t == tag and nn == n]
        base = {k: np.array([m["verdicts"][k] for m in reps]) for k in ("ap", "p50", "p100")}
        for v in VARIANTS:
            row = {"tag": tag, "n": n, "method": v, "repeats": len(reps),
                   "library_share": prevalence.get(tag, float("nan")),
                   "group": "rare" if prevalence.get(tag, 1) < RARE_BELOW else "common",
                   "test_pos_rate": round(np.mean([m[v]["test_pos"] / m[v]["test_size"]
                                                   for m in reps]), 4)}
            for k in ("ap", "p50", "p100", "unjudged_in_top100"):
                a = np.array([m[v][k] for m in reps])
                row[f"{k}_mean"] = round(a.mean(), 4)
                row[f"{k}_sd"] = round(a.std(ddof=1), 4)
                if k in base:
                    d = a - base[k]
                    row[f"d_{k}_mean"] = round(d.mean(), 4)
                    row[f"d_{k}_sd"] = round(d.std(ddof=1), 4)
                    row[f"d_{k}_wins"] = f"{int((d > 0).sum())}/{int((d < 0).sum())}"
            # counts: p100 up by more than its paired SD/sqrt(reps)*2, and AP not down likewise
            se = lambda k: row[f"d_{k}_sd"] / np.sqrt(len(reps))  # noqa: E731
            row["p100_up"] = bool(row["d_p100_mean"] > 2 * se("p100"))
            row["ap_down"] = bool(row["d_ap_mean"] < -2 * se("ap"))
            row["counts"] = row["p100_up"] and not row["ap_down"]
            out.append(row)
    return out


def plot(summary, evidence_dir, n=100):
    rows = [r for r in summary if r["n"] == n and r["method"] == "verdicts"]
    rows.sort(key=lambda r: (r["group"] != "rare", r["library_share"]))
    tags = [r["tag"] for r in rows]
    show = ["bg300_w0.1", "bg300_w0.3", "bg1000_w0.3", "bg1000_w1.0"]
    colours = ["#9ecae1", "#3182bd", "#fd8d3c", "#d94801"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 0.3 * len(tags) + 1.6), sharey=True)
    y = np.arange(len(tags))
    for ax, k, lab in zip(axes, ("p100", "ap", "unjudged_in_top100"),
                          ("precision in top 100 held-out verdicts",
                           "average precision, held-out verdicts",
                           "share of top 100 that is unjudged\n(verdicts ranked with 5,020 unjudged)")):
        for i, (v, c) in enumerate(zip(show, colours)):
            sel = {r["tag"]: r for r in summary if r["n"] == n and r["method"] == v}
            key = f"d_{k}" if k != "unjudged_in_top100" else k
            xs = [sel[t][f"{key}_mean"] for t in tags]
            if k == "unjudged_in_top100":
                base = {r["tag"]: r for r in rows}
                xs = [sel[t][f"{k}_mean"] - base[t][f"{k}_mean"] for t in tags]
                err = None
            else:
                err = [sel[t][f"{key}_sd"] for t in tags]
            ax.errorbar(xs, y + (i - 1.5) * 0.18, xerr=err, fmt="o", ms=3, color=c, lw=0.8,
                        capsize=1.5, label=v.replace("bg", "+").replace("_w", " bg, weight "))
        ax.axvline(0, color="#444", lw=0.8)
        spread = " (mean ± 1 SD)" if k != "unjudged_in_top100" else " (mean)"
        ax.set_xlabel(f"{lab}\nchange vs verdicts only, {n} verdicts{spread}", fontsize=8)
        ax.tick_params(labelsize=7)
    nrare = sum(r["group"] == "rare" for r in rows)
    for ax in axes:
        ax.axhline(nrare - 0.5, color="#999", lw=0.6, ls="--")
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([f"{r['tag']} ({r['library_share']:.0%} of library)" for r in rows],
                            fontsize=7)
    axes[0].invert_yaxis()
    axes[2].legend(fontsize=7, loc="lower right")
    fig.suptitle("Rare tags (under 30% of the library) above the dashed line, common below",
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(evidence_dir, f"change_at_{n}.png"), dpi=110)
    plt.close(fig)


def run(labels, ids, emb, prevalence, repeats, evidence_dir, jobs=8):
    emb = unit(emb)
    row_of = {int(p): i for i, p in enumerate(ids)}
    by_tag = defaultdict(dict)
    for pid, tag, state in labels:
        if pid in row_of:
            by_tag[tag][row_of[pid]] = 1 if state == "POS" else 0
    judged = {r for d in by_tag.values() for r in d}
    unjudged = np.array(sorted(set(range(len(ids))) - judged))
    tags = [t for t in sorted(by_tag, key=lambda t: -len(by_tag[t]))
            if len(by_tag[t]) >= MIN_VERDICTS
            and min(sum(by_tag[t].values()), len(by_tag[t]) - sum(by_tag[t].values())) >= MIN_CLASS]
    print(f"{len(tags)} tags, {len(unjudged)} unjudged pictures", file=sys.stderr)
    with ProcessPoolExecutor(jobs) as ex:
        rows = [r for rs in ex.map(run_tag, [(t, by_tag[t], emb, unjudged, repeats)
                                             for t in tags]) for r in rs]
    os.makedirs(evidence_dir, exist_ok=True)
    write_csv(os.path.join(evidence_dir, "runs.csv"), rows)
    summary = summarise(rows, prevalence)
    write_csv(os.path.join(evidence_dir, "summary.csv"), summary)
    plot(summary, evidence_dir)
    return summary


def selftest():
    """Pipeline check on a synthetic tag: positives and judged near-misses share a direction,
    ordinary pictures do not; verdicts-only must beat chance, and heavy background must not
    put more ordinary pictures in the top 100 than verdicts-only does."""
    rng = np.random.default_rng(0)
    d_tag, d_near = unit(rng.normal(size=(2, 64)))
    x = rng.normal(size=(4000, 64)).astype(np.float32)
    y = np.zeros(4000, int)
    y[:150] = 1
    x[:150] += 3 * d_tag + 3 * d_near        # positives: tag + "looks like the tag"
    x[150:900] += 3 * d_near                  # near-misses: judged rejects
    labels = [(i, "syn", "POS" if y[i] else "NEG") for i in range(900)]  # 900+ are ordinary
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        s = run(labels, np.arange(4000), np.pad(x, ((0, 0), (0, 448))), {"syn": 0.04}, 3, d, 2)
    r = {row["method"]: row for row in s if row["n"] == 100}
    assert r["verdicts"]["ap_mean"] > 0.5, r["verdicts"]
    assert r["bg1000_w1.0"]["unjudged_in_top100_mean"] <= r["verdicts"]["unjudged_in_top100_mean"]
    print("selftest ok", {k: (v["ap_mean"], v["unjudged_in_top100_mean"]) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", nargs="?")
    ap.add_argument("prevalence_csv", nargs="?")
    ap.add_argument("evidence_dir", nargs="?")
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    labels, ids, emb = load(a.data_dir)
    with open(a.prevalence_csv) as f:
        prevalence = {r["tag"]: int(r["library_count"]) / len(ids) for r in csv.DictReader(f)}
    run(labels, ids, emb, prevalence, a.repeats, a.evidence_dir, a.jobs)


if __name__ == "__main__":
    main()
