/**
 * The cut applied to "Suggest more pictures of <person>" (#636).
 *
 * Two knobs, both applied to the SAME cached ranked list so neither costs a
 * round trip:
 *
 * - **match strength**: the cosine floor a reference face has to clear.
 * - **reference agreement**: how many of the person's reference faces have to
 *   clear it. The backend combines a character query with `combine=max`, so a
 *   candidate that resembles one reference perfectly outranks one that resembles
 *   all of them well; `likeness` alone cannot tell those apart, and on a person
 *   whose references span years and angles that is exactly the distinction
 *   between "same person" and "same haircut".
 *
 * The two share one number deliberately: agreement counts the references that
 * clear the *strength* floor, rather than owning a second floor of its own. Two
 * independent percentages describing the same comparison is a thing to be
 * reasoned about; "how good, and how many that good" is a thing to be read.
 *
 * Lives outside the components because the grid rebuild (`useGridFetch`) and the
 * count in the action pill (`ImageGrid`) both apply it, and a count that
 * disagrees with the grid under it is the bug this file exists to prevent.
 */

/**
 * How many of the query's reference faces this match satisfies at `cut`.
 *
 * Falls back to the combined `likeness` when the backend did not send the
 * per-reference row (an older server, or `include_reference_scores` off): a
 * match at or above the cut then counts as one agreeing reference, which is
 * exactly what `combine=max` already told us and keeps `minRefs = 1` correct.
 *
 * @param {{likeness?: number, reference_likeness?: number[]}} match
 * @param {number} cut - the strength floor, 0-1.
 * @returns {number}
 */
export function agreeingReferenceCount(match, cut) {
  const refs = match?.reference_likeness;
  if (!Array.isArray(refs) || refs.length === 0) {
    return (match?.likeness ?? 0) >= cut ? 1 : 0;
  }
  let n = 0;
  for (const value of refs) {
    if ((value ?? 0) >= cut) n += 1;
  }
  return n;
}

/**
 * How many reference faces the query carried, or 0 when it is not knowable.
 *
 * Read off the matches rather than fetched separately: every row carries the
 * same-length row, so the ranked list already answers it. Zero means the
 * agreement slider has nothing to offer and should not be shown at all.
 *
 * @param {Array<{reference_likeness?: number[]}>} matches
 * @returns {number}
 */
export function referenceFaceCount(matches) {
  if (!Array.isArray(matches)) return 0;
  for (const match of matches) {
    const refs = match?.reference_likeness;
    if (Array.isArray(refs) && refs.length) return refs.length;
  }
  return 0;
}

/**
 * Apply both knobs to a ranked list.
 *
 * @param {Array<Object>} matches - the cached ranked list, best first.
 * @param {number} cut - match-strength floor, 0-1.
 * @param {number} [minRefs=1] - reference faces that must clear it.
 * @returns {Array<Object>} the surviving matches, order preserved.
 */
export function cutFaceSuggestions(matches, cut, minRefs = 1) {
  if (!Array.isArray(matches)) return [];
  const floor = Number.isFinite(cut) ? cut : 0;
  const needed = Math.max(1, Math.round(Number(minRefs) || 1));
  return matches.filter(
    (match) => agreeingReferenceCount(match, floor) >= needed,
  );
}
