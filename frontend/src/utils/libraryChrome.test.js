import { describe, expect, it } from "vitest";

import { libraryDocumentTitle } from "./libraryChrome";

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
