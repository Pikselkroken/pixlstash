/**
 * Drift guard for the #1299 cleanup (docs/design/buttons.md, "Tooltips" and
 * "Off-token values"). Each rule was a sweep across the whole app; this is
 * what stops the count climbing back one call site at a time.
 *
 * Reads the CSS and templates of `.vue` and `.css` files (not styles set from
 * JS), as source text with comments stripped, so a token value named in a
 * note is never counted as a use.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const SRC = dirname(dirname(fileURLToPath(import.meta.url)));

function sources(dir = SRC) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.(vue|css)$/.test(entry.name) ? [path] : [];
  });
}

/**
 * A comment, or a lone comment delimiter left behind by removing one.
 *
 * The delimiters are in the pattern on purpose: `<!-- <!-- x --> -->` has its
 * inner comment removed first, and what is left of the outer one is a bare
 * `-->` that no comment rule matches.
 */
const COMMENT = /\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->|<!--|-->|\/\*|\*\//g;

/**
 * *text* with its comments removed, repeated until a pass changes nothing.
 *
 * One pass is not enough in either direction: removing a comment can join
 * what surrounded it into a new one, and removing a delimiter can spell a new
 * delimiter (`<!<!---->` leaves `<!--`). Looping to a fixed point is what
 * makes the result free of both, so a token value named in a note is never
 * counted as a use.
 */
function withoutComments(text) {
  let stripped = text;
  let previous;
  do {
    previous = stripped;
    stripped = stripped.replace(COMMENT, "");
  } while (stripped !== previous);
  return stripped;
}

const files = sources()
  .filter((path) => !path.endsWith(join("styles", "design-tokens.css")))
  .map((path) => ({
    name: relative(SRC, path),
    text: withoutComments(readFileSync(path, "utf8")),
  }));

/** Every match of `re` across the sources, as `file: match`. */
function hits(re, only = () => true) {
  return files
    .filter(only)
    .flatMap(({ name, text }) =>
      [...text.matchAll(re)].map((m) => `${name}: ${m[0].trim()}`),
    );
}

/**
 * Raw z-indexes above --z-drawer that are known and not yet fixed. Each one is
 * a teleported or fixed element that has to clear a modal, which means it has
 * to become an overlay rather than carry a bigger number; that is a
 * restructuring per site, not a substitution. This list may only shrink.
 */
const Z_ABOVE_DRAWER_DEBT = [
  "components/editors/CharacterEditor.vue: z-index: 9999",
  "components/panels/SideBar.css: z-index: 1200",
  "components/panels/TbTagPanel.vue: z-index: 9999",
  "components/widgets/AddToEntityControl.vue: z-index: 2500",
  "components/widgets/BaseModelInput.vue: z-index: 9999",
];

describe("design drift", () => {
  it("has one tooltip surface", () => {
    expect(
      hits(/<v-tooltip\b/g, ({ name }) => !name.endsWith("Tooltip.vue")),
    ).toEqual([]);
  });

  // A tip lands on the neighbouring control; if it takes the pointer, it takes
  // that control's click (#1380). Vuetify's own rule is `pointer-events: none`.
  it("leaves the tooltip surface click-through", () => {
    expect(
      hits(
        /(?:app-tooltip|v-tooltip)[^{]*\{[^}]*pointer-events:\s*(?!none\b)\w[^;]*/g,
      ),
    ).toEqual([]);
  });

  it("gives the two button dialects a tooltip, never a native title", () => {
    expect(
      hits(/<App(?:Bar)?Button\b(?:[^>"']|"[^"]*"|'[^']*')*?\s:?title=/g),
    ).toEqual([]);
  });

  // Vue's mergeProps keeps whichever `ref` comes last, so an activator's slot
  // props and a template ref on one element silently drop one of the two:
  // the tip or menu opens unanchored, or the app's ref stays null.
  it("binds an activator's props and a template ref through withRef", () => {
    const tag = /<[A-Za-z][\w-]*\b(?:[^>"']|"[^"]*"|'[^']*')*>/g;
    const found = files.flatMap(({ name, text }) =>
      [...text.matchAll(tag)]
        .map((m) => m[0])
        .filter(
          (t) =>
            /\sv-bind="\w*[Pp]rops"/.test(t) && /\s:?ref=/.test(t),
        )
        .map((t) => `${name}: ${t.replace(/\s+/g, " ").slice(0, 80)}`),
    );
    expect(found).toEqual([]);
  });

  it("has no sub-pixel type", () => {
    expect(
      hits(/font-size:\s*(?:\d+\.\d+px|\d*\.\d+rem)/g).filter(
        // Whole-pixel rem values are on the ramp's own terms.
        (hit) => !/:\s*0\.(?:6875|75|8125|875)rem|1\.(?:125|375|75)rem/.test(hit),
      ),
    ).toEqual([]);
  });

  it("spells a font size or radius that is a token as the token", () => {
    expect(hits(/font-size:\s*(?:11|12|13|14|16|18|22|28)px\b/g)).toEqual([]);
    expect(hits(/border-radius:[^;}]*\b(?:4|8|12|999|9999)px\b/g)).toEqual([]);
  });

  it("writes no z-index above --z-drawer beyond the known debt", () => {
    const found = hits(/z-index:\s*\d{4,}/g).filter(
      (hit) => Number(hit.match(/\d+$/)[0]) > 1000,
    );
    expect(found.sort()).toEqual([...Z_ABOVE_DRAWER_DEBT].sort());
  });
});
