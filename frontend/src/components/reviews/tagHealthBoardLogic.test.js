// Unit coverage for TagHealthBoard.vue's pure ranking/explanation logic
// (docs/reviews/tag-review-board-redesign-ux-spec.md §7c and §8). Split out
// into tagHealthBoardLogic.js specifically so this suite can import the
// functions directly instead of mounting the SFC.

import { describe, it, expect } from "vitest";
import {
  corrections,
  rawCorrections,
  whyText,
  F1_BOOST_THRESHOLD,
  F1_BOOST_MAX,
  isBoostEligible,
  boostFactor,
  boostedScore,
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
    eval_metric_kind: null,
    eval_threshold_source: null,
    eval_f1: null,
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
      "not in the tagger's vocabulary — similarity review still works",
    );
  });

  it("picks the dominant signal: missing > wrong > mismatch", () => {
    const r = row({ est_wrong: 3, est_missing: 10, mismatch: 1 });
    expect(whyText(r)).toBe("mostly missing — model is confident but untagged");
  });

  it("picks wrong when it dominates missing and mismatch", () => {
    const r = row({ est_wrong: 10, est_missing: 2, mismatch: 1 });
    expect(whyText(r)).toBe("mostly wrong — tagged but model disagrees");
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
    expect(whyText(r)).toBe("mostly missing — model is confident but untagged");
  });

  it("falls back to a lopsided overturn_rate when there's no wrong/missing/mismatch signal", () => {
    const confirmed = row({ overturn_rate: 0.8 });
    expect(whyText(confirmed)).toBe("past suggestions mostly confirmed (80%)");

    const dismissed = row({ overturn_rate: 0.1 });
    expect(whyText(dismissed)).toBe(
      "past suggestions mostly dismissed (10%) — low signal",
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
    // raw disagreement volume — the case the tie-break exists to resolve.
    const a = row({ est_wrong_adj: 8.4, est_missing_adj: 0, mismatch: 0, est_wrong: 12, est_missing: 3 });
    const b = row({ est_wrong_adj: 8, est_missing_adj: 0, mismatch: 0, est_wrong: 8, est_missing: 0 });
    expect(corrections(a)).toBe(corrections(b));
    expect(rawCorrections(a)).toBeGreaterThan(rawCorrections(b));
  });
});

describe("Spec F accuracy tie-breaker", () => {
  function frozenF1Row(f1, overrides = {}) {
    return row({
      eval_metric_kind: "F1",
      eval_threshold_source: "calibrated",
      eval_f1: f1,
      est_wrong: 5,
      ...overrides,
    });
  }

  it("is eligible only for a frozen, calibrated F1 row below the threshold", () => {
    expect(isBoostEligible(frozenF1Row(0.5))).toBe(true);
    expect(isBoostEligible(frozenF1Row(F1_BOOST_THRESHOLD))).toBe(false); // at threshold: no boost
    expect(isBoostEligible(frozenF1Row(0.9))).toBe(false);
  });

  it("is never eligible for an AP-kind row, regardless of value", () => {
    const r = row({ eval_metric_kind: "AP", eval_threshold_source: "calibrated", eval_f1: 0.1 });
    expect(isBoostEligible(r)).toBe(false);
  });

  it("is never eligible for an unfrozen row (no eval_metric_kind)", () => {
    const r = row({ eval_metric_kind: null, eval_f1: 0.1 });
    expect(isBoostEligible(r)).toBe(false);
  });

  it("is never eligible for an uncalibrated_fallback threshold", () => {
    const r = frozenF1Row(0.2, { eval_threshold_source: "uncalibrated_fallback" });
    expect(isBoostEligible(r)).toBe(false);
  });

  it("boostFactor is 1 (no-op) for an ineligible row", () => {
    expect(boostFactor(row())).toBe(1);
  });

  it("boostFactor is capped at F1_BOOST_MAX when eval_f1 is 0", () => {
    expect(boostFactor(frozenF1Row(0))).toBeCloseTo(F1_BOOST_MAX);
  });

  it("boostFactor grows continuously as eval_f1 falls further below the threshold", () => {
    const weak = boostFactor(frozenF1Row(0.6));
    const weaker = boostFactor(frozenF1Row(0.3));
    const weakest = boostFactor(frozenF1Row(0));
    expect(weak).toBeGreaterThan(1);
    expect(weak).toBeLessThan(weaker);
    expect(weaker).toBeLessThan(weakest);
    expect(weakest).toBeLessThanOrEqual(F1_BOOST_MAX);
  });

  it("boostedScore multiplies corrections() by the boost factor, never altering corrections() itself", () => {
    const r = frozenF1Row(0, { est_wrong: 4, est_missing: 4, mismatch: 2 });
    expect(corrections(r)).toBe(10);
    expect(boostedScore(r)).toBeCloseTo(10 * F1_BOOST_MAX);
  });

  it("a small boosted score can never leapfrog a much larger unboosted one (continuous+capped, not a band)", () => {
    const weakLowPriority = frozenF1Row(0, { est_wrong: 1, est_missing: 0, mismatch: 0 }); // corrections=1
    const strongHighPriority = row({ est_wrong: 40, est_missing: 0, mismatch: 0 }); // corrections=40, ineligible
    expect(boostedScore(weakLowPriority)).toBeLessThan(boostedScore(strongHighPriority));
  });
});
