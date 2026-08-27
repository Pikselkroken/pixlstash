// Tags resource - the library vocabulary and per-picture tag edits.
//
// Tags live under two paths: `/tags` is the vocabulary (what tags exist and
// how often), while `/pictures/{id}/tags` is one picture's assignment. They
// are grouped here because callers reason about them together; the tag-review
// workflow is a separate resource (see api/tagSuggestions.js).

import { apiClient, operationBatchHeaders} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * List the tag vocabulary.
 * @param {Object} [options]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the tags (the response body).
 */
export async function listTags({ params } = {}) {
  return unwrap(apiClient.get(
    `/tags`,
    params ? { params } : undefined,
  ));
}

/**
 * Add a tag to one picture.
 * @param {number|string} pictureId
 * @param {string} tag
 * @returns {Promise<Object>} the response body, whose `tags` is the picture's
 *   full tag list after the edit.
 */
export async function addPictureTag(pictureId, tag) {
  return unwrap(apiClient.post(`/pictures/${pictureId}/tags`, {
    tag,
  }));
}

/**
 * Remove a tag from one picture.
 *
 * Addressed by TAG ID rather than by name: two tags can render the same and
 * the id is what identifies the row to drop.
 *
 * @param {number|string} pictureId
 * @param {number|string} tagId
 * @param {Object} [options]
 * @param {string} [options.batchId] - gesture batch id; every request of one
 *   user gesture shares it so the whole gesture is one undo step.
 * @returns {Promise<Object>} the response body.
 */
export async function removePictureTag(
  pictureId,
  tagId,
  { batchId } = {},
) {
  return unwrap(apiClient.delete(
    `/pictures/${pictureId}/tags/${tagId}`,
    operationBatchHeaders(batchId),
  ));
}

/**
 * Read the tags of many pictures in one request.
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Array<Object>>} one row per picture (the response body).
 */
export async function bulkFetchTags(pictureIds) {
  return unwrap(apiClient.post(`/pictures/tags/bulk_fetch`, {
    picture_ids: pictureIds,
  }));
}

/**
 * Remove a tag from EVERY picture that carries it, by name.
 *
 * The picture id only scopes which library the call runs against; the removal
 * itself is library-wide, unlike {@link removePictureTag}.
 *
 * @param {number|string} pictureId
 * @param {string} tag
 * @param {Object} [options]
 * @param {string} [options.batchId] - gesture batch id; the overlay's chip
 *   delete shares one with the reject that follows it, so both are undone by a
 *   single Ctrl+Z.
 * @returns {Promise<Object>} the response body.
 */
export async function removeTagEverywhere(
  pictureId,
  tag,
  { batchId } = {},
) {
  return unwrap(apiClient.post(
    `/pictures/${pictureId}/tags/remove_all`,
    { tag },
    operationBatchHeaders(batchId),
  ));
}

/**
 * Read the tagger's predictions for one picture.
 *
 * @param {number|string} pictureId
 * @param {Object} [options]
 * @param {string} [options.status] - filter to one status, e.g. `"REJECTED"`.
 * @param {boolean} [options.includeMeta=true] - include the acceptance
 *   threshold, which the UI needs to draw the near-miss band.
 * @returns {Promise<Object|Array<Object>>} the response body: either a bare
 *   array or a `{ tag_predictions, meta }` envelope, depending on server
 *   version.
 */
export async function listTagPredictions(
  pictureId,
  { status, includeMeta = true } = {},
) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (includeMeta) params.set("include_meta", "1");
  const query = params.toString();
  return unwrap(apiClient.get(
    query
      ? `/pictures/${pictureId}/tag_predictions?${query}`
      : `/pictures/${pictureId}/tag_predictions`,
  ));
}

/**
 * Accept a predicted tag, applying it to the picture.
 * @param {number|string} pictureId
 * @param {string} tag
 * @param {Object} [options]
 * @param {string} [options.batchId] - gesture batch id; a confirm-on-all fans
 *   out over N pictures and shares one, so it is one history step.
 * @returns {Promise<Object>} the response body.
 */
export async function confirmTagPrediction(
  pictureId,
  tag,
  { batchId } = {},
) {
  return unwrap(apiClient.post(
    `/pictures/${pictureId}/tag_predictions/${encodeURIComponent(tag)}/confirm`,
    undefined,
    operationBatchHeaders(batchId),
  ));
}

/**
 * Reject a predicted tag. The server records a negative human label, which is
 * what stops the tag being re-suggested.
 * @param {number|string} pictureId
 * @param {string} tag
 * @param {Object} [options]
 * @param {string} [options.batchId] - gesture batch id, shared with the tag
 *   removal this reject makes durable.
 * @returns {Promise<Object>} the response body.
 */
export async function rejectTagPrediction(
  pictureId,
  tag,
  { batchId } = {},
) {
  return unwrap(apiClient.post(
    `/pictures/${pictureId}/tag_predictions/${encodeURIComponent(tag)}/reject`,
    undefined,
    operationBatchHeaders(batchId),
  ));
}
