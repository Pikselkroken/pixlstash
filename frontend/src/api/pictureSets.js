// Picture sets resource — /picture_sets.
//
// Membership is per-picture: a set is joined or left one picture at a time
// (`/picture_sets/{id}/members/{pictureId}`), so bulk actions are the caller's
// loop over these calls, not a bulk endpoint.
//
// Several call sites address the backend through an explicit base rather than
// a relative path, so the functions take an optional `baseUrl`.

import { apiClient } from "../utils/apiClient";

/**
 * Build a picture-sets route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/picture_sets`.
 * @param {string} [baseUrl=""]
 * @returns {string}
 */
function setsUrl(path = "", baseUrl = "") {
  return `${baseUrl}/picture_sets${path}`;
}

/**
 * List picture sets.
 *
 * The list includes the internal reference sets; callers that show user-facing
 * sets filter them out themselves.
 *
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the picture-set list (the response body).
 */
export async function listPictureSets({ baseUrl = "", params } = {}) {
  const res = await apiClient.get(
    setsUrl("", baseUrl),
    params ? { params } : undefined,
  );
  return res.data;
}

/**
 * Create a picture set.
 * @param {Object} body - the set's fields (name, icon, colour, ...).
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the created set (the response body).
 */
export async function createPictureSet(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(setsUrl("", baseUrl), body);
  return res.data;
}

/**
 * Patch a picture set.
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the updated set (the response body).
 */
export async function patchPictureSet(id, body, { baseUrl = "" } = {}) {
  const res = await apiClient.patch(setsUrl(`/${id}`, baseUrl), body);
  return res.data;
}

/**
 * Delete a picture set. The pictures themselves are untouched.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function deletePictureSet(id, { baseUrl = "" } = {}) {
  const res = await apiClient.delete(setsUrl(`/${id}`, baseUrl));
  return res.data;
}

/**
 * Ask which of the given pictures belong to which sets.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {boolean} [options.includeDeleted=false] - count scrapheaped pictures
 *   as members, so a set does not look empty while its pictures are recoverable.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: set id → member picture ids.
 */
export async function getPictureSetMembership(
  pictureIds,
  { includeDeleted = false, baseUrl = "" } = {},
) {
  const res = await apiClient.post(setsUrl("/membership", baseUrl), {
    picture_ids: pictureIds,
    include_deleted: includeDeleted,
  });
  return res.data;
}

/**
 * Add one picture to a set.
 * @param {number|string} setId
 * @param {number|string} pictureId
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function addPictureToSet(setId, pictureId, { baseUrl = "" } = {}) {
  const res = await apiClient.post(
    setsUrl(`/${setId}/members/${pictureId}`, baseUrl),
  );
  return res.data;
}

/**
 * Remove one picture from a set.
 * @param {number|string} setId
 * @param {number|string} pictureId
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function removePictureFromSet(
  setId,
  pictureId,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.delete(
    setsUrl(`/${setId}/members/${pictureId}`, baseUrl),
  );
  return res.data;
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
  const res = await apiClient.get(setsUrl("/locked-members"));
  return res.data;
}
