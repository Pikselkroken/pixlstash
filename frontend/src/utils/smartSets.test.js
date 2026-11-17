import { describe, expect, it } from "vitest";

import { smartSetDescription, smartSetSummary } from "./smartSets";

describe("smartSetSummary", () => {
  it("names the view, then each chip", () => {
    expect(
      smartSetSummary({
        view: "Anna",
        chips: [
          { kind: "Score", value: "4+" },
          { kind: "Tag", value: "portrait" },
        ],
      }),
    ).toBe("Anna · Score 4+ · Tag portrait");
  });

  it("leaves out an All pictures view", () => {
    expect(
      smartSetSummary({ view: null, chips: [{ kind: "Tag", value: "cat" }] }),
    ).toBe("Tag cat");
    expect(smartSetSummary()).toBe("");
  });
});

describe("smartSetDescription", () => {
  it("is null for an ordinary set", () => {
    expect(smartSetDescription({ id: 1, smart: null })).toBeNull();
  });

  it("says automatic, custom or locked", () => {
    const smart = { filter: "tag=cat", summary: "Tag cat", custom: false };
    expect(smartSetDescription({ smart })).toBe(
      "Smart set: fills itself with Tag cat. Automatic.",
    );
    expect(smartSetDescription({ smart: { ...smart, custom: true } })).toMatch(
      /Custom: you added or took out pictures by hand\.$/,
    );
    expect(
      smartSetDescription({ smart: { ...smart, custom: true }, locked: true }),
    ).toMatch(/Locked, so it keeps the pictures it has\.$/);
    expect(smartSetDescription({ smart: { filter: "tag=cat" } })).toBe(
      "Smart set: fills itself from a saved filter. Automatic.",
    );
  });
});
