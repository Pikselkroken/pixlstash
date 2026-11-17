// Smart picture sets (#2148): a set whose members come from a saved filter.
//
// The backend stores the grid's own `GET /pictures` query and keeps the set's
// members up to date; these helpers only word it for a person.

/**
 * What a new smart set is filled by, in the words the screen already uses:
 * the view it was saved from (a person, a set) and each active filter chip.
 *
 * @param {Object} options
 * @param {string|null} [options.view] - the view's name, null in All pictures.
 * @param {Array<{kind: string, value: string}>} [options.chips] - filterChips().
 * @returns {string} e.g. "Anna · Score 4+ · Tag portrait"; "" when empty.
 */
export function smartSetSummary({ view = null, chips = [] } = {}) {
  return [view, ...chips.map((c) => `${c.kind} ${c.value}`.trim())]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The line a smart set's row and editor show: what fills it and whether the
 * owner has edited it by hand. Null for an ordinary set.
 *
 * @param {Object} set - a set row from `GET /picture_sets`.
 * @returns {string|null}
 */
export function smartSetDescription(set) {
  const smart = set?.smart;
  if (!smart) return null;
  const from = smart.summary
    ? `Smart set: fills itself with ${smart.summary}.`
    : "Smart set: fills itself from a saved filter.";
  if (set.locked) return `${from} Locked, so it keeps the pictures it has.`;
  return smart.custom
    ? `${from} Custom: you added or took out pictures by hand.`
    : `${from} Automatic.`;
}
