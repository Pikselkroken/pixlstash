// The first frame's theme. Everything here exists so that a person who chose
// light never watches the app open dark and change its mind, and someone new
// never watches the reverse.

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { readRememberedTheme, rememberTheme } from "./themeMemory";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the remembered theme", () => {
  it("has nothing to say on a first run, so the caller keeps its default", () => {
    expect(readRememberedTheme()).toBe(null);
  });

  it("remembers what was used and hands it back", () => {
    rememberTheme("light");
    expect(readRememberedTheme()).toBe("light");
    rememberTheme("dark");
    expect(readRememberedTheme()).toBe("dark");
  });

  it("ignores a value that is not a theme, rather than painting one", () => {
    rememberTheme("chartreuse");
    expect(readRememberedTheme()).toBe(null);
    localStorage.setItem("pixlstash.themeMode", "chartreuse");
    expect(readRememberedTheme()).toBe(null);
  });

  it("survives a localStorage that refuses to answer", () => {
    // A private window or a locked-down profile throws on access. That is a
    // missing memory, not a broken app: the caller falls back to its default.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(readRememberedTheme()).toBe(null);
  });

  it("survives a localStorage that refuses to be written to", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(() => rememberTheme("dark")).not.toThrow();
  });
});
