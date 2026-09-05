// Characters (people) resource - /characters.
//
// Membership here is by FACE, not by picture: assigning a person to a picture
// attaches that person to the faces detected in it, so a picture with no
// detected face cannot be assigned and will not appear in the membership
// response. Callers surface that difference rather than treating it as an
// error.
//
// See docs/frontend_architecture.md §8 ("The `src/api/` resource layer").

import { API_BASE_URL, apiClient, appendShareToken } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Build a characters route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/characters`.
 * @returns {string}
 */
function charactersUrl(path = "") {
  return `/characters${path}`;
}

/**
 * The URL of a character's thumbnail, for an `<img src>`.
 *
 * The other half of {@link getCharacterThumbnail}, for callers that draw MANY
 * of them: a blob costs a request and an object URL per character, while a
 * `src` lets the browser fetch and cache one response however many marks name
 * that character (the model shelf's assignment ring, #892/#904). The blob form
 * stays for the sidebar, which wants the failure as a rejection.
 *
 * The route is cookie-authenticated, so a share token has to ride in the query
 * - an `<img>` sends no header.
 *
 * @param {number|string} id
 * @returns {string}
 */
export function characterThumbnailUrl(id) {
  // An <img src> never reaches the apiClient interceptor, so the API base has
  // to be spelled out here rather than left for axios to prepend.
  return appendShareToken(
    `${API_BASE_URL}${charactersUrl(`/${encodeURIComponent(id)}/thumbnail`)}`,
  );
}

/**
 * List characters.
 * @param {Object} [options]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the character list (the response body).
 */
export async function listCharacters({ params } = {}) {
  return unwrap(
    apiClient.get(charactersUrl(""), params ? { params } : undefined),
  );
}

/**
 * Create a character.
 *
 * NOTE the shape: the route answers with `CharacterMutationResponse`, so the
 * body is the ENVELOPE `{status, character}` and the created record (with its
 * server-assigned id) is nested under `.character`. Callers that need the id
 * must unwrap; reading `.id` off the envelope silently yields undefined.
 *
 * @param {Object} body - the character's fields (name, notes, ...).
 * @returns {Promise<{status: string, character: Object|null}>} the response
 *   body: the mutation envelope, NOT the bare character.
 */
export async function createCharacter(body) {
  return unwrap(apiClient.post(charactersUrl(""), body));
}

/**
 * Patch a character.
 *
 * Shares `CharacterMutationResponse` with the create route above, so this body
 * is the same `{status, character}` envelope and the record is nested.
 *
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @returns {Promise<{status: string, character: Object|null}>} the response
 *   body: the mutation envelope, NOT the bare character.
 */
export async function patchCharacter(id, body) {
  return unwrap(apiClient.patch(charactersUrl(`/${id}`), body));
}

/**
 * Delete a character by id.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteCharacter(id) {
  return unwrap(apiClient.delete(charactersUrl(`/${id}`)));
}

/**
 * Ask which of the given pictures show which people.
 *
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body: character id → picture ids,
 *   plus `pictures_with_faces` - the subset that has a face at all, which is
 *   the only subset an assignment can ever apply to.
 */
export async function getCharacterMembership(pictureIds) {
  return unwrap(
    apiClient.post(charactersUrl("/membership"), {
      picture_ids: pictureIds,
    }),
  );
}

/**
 * Assign a character to the faces found in the given pictures.
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body.
 */
export async function addCharacterFaces(id, pictureIds) {
  return unwrap(
    apiClient.post(charactersUrl(`/${id}/faces`), {
      picture_ids: pictureIds,
    }),
  );
}

/**
 * Unassign a character from the faces in the given pictures.
 *
 * The ids travel in a request BODY on a DELETE, which Axios only sends when it
 * is passed as `config.data` - hence the shape below.
 *
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body.
 */
export async function removeCharacterFaces(id, pictureIds) {
  return unwrap(
    apiClient.delete(charactersUrl(`/${id}/faces`), {
      data: { picture_ids: pictureIds },
    }),
  );
}

/**
 * Read one character.
 * @param {number|string} id
 * @returns {Promise<Object>} the character (the response body).
 */
export async function getCharacter(id) {
  return unwrap(apiClient.get(charactersUrl(`/${id}`)));
}

/**
 * Read just a character's name.
 *
 * A deliberately narrow read: the overlay resolves a name per detected face
 * and does not need the rest of the record.
 *
 * @param {number|string} id
 * @returns {Promise<Object>} the response body, whose `name` is the name.
 */
export async function getCharacterName(id) {
  return unwrap(apiClient.get(charactersUrl(`/${id}/name`)));
}

/**
 * Fetch a character's thumbnail image.
 *
 * A binary read: the module forwards `responseType: "blob"` so the caller gets
 * a Blob it can turn into an object URL, not a mangled string.
 *
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string|number} [options.cacheBuster] - forces a fresh image past the
 *   HTTP cache after the thumbnail has been regenerated.
 * @returns {Promise<Blob>} the image data.
 */
export async function getCharacterThumbnail(id, { cacheBuster } = {}) {
  const path =
    cacheBuster != null
      ? `/${id}/thumbnail?cb=${cacheBuster}`
      : `/${id}/thumbnail`;
  return unwrap(
    apiClient.get(charactersUrl(path), {
      responseType: "blob",
    }),
  );
}

/**
 * Assign a character to specific FACES (rather than to whole pictures).
 * @param {number|string} id
 * @param {Array<number|string>} faceIds
 * @returns {Promise<Object>} the response body.
 */
export async function addCharacterFacesByFaceId(id, faceIds) {
  return unwrap(
    apiClient.post(charactersUrl(`/${id}/faces`), {
      face_ids: faceIds,
    }),
  );
}

/**
 * Assign the exact face/picture pairs returned by character face search.
 * This is intentionally distinct from the manual picture-id fallback: a
 * suggestion has already identified its winning face and must not be rescored
 * between review and assignment.
 */
export async function addCharacterFaceAssignments(id, faceAssignments) {
  return unwrap(
    apiClient.post(charactersUrl(`/${id}/faces`), {
      face_assignments: faceAssignments,
    }),
  );
}

/**
 * Read a character's summary counts.
 *
 * The sidebar's pseudo-characters (all pictures, unassigned, scrapheap) are
 * addressed the same way, by passing their sentinel id.
 *
 * @param {number|string} id
 * @param {Object} [params] - optional scope params such as `project_id` or
 *   `apply_tag_filter`.
 * @returns {Promise<Object>} the response body, whose `image_count` is the
 *   number of pictures in scope.
 */
export async function getCharacterSummary(id, params) {
  return unwrap(
    apiClient.get(
      charactersUrl(`/${id}/summary`),
      params ? { params } : undefined,
    ),
  );
}

/**
 * Unassign a character from specific FACES.
 *
 * The picture-scoped sibling above clears every face in those pictures; this
 * one targets individual detections, which is what the grid's face selection
 * operates on.
 *
 * @param {number|string} id
 * @param {Array<number|string>} faceIds
 * @returns {Promise<Object>} the response body.
 */
export async function removeCharacterFacesByFaceId(id, faceIds) {
  return unwrap(
    apiClient.delete(charactersUrl(`/${id}/faces`), {
      data: { face_ids: faceIds },
    }),
  );
}

/**
 * List the picture ids chosen as a character's reference pictures.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body, whose
 *   `reference_picture_ids` is the ordered list.
 */
export async function getReferencePictures(id) {
  return unwrap(apiClient.get(charactersUrl(`/${id}/reference_pictures`)));
}
