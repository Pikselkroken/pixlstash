// The four facets a folder can be mapped onto, plus "just a folder" - the
// owner's own choice, never something a Phase 2 signal proposes (see
// integration_architecture.md §20). Shared between the mapping tree and the
// preview screen so the icon/label/key for a kind is spelled once.
//
// Digits 1-4 and 0: DECISIONS.md's own reasoning - Project and Person both
// want P, so the mapping screen keys by position instead. The order is the
// MapTree artboard's.
//
// `color` is the theme colour a kind is drawn in, everywhere it appears: the
// artboard's four entity colours are exactly `tertiary`, `accent`, `secondary`
// and `primary` from main.js. Spend them as fills and edges with the matching
// `on-*` label; they are too dark to carry small text on their own.

export const FACET_KINDS = [
  { value: "project", label: "Project", plural: "Projects", icon: "mdi-briefcase-outline", digit: "1", color: "tertiary" },
  { value: "set", label: "Set", plural: "Sets", icon: "mdi-folder-multiple-image", digit: "2", color: "accent" },
  { value: "person", label: "Person", plural: "People", icon: "mdi-account-group", digit: "3", color: "secondary" },
  { value: "tag", label: "Tag", plural: "Tags", icon: "mdi-tag-outline", digit: "4", color: "primary" },
];

export const JUST_A_FOLDER_KIND = {
  value: "folder",
  label: "Just a folder",
  plural: "Just a folder",
  icon: "mdi-folder-remove-outline",
  digit: "0",
  color: "on-background",
};

export const ALL_KINDS = [...FACET_KINDS, JUST_A_FOLDER_KIND];

const BY_VALUE = new Map(ALL_KINDS.map((k) => [k.value, k]));
const BY_DIGIT = new Map(ALL_KINDS.map((k) => [k.digit, k]));

/** @param {string} value @returns {{value:string,label:string,icon:string,digit:string}|undefined} */
export function kindByValue(value) {
  return BY_VALUE.get(value);
}

/**
 * Inline style carrying a kind's colour as `--kind` (an r,g,b triplet) and its
 * label colour as `--on-kind`, for CSS to spend as `rgb(var(--kind))`.
 * @param {string|null|undefined} value @returns {Record<string,string>}
 */
export function kindStyle(value) {
  const kind = BY_VALUE.get(value);
  if (!kind) return {};
  const on = kind.color === "on-background" ? "background" : `on-${kind.color}`;
  return { "--kind": `var(--v-theme-${kind.color})`, "--on-kind": `var(--v-theme-${on})` };
}

/** @param {string} digit @returns {{value:string,label:string,icon:string,digit:string}|undefined} */
export function kindByDigit(digit) {
  return BY_DIGIT.get(digit);
}
