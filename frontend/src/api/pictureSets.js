// Picture sets resource - /picture_sets.
//
// Membership is per-picture: a set is joined or left one picture at a time
// (`/picture_sets/{id}/members/{pictureId}`), so bulk actions are the caller's
// loop over these calls, not a bulk endpoint.

import { API_BASE_URL, apiClient, appendShareToken } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Build a picture-sets route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/picture_sets`.
 * @returns {string}
 */
function setsUrl(path = "") {
  return `/picture_sets${path}`;
}

/**
 * The URL of a set's thumbnail, for an `<img src>`.
 *
 * The list row carries a `thumbnail_url` too, and the sidebar uses that because
 * it also wants the row's `top_picture_ids` as a cache-versioning key. This is
 * the plain form for a caller that has only an id - the model shelf's
 * assignment ring, which resolves from an attachment rather than a row.
 *
 * The route is cookie-authenticated, so a share token has to ride in the query
 * - an `<img>` sends no header.
 *
 * @param {number|string} id
 * @returns {string}
 */
export function pictureSetThumbnailUrl(id) {
  // An <img src> never reaches the apiClient interceptor, so the API base has
  // to be spelled out here rather than left for axios to prepend.
  return appendShareToken(
    `${API_BASE_URL}${setsUrl(`/${encodeURIComponent(id)}/thumbnail`)}`,
  );
}

/**
 * List picture sets.
 *
 * The list includes the internal reference sets; callers that show user-facing
 * sets filter them out themselves.
 *
 * @param {Object} [options]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the picture-set list (the response body).
 */
export async function listPictureSets({ params } = {}) {
  return unwrap(apiClient.get(setsUrl(""), params ? { params } : undefined));
}

/**
 * Read one picture set.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body, which nests the set itself
 *   under `set` alongside its membership details.
 */
export async function getPictureSet(id) {
  return unwrap(apiClient.get(setsUrl(`/${id}`)));
}

/**
 * Create a picture set.
 * @param {Object} body - the set's fields (name, icon, colour, ...).
 * @returns {Promise<Object>} the created set (the response body).
 */
export async function createPictureSet(body) {
  return unwrap(apiClient.post(setsUrl(""), body));
}

/**
 * Patch a picture set.
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @returns {Promise<Object>} the updated set (the response body).
 */
export async function patchPictureSet(id, body) {
  return unwrap(apiClient.patch(setsUrl(`/${id}`), body));
}

/**
 * Delete a picture set. The pictures themselves are untouched.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deletePictureSet(id) {
  return unwrap(apiClient.delete(setsUrl(`/${id}`)));
}

/**
 * Ask which of the given pictures belong to which sets.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {boolean} [options.includeDeleted=false] - count scrapheaped pictures
 *   as members, so a set does not look empty while its pictures are recoverable.
 * @returns {Promise<Object>} the response body: set id → member picture ids.
 */
export async function getPictureSetMembership(
  pictureIds,
  { includeDeleted = false } = {},
) {
  return unwrap(
    apiClient.post(setsUrl("/membership"), {
      picture_ids: pictureIds,
      include_deleted: includeDeleted,
    }),
  );
}

/**
 * Add one picture to a set.
 * @param {number|string} setId
 * @param {number|string} pictureId
 * @returns {Promise<Object>} the response body.
 */
export async function addPictureToSet(setId, pictureId) {
  return unwrap(apiClient.post(setsUrl(`/${setId}/members/${pictureId}`)));
}

/**
 * Remove one picture from a set.
 * @param {number|string} setId
 * @param {number|string} pictureId
 * @returns {Promise<Object>} the response body.
 */
export async function removePictureFromSet(setId, pictureId) {
  return unwrap(apiClient.delete(setsUrl(`/${setId}/members/${pictureId}`)));
}

/**
 * List the pictures frozen by a locked set.
 *
 * The badges this drives are advisory over a hard server-side 423 guard, so a
 * failed refresh is a display problem rather than a correctness one.
 *
 * @returns {Promise<Object>} the response body, whose `sets` carries each
 *   locked set and its frozen members.
 */
export async function getLockedMembers() {
  return unwrap(apiClient.get(setsUrl("/locked-members")));
}
