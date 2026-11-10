import { describe, expect, it } from "vitest";
import {
  normalizeLightboxSaveAsFilename,
  lightboxSaveAsStem,
} from "./lightboxSaveAsFilename.js";

describe("lightbox Save As filename", () => {
  it("prefills the editable stem without the fixed original extension", () => {
    expect(lightboxSaveAsStem("holiday.JPG", ".jpg")).toBe("holiday");
    expect(lightboxSaveAsStem("archive.photo.jpg", "jpg")).toBe(
      "archive.photo",
    );
  });

  it("trims the stem and appends the fixed original extension", () => {
    expect(normalizeLightboxSaveAsFilename("  renamed  ", ".JPG")).toEqual({
      filename: "renamed.jpg",
      error: "",
    });
  });

  it("does not allow typed text to replace the original extension", () => {
    expect(normalizeLightboxSaveAsFilename("renamed.png", "jpg").filename).toBe(
      "renamed.png.jpg",
    );
  });

  it.each(["", "../photo", "folder\\photo", "bad:name", "bad\nname", "bad."])(
    "rejects an empty or unsafe name: %j",
    (name) => {
      expect(normalizeLightboxSaveAsFilename(name, "jpg").error).not.toBe("");
    },
  );
});
