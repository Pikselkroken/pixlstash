import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  chipText,
  colourKey,
  fillAlpha,
  GROUNDS,
  PALETTE,
} from "./tagColour.js";
import { composite, contrastRatio, readThemeColors } from "./contrastAudit.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOKENS = join(HERE, "..", "styles", "design-tokens.css");
const css = readFileSync(TOKENS, "utf8");

/** The `--tag-*` declarations inside one CSS block, as `{key: [r,g,b]}`. */
function tagTokens(block) {
  const out = {};
  for (const m of block.matchAll(/--tag-([a-z]+):\s*(\d+),\s*(\d+),\s*(\d+);/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return out;
}
const blockAt = (selector) => {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`no ${selector} block`);
  return css.slice(start, css.indexOf("\n}", start));
};
const CSS_LIGHT = tagTokens(blockAt(":root"));
const CSS_DARK = tagTokens(blockAt(".v-theme--pixlStashDark"));

const THEMES = readThemeColors();
// Every surface a chip can sit on: the rail's own (surface, sidebar) and the
// lightbox's dark pane (dark-surface), in each theme.
const GROUND_NAMES = ["surface", "sidebar", "dark-surface"];
const hex = (h) => ({
  r: parseInt(h.slice(1, 3), 16),
  g: parseInt(h.slice(3, 5), 16),
  b: parseInt(h.slice(5, 7), 16),
});
const rgb = ([r, g, b]) => ({ r, g, b });

describe("colourKey: the owner's examples", () => {
  it.each([
    ["brown hair", "brown"],
    ["blonde hair", "yellow"],
    ["cyan", "cyan"],
    ["green eyes", "green"],
    ["red", "red"],
  ])("%s -> %s", (name, key) => {
    expect(colourKey(name)).toBe(key);
  });
});

describe("colourKey: matching", () => {
  it("the first colour word wins", () => {
    expect(colourKey("red shorts with black bottoms")).toBe("red");
    expect(colourKey("black and white")).toBe("black");
  });

  it("a hyphenated or capitalised colour still matches as a word", () => {
    expect(colourKey("Blue-Eyed")).toBe("blue");
    expect(colourKey("GREEN eyes")).toBe("green");
  });

  it("light blue is one colour, however it is spelt", () => {
    expect(colourKey("light blue")).toBe("lightblue");
    expect(colourKey("light-blue jacket")).toBe("lightblue");
    expect(colourKey("LIGHT BLUE")).toBe("lightblue");
    expect(colourKey("lightblue")).toBe("lightblue");
  });

  it("a dark or light qualifier leaves the base colour", () => {
    expect(colourKey("dark blue")).toBe("blue");
  });

  it("synonyms map to their palette key", () => {
    expect(colourKey("gray")).toBe("grey");
    expect(colourKey("golden hour")).toBe("yellow");
    expect(colourKey("gold")).toBe("yellow");
    expect(colourKey("lavender")).toBe("violet");
    expect(colourKey("turquoise")).toBe("aqua");
    expect(colourKey("blond")).toBe("yellow");
  });

  it("a colour inside a longer word never matches", () => {
    expect(colourKey("redhead")).toBeNull();
    expect(colourKey("bored")).toBeNull();
    expect(colourKey("greenhouse")).toBeNull();
    expect(colourKey("blueprint")).toBeNull();
    expect(colourKey("whiteboard")).toBeNull();
  });

  it("names with no colour give null", () => {
    expect(colourKey("film grain")).toBeNull();
    expect(colourKey("")).toBeNull();
    expect(colourKey(null)).toBeNull();
    expect(colourKey(undefined)).toBeNull();
  });
});

describe("colourKey: multi-colour names", () => {
  it.each([
    "multicolored dress",
    "multicolour shirt",
    "two-tone shoes",
    "two tone shoes",
    "rainbow",
    "gradient sky",
    "multi-coloured coat",
    "red and multicolored",
  ])("%s gives no colour", (name) => {
    expect(colourKey(name)).toBeNull();
  });
});

describe("the palette mirror", () => {
  it("has a light and a dark value for every key, in both the mirror and the CSS", () => {
    const keys = Object.keys(PALETTE).sort();
    expect(Object.keys(CSS_LIGHT).sort()).toEqual(keys);
    expect(Object.keys(CSS_DARK).sort()).toEqual(keys);
  });

  it("matches design-tokens.css in both themes", () => {
    for (const key of Object.keys(PALETTE)) {
      expect(PALETTE[key].light, `light ${key}`).toEqual(CSS_LIGHT[key]);
      expect(PALETTE[key].dark, `dark ${key}`).toEqual(CSS_DARK[key]);
    }
  });

  it("keeps the anomaly red well away from the brick error hue", () => {
    // error #b0392b in both themes. Euclidean RGB distance, not a ratio.
    const [r, g, b] = PALETTE.red.light;
    const distance = Math.hypot(r - 176, g - 57, b - 43);
    expect(distance).toBeGreaterThan(80);
    const [dr, dg, db] = PALETTE.red.dark;
    expect(Math.hypot(dr - 201, dg - 120, db - 111)).toBeGreaterThan(60);
  });

  it("keeps the grounds in step with main.js's surfaces", () => {
    expect(GROUNDS.light).toEqual(Object.values(hex(THEMES.light.surface)));
    expect(GROUNDS.dark).toEqual(Object.values(hex(THEMES.dark.surface)));
  });
});

describe("fillAlpha", () => {
  it("runs the same curve as RailTags' CSS", () => {
    expect(fillAlpha(1, true)).toBeCloseTo(0.52);
    expect(fillAlpha(0, true)).toBeCloseTo(0.08);
    expect(fillAlpha(1, false)).toBeCloseTo(0.24);
    expect(fillAlpha(0, false)).toBeCloseTo(0.04);
  });
});

describe("chipText: the ink for a fill", () => {
  it("picks white for a dark blend and black for a light one", () => {
    // Blue at full confidence: dark over a black ground, light over the rail.
    expect(chipText("blue", 1, true, [0, 0, 0], false).ink).toEqual([255, 255, 255]);
    expect(chipText("blue", 1, true, GROUNDS.light, false).ink).toEqual([0, 0, 0]);
  });

  it("returns only pure black or pure white", () => {
    for (const key of Object.keys(PALETTE)) {
      const { ink } = chipText(key, 0.5, false, GROUNDS.dark, true);
      expect([[0, 0, 0], [255, 255, 255]]).toContainEqual(ink);
    }
  });
});

describe("contrast sweep: tint 0..1, both themes, every ground", () => {
  // A chip's text is ink on the fill blended over the ground. The sweep
  // measures that with contrastAudit.js (an independent composite and
  // contrast), at every tint, for both the confidence and the coverage curve.
  const STEPS = 100;
  const failures = [];

  for (const theme of ["light", "dark"]) {
    for (const groundName of GROUND_NAMES) {
      const ground = hex(THEMES[theme][groundName]);
      const dark = theme === "dark";
      for (const key of Object.keys(PALETTE)) {
        it(`${key} clears 4:1 at every tint on ${groundName} (${theme})`, () => {
          let low = Infinity;
          for (let i = 0; i <= STEPS; i += 1) {
            const tint = i / STEPS;
            for (const single of [true, false]) {
              const { ink } = chipText(key, tint, single, Object.values(ground), dark);
              const blended = composite(
                rgb(PALETTE[key][dark ? "dark" : "light"]),
                ground,
                fillAlpha(tint, single),
              );
              const ratio = contrastRatio(rgb(ink), blended);
              low = Math.min(low, ratio);
              if (ratio < 4) failures.push(`${key} ${theme} ${groundName} t=${tint}`);
            }
          }
          expect(low, `${theme}/${groundName}`).toBeGreaterThanOrEqual(4);
        });
      }
    }
  }

  it("has no failing sample anywhere in the sweep", () => {
    expect(failures).toEqual([]);
  });
});
