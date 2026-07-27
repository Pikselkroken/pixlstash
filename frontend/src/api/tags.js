// Tags resource — the library vocabulary and per-picture tag edits.
//
// Tags live under two paths: `/tags` is the vocabulary (what tags exist and
// how often), while `/pictures/{id}/tags` is one picture's assignment. They
// are grouped here because callers reason about them together; the tag-review
// workflow is a separate resource (see api/tagSuggestions.js).

import { apiClient } from "../utils/apiClient";

/**
 * List the tag vocabulary.
 * @param {Object} [options]
 * @param {Object} [options.params] - optional query params.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} the tags (the response body).
 */
export async function listTags({ params, baseUrl = "" } = {}) {
  const res = await apiClient.get(
    `${baseUrl}/tags`,
    params ? { params } : undefined,
  );
  return res.data;
}

/**
 * Add a tag to one picture.
 * @param {number|string} pictureId
 * @param {string} tag
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `tags` is the picture's
 *   full tag list after the edit.
 */
export async function addPictureTag(pictureId, tag, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}/pictures/${pictureId}/tags`, {
    tag,
  });
  return res.data;
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
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function removePictureTag(
  pictureId,
  tagId,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.delete(
    `${baseUrl}/pictures/${pictureId}/tags/${tagId}`,
  );
  return res.data;
}

/**
 * Read the tags of many pictures in one request.
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} one row per picture (the response body).
 */
export async function bulkFetchTags(pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}/pictures/tags/bulk_fetch`, {
    picture_ids: pictureIds,
  });
  return res.data;
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
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function removeTagEverywhere(
  pictureId,
  tag,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/${pictureId}/tags/remove_all`,
    { tag },
  );
  return res.data;
}

/**
 * Read the tagger's predictions for one picture.
 *
 * @param {number|string} pictureId
 * @param {Object} [options]
 * @param {string} [options.status] - filter to one status, e.g. `"REJECTED"`.
 * @param {boolean} [options.includeMeta=true] - include the acceptance
 *   threshold, which the UI needs to draw the near-miss band.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object|Array<Object>>} the response body: either a bare
 *   array or a `{ tag_predictions, meta }` envelope, depending on server
 *   version.
 */
export async function listTagPredictions(
  pictureId,
  { status, includeMeta = true, baseUrl = "" } = {},
) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (includeMeta) params.set("include_meta", "1");
  const query = params.toString();
  const res = await apiClient.get(
    query
      ? `${baseUrl}/pictures/${pictureId}/tag_predictions?${query}`
      : `${baseUrl}/pictures/${pictureId}/tag_predictions`,
  );
  return res.data;
}

/**
 * Accept a predicted tag, applying it to the picture.
 * @param {number|string} pictureId
 * @param {string} tag
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function confirmTagPrediction(
  pictureId,
  tag,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/${pictureId}/tag_predictions/${encodeURIComponent(tag)}/confirm`,
  );
  return res.data;
}

/**
 * Reject a predicted tag. The server records a negative human label, which is
 * what stops the tag being re-suggested.
 * @param {number|string} pictureId
 * @param {string} tag
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function rejectTagPrediction(
  pictureId,
  tag,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(
    `${baseUrl}/pictures/${pictureId}/tag_predictions/${encodeURIComponent(tag)}/reject`,
  );
  return res.data;
}
