/**
 * Contrast guard for `--opacity-text-secondary` (#836).
 *
 * The bug this exists to stop: secondary text was written as a raw `opacity: 0.6`
 * in 37 places and measured 4.01:1 on the light sidebar and 4.48:1 on the dark
 * one - both under the 4.5:1 AA floor for body text (WCAG 1.4.3). The value now
 * lives in one token, so the only way to reintroduce the failure is to lower
 * that token, and this test is what refuses it.
 *
 * Both inputs are read from the files that actually ship - the token from
 * design-tokens.css, the theme colours from main.js - so the assertion tracks a
 * palette change too, not just the opacity.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");

/** Relative luminance, WCAG 2.x §relativeluminancedef. */
function luminance([r, g, b]) {
  const [lr, lg, lb] = [r, g, b].map((v) =>
    v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}

const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);

/** Contrast of `fg` at `alpha` composited over the opaque `bg`. */
function contrast(fg, bg, alpha) {
  const over = hex(fg).map((f, i) => f * alpha + hex(bg)[i] * (1 - alpha));
  const [hi, lo] = [luminance(over), luminance(hex(bg))].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Pull `"<name>": "#rrggbb"` (or unquoted key) out of a Vuetify theme block.
 * The lookbehind matters: without it, `surface` also matches the tail of
 * `"dark-surface"` and the test silently compares the wrong pair.
 */
function themeColor(block, name) {
  const m = block.match(
    new RegExp(
      `(?<![-\\w])["']?${name}["']?\\s*:\\s*["'](#[0-9a-fA-F]{6})["']`,
    ),
  );
  if (!m) throw new Error(`theme colour ${name} not found`);
  return m[1];
}

const tokens = read("./design-tokens.css");
const mainJs = read("../main.js");

const opacity = Number(
  tokens.match(/--opacity-text-secondary:\s*([\d.]+);/)?.[1],
);

/**
 * Slice one Vuetify theme out of main.js by its declaration, so each half
 * yields its own palette (the two themes reuse the same key names).
 *
 * Keyed on the theme's NAME, never on one of its colours: keying on a hex
 * would break the moment the palette it is meant to police changes - the
 * Camp B migration edits exactly those values - and the guard would then fail
 * for the wrong reason. Both failure modes throw and say what moved, so a
 * rename can never degrade into a silently wrong comparison.
 */
function themeBlock(name) {
  const start = mainJs.indexOf(`const ${name} = {`);
  if (start === -1) {
    throw new Error(
      `theme '${name}' not found in main.js - renamed or moved? This guard ` +
        `must be repointed at the live themes, not deleted.`,
    );
  }
  const end = mainJs.indexOf("\n};", start);
  if (end === -1) throw new Error(`theme '${name}' block is unterminated`);
  return mainJs.slice(start, end);
}

const themes = {
  light: themeBlock("pixlStashLight"),
  dark: themeBlock("pixlStashDark"),
};

describe("--opacity-text-secondary", () => {
  it("is a usable opacity", () => {
    expect(opacity).toBeGreaterThan(0);
    expect(opacity).toBeLessThanOrEqual(1);
  });

  // The sidebar is the worst canvas in both themes and the one the original
  // 0.6 measurement missed, so it is listed explicitly rather than assumed.
  for (const [theme, block] of Object.entries(themes)) {
    for (const [fgName, bgName] of [
      ["on-background", "background"],
      ["on-surface", "surface"],
      ["sidebar-text", "sidebar"],
    ]) {
      it(`clears AA 4.5:1 for ${fgName} on ${bgName} (${theme})`, () => {
        const ratio = contrast(
          themeColor(block, fgName),
          themeColor(block, bgName),
          opacity,
        );
        expect(ratio).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  it("rejects the 0.6 that #836 measured as failing", () => {
    // Guards the guard: if this ever passes, the maths or the palette drifted
    // and the assertions above have stopped meaning anything.
    const worst = contrast(
      themeColor(themes.light, "sidebar-text"),
      themeColor(themes.light, "sidebar"),
      0.6,
    );
    expect(worst).toBeLessThan(4.5);
  });
});
