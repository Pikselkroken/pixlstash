import { describe, it, expect } from "vitest";

import { cssDurationMs } from "./dom.js";

const styleOf = (value) => ({ getPropertyValue: () => value });

describe("cssDurationMs", () => {
  it("reads milliseconds and the minifier's seconds alike", () => {
    expect(cssDurationMs(styleOf("700ms"), "--d")).toBe(700);
    expect(cssDurationMs(styleOf(" .7s"), "--d")).toBe(700);
    expect(cssDurationMs(styleOf("1s"), "--d")).toBe(1000);
  });

  it("falls back when the property is unset", () => {
    expect(cssDurationMs(styleOf(""), "--d")).toBe(0);
    expect(cssDurationMs(styleOf(""), "--d", 420)).toBe(420);
  });
});
