#!/usr/bin/env python
"""Backfill idea 1 from what pixltagger already saved. Reads only; trains nothing.

Sources (all under ~/Projects/pixltagger, read-only):
  tmp/thirdparty/probe_results_dinov3.md   5-fold in-domain probe table + HAD/AbHuman transfer (DINOv3 ViT-B/16)
  tmp/thirdparty/probe_results.md          the same with DINOv2-base (first pass)
  tmp/base_bakeoff/results.md              per-tag probe AUC, three DINOv3 backbones, train->eval
  tmp/loop/scores/probe_eval_oof.json      5-fold out-of-fold probe scores on the 1,707-picture eval
  tmp/thirdparty/feats_dinov3/eval.json    the labels those scores were fitted on

  python code/backfill.py            (run from the round directory; writes evidence/)
"""

import csv
import json
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

PT = os.path.expanduser("~/Projects/pixltagger")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evidence")
ANOMALY4 = ["extra limb", "malformed hand", "malformed foot", "bad anatomy"]


def md_tables(path):
    """Every markdown table in a file, as (preceding heading/paragraph line, rows of cells)."""
    tables, cur, title, last = [], None, None, ""
    for ln in open(path, encoding="utf-8"):
        ln = ln.rstrip("\n")
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cur is None:
                cur, title = [], last
            if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                cur.append(cells)
        else:
            if cur is not None:
                tables.append((title, cur))
                cur = None
            if ln.strip():
                last = ln.strip()
    if cur is not None:
        tables.append((title, cur))
    return tables


def write_csv(name, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        csv.writer(f).writerows(rows)


def auc(s, y):
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s))
    ranks[order] = np.arange(1, len(s) + 1)
    for v in np.unique(s):  # average ranks over ties
        m = s == v
        ranks[m] = ranks[m].mean()
    npos, nneg = y.sum(), (~y).sum()
    return (ranks[y].sum() - npos * (npos + 1) / 2) / (npos * nneg)


def indomain():
    """5-fold in-domain CV AUC, DINOv3 vs DINOv2 (the 'ceiling' table)."""
    get = {}
    for fn, key in (
        ("probe_results_dinov3.md", "DINOv3 ViT-B/16"),
        ("probe_results.md", "DINOv2-base"),
    ):
        for title, rows in md_tables(os.path.join(PT, "tmp/thirdparty", fn)):
            if rows[0] == ["tag", "eval pos", "CV AUC"]:
                get[key] = {r[0]: (int(r[1]), float(r[2])) for r in rows[1:]}
            if "src pos/neg" in rows[0] and key.startswith("DINOv3"):
                write_csv("transfer_had_abhuman_dinov3.csv", rows)
    rows = [
        ["tag", "eval_positives_of_1516", "cv_auc_dinov3_vitb16", "cv_auc_dinov2_base"]
    ]
    for t in ANOMALY4:
        rows.append(
            [
                t,
                get["DINOv3 ViT-B/16"][t][0],
                get["DINOv3 ViT-B/16"][t][1],
                get["DINOv2-base"][t][1],
            ]
        )
    write_csv("indomain_cv_auc.csv", rows)
    return rows


def bakeoff():
    ((_, rows),) = md_tables(os.path.join(PT, "tmp/base_bakeoff/results.md"))
    out = [
        [
            "tag",
            "eval_positives",
            "auc_convnext_b",
            "auc_vitb16",
            "vitb16_ci_vs_convnext",
            "auc_convnext_l",
        ]
    ]
    for r in rows[1:]:
        v, ci = r[3].split(" ", 1)
        out.append(
            [r[0], int(r[1]), float(r[2]), float(v), ci, float(r[4].split(" ")[0])]
        )
    write_csv("bakeoff_probe_auc.csv", out)
    data = sorted(out[1:], key=lambda r: r[3])
    fig, ax = plt.subplots(figsize=(7, 5.5))
    yy = np.arange(len(data))
    ax.barh(yy, [r[3] - 0.5 for r in data], left=0.5, color="#3b6ea5", height=0.6)
    for i, r in enumerate(data):
        ax.text(r[3] + 0.005, i, f"{r[3]:.3f}  ({r[1]} pos)", va="center", fontsize=8)
    ax.set_yticks(yy, [r[0] for r in data], fontsize=9)
    ax.set_xlim(0.5, 1.08)
    ax.axvline(0.5, color="#888", lw=0.8)
    ax.set_xlabel("held-out AUC (0.5 = chance)")
    ax.set_title(
        "Frozen DINOv3 ViT-B/16 + logistic probe (C = 0.05) fitted on the library train set,\n"
        "scored on the 1,653-picture eval (pixltagger base_bakeoff)",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "bakeoff_probe_auc.png"), dpi=130)
    plt.close(fig)


