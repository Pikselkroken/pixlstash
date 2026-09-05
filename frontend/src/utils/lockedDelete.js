// Copy for "some of what you tried to delete is frozen by a locked set".
//
// A picture in a locked picture set is read-only everywhere, and the bulk
// `DELETE /pictures` endpoint honours that by SKIPPING it and reporting the ids
// back as `skipped_locked` - the request still returns 200. Throwing that array
// away is what produced the reported bug: the user deleted a locked selection,
// got a success, and watched nothing happen with no explanation.
//
// The message construction lives here, as pure functions, for two reasons:
//   * the same wording is needed on two paths - the pre-flight block (we refuse
//     to send a request that we know would delete nothing) and the post-response
//     partial outcome - and they must not drift apart;
//   * counts drive singular/plural in three places, which is exactly the kind of
//     thing that silently regresses without tests.
//
// Every message names the LEVER. "These are locked" is a dead end; "unlock the
// set and try again" is something the user can act on.

/** Outcome kinds, in the order of how much the user needs to care. */
export const LOCKED_DELETE_PARTIAL = "partial";
export const LOCKED_DELETE_ALL_LOCKED = "all-locked";

/**
 * How to change the outcome. Shortened from `buildLockReason` in
 * `useLockedSetsStore` so the two never contradict each other on the mechanism.
 */
export const LOCKED_DELETE_HINT =
  "To delete them, unlock the set first: right-click it in the sidebar and choose Unlock, or untick Locked in Edit set.";

function plural(count, singular, pluralForm) {
  return count === 1 ? singular : pluralForm;
}

/**
 * Describe how many of the selected pictures were kept because a locked set
 * freezes them.
 *
 * @param {Object} counts
 * @param {number} counts.lockedCount - pictures skipped because they are locked.
 * @param {number} counts.deletedCount - pictures actually moved to the scrapheap.
 * @returns {{kind: string, title: string, body: string, hint: string}|null}
 *   `null` when nothing was skipped - i.e. there is nothing to tell the user.
 */
export function buildLockedDeleteMessage({
  lockedCount = 0,
  deletedCount = 0,
} = {}) {
  const locked = Number.isFinite(lockedCount) ? Math.max(0, lockedCount) : 0;
  const deleted = Number.isFinite(deletedCount) ? Math.max(0, deletedCount) : 0;

  // Nothing was frozen: the delete did exactly what the user asked. Stay quiet.
  if (locked === 0) return null;

  const lockedNoun = plural(locked, "picture", "pictures");
  const lockedIsAre = plural(locked, "is", "are");
  const lockedSet = plural(locked, "a locked set", "locked sets");

  if (deleted === 0) {
    return {
      kind: LOCKED_DELETE_ALL_LOCKED,
      title: "Nothing was deleted",
      body:
        `All ${locked} selected ${lockedNoun} ${lockedIsAre} in ${lockedSet}, ` +
        `so ${plural(locked, "it was", "they were")} kept.`,
      hint: LOCKED_DELETE_HINT,
    };
  }

  return {
    kind: LOCKED_DELETE_PARTIAL,
    title: "Some pictures were kept",
    body:
      `${deleted} ${plural(deleted, "picture", "pictures")} moved to the scrapheap; ` +
      `${locked} ${lockedIsAre} in ${lockedSet} and ${plural(locked, "was", "were")} kept.`,
    hint: LOCKED_DELETE_HINT,
  };
}

/**
 * How many pictures each delete-forever action actually destroys.
 *
 * `POST /pictures/scrapheap/delete-preview` returns three **disjoint** buckets
 * that sum to `total_count`, classified LOCKED FIRST:
 *   - `locked_count`      - frozen by a lock (whether or not also protected)
 *   - `protected_count`   - a reference-folder original AND not locked
 *   - `unprotected_count` - neither
 *
 * Because they partition the total, the destroyable figures are plain sums of
 * the server's own buckets - never arithmetic on `total_count`. That matters:
 * deriving a count as `total - locked` would silently drift the moment the
 * server changed how it classifies a picture, which is exactly the class of bug
 * that produced the original silent no-op.
 *
 * @param {Object} preview - the delete-preview counts.
 * @param {number} [preview.protectedCount]
 * @param {number} [preview.unprotectedCount]
 * @param {number} [preview.lockedCount] - missing reads as 0 (older servers).
 * @returns {{deleteAll: number, deleteUnprotectedOnly: number, kept: number}}
 */
export function deleteForeverDestroyCounts({
  protectedCount = 0,
  unprotectedCount = 0,
  lockedCount = 0,
} = {}) {
  const safe = (n) => (Number.isFinite(n) ? Math.max(0, n) : 0);
  const prot = safe(protectedCount);
  const unprot = safe(unprotectedCount);
  return {
    deleteAll: prot + unprot,
    deleteUnprotectedOnly: unprot,
    kept: safe(lockedCount),
  };
}

/**
 * Copy for the delete-forever confirm when the target set contains pictures a
 * locked set freezes. Neither "delete all" nor "delete unprotected only"
 * destroys them, so the dialog must say so up front rather than let the user
 * discover it from a count that does not add up.
 *
 * @param {number} lockedCount - `locked_count` from the delete preview.
 * @returns {string} `""` when nothing is locked.
 */
export function buildLockedPurgeNote(lockedCount = 0) {
  const locked = Number.isFinite(lockedCount) ? Math.max(0, lockedCount) : 0;
  if (locked === 0) return "";
  const noun = plural(locked, "picture", "pictures");
  const isAre = plural(locked, "is", "are");
  const set = plural(locked, "a locked set", "locked sets");
  const itThem = plural(locked, "it", "them");
  return (
    `${locked} ${noun} ${isAre} in ${set} and will be kept - ` +
    `neither action below deletes ${itThem}.`
  );
}
