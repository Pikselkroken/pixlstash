// The four facets a folder can be mapped onto, plus "just a folder" - the
// owner's own choice, never something a Phase 2 signal proposes (see
// integration_architecture.md §20). Shared between the mapping tree and the
// preview screen so the icon/label/key for a kind is spelled once.
//
// Digits 1-4 and 0: DECISIONS.md's own reasoning - Project and Person both
// want P, so the mapping screen keys by position instead.

export const FACET_KINDS = [
  { value: "project", label: "Project", plural: "Projects", icon: "mdi-briefcase-outline", digit: "1" },
  { value: "person", label: "Person", plural: "People", icon: "mdi-account-group", digit: "2" },
  { value: "set", label: "Set", plural: "Sets", icon: "mdi-folder-multiple-image", digit: "3" },
  { value: "tag", label: "Tag", plural: "Tags", icon: "mdi-tag-outline", digit: "4" },
];

export const JUST_A_FOLDER_KIND = {
  value: "folder",
  label: "Just a folder",
  plural: "Just a folder",
  icon: "mdi-folder-remove-outline",
  digit: "0",
};

export const ALL_KINDS = [...FACET_KINDS, JUST_A_FOLDER_KIND];

const BY_VALUE = new Map(ALL_KINDS.map((k) => [k.value, k]));
const BY_DIGIT = new Map(ALL_KINDS.map((k) => [k.digit, k]));

/** @param {string} value @returns {{value:string,label:string,icon:string,digit:string}|undefined} */
export function kindByValue(value) {
  return BY_VALUE.get(value);
}

/** @param {string} digit @returns {{value:string,label:string,icon:string,digit:string}|undefined} */
export function kindByDigit(digit) {
  return BY_DIGIT.get(digit);
}
