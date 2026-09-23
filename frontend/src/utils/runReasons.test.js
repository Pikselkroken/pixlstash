// The one place a pre-flight code becomes a sentence (`runReasons.js`). Its
// other branches are covered through the popups that draw them; this file
// holds the ones whose wording carries a contract of its own.

import { describe, it, expect } from "vitest";

import {
  PICTURE_INPUT_UNFILLED,
  readReason,
  reasonsBlock,
} from "./runReasons";

describe("picture_input_unfilled (#1457)", () => {
  const reason = {
    code: PICTURE_INPUT_UNFILLED,
    inputs: [
      { slot_label: "9f".repeat(32), input_name: "image", title: "Reference" },
    ],
  };

  it("names the input by its title, never by the slot label hash", () => {
    const read = readReason(reason);
    expect(read.text).toContain("Reference");
    expect(read.text).not.toContain("9f9f");
    expect(read.blocking).toBe(true);
  });

  it("still reads when an older server sent no title", () => {
    const read = readReason({
      code: PICTURE_INPUT_UNFILLED,
      inputs: [{ slot_label: "9f".repeat(32), input_name: "image" }],
    });
    expect(read.text).toContain("one of its picture inputs");
    expect(read.text).not.toContain("9f9f");
  });

  it("blocks, as every refusal does", () => {
    expect(reasonsBlock([reason])).toBe(true);
  });

  it("is not read as the retired fixed_input_deleted", () => {
    // A client pinned to the old code falls back to the generic sentence; the
    // old one's "Set it again on the workflow's own tab" pointed nowhere.
    expect(readReason({ code: "fixed_input_deleted" }).text).toBe(
      "This workflow cannot be run just now.",
    );
  });
});
