import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";

import { nameScale, namedTile } from "./modelTile";

describe("namedTile", () => {
  it("is null for a row the server named nothing for", () => {
    expect(namedTile({ mark: null }, "hsl(1 2% 3%)")).toBeNull();
    expect(namedTile({}, "hsl(1 2% 3%)")).toBeNull();
  });

  it("takes the hashed hue for a family with no maker colour", () => {
    const tile = namedTile({ mark: ["Noob"] }, "hsl(10 55% 30%)");
    expect(tile.background).toBe("hsl(10 55% 30%)");
    expect(tile.edge).toBe("hsl(10 55% 30%)");
  });

  it("puts the tab in the gradient's last stop", () => {
    const tile = namedTile(
      { mark: ["SD", "1.5"], mark_colors: ["#9d39ff", "#e80000"] },
      "x",
    );
    expect(tile.edge).toBe("#e80000");
    expect(tile.tab).toBeNull();
  });

  it("holds an icon tile to one line", () => {
    const tile = namedTile({ mark: ["Pony", "V7"], mark_icon: "pony" }, "x");
    expect(tile.lines).toEqual(["Pony"]);
    expect(tile.icon.band).toBe(false);
  });

  it("ignores an icon it does not ship", () => {
    expect(namedTile({ mark: ["X"], mark_icon: "nope" }, "x").icon).toBeNull();
  });
});

describe("nameScale", () => {
  it("caps one line higher than two, and an icon's band lower still", () => {
    const one = nameScale(["Z"]);
    const two = nameScale(["Z", "1"]);
    const band = nameScale(["Z"], true);
    expect(one).toBeGreaterThan(two);
    expect(two).toBeGreaterThan(band);
  });

  it("shrinks a long name to fit its line", () => {
    // The 8.25px floor is the CSS's, so the share itself is not clamped.
    expect(nameScale(["WWWWWWWWWW"])).toBeCloseTo(1 / (10 * 0.62), 5);
    expect(nameScale(["WWW"])).toBeLessThan(nameScale(["W"]));
  });
});

describe("the name's floor", () => {
  it("is a fixed 8.25px in the tile's CSS, whatever the tile's size", () => {
    // From the frontend root, as the suite runs: this file's own URL is not a
    // `file:` one under this module's environment.
    const css = readFileSync(
      `${process.cwd()}/src/components/widgets/ModelMark.vue`,
      "utf8",
    );
    const rule = css.match(/\.mmark-name \{[^}]*\}/)[0];
    expect(rule.replace(/\s+/g, " ")).toContain("font-size: max( 8.25px, calc(");
  });
});
