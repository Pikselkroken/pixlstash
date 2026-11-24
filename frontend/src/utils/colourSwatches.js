/**
 * Search by colour (#2151): the twelve swatches a picture's colours are
 * counted into, and the one "contains at least" amount every picked swatch
 * shares.
 *
 * The order is the server's: `colour_shares` on a picture is twelve
 * percentages in exactly this order, and `colour=<id>:<percent>` filters on
 * one of them. The fills are the buckets' own centres (the backend counts each
 * thumbnail pixel toward the nearest one in CIELAB), so they are content
 * colours, not UI colours: they appear only as swatches and in colour bars,
 * never on a control's chrome. `dark` marks the light fills a check or a
 * hairline has to be dark on to stay readable.
 */
export const COLOUR_SWATCHES = Object.freeze([
  { id: "red", label: "Red", fill: "#b8312c" },
  { id: "orange", label: "Orange", fill: "#dd7a2c" },
  { id: "yellow", label: "Yellow", fill: "#dcc23e", dark: true },
  { id: "green", label: "Green", fill: "#5b8a3c" },
  { id: "teal", label: "Teal", fill: "#3b9690" },
  { id: "blue", label: "Blue", fill: "#3c66ae" },
  { id: "purple", label: "Purple", fill: "#764aa0" },
  { id: "pink", label: "Pink", fill: "#dc8fac", dark: true },
  { id: "brown", label: "Brown", fill: "#86593a" },
  { id: "white", label: "White", fill: "#efede6", dark: true },
  { id: "grey", label: "Grey", fill: "#8b8b88", dark: true },
  { id: "black", label: "Black", fill: "#1b1b1b" },
]);

/** The slider's range and where it starts, in percent of the picture. */
export const COLOUR_AMOUNT_MIN = 2;
export const COLOUR_AMOUNT_MAX = 60;
export const COLOUR_AMOUNT_DEFAULT = 10;

// A picture's colour bar lists the shares this large and up.
const LEGEND_MIN_SHARE = 2;

export function colourSwatch(id) {
  return COLOUR_SWATCHES.find((s) => s.id === id) ?? null;
}

/** One `colour` filter value: `"blue:10"`. */
export function colourEntry(id, amount) {
  return `${id}:${amount}`;
}

/** The chip's and the token's words: "blue ≥10%". */
export function colourChipValue(id, amount) {
  const label = colourSwatch(id)?.label ?? id;
  return `${label.toLowerCase()} ≥${amount}%`;
}

/**
 * A picture's `colour_shares` as its bar's segments: every swatch it holds,
 * largest first, with `legend` on the ones big enough to name.
 *
 * @param {Array<number>|null} shares twelve percentages in swatch order, or
 *   null while the picture has not been counted.
 * @returns {Array<{id, label, fill, dark, share, legend}>} empty for none.
 */
export function colourShareSegments(shares) {
  if (!Array.isArray(shares) || shares.length !== COLOUR_SWATCHES.length) {
    return [];
  }
  return COLOUR_SWATCHES.map((swatch, i) => ({
    ...swatch,
    share: Number(shares[i]) || 0,
  }))
    .filter((s) => s.share > 0)
    .sort((a, b) => b.share - a.share)
    .map((s) => ({ ...s, legend: s.share >= LEGEND_MIN_SHARE }));
}
