// How a review's known-answer controls scored, as short phrases for a receipt.
//
// A control is a picture that already carried a human verdict for the tag and
// was mixed, unmarked, into a review of a supplied list. `controls` is the
// server's `receipt.controls`; it is absent for a review that had none.

/**
 * @param {Object|null|undefined} controls - `{ pos_confirmed, pos_overturned,
 *   neg_confirmed, neg_overturned, skipped, pending }`.
 * @returns {string[]} one phrase per fact worth stating; empty when no control
 *   was answered or skipped.
 */
export function controlScoreParts(controls) {
  if (!controls) return [];
  const repeated = (confirmed = 0, overturned = 0, answer) => {
    const judged = confirmed + overturned;
    return judged
      ? `${confirmed} of ${judged} earlier “${answer}” repeated`
      : null;
  };
  return [
    repeated(controls.pos_confirmed, controls.pos_overturned, "yes"),
    repeated(controls.neg_confirmed, controls.neg_overturned, "no"),
    controls.skipped ? `${controls.skipped} skipped` : null,
  ].filter(Boolean);
}