def oof(clean=True):
    """Out-of-fold probe on our own eval: what lands in each tag's top 100."""
    scores = json.load(open(os.path.join(PT, "tmp/loop/scores/probe_eval_oof.json")))
    meta = [
        m
        for m in json.load(
            open(os.path.join(PT, "tmp/thirdparty/feats_dinov3/eval.json"))
        )
        if m["ok"]
    ]
    assert {m["id"] for m in meta} == set(scores), (
        "score file and label file disagree on pictures"
    )
    # The OOF fit kept 191 blocky copies, so a copy and its source can sit in different folds and
    # leak into each other's score. Keep only pictures that are neither a copy nor a source.
    pairs = json.load(
        open(os.path.join(PT, "tmp/blocky_eval_sources.json"))
    )  # {copy_id: source_id}
    tainted = set(pairs) | set(pairs.values())
    if not clean:
        tainted = set()
    meta = [m for m in meta if m["id"] not in tainted]
    ids = [m["id"] for m in meta]
    tags = sorted({t for v in scores.values() for t in v})
    rows = [
        [
            "tag",
            "pictures",
            "positives",
            "base_rate",
            "auc",
            "p_at_10",
            "p_at_25",
            "p_at_50",
            "p_at_100",
            "positives_in_top100",
            "lift_at_100",
        ]
    ]
    curves = {}
    for t in tags:
        s = np.array([scores[i][t] for i in ids])
        y = np.array([t in m["tags"] for m in meta])
        order = np.argsort(-s, kind="mergesort")
        hits = np.cumsum(y[order])
        base = y.mean()
        pk = {k: hits[k - 1] / k for k in (10, 25, 50, 100)}
        rows.append(
            [
                t,
                len(y),
                int(y.sum()),
                round(base, 4),
                round(auc(s, y), 3),
                *(round(pk[k], 2) for k in (10, 25, 50, 100)),
                int(hits[99]),
                round(pk[100] / base, 1),
            ]
        )
        curves[t] = (hits[:300] / np.arange(1, 301), base)
    rows[1:] = sorted(rows[1:], key=lambda r: -r[2])
    write_csv(
        "oof_probe_top100.csv" if clean else "oof_probe_top100_with_copies.csv", rows
    )
    if not clean:
        return rows

    fig, axes = plt.subplots(1, 4, figsize=(13, 3.6), sharey=True)
    for ax, t in zip(axes, ANOMALY4):
        prec, base = curves[t]
        r = next(r for r in rows if r[0] == t)
        ax.plot(np.arange(1, 301), prec, color="#3b6ea5", lw=1.8, label="probe, top-k")
        ax.axhline(
            base, color="#c0504d", ls="--", lw=1.2, label="random pick (base rate)"
        )
        ax.axvline(100, color="#888", lw=0.6)
        ax.set_title(f"{t}\n{r[2]} pos / {r[1]}; top-100 holds {r[9]}", fontsize=9)
        ax.set_xlabel("k (pictures taken from the top)")
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("share of top-k that are positive")
    axes[0].legend(fontsize=8, loc="upper right")
    fig.suptitle(
        f"Out-of-fold DINOv3 probe on pixltagger's eval: {len(ids)} pictures with no blocky copy "
        "(each fold fitted on ~1,365 labelled pictures)",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "oof_probe_precision_at_k.png"), dpi=130)
    plt.close(fig)
    return rows


def indomain_plot(rows):
    data = rows[1:]
    x = np.arange(len(data))
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    ax.bar(
        x - 0.18, [r[2] for r in data], 0.36, label="DINOv3 ViT-B/16", color="#3b6ea5"
    )
    ax.bar(x + 0.18, [r[3] for r in data], 0.36, label="DINOv2-base", color="#9bb7d4")
    for i, r in enumerate(data):
        ax.text(i - 0.18, r[2] + 0.01, f"{r[2]:.2f}", ha="center", fontsize=8)
    ax.set_xticks(x, [f"{r[0]}\n({r[1]} pos)" for r in data], fontsize=8)
    ax.set_ylim(0.5, 1.02)
    ax.set_ylabel("5-fold CV AUC")
    ax.set_title(
        "Round 0's numbers: in-domain probe on 1,516 eval pictures (C = 0.1)",
        fontsize=10,
    )
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "indomain_cv_auc.png"), dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    indomain_plot(indomain())
    bakeoff()
    oof(clean=False)
    for r in oof():
        print(r)
