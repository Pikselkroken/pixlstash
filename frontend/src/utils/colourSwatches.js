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

// sRGB "#rrggbb" to CIELAB (D65), the same maths as the backend's
// `_lab_of_hex`, so a picked colour lands in the swatch its pixels would.
function labOfHex(hex) {
  const lin = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const [r, g, b] = lin;
  const xyz = [
    (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047,
    0.2126729 * r + 0.7151522 * g + 0.072175 * b,
    (0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883,
  ];
  const [fx, fy, fz] = xyz.map((t) =>
    t > 0.008856 ? Math.cbrt(t) : (903.3 * t + 16) / 116,
  );
  return [116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)];
}

/** The swatch a "#rrggbb" colour counts toward: the nearest centre in CIELAB. */
export function nearestSwatch(hex) {
  const lab = labOfHex(hex);
  let best = null;
  let bestDistance = Infinity;
  for (const swatch of COLOUR_SWATCHES) {
    const centre = labOfHex(swatch.fill);
    const d = centre.reduce((sum, v, i) => sum + (v - lab[i]) ** 2, 0);
    if (d < bestDistance) {
      bestDistance = d;
      best = swatch;
    }
  }
  return best;
}
