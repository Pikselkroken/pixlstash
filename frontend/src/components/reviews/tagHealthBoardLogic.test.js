// Unit coverage for TagHealthBoard.vue's pure ranking/explanation logic
// (docs/reviews/tag-review-board-redesign-ux-spec.md §7c and §8). Split out
// into tagHealthBoardLogic.js specifically so this suite can import the
// functions directly instead of mounting the SFC.

import { describe, it, expect } from "vitest";
import {
  corrections,
  rawCorrections,
  whyText,
  estDisplay,
  estRawTitle,
  zeroYieldReason,
  zeroTailStart,
  ZERO_YIELD_TITLE,
} from "./tagHealthBoardLogic";

function row(overrides = {}) {
  return {
    tag: "shirt",
    est_wrong: 0,
    est_missing: 0,
    mismatch: 0,
    model_disputes: 0,
    overturn_rate: null,
    has_model: true,
    ...overrides,
  };
}

describe("whyText", () => {
  it("wins with model_disputes over everything else, singular phrasing", () => {
    const r = row({ model_disputes: 1, est_wrong: 5, mismatch: 3 });
    expect(whyText(r)).toBe("model disputes 1 of your past call");
  });

  it("pluralises model_disputes when > 1", () => {
    const r = row({ model_disputes: 2 });
    expect(whyText(r)).toBe("model disputes 2 of your past calls");
  });

  it("has_model === false short-circuits before any other signal", () => {
    const r = row({ has_model: false, model_disputes: 4 });
    expect(whyText(r)).toBe(
      "not in the tagger's vocabulary, similarity review still works",
    );
  });

  it("picks the dominant signal: missing > wrong > mismatch", () => {
    const r = row({ est_wrong: 3, est_missing: 10, mismatch: 1 });
    expect(whyText(r)).toBe("mostly missing - model is confident but untagged");
  });

  it("picks wrong when it dominates missing and mismatch", () => {
    const r = row({ est_wrong: 10, est_missing: 2, mismatch: 1 });
    expect(whyText(r)).toBe("mostly wrong - tagged but model disagrees");
  });

  it("picks mismatch when it dominates wrong and missing", () => {
    const r = row({ est_wrong: 1, est_missing: 1, mismatch: 5 });
    expect(whyText(r)).toBe("near-identical shots disagree on this tag");
  });

  it("prefers the *_adj discounted counts over raw when present", () => {
    // Raw counts would pick "wrong"; adjusted counts flip it to "missing".
    const r = row({
      est_wrong: 10,
      est_wrong_adj: 1,
      est_missing: 2,
      est_missing_adj: 8,
      mismatch: 0,
    });
    expect(whyText(r)).toBe("mostly missing - model is confident but untagged");
  });

  it("falls back to a lopsided overturn_rate when there's no wrong/missing/mismatch signal", () => {
    const confirmed = row({ overturn_rate: 0.8 });
    expect(whyText(confirmed)).toBe("past suggestions mostly confirmed (80%)");

    const dismissed = row({ overturn_rate: 0.1 });
    expect(whyText(dismissed)).toBe(
      "past suggestions mostly dismissed (10%) - low signal",
    );
  });

  it("is empty for a middling overturn_rate with no other signal", () => {
    const r = row({ overturn_rate: 0.5 });
    expect(whyText(r)).toBe("");
  });

  it("is empty when there is no signal at all", () => {
    const r = row();
    expect(whyText(r)).toBe("");
  });
});

describe("zeroYieldReason", () => {
  it("fires only when ground_truth === 0 AND est_missing === 0", () => {
    expect(zeroYieldReason(row({ ground_truth: 0, est_missing: 0 }))).toBe(
      ZERO_YIELD_TITLE,
    );
  });

  it("names both the cause and the remedy, never a bare 'unavailable'", () => {
    // Same convention as lockedSetCopy.js: cause, then what to do about it.
    expect(ZERO_YIELD_TITLE).toMatch(/nothing to compare/i);
    expect(ZERO_YIELD_TITLE).toMatch(/confirm this tag on a few pictures/i);
    expect(ZERO_YIELD_TITLE).toMatch(/re-run the tagger/i);
    expect(ZERO_YIELD_TITLE).not.toMatch(/unavailable/i);
  });

  it("does NOT fire when the tag has confirmed examples (ground_truth > 0)", () => {
    // The false-negative case the gate exists to avoid: no flagged pictures at
    // all, but real confirmed examples, so the kNN scan has a seed to work
    // from and a review can absolutely find cards.
    const r = row({
      ground_truth: 12,
      est_wrong: 0,
      est_missing: 0,
      mismatch: 0,
    });
    expect(corrections(r)).toBe(0); // Priority 0 …
    expect(zeroYieldReason(r)).toBeNull(); // … but NOT provably empty.
  });

  it("does NOT fire when there are confident predictions but no ground truth", () => {
    expect(
      zeroYieldReason(row({ ground_truth: 0, est_missing: 4 })),
    ).toBeNull();
  });

  it("reads the RAW est_missing, never the precision-discounted _adj value", () => {
    // The exact false negative the gate must not reintroduce: 3 raw confident
    // predictions discounted to 0.4, which estDisplay() renders as "0".
    // Gating on the displayed/adjusted number would disable a button on a tag
    // that has genuine work.
    const r = row({ ground_truth: 0, est_missing: 3, est_missing_adj: 0.4 });
    expect(estDisplay(r.est_missing, r.est_missing_adj)).toBe(0); // displays 0…
    expect(zeroYieldReason(r)).toBeNull(); // …and the gate still stays open.
  });

  it("does not fire on a row that predates the ground_truth field", () => {
    // undefined is not evidence of emptiness - the button stays enabled.
    const r = row({ est_missing: 0 });
    expect(r.ground_truth).toBeUndefined();
    expect(zeroYieldReason(r)).toBeNull();
  });

  it("ignores est_wrong and mismatch entirely", () => {
    // Neither can seed a review on its own once both sides are empty, so
    // neither widens or narrows the gate.
    expect(
      zeroYieldReason(
        row({ ground_truth: 0, est_missing: 0, est_wrong: 9, mismatch: 9 }),
      ),
    ).toBe(ZERO_YIELD_TITLE);
  });
});

