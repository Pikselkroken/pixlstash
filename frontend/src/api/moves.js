// Moves made outside PixlStash - the reconciliation queue (v1.11 Phase 5).
//
// One GET, classified live on every call - there is no cache behind it, so
// "look again" is just another GET, same as /insights. Apply and dismiss both
// take review_id values straight from that GET's response.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Read the reconciliation queue, bucketed into the three outcomes the release
 * plan names.
 *
 * @returns {Promise<Object>} `{ unambiguous, ambiguous, off_layout }`, each a
 *   list of `{ review_id, picture_id, old_path, new_path, removals,
 *   additions, current }`. `current` is only populated for the ambiguous
 *   bucket - the picture's own names for the facet a removal is ambiguous
 *   about, e.g. why leaving one project's folder does not say which project
 *   the owner left.
 */
export async function getPendingMoves() {
  return unwrap(apiClient.get("/moves/pending"));
}

/**
 * Apply the given pending moves and clear them from the queue.
 *
 * Pass every currently-unambiguous `review_id` for "Apply all N", or a single
 * ambiguous one to resolve it ("Only <project> now"). Reconciliation is
 * recomputed fresh on the backend, never trusted from an earlier GET.
 *
 * @param {Array<number>} reviewIds
 * @returns {Promise<Object>} `{ applied_picture_ids }`.
 */
export async function applyMoves(reviewIds) {
  return unwrap(apiClient.post("/moves/apply", { review_ids: reviewIds }));
}

/**
 * Drop the given pending moves without changing any assignment.
 *
 * "Keep both" on one ambiguous row, or "Leave everything as it was" on the
 * whole strip. The files stay exactly where the owner put them either way.
 *
 * @param {Array<number>} reviewIds
 * @returns {Promise<Object>} `{ dismissed_review_ids }`.
 */
export async function dismissMoves(reviewIds) {
  return unwrap(apiClient.post("/moves/dismiss", { review_ids: reviewIds }));
}
