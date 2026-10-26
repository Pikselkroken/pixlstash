import { describe, expect, it } from "vitest";

import { driveFullness, libraryDocumentTitle } from "./libraryChrome";

describe("libraryDocumentTitle", () => {
  it("names the active owner library in browser and Electron titles", () => {
    expect(libraryDocumentTitle("Family Photos")).toBe(
      "PixlStash - Family Photos",
    );
  });

  it("never discloses a library name in read-only/share mode", () => {
    expect(libraryDocumentTitle("Private client work", true)).toBe("PixlStash");
  });
});

describe("driveFullness", () => {
  const drive = (used) => ({ total_bytes: 1000, free_bytes: 1000 - used * 10 });

  it("turns amber at 75 % and red at 90 %, never earlier", () => {
    expect(driveFullness(drive(74)).state).toBe("success");
    expect(driveFullness(drive(75)).state).toBe("warning");
    expect(driveFullness(drive(89)).state).toBe("warning");
    expect(driveFullness(drive(90)).state).toBe("error");
  });

  it("says the state in words, so the meter is not colour alone", () => {
    expect(driveFullness(drive(93))).toMatchObject({
      used: 93,
      words: "almost full",
    });
  });

  it("draws no meter for a drive that was not measured", () => {
    // An empty bar would read as a full drive.
    expect(driveFullness({ total_bytes: null, free_bytes: null })).toBeNull();
    expect(driveFullness({})).toBeNull();
    expect(driveFullness({ total_bytes: 0, free_bytes: 0 })).toBeNull();
  });
});
