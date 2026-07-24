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
