import { describe, expect, it } from "vitest";
import { normalizeOverlaySaveAsFilename } from "./overlaySaveAsFilename.js";

describe("overlay Save As filename", () => {
  it("trims the name and appends the original extension when omitted", () => {
    expect(normalizeOverlaySaveAsFilename("  renamed  ", ".JPG")).toEqual({
      filename: "renamed.jpg",
      error: "",
    });
  });

  it("preserves an explicit extension", () => {
    expect(normalizeOverlaySaveAsFilename("renamed.jpeg", "jpg").filename).toBe(
      "renamed.jpeg",
    );
  });

  it.each(["", "../photo", "folder\\photo", "bad:name", "bad\nname", "bad."])(
    "rejects an empty or unsafe name: %j",
    (name) => {
      expect(normalizeOverlaySaveAsFilename(name, "jpg").error).not.toBe("");
    },
  );
});
