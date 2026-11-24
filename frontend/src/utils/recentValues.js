/**
 * Typed values remembered newest first: the sheet step's Top, Bottoms, Shoes
 * and Background, and Photo look's typed entries, so both behave the same.
 *
 * One `localStorage` document, the sheet step's remembered choices, whose
 * `recent` maps a field to its list. A value is recorded when it is drawn or
 * run with, never per keystroke.
 */
export const CHOICES_KEY = "pixlstash:characterSheetChoices";
export const RECENT_KEPT = 20;

export function readChoices() {
  try {
    return JSON.parse(localStorage.getItem(CHOICES_KEY) || "{}") || {};
  } catch (e) {
    console.warn("Couldn't read the remembered sheet choices", e);
    return {};
  }
}

/** Change the remembered choices with *update(all)* and store them. */
export function writeChoices(update) {
  try {
    const all = readChoices();
    update(all);
    localStorage.setItem(CHOICES_KEY, JSON.stringify(all));
  } catch (e) {
    console.warn("Couldn't remember the sheet choices", e);
  }
}

/** *value* first in *list*, a case-folded repeat dropped, RECENT_KEPT kept. */
export function withRecent(list, value) {
  // Case folded, as the menu matches: "A red coat" replaces "a red coat".
  const same = (v) => v.toLowerCase() === value.toLowerCase();
  return [value, ...list.filter((v) => !same(v))].slice(0, RECENT_KEPT);
}
