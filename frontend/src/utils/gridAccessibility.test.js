import { describe, expect, it } from "vitest";
import { pictureGridLabel, pictureGridTabIndex } from "./gridAccessibility.js";

describe("pictureGridLabel", () => {
  it("prefers the filename and adds useful visible metadata", () => {
    expect(
      pictureGridLabel(
        { id: 7, idx: 3, file_path: "/library/雪 portrait.png", width: 1024, height: 768 },
        { video: false },
      ),
    ).toBe("Picture 雪 portrait.png, 1024 by 768 pixels");
  });

  it("falls back to the stable grid position without exposing a raw path", () => {
    expect(pictureGridLabel({ id: 7, idx: 3 })).toBe("Picture item 4");
    expect(pictureGridLabel({ id: 7 }, { video: true })).toBe("Video ID 7");
  });
});

describe("pictureGridTabIndex", () => {
  const image = { id: 11, idx: 4 };

  it("uses the cursor when present and otherwise the rendered fallback", () => {
    expect(pictureGridTabIndex(image, { cursorIndex: 4, fallbackIndex: 2 })).toBe(0);
    expect(pictureGridTabIndex(image, { cursorIndex: null, fallbackIndex: 4 })).toBe(0);
    expect(pictureGridTabIndex(image, { cursorIndex: 2, fallbackIndex: 4 })).toBe(-1);
  });

  it("keeps placeholders and undo ghosts out of the tab order", () => {
    expect(pictureGridTabIndex({ id: null, idx: 4 }, { fallbackIndex: 4 })).toBe(-1);
    expect(pictureGridTabIndex(image, { fallbackIndex: 4, ghosted: true })).toBe(-1);
  });
});
