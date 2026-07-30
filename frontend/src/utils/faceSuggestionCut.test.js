// The cut behind "Suggest more pictures of <person>" (#636). It runs in two
// places (the grid rebuild and the count in the action pill), so the thing
// worth pinning is that both knobs mean what the panel says they mean, and that
// a response without per-reference scores still behaves like the old one.

import { describe, it, expect } from "vitest";

import {
  agreeingReferenceCount,
  cutFaceSuggestions,
  referenceFaceCount,
} from "./faceSuggestionCut.js";

/** A match whose winning face scored `refs` against the person's references. */
function match(id, refs) {
  return {
    picture_id: id,
    likeness: Math.max(...refs),
    reference_likeness: refs,
  };
}

describe("agreeingReferenceCount", () => {
  it("counts the references that clear the cut", () => {
    expect(agreeingReferenceCount(match(1, [0.9, 0.72, 0.4]), 0.7)).toBe(2);
  });

  it("falls back to the combined score when the row is absent", () => {
    // An older server, or `include_reference_scores` off. `combine=max` already
    // told us one reference matched, which is what keeps minRefs=1 correct
    // rather than silently emptying the grid.
    expect(agreeingReferenceCount({ likeness: 0.8 }, 0.7)).toBe(1);
    expect(agreeingReferenceCount({ likeness: 0.6 }, 0.7)).toBe(0);
  });
});

describe("referenceFaceCount", () => {
  it("reads the reference count off the ranked list", () => {
    expect(referenceFaceCount([match(1, [0.9, 0.5])])).toBe(2);
  });

  it("is zero when nothing can answer it, so the slider is dropped", () => {
    expect(referenceFaceCount([])).toBe(0);
    expect(referenceFaceCount([{ likeness: 0.9 }])).toBe(0);
    expect(referenceFaceCount(null)).toBe(0);
  });
});

describe("cutFaceSuggestions", () => {
  // `strong` resembles one reference perfectly and the others not at all;
  // `broad` resembles all three moderately. Their order under `likeness` is the
  // reverse of their order under agreement, which is the whole point of the
  // second knob.
  const strong = match(1, [0.99, 0.2, 0.1]);
  const broad = match(2, [0.78, 0.75, 0.73]);
  const ranked = [strong, broad];

  it("cuts on strength alone at the default agreement", () => {
    expect(cutFaceSuggestions(ranked, 0.7, 1).map((m) => m.picture_id)).toEqual(
      [1, 2],
    );
    expect(cutFaceSuggestions(ranked, 0.8, 1).map((m) => m.picture_id)).toEqual(
      [1],
    );
  });

  it("drops the higher-scoring match when it satisfies too few references", () => {
    // This is the case `likeness` cannot express: 0.99 outranks 0.78, and yet
    // it is the 0.78 that looks like the person from three angles.
    expect(cutFaceSuggestions(ranked, 0.7, 2).map((m) => m.picture_id)).toEqual(
      [2],
    );
    expect(cutFaceSuggestions(ranked, 0.7, 3).map((m) => m.picture_id)).toEqual(
      [2],
    );
  });

  it("counts agreement at the strength cut, not at a floor of its own", () => {
    // Raising the strength cut also tightens what "agrees" means, which is why
    // the panel can describe the pair in one sentence.
    expect(cutFaceSuggestions(ranked, 0.76, 2)).toEqual([]);
  });

  it("preserves the ranked order", () => {
    expect(cutFaceSuggestions(ranked, 0.5, 1).map((m) => m.picture_id)).toEqual(
      [1, 2],
    );
  });

  it("treats a missing or nonsense knob as no constraint", () => {
    expect(cutFaceSuggestions(ranked, 0.7).map((m) => m.picture_id)).toEqual([
      1, 2,
    ]);
    expect(cutFaceSuggestions(ranked, 0.7, 0).map((m) => m.picture_id)).toEqual(
      [1, 2],
    );
    expect(cutFaceSuggestions(null, 0.7, 1)).toEqual([]);
  });
});
