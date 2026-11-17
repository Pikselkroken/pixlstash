"""Plot each method's confirm rate per tag from evidence/confirms_<tag>.csv, with 95%
Wilson intervals, and test each pair of methods with Fisher's exact test.

    python plot.py EVIDENCE_DIR TAG=SHARE [TAG=SHARE ...]

SHARE is the share of the library carrying the tag (count_pictures with the tag over
count_pictures), drawn as a reference mark. Writes confirmed_top100.png, pairwise.csv and
controls.csv. The rate is over judged cards only; cards the person did not reach are left
out (the list is shuffled, so the judged part is a fair sample of each method's 100).
"""

import csv
import json
import os
import sys

import matplotlib
from build_lists import write_csv
from scipy.stats import binomtest, fisher_exact

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LABELS = {
    "logreg": "classifier (logistic regression)",
    "vote": "shipped near-neighbour vote",
    "vote_judged": "vote over judged pictures only",
}
COLOURS = {"logreg": "#1b6ca8", "vote": "#d1495b", "vote_judged": "#edae49"}


def main():
    ev = sys.argv[1]
    shares = dict(a.split("=") for a in sys.argv[2:])
    by_tag = {}
    for tag in shares:
        path = os.path.join(ev, f"confirms_{tag.replace(' ', '_')}.csv")
        rows = [r for r in csv.DictReader(open(path)) if r["method"] in LABELS]
        for r in rows:
            r["k"], r["n"] = int(r["confirmed"]), int(r["confirmed"]) + int(r["rejected"])
        by_tag[tag] = rows

    pairs, controls = [], []
    for tag, rows in by_tag.items():
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                t = [[x["k"], x["n"] - x["k"]] for x in (a, b)]
                pairs.append(
                    {
                        "tag": tag,
                        "a": a["method"],
                        "b": b["method"],
                        "a_confirmed": f"{a['k']}/{a['n']}",
                        "b_confirmed": f"{b['k']}/{b['n']}",
                        "a_rate": round(a["k"] / a["n"], 3),
                        "b_rate": round(b["k"] / b["n"], 3),
                        "fisher_p": round(fisher_exact(t).pvalue, 4),
                    }
                )
        c = json.loads(rows[0]["controls"])
        yes = c["pos_confirmed"] + c["pos_overturned"]
        no = c["neg_confirmed"] + c["neg_overturned"]
        controls.append(
            {
                "tag": tag,
                **c,
                "earlier_yes_repeated": f"{c['pos_confirmed']}/{yes}",
                "earlier_no_repeated": f"{c['neg_confirmed']}/{no}",
            }
        )
    write_csv(os.path.join(ev, "pairwise.csv"), pairs)
    write_csv(os.path.join(ev, "controls.csv"), controls)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    w = 0.26
    for ti, (tag, rows) in enumerate(by_tag.items()):
        for mi, r in enumerate(rows):
            x = ti + (mi - 1) * w
            p = r["k"] / r["n"]
            ci = binomtest(r["k"], r["n"]).proportion_ci(method="wilson")
            label = LABELS[r["method"]] if ti == 0 else None
            ax.bar(x, p, w * 0.92, color=COLOURS[r["method"]], label=label)
            ax.errorbar(x, p, yerr=[[p - ci.low], [ci.high - p]], color="black", capsize=4)
            ax.text(x, 0.02, f"{r['k']}/{r['n']}", ha="center", color="white", fontsize=8)
        ax.hlines(
            float(shares[tag]),
            ti - 1.5 * w,
            ti + 1.5 * w,
            colors="grey",
            linestyles="--",
            label="share of library the tagger tags" if ti == 0 else None,
        )
    ax.set_xticks(range(len(by_tag)), list(by_tag))
    ax.set_ylim(0, 1.2)
    ax.set_ylabel("share of judged top-100 cards confirmed")
    ax.set_title("Person-judged top 100 per method (95% intervals)", fontsize=10)
    ax.legend(fontsize=8, loc="upper right", ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(ev, "confirmed_top100.png"), dpi=120)


if __name__ == "__main__":
    main()