describe("zeroTailStart", () => {
  const score = (r) => r.v;

  it("returns the length of the scored head, excluding the zero tail", () => {
    const rows = [{ v: 5 }, { v: 2 }, { v: 0 }, { v: 0 }];
    expect(zeroTailStart(rows, score)).toBe(2);
  });

  it("returns rows.length when there is no zero tail", () => {
    expect(zeroTailStart([{ v: 3 }, { v: 1 }], score)).toBe(2);
  });

  it("returns 0 when every row is zero", () => {
    expect(zeroTailStart([{ v: 0 }, { v: 0 }], score)).toBe(0);
  });

  it("handles an empty list", () => {
    expect(zeroTailStart([], score)).toBe(0);
  });

  it("only counts the CONTIGUOUS trailing run, never interior zeros", () => {
    // An interior zero belongs to the visible head - the tail is positional.
    const rows = [{ v: 5 }, { v: 0 }, { v: 2 }, { v: 0 }];
    expect(zeroTailStart(rows, score)).toBe(3);
  });
});

describe("corrections", () => {
  it("sums raw est_wrong + est_missing + mismatch when no _adj fields exist", () => {
    expect(corrections(row({ est_wrong: 3, est_missing: 4, mismatch: 2 }))).toBe(9);
  });

  it("prefers the _adj discounted fields when present", () => {
    expect(
      corrections(
        row({ est_wrong: 10, est_wrong_adj: 1.4, est_missing: 10, est_missing_adj: 2.2, mismatch: 1 }),
      ),
    ).toBe(Math.round(1.4 + 2.2 + 1));
  });
});

describe("rawCorrections", () => {
  it("sums the raw, un-rounded, un-discounted est_wrong + est_missing + mismatch", () => {
    expect(rawCorrections(row({ est_wrong: 3, est_missing: 4, mismatch: 2 }))).toBe(9);
  });

  it("ignores the _adj discounted fields entirely, unlike corrections()", () => {
    const r = row({
      est_wrong: 10,
      est_wrong_adj: 1.4,
      est_missing: 10,
      est_missing_adj: 2.2,
      mismatch: 1,
    });
    expect(rawCorrections(r)).toBe(21);
    expect(corrections(r)).toBe(Math.round(1.4 + 2.2 + 1)); // stays discounted
  });

  it("can differ between two rows whose corrections() rounds to the same value", () => {
    // 8.4 -> rounds to 8; 8.0 -> stays 8. Same displayed Priority, different
    // raw disagreement volume - the case the tie-break exists to resolve.
    const a = row({ est_wrong_adj: 8.4, est_missing_adj: 0, mismatch: 0, est_wrong: 12, est_missing: 3 });
    const b = row({ est_wrong_adj: 8, est_missing_adj: 0, mismatch: 0, est_wrong: 8, est_missing: 0 });
    expect(corrections(a)).toBe(corrections(b));
    expect(rawCorrections(a)).toBeGreaterThan(rawCorrections(b));
  });
});

describe("estDisplay", () => {
  it("shows the rounded precision-adjusted estimate when present", () => {
    expect(estDisplay(61, 41.6)).toBe(42);
  });

  it("falls back to the raw count when no _adj value exists", () => {
    expect(estDisplay(7, undefined)).toBe(7);
    expect(estDisplay(7, null)).toBe(7);
  });

  it("treats a missing raw count as 0", () => {
    expect(estDisplay(undefined, undefined)).toBe(0);
  });

  it("can round a non-zero raw count's estimate down to 0 (unreliable tag)", () => {
    // A tag whose precision is so low the estimate rounds to zero shows 0,
    // even though the model flagged some pictures (surfaced in the tooltip).
    expect(estDisplay(3, 0.4)).toBe(0);
  });
});

describe("estRawTitle", () => {
  it("names the raw model-flag count when the estimate is discounted", () => {
    expect(estRawTitle(61, 41.6)).toMatch(/^61 flagged by the model/);
  });

  it("returns undefined (no tooltip) when there is no _adj value", () => {
    expect(estRawTitle(7, undefined)).toBeUndefined();
    expect(estRawTitle(7, null)).toBeUndefined();
  });

  it("returns undefined when the discounted number equals the raw count", () => {
    // precision ~1.0 → nothing to explain.
    expect(estRawTitle(5, 5)).toBeUndefined();
    expect(estRawTitle(5, 4.7)).toBeUndefined(); // rounds back to 5
  });
});
