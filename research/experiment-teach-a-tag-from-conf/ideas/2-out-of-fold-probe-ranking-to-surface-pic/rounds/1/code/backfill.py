"""Backfill the two out-of-fold surfacing games from pixltagger's files (read-only). Re-runs nothing.

Sources (under --root):
  tmp/loop/ba_paired_meta.json + ba_paired_game/verdicts.json   bad anatomy: 10 ranked / 10 random / 10 salt
  tmp/loop/judged_tags.jsonl, game mine_eyes_oof                malformed eyes: out-of-fold top 60 (ranked)
  tmp/loop/judged_tags.jsonl, game mine_eyes_c6                 malformed eyes: random 58 in cluster 6
  tmp/loop/mine_eyes_oof/top60_scores.json                      fold-model score of each ranked eyes picture
"""
import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from scipy.stats import fisher_exact, mannwhitneyu  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path.home() / "Projects/pixltagger"))
A = ap.parse_args()
LOOP = Path(A.root) / "tmp/loop"
OUT = Path(__file__).resolve().parents[1] / "evidence"
OUT.mkdir(exist_ok=True)


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def wcsv(name, rows):
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def arm(tag, name, served, judged, hits):
    lo, hi = wilson(hits, judged)
    return dict(tag=tag, arm=name, served=served, skipped=served - judged, judged=judged, hits=hits,
                rate_of_judged=round(hits / judged, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                rate_of_served=round(hits / served, 3))


rows = []
# bad anatomy: the paired game
meta = json.loads((LOOP / "ba_paired_meta.json").read_text())
ver = json.loads((LOOP / "ba_paired_game/verdicts.json").read_text())
for name in ("ranked", "random", "salt"):
    ids = [i for i, a in meta.items() if a == name]
    judged = [i for i in ids if not ver[i].get("skip")]
    rows.append(arm("bad anatomy", name, len(ids), len(judged), sum("bad anatomy" in ver[i]["add"] for i in judged)))

# malformed eyes: ranked top 60 vs the random cluster-6 sample
eyes = {"mine_eyes_oof": {}, "mine_eyes_c6": {}}
for line in (LOOP / "judged_tags.jsonl").read_text().splitlines():
    d = json.loads(line)
    if d.get("game") in eyes and d["tag"] == "malformed eyes":
        eyes[d["game"]][d["picture"]] = d["verdict"]
for game, name in (("mine_eyes_oof", "ranked"), ("mine_eyes_c6", "random")):
    v = eyes[game]
    judged = [p for p, x in v.items() if x != "skip"]
    rows.append(arm("malformed eyes", name, len(v), len(judged), sum(v[p] == "add" for p in judged)))
wcsv("surfacing_games.csv", rows)

# ranked vs random, one-sided Fisher exact on judged pictures
tests = []
for tag in ("bad anatomy", "malformed eyes"):
    r, q = (next(x for x in rows if x["tag"] == tag and x["arm"] == a) for a in ("ranked", "random"))
    table = [[r["hits"], r["judged"] - r["hits"]], [q["hits"], q["judged"] - q["hits"]]]
    tests.append(dict(tag=tag, ranked=f'{r["hits"]}/{r["judged"]}', random=f'{q["hits"]}/{q["judged"]}',
                      difference=round(r["rate_of_judged"] - q["rate_of_judged"], 3),
                      fisher_p_ranked_greater=round(fisher_exact(table, alternative="greater")[1], 3)))
wcsv("ranked_vs_random.csv", tests)

fig, axes = plt.subplots(1, 2, figsize=(8, 3.4), sharey=True)
colors = {"ranked": "#4c72b0", "random": "#999999", "salt": "#55a868"}
for ax, tag in zip(axes, ("bad anatomy", "malformed eyes")):
    sub = [x for x in rows if x["tag"] == tag]
    for k, g in enumerate(sub):
        ax.bar(k, g["rate_of_judged"], color=colors[g["arm"]])
        ax.errorbar(k, g["rate_of_judged"], yerr=[[g["rate_of_judged"] - g["ci_lo"]], [g["ci_hi"] - g["rate_of_judged"]]],
                    color="k", capsize=4)
        ax.text(k, g["ci_hi"] + 0.03, f'{g["hits"]}/{g["judged"]}', ha="center")
    ax.set_xticks(range(len(sub)), [{"ranked": "out-of-fold top", "random": "random", "salt": "known positives"}[g["arm"]]
                                    for g in sub], fontsize=8)
    ax.set_title(tag, fontsize=10)
    ax.set_ylim(0, 1.15)
axes[0].set_ylabel("share confirmed positive (of judged)")
fig.suptitle("Out-of-fold ranked batch vs random batch (Wilson 95% CI)", fontsize=10)
fig.tight_layout()
fig.savefig(OUT / "surfacing_games.png", dpi=130)
plt.close(fig)

# inside the ranked eyes batch: did a higher out-of-fold score mean a likelier positive?
scores = json.loads((LOOP / "mine_eyes_oof/top60_scores.json").read_text())
v = eyes["mine_eyes_oof"]
order = sorted(v, key=lambda p: -scores[p])
per = [dict(rank=i + 1, picture_score=scores[p], verdict=v[p]) for i, p in enumerate(order)]
wcsv("eyes_ranked_by_score.csv", per)
pos = [scores[p] for p in v if v[p] == "add"]
neg = [scores[p] for p in v if v[p] == "clean"]
auc = mannwhitneyu(pos, neg).statistic / (len(pos) * len(neg))
thirds = []
for k in range(3):
    chunk = per[k * 20:(k + 1) * 20]
    j = [x for x in chunk if x["verdict"] != "skip"]
    h = sum(x["verdict"] == "add" for x in j)
    thirds.append(dict(ranks=f"{k * 20 + 1}-{k * 20 + 20}", score_range=f'{chunk[-1]["picture_score"]:.3f}-{chunk[0]["picture_score"]:.3f}',
                       judged=len(j), hits=h, rate=round(h / len(j), 3)))
thirds.append(dict(ranks="AUC of score, judged only", score_range="", judged=len(pos) + len(neg), hits=len(pos), rate=round(auc, 3)))
wcsv("eyes_ranked_thirds.csv", thirds)

fig, ax = plt.subplots(figsize=(6.5, 3.2))
for x in per:
    c = {"add": "#c44e52", "clean": "#4c72b0", "skip": "#cccccc"}[x["verdict"]]
    ax.scatter(x["rank"], x["picture_score"], color=c, s=28 if x["verdict"] == "add" else 14, zorder=3 if x["verdict"] == "add" else 2)
for lab, c in (("confirmed", "#c44e52"), ("clean", "#4c72b0"), ("skipped", "#cccccc")):
    ax.scatter([], [], color=c, label=lab)
ax.set_xlabel("rank in the out-of-fold top 60")
ax.set_ylabel("out-of-fold eyes score")
ax.set_title(f"Malformed eyes: positives sit anywhere in the ranked 60 (score AUC {auc:.2f} on judged)", fontsize=9)
ax.legend(fontsize=8)
fig.tight_layout()
fig.savefig(OUT / "eyes_ranked_by_score.png", dpi=130)
plt.close(fig)

for r in rows + tests + thirds:
    print(r)
