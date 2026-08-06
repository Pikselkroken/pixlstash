// Tests for the create-person-from-context-menu flow helpers (#645):
// the default "Character NNNN" naming and the assign-after-save decision
// (face ids win over picture ids). ImageGrid.handleCreatePersonSaved calls
// addCharacterFacesByFaceId for mode "faces" and addCharacterFaces for mode
// "pictures", so the mode decided here IS the endpoint choice.

import { describe, it, expect } from "vitest";
import {
  nextFreeCharacterName,
  chooseCharacterAssignment,
} from "./characterCreateFlow.js";

describe("nextFreeCharacterName", () => {
  it("starts the series at Character 0001", () => {
    expect(nextFreeCharacterName([])).toBe("Character 0001");
  });

  it("skips names that are already taken", () => {
    const existing = [
      { name: "Character 0001" },
      { name: "Character 0002" },
      { name: "Alice" },
    ];
    expect(nextFreeCharacterName(existing)).toBe("Character 0003");
  });

  it("fills gaps in the series", () => {
    const existing = [{ name: "Character 0002" }];
    expect(nextFreeCharacterName(existing)).toBe("Character 0001");
  });

  it("tolerates a non-array and rows without a name", () => {
    expect(nextFreeCharacterName(null)).toBe("Character 0001");
    expect(nextFreeCharacterName([{}, { name: null }])).toBe("Character 0001");
  });
});

describe("chooseCharacterAssignment", () => {
  it("prefers face ids when a face selection exists", () => {
    const result = chooseCharacterAssignment({
      pictureIds: ["10", "11"],
      faceEntries: [
        { imageId: "10", faceIdx: 0, faceId: 7 },
        { imageId: "12", faceIdx: 1, faceId: 9 },
      ],
    });
    expect(result.mode).toBe("faces");
    expect(result.ids).toEqual([7, 9]);
    // Bookkeeping targets the faces' pictures, not the picture selection.
    expect(result.pictureIds).toEqual(["10", "12"]);
  });

  it("deduplicates picture ids for multiple faces in one picture", () => {
    const result = chooseCharacterAssignment({
      faceEntries: [
        { imageId: "10", faceIdx: 0, faceId: 1 },
        { imageId: "10", faceIdx: 1, faceId: 2 },
      ],
    });
    expect(result.ids).toEqual([1, 2]);
    expect(result.pictureIds).toEqual(["10"]);
  });

  it("ignores face entries without a face id", () => {
    const result = chooseCharacterAssignment({
      pictureIds: ["10"],
      faceEntries: [{ imageId: "10", faceIdx: 0, faceId: null }],
    });
    expect(result.mode).toBe("pictures");
    expect(result.ids).toEqual(["10"]);
  });

  it("uses picture ids when no faces are selected", () => {
    const result = chooseCharacterAssignment({
      pictureIds: ["10", "11"],
      faceEntries: [],
    });
    expect(result.mode).toBe("pictures");
    expect(result.ids).toEqual(["10", "11"]);
    expect(result.pictureIds).toEqual(["10", "11"]);
  });

  it("reports none for an empty selection", () => {
    expect(chooseCharacterAssignment({}).mode).toBe("none");
    expect(chooseCharacterAssignment().mode).toBe("none");
  });
});
