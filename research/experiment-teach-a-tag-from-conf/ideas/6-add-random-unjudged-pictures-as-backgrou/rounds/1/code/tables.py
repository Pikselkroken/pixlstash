"""Roll summary.csv up into the report's tables.

    python tables.py EVIDENCE_DIR

Writes EVIDENCE_DIR/by_variant.csv: per n, variant and tag group (rare / common), how many
tags the idea "counts" on (precision in the top 100 held-out verdicts up by more than two
standard errors of the paired difference, AP not down by more than two), how many it hurts
(top-100 precision or AP down by more than two), and the mean changes.
"""

import csv
import os
import sys
from collections import defaultdict


def main(evidence_dir):
    with open(os.path.join(evidence_dir, "summary.csv")) as f:
        rows = list(csv.DictReader(f))  # "verdicts" rows give the baseline unjudged share
    groups = defaultdict(list)
    for r in rows:
        groups[(int(r["n"]), r["method"], r["group"])].append(r)
    out = []
    for (n, m, g), rs in sorted(groups.items()):
        f = lambda k: round(sum(float(r[k]) for r in rs) / len(rs), 4)  # noqa: E731
        out.append({
            "n": n, "variant": m, "group": g, "tags": len(rs),
            "counts": sum(r["counts"] == "True" for r in rs),
            "p100_down": sum(float(r["d_p100_mean"]) < -2 * float(r["d_p100_sd"])
                             / int(r["repeats"]) ** 0.5 for r in rs),
            "ap_down": sum(r["ap_down"] == "True" for r in rs),
            "mean_d_p100": f("d_p100_mean"), "mean_d_p50": f("d_p50_mean"),
            "mean_d_ap": f("d_ap_mean"),
            "mean_unjudged_in_top100": f("unjudged_in_top100_mean"),
        })
    with open(os.path.join(evidence_dir, "by_variant.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    for r in out:
        if r["n"] == 100:
            print(r)


if __name__ == "__main__":
    main(sys.argv[1])
