/**
 * Colour-named tags: which colour a tag name carries, and the text ink its
 * chip needs over that colour's fill.
 *
 * The palette is the `--tag-*` tokens in `styles/design-tokens.css`, mirrored
 * here as numbers because the ink is computed in JS. tagColour.test.js reads
 * the CSS and fails if the two drift apart.
 */

/** `[r, g, b]` per palette key, light theme and dark theme. */
export const PALETTE = {
  red: { light: [224, 0, 0], dark: [239, 68, 82] },
  orange: { light: [224, 112, 32], dark: [245, 147, 66] },
  yellow: { light: [232, 184, 0], dark: [240, 200, 40] },
  green: { light: [46, 139, 58], dark: [90, 190, 110] },
  lime: { light: [140, 198, 63], dark: [170, 220, 90] },
  aqua: { light: [95, 211, 208], dark: [120, 225, 220] },
  cyan: { light: [0, 181, 216], dark: [60, 205, 235] },
  teal: { light: [42, 140, 134], dark: [80, 178, 170] },
  blue: { light: [47, 95, 179], dark: [90, 140, 230] },
  lightblue: { light: [127, 184, 232], dark: [160, 205, 245] },
  navy: { light: [31, 47, 107], dark: [110, 130, 210] },
  purple: { light: [122, 63, 176], dark: [165, 110, 225] },
  violet: { light: [155, 107, 214], dark: [185, 140, 235] },
  pink: { light: [240, 143, 176], dark: [245, 165, 195] },
  magenta: { light: [194, 38, 143], dark: [225, 90, 190] },
  brown: { light: [122, 74, 34], dark: [170, 115, 70] },
  tan: { light: [196, 154, 108], dark: [215, 180, 140] },
  beige: { light: [227, 211, 176], dark: [240, 230, 205] },
  black: { light: [0, 0, 0], dark: [0, 0, 0] },
  white: { light: [255, 255, 255], dark: [255, 255, 255] },
  grey: { light: [138, 138, 138], dark: [150, 150, 150] },
  silver: { light: [192, 196, 200], dark: [200, 205, 210] },
};

/** Black and white get an on-surface edge: their fill vanishes on one ground. */
export const NEUTRAL_KEYS = new Set(["black", "white"]);

/** The surfaces a chip sits on when nothing opaque is found behind it. */
export const GROUNDS = {
  light: [240, 239, 236], // theme `surface` #f0efec
  dark: [35, 40, 47], // theme `surface` #23282f
};

/** Single-word colour names. Matched whole-word, case-insensitively. */
const WORDS = {
  red: "red",
  orange: "orange",
  yellow: "yellow",
  blonde: "yellow",
  blond: "yellow",
  gold: "yellow",
  golden: "yellow",
  green: "green",
  lime: "lime",
  aqua: "aqua",
  aquamarine: "aqua",
  turquoise: "aqua",
  cyan: "cyan",
  teal: "teal",
  blue: "blue",
  lightblue: "lightblue",
  navy: "navy",
  purple: "purple",
  violet: "violet",
  lavender: "violet",
  pink: "pink",
  magenta: "magenta",
  brown: "brown",
  tan: "tan",
  beige: "beige",
  black: "black",
  white: "white",
  grey: "grey",
  gray: "grey",
  silver: "silver",
};

/** Two-word colour names: "light blue" and "light-blue" both name lightblue. */
const PHRASES = {
  "light blue": "lightblue",
  "baby blue": "lightblue",
  "sky blue": "lightblue",
};

/** Words that make a tag multi-coloured: no single colour, so no fill. */
const MULTI_WORDS = new Set([
  "multi",
  "multicolored",
  "multicolor",
  "multicoloured",
  "multicolour",
  "rainbow",
  "gradient",
]);
const MULTI_PHRASES = ["two tone"];

/**
 * The palette key of the colour a tag name carries, or null.
 *
 * The name is split into words on anything that is not a letter, so "blue-eyed"
 * and "Blue Eyes" both give blue. The first colour word wins, so "red shorts
 * with black bottoms" is red. A multi-colour word anywhere gives null. A colour
 * inside a longer word never matches: "redhead", "bored", "greenhouse" give null.
 *
 * @param {string} name the tag name as stored.
 * @returns {string|null} a PALETTE key, or null.
 */
export function colourKey(name) {
  const words = String(name ?? "")
    .toLowerCase()
    .replace(/[^a-z]+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean);
  const joined = ` ${words.join(" ")} `;
  if (words.some((w) => MULTI_WORDS.has(w))) return null;
  if (MULTI_PHRASES.some((p) => joined.includes(` ${p} `))) return null;
  for (let i = 0; i < words.length; i += 1) {
    const pair = i + 1 < words.length ? `${words[i]} ${words[i + 1]}` : null;
    if (pair && PHRASES[pair]) return PHRASES[pair];
    if (WORDS[words[i]]) return WORDS[words[i]];
  }
  return null;
}

/**
 * The chip's fill alpha over its surface, the same curve RailTags' CSS draws:
 * confidence for one picture (`.rail-chip--conf`), coverage for several.
 *
 * @param {number} tint 0..1, the chip's `--tint`.
 * @param {boolean} single true for one picture (confidence), false for several.
 * @returns {number}
 */
export function fillAlpha(tint, single) {
  return single ? 0.08 + tint * 0.44 : 0.04 + tint * 0.2;
}

/** WCAG relative luminance of an `[r, g, b]` colour. */
function luminance([r, g, b]) {
  const [lr, lg, lb] = [r, g, b].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
}

/**
 * The text ink for a colour chip: pure black or pure white, whichever contrasts
 * more with the fill blended over the surface. The better of the two always
 * clears 4.5:1 on any ground, so no fixed ink is needed per colour.
 *
 * @param {string} key a PALETTE key.
 * @param {number} tint 0..1.
 * @param {boolean} single one picture (confidence) or several (coverage).
 * @param {number[]|null} ground the opaque surface behind the chip, `[r, g, b]`;
 *   null falls back to the theme's surface.
 * @param {boolean} dark the dark theme.
 * @returns {{ink: number[], ratio: number, blended: number[]}}
 */
export function chipText(key, tint, single, ground, dark) {
  const theme = dark ? "dark" : "light";
  const base = ground ?? GROUNDS[theme];
  const a = fillAlpha(tint, single);
  const fill = PALETTE[key][theme];
  const blended = fill.map((f, i) => f * a + base[i] * (1 - a));
  const L = luminance(blended);
  const toBlack = (L + 0.05) / 0.05;
  const toWhite = 1.05 / (L + 0.05);
  return toBlack >= toWhite
    ? { ink: [0, 0, 0], ratio: toBlack, blended }
    : { ink: [255, 255, 255], ratio: toWhite, blended };
}
