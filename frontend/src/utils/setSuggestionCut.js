/**
 * The cut applied to "Suggest more pictures for <set>" (#1489), the twin of
 * `faceSuggestionCut.js`.
 *
 * Two independent knobs over the SAME cached ranked list, so neither costs a
 * round trip:
 *
 * - **match strength**: the cosine floor to the set's centroid (`likeness`).
 * - **tags in common**: how many of the set's signature tags (those on at least
 *   half its members) a candidate has to carry. Zero switches it off.
 *
 * Lives outside the components because the grid rebuild (`useGridFetch`) and the
 * count in the action pill (`ImageGrid`) both apply it, and a count that
 * disagrees with the grid under it is the bug this file exists to prevent.
 */

/**
 * The strength cut seated when the set reports no cohesion (a one-picture set
 * has nothing to compare its member against). A CLIP cosine this high reads as
 * "clearly the same kind of picture" without demanding near-duplicates.
 */
export const SET_SUGGEST_FALLBACK_THRESHOLD = 0.75;

/**
 * How many signature tags the set has, read off the ranked list (every match
 * carries the same `tags_total`). Zero when unknown or when the set has none.
 *
 * @param {Array<{tags_total?: number}>} matches
 * @returns {number}
 */
export function signatureTagCount(matches) {
  if (!Array.isArray(matches)) return 0;
  for (const match of matches) {
    if (Number.isFinite(match?.tags_total)) return match.tags_total;
  }
  return 0;
}

/**
 * The set's cohesion - the median similarity of its own members to their
 * centroid - read off the ranked list, or null when it is empty.
 *
 * @param {Array<{cohesion?: number}>} matches
 * @returns {number|null}
 */
export function setCohesion(matches) {
  if (!Array.isArray(matches)) return null;
  for (const match of matches) {
    if (Number.isFinite(match?.cohesion)) return match.cohesion;
  }
  return null;
}

/**
 * Apply both knobs to a ranked list.
 *
 * @param {Array<Object>} matches - the cached ranked list, best first.
 * @param {number} cut - match-strength floor, 0-1.
 * @param {number} [minTags=0] - signature tags a match must carry.
 * @returns {Array<Object>} the surviving matches, order preserved.
 */
export function cutSetSuggestions(matches, cut, minTags = 0) {
  if (!Array.isArray(matches)) return [];
  const floor = Number.isFinite(cut) ? cut : 0;
  const needed = Math.max(0, Math.round(Number(minTags) || 0));
  return matches.filter(
    (match) =>
      (match?.likeness ?? 0) >= floor && (match?.tags_matched ?? 0) >= needed,
  );
}
