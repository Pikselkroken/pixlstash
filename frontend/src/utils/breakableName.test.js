import { describe, it, expect } from "vitest";

import { breakableName } from "./breakableName.js";

describe("breakableName", () => {
  it("splits at whitespace, camelCase and separators", () => {
    expect(
      breakableName("moodyKrea2MixUncensored Image to_image XMLParser"),
    ).toEqual([
      ["moody", "Krea2", "Mix", "Uncensored"],
      ["Image"],
      ["to_", "image"],
      ["XML", "Parser"],
    ]);
  });

  it("leaves an unsplittable word whole", () => {
    expect(breakableName("  aaaaaaaaaaaaaaaa ")).toEqual([["aaaaaaaaaaaaaaaa"]]);
    expect(breakableName(null)).toEqual([]);
  });
});
