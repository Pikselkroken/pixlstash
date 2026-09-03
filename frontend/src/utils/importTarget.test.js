import { describe, it, expect } from "vitest";

import { resolveImportTarget } from "./importTarget.js";

describe("resolveImportTarget", () => {
  it("files into the set you are looking at", () => {
    expect(resolveImportTarget({ selectedSet: 7 })).toEqual({
      setId: 7,
      characterId: null,
    });
  });

  it("files into the character you are looking at", () => {
    expect(resolveImportTarget({ selectedCharacter: 12 })).toEqual({
      setId: null,
      characterId: 12,
    });
  });

  it("carries both when a character is open inside a set", () => {
    expect(
      resolveImportTarget({ selectedSet: 7, selectedCharacter: 12 }),
    ).toEqual({ setId: 7, characterId: 12 });
  });

  // The default selection is the "All Pictures" sentinel, not a real character.
  // Treating it as one would try to file every pasted picture into a character
  // whose id is the string "ALL".
  it.each(["ALL", "UNASSIGNED", "SCRAPHEAP"])(
    "treats the %s view as no destination",
    (sentinel) => {
      expect(resolveImportTarget({ selectedCharacter: sentinel })).toEqual({
        setId: null,
        characterId: null,
      });
    },
  );

  // Picking the first of several would be a coin toss the user cannot see.
  // Filing nowhere is recoverable by hand; filing it wrong quietly is not.
  it("declines an ambiguous multi-selection", () => {
    expect(resolveImportTarget({ selectedSetIds: [3, 4] })).toEqual({
      setId: null,
      characterId: null,
    });
  });

  it("accepts a multi-selection that names exactly one", () => {
    expect(resolveImportTarget({ selectedSetIds: [3] }).setId).toBe(3);
  });

  it("prefers the multi-select list over the single value", () => {
    expect(
      resolveImportTarget({ selectedSetIds: [3, 4], selectedSet: 9 }).setId,
    ).toBeNull();
  });

  it("survives an empty selection", () => {
    expect(resolveImportTarget()).toEqual({ setId: null, characterId: null });
    expect(resolveImportTarget({})).toEqual({ setId: null, characterId: null });
  });
});
