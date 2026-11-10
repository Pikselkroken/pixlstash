// An `on-<x>` token is only correct on a solid, full-opacity `<x>` fill.
//
// `on-primary` / `on-secondary` / `on-tertiary` / `on-accent` are measured
// against their own opaque fill. Put one over a translucent wash and the canvas
// shows through: warm white on `rgba(primary, 0.7)` over the light sidebar is
// 2.9:1. It always looks right in the dark theme, where the wash darkens the
// fill, which is why it kept coming back (visual-language.md §4,
// design-system-handoff.md §9.2).
//
// So the invariant is asserted over the whole tree: no rule may pair an
// action-fill `on-*` colour with a translucent or mixed background. It reads one
// rule at a time, so an `on-*` in a child rule under a see-through parent is
// still a review catch, not a test one.

import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

// vitest runs from the frontend/ package root.
const SRC = join(process.cwd(), "src");

function styleFiles(dir) {
  const found = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) found.push(...styleFiles(path));
    else if (/\.(vue|css)$/.test(name)) found.push(path);
  }
  return found;
}

const ON_FILL = /v-theme-on-(accent|primary|secondary|tertiary)\b/;
const SEE_THROUGH = /background(-color)?\s*:\s*(rgba|color-mix)\(/;

/** Declaration blocks with no nested braces, `selector { ... }`. */
function mispairedRules(css) {
  return [...css.matchAll(/([^{}]*)\{([^{}]*)\}/g)]
    .filter(([, , body]) => ON_FILL.test(body) && SEE_THROUGH.test(body))
    .map(([, selector]) => selector.trim());
}

describe("on-<x> pairing", () => {
  it("never puts an action-fill on-* colour over a see-through background", () => {
    const offenders = styleFiles(SRC).flatMap((file) =>
      mispairedRules(readFileSync(file, "utf8")).map(
        (sel) => `${relative(SRC, file)}: ${sel}`,
      ),
    );
    expect(offenders).toEqual([]);
  });

  it("catches the shape it guards", () => {
    // Guards the guard: the regexes must still see the original defect.
    expect(
      mispairedRules(`.x {
  background: rgba(var(--v-theme-secondary), 0.75);
  color: rgb(var(--v-theme-on-secondary));
}`),
    ).toEqual([".x"]);
    expect(
      mispairedRules(`.y {
  background: rgb(var(--v-theme-secondary));
  color: rgb(var(--v-theme-on-secondary));
}`),
    ).toEqual([]);
  });
});

// `on-surface-variant` is the one `on-*` neither PixlStash theme authors, so it
// is Vuetify's stock grey: `#424242` in dark and `#EEEEEE` in light, the ink
// for a solid stock `surface-variant` fill. Used as muted text on an ordinary
// surface it nearly vanishes in both themes (the shelf's run members, and every
// muted line of five dialogs). Muted text is `--opacity-text-secondary`.
const ON_SURFACE_VARIANT = /v-theme-on-surface-variant\b/;
const SOLID_SURFACE_VARIANT =
  /background(-color)?\s*:\s*rgb\(var\(--v-theme-surface-variant\)\)/;

/** Rules that use `on-surface-variant` without painting its own fill. */
function strayOnSurfaceVariant(css) {
  return [...css.matchAll(/([^{}]*)\{([^{}]*)\}/g)]
    .filter(
      ([, , body]) =>
        ON_SURFACE_VARIANT.test(body) && !SOLID_SURFACE_VARIANT.test(body),
    )
    .map(([, selector]) => selector.trim());
}

describe("on-surface-variant pairing", () => {
  it("only inks a rule that paints the solid surface-variant fill", () => {
    const offenders = styleFiles(SRC).flatMap((file) =>
      strayOnSurfaceVariant(readFileSync(file, "utf8")).map(
        (sel) => `${relative(SRC, file)}: ${sel}`,
      ),
    );
    expect(offenders).toEqual([]);
  });

  it("catches the shape it guards", () => {
    expect(
      strayOnSurfaceVariant(`.x {
  padding-left: var(--space-7);
  color: rgb(var(--v-theme-on-surface-variant));
}`),
    ).toEqual([".x"]);
    expect(
      strayOnSurfaceVariant(`.y {
  background: rgb(var(--v-theme-surface-variant));
  color: rgb(var(--v-theme-on-surface-variant));
}`),
    ).toEqual([]);
  });
});
