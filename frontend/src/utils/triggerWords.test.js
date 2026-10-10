import { describe, it, expect } from "vitest";

import { markWords, namesWord, triggerWord } from "./triggerWords";

describe("triggerWord", () => {
  it("is the first word, and nothing for a LoRA that needs none", () => {
    expect(triggerWord({ trigger_words: [" ohwx ", "woman"] })).toBe("ohwx");
    expect(triggerWord({ trigger_words: [] })).toBe("");
    expect(triggerWord({ trigger_words: null })).toBe("");
    expect(triggerWord(null)).toBe("");
  });
});

describe("markWords", () => {
  it("cuts the text at each whole-word hit, any case", () => {
    expect(markWords("Mira on a beach, mira smiling", ["mira"])).toEqual([
      { text: "Mira", hit: true },
      { text: " on a beach, ", hit: false },
      { text: "mira", hit: true },
      { text: " smiling", hit: false },
    ]);
  });

  it("does not find a word inside another", () => {
    expect(namesWord("an admiral, miranda", "mira")).toBe(false);
    expect(namesWord("palmira, casimira", "mira")).toBe(false);
    expect(namesWord("photo of mira.", "mira")).toBe(true);
  });

  it("takes a phrase, and a word with regex characters in it", () => {
    expect(namesWord("a photo of ohwx woman", "ohwx woman")).toBe(true);
    expect(namesWord("a photo of ohwx", "ohwx woman")).toBe(false);
    expect(namesWord("style of m.i+ra", "m.i+ra")).toBe(true);
    expect(namesWord("style of mxiira", "m.i+ra")).toBe(false);
  });

  it("returns the text whole when there is nothing to look for", () => {
    expect(markWords("plain", [])).toEqual([{ text: "plain", hit: false }]);
    expect(markWords("", ["mira"])).toEqual([{ text: "", hit: false }]);
    expect(namesWord("plain", "")).toBe(false);
  });
});
