// The cut behind "Suggest more pictures for <set>" (#1489). Like the person
// twin it runs in two places (the grid rebuild and the count in the action
// pill), so what is pinned is that both knobs mean what the panel says.

import { describe, it, expect } from "vitest";

import {
  cutSetSuggestions,
  setCohesion,
  signatureTagCount,
} from "./setSuggestionCut.js";

function match(id, likeness, tagsMatched, tagsTotal = 3) {
  return {
    picture_id: id,
    likeness,
    cohesion: 0.82,
    tags_matched: tagsMatched,
    tags_total: tagsTotal,
  };
}

const ranked = [match(1, 0.9, 0), match(2, 0.8, 3), match(3, 0.6, 2)];

describe("cutSetSuggestions", () => {
  it("cuts on match strength alone while the tag knob is at zero", () => {
    expect(cutSetSuggestions(ranked, 0.75, 0).map((m) => m.picture_id)).toEqual(
      [1, 2],
    );
  });

  it("drops a strong match that carries too few of the set's tags", () => {
    expect(cutSetSuggestions(ranked, 0.5, 2).map((m) => m.picture_id)).toEqual(
      [2, 3],
    );
  });

  it("applies both knobs together, order preserved", () => {
    expect(cutSetSuggestions(ranked, 0.7, 1).map((m) => m.picture_id)).toEqual(
      [2],
    );
  });

  it("treats a match without tag counts as carrying none", () => {
    const bare = [{ picture_id: 9, likeness: 0.9 }];
    expect(cutSetSuggestions(bare, 0.5, 0)).toHaveLength(1);
    expect(cutSetSuggestions(bare, 0.5, 1)).toHaveLength(0);
  });

  it("is empty for no list", () => {
    expect(cutSetSuggestions(null, 0.5, 0)).toEqual([]);
  });
});

describe("signatureTagCount / setCohesion", () => {
  it("read the set-wide values off the ranked list", () => {
    expect(signatureTagCount(ranked)).toBe(3);
    expect(setCohesion(ranked)).toBe(0.82);
  });

  it("report nothing for an empty list", () => {
    expect(signatureTagCount([])).toBe(0);
    expect(setCohesion([])).toBeNull();
  });
});
