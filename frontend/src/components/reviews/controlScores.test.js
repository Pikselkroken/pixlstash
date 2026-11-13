import { describe, expect, it } from "vitest";
import { controlScoreParts } from "./controlScores";

describe("controlScoreParts", () => {
  it("says nothing for a review without controls", () => {
    expect(controlScoreParts(undefined)).toEqual([]);
    expect(controlScoreParts(null)).toEqual([]);
  });

  it("counts repeats out of the controls actually answered", () => {
    expect(
      controlScoreParts({
        pos_confirmed: 9,
        pos_overturned: 1,
        neg_confirmed: 4,
        neg_overturned: 1,
        skipped: 2,
        pending: 3,
      }),
    ).toEqual([
      "9 of 10 earlier “yes” repeated",
      "4 of 5 earlier “no” repeated",
      "2 skipped",
    ]);
  });

  it("leaves out a side with nothing answered, and pending controls", () => {
    expect(
      controlScoreParts({
        pos_confirmed: 0,
        pos_overturned: 2,
        neg_confirmed: 0,
        neg_overturned: 0,
        skipped: 0,
        pending: 4,
      }),
    ).toEqual(["0 of 2 earlier “yes” repeated"]);
    expect(controlScoreParts({ pending: 5 })).toEqual([]);
  });
});
