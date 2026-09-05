// Tag-suggestion decisions resource - /tag_suggestions.
//
// One suggestion is one card in a review queue. Decisions are written through
// per card as the user makes them, which is why aborting a review leaves the
// decisions already taken in place (see api/reviews.js).

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** Base path of the tag-suggestions collection. */
const SUGGESTIONS_URL = "/tag_suggestions";

/**
 * Apply a decision to one suggestion.
 * @param {number|string} id
 * @param {string} action - the decision verb the card offered (accept,
 *   dismiss, fix-twin, swap, ...). Rejects if the server does not know it.
 * @returns {Promise<Object>} the response body.
 */
export async function resolveTagSuggestion(id, action) {
  return unwrap(apiClient.post(`${SUGGESTIONS_URL}/${id}/${action}`));
}

/**
 * Skip one suggestion, leaving it undecided.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function skipTagSuggestion(id) {
  return unwrap(apiClient.post(`${SUGGESTIONS_URL}/${id}/skip`));
}

/**
 * Reopen one suggestion, moving it back to PENDING.
 *
 * A 404 here means the row is already gone rather than that something broke;
 * callers undoing a batch use that to stop tracking the entry. The rejection
 * is the raw Axios error, so `err.response.status` is available.
 *
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function reopenTagSuggestion(id) {
  return unwrap(apiClient.post(`${SUGGESTIONS_URL}/${id}/reopen`));
}

/**
 * Reopen every decided suggestion belonging to one review.
 *
 * Skipped items are not changes and are deliberately left alone by the
 * backend, so this undoes a review's edits without resurrecting its skips.
 *
 * @param {number|string} reviewId
 * @returns {Promise<Object>} the response body.
 */
export async function bulkReopenTagSuggestions(reviewId) {
  return unwrap(apiClient.post(`${SUGGESTIONS_URL}/bulk-reopen`, {
    review_id: reviewId,
  }));
}
