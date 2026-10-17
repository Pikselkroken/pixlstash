// The named tile a workflow card draws a model of a known base model with
// (#1951): the short name people write for it (SDXL over 1.0, ZIT, Qwen over
// 2511), a tab for its role or a bolt for a few-step model, on its maker's
// colour. The server works all of it out (`known_base_models.model_tile`) and
// sends it on the row as `mark`, `mark_tab`, `mark_colors` and `mark_icon`;
// this only lays it out. The shelf keeps the generated initials, because each
// of its rows prints the name beside the mark.

import chromaIcon from "../assets/model-families/chroma.webp";
import illustriousIcon from "../assets/model-families/illustrious.webp";
import ponyIcon from "../assets/model-families/pony.webp";

/**
 * The owner's family icons. `band` puts a solid band behind the name: the
 * tiger's eye is busy enough to swallow it, the horse and the star sit low on
 * a plain field that needs none.
 */
const FAMILY_ICONS = {
  chroma: { url: chromaIcon, band: true },
  pony: { url: ponyIcon, band: false },
  illustrious: { url: illustriousIcon, band: false },
};

/** What the server's tab sends for a few-step model; drawn as a bolt icon. */
export const FEW_STEP_TAB = "⚡";

// The name's size, as a share of the tile's line (the tile less its padding):
// it grows until its widest line fills the line, up to 0.64 of it for one
// line (today's initials) and 0.475 for two, so they stay clear of the tab,
// and 0.375 in an icon's band, which holds one line over the picture. The
// shares were measured on a 24px line (15.4px, 11.4px, 9px) and scale with
// the tile. The 8.25px floor is fixed, not a share, and lives in
// `.mmark-name`'s CSS; every known name fits the card's line at it, which
// `frontend/e2e/specs/model-tile-names.spec.js` measures.
const ONE_LINE = 15.4 / 24;
const TWO_LINES = 11.4 / 24;
const BAND = 9 / 24;
// The tile's tracking, `letter-spacing: -0.02em`, which canvas does not apply.
const TRACKING = -0.02;
// What a character measures where nothing can be measured (jsdom), in em.
const FALLBACK_EM = 0.62;

let context;
const widths = new Map();

/**
 * The width of *text* at 1px, semibold, tracked, in `--font-ui`: the face
 * `.mmark-name` sets. A system font stack, so nothing loads late and a width
 * once measured stays true.
 */
function widthEm(text) {
  if (!widths.has(text)) {
    if (context === undefined) {
      context =
        typeof OffscreenCanvas === "function"
          ? new OffscreenCanvas(1, 1).getContext("2d")
          : null;
    }
    const chars = [...text].length;
    let em = FALLBACK_EM * chars;
    if (context) {
      const face = getComputedStyle(document.documentElement)
        .getPropertyValue("--font-ui")
        .trim();
      context.font = `600 100px ${face || "system-ui"}`;
      em = context.measureText(text).width / 100 + TRACKING * chars;
    }
    widths.set(text, em);
  }
  return widths.get(text);
}

/**
 * The name's font size as a share of the tile's line: the cap for its shape,
 * shrunk until its widest line fits. The CSS holds it at its 8.25px floor.
 */
export function nameScale(lines, band = false) {
  const cap = band ? BAND : lines.length > 1 ? TWO_LINES : ONE_LINE;
  const widest = Math.max(...lines.map(widthEm));
  return Math.min(cap, 1 / widest);
}

/**
 * How a row's named tile draws, or null for a row the server named nothing
 * for (no known base model), which keeps the generated mark.
 *
 * @param {Object} row - a shelf row or a card slot as `ModelMark` reads it.
 * @param {string} fallbackColor - the generated mark's hashed hue, for a
 *   family with no maker colour.
 * @returns {{lines: string[], tab: ?{bolt: boolean, text: string},
 *   background: string, edge: string, icon: ?{url: string, band: boolean},
 *   scale: number} | null}
 *   `edge` is the colour the tab takes: the gradient's last stop.
 */
export function namedTile(row, fallbackColor) {
  if (!row?.mark?.length) return null;
  const stops = row.mark_colors?.length ? row.mark_colors : [fallbackColor];
  const icon = FAMILY_ICONS[row.mark_icon] ?? null;
  // A family icon holds one line: a second covered the eye.
  const lines = icon ? row.mark.slice(0, 1) : row.mark;
  const tab = row.mark_tab
    ? row.mark_tab.startsWith(FEW_STEP_TAB)
      ? { bolt: true, text: row.mark_tab.slice(FEW_STEP_TAB.length) }
      : { bolt: false, text: row.mark_tab }
    : null;
  return {
    lines,
    tab,
    background:
      stops.length > 1
        ? `linear-gradient(135deg, ${stops.join(", ")})`
        : stops[0],
    edge: stops[stops.length - 1],
    icon,
    scale: nameScale(lines, Boolean(icon)),
  };
}
