// Tag-review sessions resource - /reviews.
//
// A review is one tag plus a frozen scope plus one scan's results. The session
// bookkeeping (create / list / refresh / archive / abort) lives here; the
// per-card decisions are a separate resource (see api/tagSuggestions.js).

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** Base path of the reviews collection. */
const REVIEWS_URL = "/reviews";

/**
 * List reviews with a given lifecycle status.
 * @param {string} status - e.g. `"OPEN"` or `"ARCHIVED"`.
 * @returns {Promise<Array<Object>>} the review list (the response body).
 */
export async function listReviews(status) {
  return unwrap(apiClient.get(REVIEWS_URL, { params: { status } }));
}

/**
 * Read one review, including its receipt statistics.
 * @param {number|string} id
 * @returns {Promise<Object>} the review (the response body).
 */
export async function getReview(id) {
  return unwrap(apiClient.get(`${REVIEWS_URL}/${id}`));
}

/**
 * Read a page of a review's suggestion queue.
 * @param {number|string} id
 * @param {Object} [params] - query params, typically `{ status, limit }`.
 * @returns {Promise<Object|Array<Object>>} the response body: either a paged
 *   `{ items }` envelope or a bare array, depending on server version.
 */
export async function listReviewSuggestions(id, params) {
  return unwrap(apiClient.get(`${REVIEWS_URL}/${id}/suggestions`, {
    params,
  }));
}

/**
 * Create a review for one tag within an optional scope.
 * @param {Object} body - `{ tag }` plus any of `project_id`, `set_id`,
 *   `character_id`, `include_reviewed`.
 * @returns {Promise<Object>} the created review (the response body).
 */
export async function createReview(body) {
  return unwrap(apiClient.post(REVIEWS_URL, body));
}

/**
 * Rescan for newly-found suspects and append them to the review.
 *
 * Refresh only appends: it never rebuilds the queue or resurrects decided
 * items.
 *
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function refreshReview(id) {
  return unwrap(apiClient.post(`${REVIEWS_URL}/${id}/refresh`));
}

/**
 * Close a review and keep its receipt.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function archiveReview(id) {
  return unwrap(apiClient.post(`${REVIEWS_URL}/${id}/archive`));
}

/**
 * Abort a review. Decisions already made stand: each card was written through
 * as it was decided, so aborting only stops the remaining queue.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function abortReview(id) {
  return unwrap(apiClient.post(`${REVIEWS_URL}/${id}/abort`));
}

/**
 * Delete one archived review's receipt. This drops the audit summary only; it
 * never reverses a tag change.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteReview(id) {
  return unwrap(apiClient.delete(`${REVIEWS_URL}/${id}`));
}

/**
 * Bulk-delete every review with the given status.
 *
 * `status` is required by the backend, which is what keeps a "clear archived"
 * action from ever reaching an open review.
 *
 * @param {string} status - e.g. `"ARCHIVED"`.
 * @returns {Promise<Object>} the response body.
 */
export async function deleteReviewsByStatus(status) {
  return unwrap(apiClient.delete(REVIEWS_URL, { params: { status } }));
}
