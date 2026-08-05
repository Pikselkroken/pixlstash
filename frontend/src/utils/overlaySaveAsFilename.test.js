import { describe, expect, it } from "vitest";
import {
  normalizeOverlaySaveAsFilename,
  overlaySaveAsStem,
} from "./overlaySaveAsFilename.js";

describe("overlay Save As filename", () => {
  it("prefills the editable stem without the fixed original extension", () => {
    expect(overlaySaveAsStem("holiday.JPG", ".jpg")).toBe("holiday");
    expect(overlaySaveAsStem("archive.photo.jpg", "jpg")).toBe(
      "archive.photo",
    );
  });

  it("trims the stem and appends the fixed original extension", () => {
    expect(normalizeOverlaySaveAsFilename("  renamed  ", ".JPG")).toEqual({
      filename: "renamed.jpg",
      error: "",
    });
  });

  it("does not allow typed text to replace the original extension", () => {
    expect(normalizeOverlaySaveAsFilename("renamed.png", "jpg").filename).toBe(
      "renamed.png.jpg",
    );
  });

  it.each(["", "../photo", "folder\\photo", "bad:name", "bad\nname", "bad."])(
    "rejects an empty or unsafe name: %j",
    (name) => {
      expect(normalizeOverlaySaveAsFilename(name, "jpg").error).not.toBe("");
    },
  );
});
