"""Backfill plots for idea 3: base-rate-relative near-neighbour vote thresholds.

No experiment is re-run here. Two things are drawn:

1. The thresholds themselves, as the shipped code computes them
   (pixlstash/services/tag_scan_service.py, scan_tag, commits 6c79e6de5 and
   02fe21c2f), against the legacy fixed 0.55/0.45 pair and the uncapped
   p +/- 0.15 formula the July permutation experiment measured.
2. An analytic zero-signal model of the remove:add skew. With shuffled labels a
   picture's k=12 neighbours each carry the tag with probability p, so the vote
   fraction is approximately Binomial(12, p) / 12 (equal neighbour weights; the
   real vote is cosine-weighted). Add-eligible: untagged and frac >= t_add.
   Remove-eligible: tagged and frac <= t_rem. The twin-similarity gate is
   ignored (it applies to both directions). This is a model of the mechanism,
   not the original measurement; the measured figures that survive are
   overlaid as recorded.

Run: python code/thresholds.py   (writes into ../evidence)
"""

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import binom

EVIDENCE = Path(__file__).resolve().parent.parent / "evidence"
K = 12  # scan_tag default
N_LIBRARY = 36_318  # embeddings in the vault the July experiment used
MARGIN, LO, HI = 0.15, 0.05, 0.95
LEGACY_ADD, LEGACY_REM = 0.55, 0.45
EPS = 1e-9  # equal-weight fractions are exact multiples of 1/12


def shipped(p):
    """Thresholds exactly as scan_tag computes them today (capped)."""
    pc = min(HI, max(LO, p))
    return min(LEGACY_ADD, min(HI, pc + MARGIN)), min(LEGACY_REM, max(LO, pc - MARGIN))


def uncapped(p):
    """6c79e6de5 before the cap: what the permutation experiment measured."""
    pc = min(HI, max(LO, p))
    return min(HI, pc + MARGIN), max(LO, pc - MARGIN)


def legacy(_p):
    return LEGACY_ADD, LEGACY_REM


def zero_signal(p, t_add, t_rem):
    """Expected add / remove eligibility rates under shuffled labels."""
    p_add = binom.sf(np.ceil(t_add * K - EPS) - 1, K, p)  # P(X >= ceil(12 t))
    p_rem = binom.cdf(np.floor(t_rem * K + EPS), K, p)  # P(X <= floor(12 t))
    adds = (1 - p) * p_add
    rems = p * p_rem
    return adds, rems, (rems / adds if adds > 0 else np.inf)


SCHEMES = {"legacy 0.55/0.45": legacy, "uncapped p±0.15": uncapped, "shipped (capped)": shipped}


def main():
    EVIDENCE.mkdir(exist_ok=True)
    grid = np.round(np.arange(0.01, 0.951, 0.01), 2)

    # --- 1. thresholds vs base rate -------------------------------------
    with open(EVIDENCE / "thresholds_vs_base_rate.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["base_rate", "legacy_add", "legacy_remove", "uncapped_add",
                    "uncapped_remove", "shipped_add", "shipped_remove"])
        for p in grid:
            w.writerow([p, *legacy(p), *(round(v, 4) for v in uncapped(p)),
                        *(round(v, 4) for v in shipped(p))])

    fig, ax = plt.subplots(figsize=(7, 4.2))
    for name, fn, style in [("legacy", legacy, ":"), ("uncapped p±0.15", uncapped, "--"),
                            ("shipped (capped)", shipped, "-")]:
        a = [fn(p)[0] for p in grid]
        r = [fn(p)[1] for p in grid]
        ax.plot(grid, a, style, color="tab:green", label=f"add, {name}")
        ax.plot(grid, r, style, color="tab:red", label=f"remove, {name}")
    ax.plot(grid, grid, color="grey", lw=0.8, label="base rate p")
    ax.axvspan(0.05, 0.48, color="tab:blue", alpha=0.07, label="range the permutation test covered")
    ax.set_xlabel("tag base rate p (share of scanned pictures carrying the tag)")
    ax.set_ylabel("vote-fraction threshold")
    ax.set_title("Near-neighbour vote thresholds by base rate")
    ax.legend(fontsize=7, ncol=2, loc="upper left")
    fig.tight_layout()
    fig.savefig(EVIDENCE / "thresholds_vs_base_rate.png", dpi=130)
    plt.close(fig)

    # --- 2. zero-signal skew --------------------------------------------
    rows = []
    for p in grid:
        for name, fn in SCHEMES.items():
            adds, rems, ratio = zero_signal(p, *fn(p))
            rows.append((p, name, *fn(p), adds * N_LIBRARY, rems * N_LIBRARY, ratio))
    with open(EVIDENCE / "zero_signal_skew_model.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["base_rate", "scheme", "t_add", "t_remove",
                    "expected_add_in_36318", "expected_remove_in_36318", "remove_to_add"])
        for p, name, ta, tr, a, r, ratio in rows:
            w.writerow([p, name, round(ta, 4), round(tr, 4), round(a, 2), round(r, 2),
                        "inf" if np.isinf(ratio) else f"{ratio:.4g}"])

    fig, ax = plt.subplots(figsize=(7, 4.2))
    colors = {"legacy 0.55/0.45": "tab:orange", "uncapped p±0.15": "tab:blue",
              "shipped (capped)": "black"}
    for name in SCHEMES:
        xs = [r[0] for r in rows if r[1] == name]
        ys = [min(r[6], 1e6) for r in rows if r[1] == name]
        ax.step(xs, ys, where="mid", color=colors[name],
                ls="--" if name.startswith("uncapped") else "-", label=f"model: {name}")
    ax.axhline(1, color="grey", lw=0.8)
    ax.fill_between([0.05, 0.48], 0.2, 1.3, color="tab:blue", alpha=0.15,
                    label="recorded: 0.2-1.3x after fix (5-48%)")
    ax.plot([0.20], [58], "o", color="tab:orange", ms=8, label="recorded: 58x legacy at 20%")
    ax.set_yscale("log")
    ax.set_ylim(0.05, 1e6)
    ax.set_xlabel("tag base rate p")
    ax.set_ylabel("remove : add suspects with no real signal (log)")
    ax.set_title("Zero-signal skew, binomial model (k=12) vs recorded figures")
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(EVIDENCE / "zero_signal_skew.png", dpi=130)
    plt.close(fig)

    # Summary rows for the report.
    picks = [0.05, 0.10, 0.20, 0.30, 0.40, 0.48, 0.61]
    with open(EVIDENCE / "zero_signal_skew_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["base_rate", "legacy_remove_to_add", "uncapped_remove_to_add",
                    "shipped_remove_to_add", "legacy_expected_adds", "shipped_expected_adds"])
        for p in picks:
            out = {n: zero_signal(p, *fn(p)) for n, fn in SCHEMES.items()}

            def fmt(x):
                return "inf" if np.isinf(x) else f"{x:.3g}"

            w.writerow([p, fmt(out["legacy 0.55/0.45"][2]), fmt(out["uncapped p±0.15"][2]),
                        fmt(out["shipped (capped)"][2]),
                        f"{out['legacy 0.55/0.45'][0] * N_LIBRARY:.1f}",
                        f"{out['shipped (capped)'][0] * N_LIBRARY:.1f}"])
    print((EVIDENCE / "zero_signal_skew_summary.csv").read_text())


if __name__ == "__main__":
    main()
