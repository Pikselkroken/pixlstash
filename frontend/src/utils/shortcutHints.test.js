import { describe, it, expect } from "vitest";
import {
  formatKeyHint,
  isApplePlatform,
  redoKeyHint,
  selectAllKeyHint,
  undoKeyHint,
} from "./shortcutHints";

const MAC_UA = { userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" };
const WIN_UA = { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" };
const LINUX_UA = { userAgent: "Mozilla/5.0 (X11; Linux x86_64)" };

describe("isApplePlatform", () => {
  it("prefers userAgentData over the user-agent string", () => {
    // A Chromium browser reporting macOS through the modern API while its
    // frozen UA string still says Windows must resolve to macOS.
    expect(
      isApplePlatform({ userAgentData: { platform: "macOS" }, ...WIN_UA }),
    ).toBe(true);
    expect(
      isApplePlatform({ userAgentData: { platform: "Windows" }, ...MAC_UA }),
    ).toBe(false);
  });

  it("falls back to the user-agent string", () => {
    expect(isApplePlatform(MAC_UA)).toBe(true);
    expect(isApplePlatform({ userAgent: "iPhone" })).toBe(true);
    expect(isApplePlatform(WIN_UA)).toBe(false);
    expect(isApplePlatform(LINUX_UA)).toBe(false);
  });

  it("is false when there is no navigator at all", () => {
    expect(isApplePlatform(null)).toBe(false);
  });
});

describe("undoKeyHint / redoKeyHint", () => {
  it("shows Ctrl keycaps off Apple platforms", () => {
    expect(undoKeyHint(WIN_UA)).toEqual(["Ctrl", "Z"]);
    expect(redoKeyHint(WIN_UA)).toEqual(["Ctrl", "Y"]);
  });

  it("shows the command keycaps on Apple platforms", () => {
    expect(undoKeyHint(MAC_UA)).toEqual(["⌘", "Z"]);
    // macOS has no Ctrl+Y convention; the system redo is shift-command-Z.
    expect(redoKeyHint(MAC_UA)).toEqual(["⇧", "⌘", "Z"]);
  });
});

describe("selectAllKeyHint", () => {
  it("follows the platform, like the undo hint beside it", () => {
    expect(selectAllKeyHint(WIN_UA)).toEqual(["Ctrl", "A"]);
    expect(selectAllKeyHint(MAC_UA)).toEqual(["⌘", "A"]);
  });
});

describe("formatKeyHint", () => {
  it("joins keycaps for a title attribute", () => {
    expect(formatKeyHint(["Ctrl", "Z"])).toBe("Ctrl+Z");
    expect(formatKeyHint(["⇧", "⌘", "Z"])).toBe("⇧+⌘+Z");
  });

  it("tolerates a missing hint", () => {
    expect(formatKeyHint(null)).toBe("");
  });
});
