/**
 * Drift guard for the #1299 cleanup (docs/design/buttons.md, "Tooltips",
 * "Dialogs" and "Off-token values"). Each rule was a sweep across the whole app; this is
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

function sources(dir = SRC, pattern = /\.(vue|css)$/) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path, pattern);
    return pattern.test(entry.name) ? [path] : [];
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

  // `title` survives only where it restates its own element's clipped text
  // (buttons.md, "Tooltips"): everything else goes through `Tooltip` or the
  // `tooltip` prop. A reveal is two facts, both read here: the title's
  // expression is printed inside the element, and the element or something in
  // it clips (`text-overflow: ellipsis` or `line-clamp` on one of its classes).
  // Repeating text that never clips is not a reveal, it is a tooltip.
  it("keeps a native title to revealing its own element's clipped text", () => {
    const clipping = new Set(
      files.flatMap(({ text }) =>
        // Innermost blocks only, so a rule inside `@media` or `@supports` is
        // read by its own selector rather than the at-rule's prelude.
        [...text.matchAll(/([^{}]*)\{([^{}]*)\}/g)]
          .filter((rule) => /text-overflow:\s*ellipsis|line-clamp/.test(rule[2]))
          .flatMap((rule) => [...rule[1].matchAll(/\.([\w-]+)/g)].map((c) => c[1])),
      ),
    );
    const clips = (markup) =>
      [...markup.matchAll(/\sclass="([^"]*)"/g)]
        .flatMap((c) => c[1].split(/\s+/))
        .some((c) => clipping.has(c));
    const tag =
      /<([a-z][\w-]*)\b((?:[^>"']|"[^"]*"|'[^']*')*?)\s((?:v-bind)?:?)title=("([^"]*)"|'([^']*)')(?:[^>"']|"[^"]*"|'[^']*')*>/g;
    const squash = (s) => s.replace(/\s+/g, "");
    const found = files.flatMap(({ name, text }) =>
      [...text.matchAll(tag)]
        .filter((m) => {
          const raw = m[5] ?? m[6];
          // `cond ? undefined : x` and `cond ? x : undefined` reveal `x`.
          const shown = squash(raw)
            .replace(/^.*\?undefined:(.+)$/, "$1")
            .replace(/^.*\?(.+):undefined$/, "$1");
          // A self-closing or void tag has no text to restate.
          const after = text.slice(m.index + m[0].length);
          const end = m[0].endsWith("/>") ? -1 : after.indexOf(`</${m[1]}`);
          const body = end < 0 ? "" : after.slice(0, end);
          const printed = [...body.matchAll(/\{\{([\s\S]*?)\}\}/g)].map(
            (b) => squash(b[1]),
          );
          // The whole expression, not a longer name that starts with it.
          const esc = shown.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          const whole = new RegExp(`(?:^|[^\\w.])${esc}(?:$|[^\\w.])`);
          const restates = m[3]
            ? printed.some((p) => whole.test(p))
            : squash(body).includes(shown);
          return !(restates && clips(m[0] + body));
        })
        .map((m) => `${name}: ${m[3]}title=${m[5] ?? m[6]}`),
    );
    expect(found).toEqual([
      // The grid caption: the title is the full text when the shown text was
      // cut to fit, and empty otherwise; one function fills both.
      "components/views/ImageGrid.vue: :title=getThumbnailInfoTitle(img.id, info.key)",
    ]);
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

  // Padding, margin and gap equal to a `--space-*` step spell the step. A
  // negative offset (`-2px`) is an optical nudge, not a step, and is left.
  it("spells a spacing value that is a token as the token", () => {
    expect(
      hits(
        /(?:padding|margin|gap|row-gap|column-gap)[\w-]*:[^;}]*(?<![-\w.])(?:2|4|8|12|16|24|32|48|64)px\b/g,
      ),
    ).toEqual([]);
  });

  // The dialog body owns the 16px gutter and spaces its children with `gap`
  // (buttons.md, "Dialogs"). Nothing outside AppDialog restyles either.
  it("leaves the dialog gutter to AppDialog", () => {
    expect(
      hits(
        /\.app-dialog__(?:body|footer|header)\b[^{]*\{[^}]*(?:padding|gap)[^;}]*/g,
        ({ name }) => !name.endsWith("AppDialog.vue"),
      ),
    ).toEqual([]);
  });

  // A progress track rounds by its height (buttons.md, "Off-token values").
  it("gives a Vuetify progress bar the pill", () => {
    expect(
      hits(/<v-progress-linear\b(?:[^>"']|"[^"]*"|'[^']*')*>/g).filter(
        (hit) => !/\srounded="pill"/.test(hit),
      ),
    ).toEqual([]);
  });

  // Every AppButton variant, as quoted literals: plain, or inside a `:variant`
  // expression. A variant held in a variable or a `v-bind` object is not seen.
  function appButtonVariants(only) {
    return hits(/<AppButton\b(?:[^>"']|"[^"]*"|'[^']*')*>/g, only).flatMap(
      (hit) =>
        [...hit.matchAll(/\svariant="([^"]*)"|:variant="([^"]*)"/g)]
          .flatMap((m) => (m[1] ? [m[1]] : m[2].match(/'[^']*'/g) || []))
          .map((v) => `${hit.split(":")[0]}: ${v.replace(/'/g, "")}`),
    );
  }

  // An unknown variant has no rule in AppButton, so it would render as a bare
  // label with no fill.
  it("gives AppButton one of its variants", () => {
    const known = ["primary", "secondary", "outline", "danger", "ghost"];
    expect(
      appButtonVariants().filter((hit) => !known.includes(hit.split(": ")[1])),
    ).toEqual([]);
  });

  // Outline is for page surfaces; inside a dialog or a popover (`.tbm`) the
  // neutral is the filled `secondary` (buttons.md, #1673). Read per file.
  it("keeps the outline button out of dialogs and popovers", () => {
    expect(
      appButtonVariants(({ text }) =>
        /<AppDialog\b|class="tbm[\s"]/.test(text),
      ).filter((hit) => hit.endsWith(": outline")),
    ).toEqual([]);
  });

  // A layer picks a rung, not a number (visual-language.md §14), and not a
  // rung plus or minus one either. Above --z-drawer that includes "be an
  // overlay": `StackLayer` puts a hand-placed layer on Vuetify's stack instead
  // of out-bidding it. Any digit in the value is a number someone chose.
  //
  // One exception, named: the filter strip tucks its top 2px under the grid
  // toolbar so the toolbar's divider shows. An equal rung would need the strip
  // before the toolbar in the template, which moves it ahead in tab order.
  it("writes no z-index as a number", () => {
    expect(hits(/z-index:[^;}]*\d[^;}]*/g)).toEqual([
      "components/panels/FilterStrip.vue: z-index: calc(var(--z-sticky) - 1)",
    ]);
    // Inline styles set from a template or script: `zIndex`, a quoted
    // `"z-index"` key, or `setProperty("z-index", ...)`. Quoted only, so a
    // `.vue` file's own CSS (covered above) is not read twice.
    const scripts = sources(SRC, /\.(vue|js)$/)
      .filter((path) => !/\.test\.js$/.test(path))
      .flatMap((path) =>
        [
          ...withoutComments(readFileSync(path, "utf8")).matchAll(
            /(?:zIndex|["']z-index["']?)\s*[:=,][^,;}\n]*\d[^,;}\n]*/g,
          ),
        ].map((m) => `${relative(SRC, path)}: ${m[0].trim()}`),
      );
    expect(scripts).toEqual([]);
  });
});
