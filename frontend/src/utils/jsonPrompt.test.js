import { describe, it, expect } from "vitest";

import { promptOutline, promptPreview } from "./jsonPrompt";

// The shape a structured captioner writes: a summary plus one entry per
// region, each with a bounding box.
const CAPTIONED = JSON.stringify({
  summary: { caption: "A reference sheet of one woman in 4 panels." },
  items: [
    { label: "character", caption: "Panel 1: front view.", bbox: [-577, -1365, 704, -746] },
    { label: "object", caption: "Panel 1: her head.", bbox: [-549, -1253, 84, -858] },
  ],
});

describe("promptOutline", () => {
  it("leaves plain text and brace-led wildcards alone", () => {
    expect(promptOutline("a castle on a hill")).toBeNull();
    expect(promptOutline("{red|blue} dress, studio light")).toBeNull();
    expect(promptOutline('{"caption": "cut off mid')).toBeNull();
    expect(promptOutline(null)).toBeNull();
    expect(promptOutline("{}")).toBeNull();
  });

  it("outlines a captioner's JSON by field, entry by entry", () => {
    expect(promptOutline(CAPTIONED)).toEqual([
      { depth: 0, key: "Summary", text: null },
      { depth: 1, key: "Caption", text: "A reference sheet of one woman in 4 panels." },
      { depth: 0, key: "Items", text: null },
      { depth: 1, key: "Label", text: "character" },
      { depth: 1, key: "Caption", text: "Panel 1: front view." },
      { depth: 1, key: "Bbox", text: "-577, -1365, 704, -746", numeric: true },
      { depth: 1, key: "Label", text: "object", divider: true },
      { depth: 1, key: "Caption", text: "Panel 1: her head." },
      { depth: 1, key: "Bbox", text: "-549, -1253, 84, -858", numeric: true },
    ]);
  });

  it("makes keys readable and drops empty fields", () => {
    expect(
      promptOutline(
        '  {"main_subject": "a fox", "cameraAngle": "low", "tags": ["fur", "snow"], "notes": "", "extra": {"x": null}}',
      ),
    ).toEqual([
      { depth: 0, key: "Main subject", text: "a fox" },
      { depth: 0, key: "Camera angle", text: "low" },
      { depth: 0, key: "Tags", text: "fur, snow", numeric: false },
    ]);
  });
});

describe("promptPreview", () => {
  it("is the prose of a JSON prompt, without keys or coordinates", () => {
    expect(promptPreview(CAPTIONED)).toBe(
      "A reference sheet of one woman in 4 panels. character Panel 1: front view. object Panel 1: her head.",
    );
  });

  it("returns plain text unchanged", () => {
    expect(promptPreview("a castle on a hill")).toBe("a castle on a hill");
    expect(promptPreview(null)).toBe("");
  });
});
