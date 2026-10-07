import { describe, it, expect } from "vitest";

import { checkCopy, comboKey, modelVerdictMark } from "./setVerdicts";

const check = (state, evidence = {}) => ({
  check: state,
  evidence: { together: 6, checked: 5, passing: 4, ...evidence },
});

describe("comboKey", () => {
  it("sorts numerically and joins with commas, as the server does", () => {
    expect(comboKey([12, 3, 7])).toBe("3,7,12");
  });
});

describe("checkCopy", () => {
  it("says nothing extra without an entry or with no evidence", () => {
    expect(checkCopy(null)).toBeNull();
    expect(checkCopy(check("none"))).toBeNull();
  });

  it("words pending with the done count", () => {
    expect(checkCopy(check("pending", { checked: 2 })).count).toBe(
      "These have all run together in 6 pictures. PixlStash is still checking them (2 of 6 done).",
    );
  });

  it("asks only on pass and fail, and the fail caveat is its own", () => {
    expect(checkCopy(check("too_few")).ask).toBeUndefined();
    expect(checkCopy(check("pass")).ask).toBe(
      "PixlStash thinks this set produces sensible output. Do you agree?",
    );
    const fail = checkCopy(check("fail"));
    expect(fail.ask).toBe(
      "PixlStash thinks this set does not produce sensible output. Do you agree?",
    );
    expect(fail.caveat).toBe(
      "This catches noise and unrelated output, not style or quality.",
    );
  });

  it("uses the payload's counts and no thresholds of its own", () => {
    // 1 checked of 1: a frontend threshold of 3 would have to override this.
    expect(
      checkCopy(check("pass", { together: 1, checked: 1, passing: 1 })).ask,
    ).toBeTruthy();
  });
});

describe("modelVerdictMark", () => {
  const problem = {
    combo_key: "1,2",
    names: ["Base", "LoRA"],
    verdict: "problem",
  };
  const fine = {
    combo_key: "1,3",
    names: ["Base", "VAE"],
    verdict: "not_problem",
  };

  it("is nothing without verdicts", () => {
    expect(modelVerdictMark([])).toBeNull();
    expect(modelVerdictMark(undefined)).toBeNull();
  });

  it("names the sets in the tooltip", () => {
    expect(modelVerdictMark([problem]).tooltip).toBe(
      "Marked a problem in 1 set: Base + LoRA",
    );
  });

  it("lets the cross win and lists both", () => {
    const mark = modelVerdictMark([fine, problem]);
    expect(mark.verdict).toBe("problem");
    expect(mark.tooltip).toContain("Marked a problem in 1 set: Base + LoRA");
    expect(mark.tooltip).toContain("Not a problem in 1 set: Base + VAE");
  });
});
