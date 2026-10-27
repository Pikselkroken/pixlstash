import { describe, expect, it } from "vitest";
import { bareDigit } from "./shortcutKeys";

const press = (code, mods = {}, key = "") => ({
  code,
  key,
  ctrlKey: false,
  shiftKey: false,
  altKey: false,
  metaKey: false,
  ...mods,
});

describe("star keys fire only on a bare digit", () => {
  it("reads the digit by position, whatever character the layout types", () => {
    // A French layout types "&" on the unshifted 1 key.
    expect(bareDigit(press("Digit1", {}, "&"))).toBe(1);
    expect(bareDigit(press("Numpad4", {}, "4"))).toBe(4);
  });

  it("ignores the numpad while Num Lock is off and it types arrows", () => {
    expect(bareDigit(press("Numpad4", {}, "ArrowLeft"))).toBeNull();
  });

  it("is silent while any modifier is held", () => {
    for (const mod of ["ctrlKey", "shiftKey", "altKey", "metaKey"]) {
      expect(bareDigit(press("Digit2", { [mod]: true }))).toBeNull();
    }
  });
});
