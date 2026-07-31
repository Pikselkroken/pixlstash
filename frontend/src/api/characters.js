// Characters (people) resource — /characters.
//
// Membership here is by FACE, not by picture: assigning a person to a picture
// attaches that person to the faces detected in it, so a picture with no
// detected face cannot be assigned and will not appear in the membership
// response. Callers surface that difference rather than treating it as an
// error.
//
// See docs/frontend_architecture.md §8 ("The `src/api/` resource layer").

import { apiClient } from "../utils/apiClient";

/**
 * Build a characters route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/characters`.
 * @param {string} [baseUrl=""]
 * @returns {string}
 */
function charactersUrl(path = "", baseUrl = "") {
  return `${baseUrl}/characters${path}`;
}

/**
 * List characters.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @param {Object} [options.params] - optional query params.
 * @returns {Promise<Array<Object>>} the character list (the response body).
 */
export async function listCharacters({ baseUrl = "", params } = {}) {
  const res = await apiClient.get(
    charactersUrl("", baseUrl),
    params ? { params } : undefined,
  );
  return res.data;
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
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<{status: string, character: Object|null}>} the response
 *   body: the mutation envelope, NOT the bare character.
 */
export async function createCharacter(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(charactersUrl("", baseUrl), body);
  return res.data;
}

/**
 * Patch a character.
 *
 * Shares `CharacterMutationResponse` with the create route above, so this body
 * is the same `{status, character}` envelope and the record is nested.
 *
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<{status: string, character: Object|null}>} the response
 *   body: the mutation envelope, NOT the bare character.
 */
export async function patchCharacter(id, body, { baseUrl = "" } = {}) {
  const res = await apiClient.patch(charactersUrl(`/${id}`, baseUrl), body);
  return res.data;
}

/**
 * Delete a character by id.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function deleteCharacter(id, { baseUrl = "" } = {}) {
  const res = await apiClient.delete(charactersUrl(`/${id}`, baseUrl));
  return res.data;
}

/**
 * Ask which of the given pictures show which people.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: character id → picture ids,
 *   plus `pictures_with_faces` — the subset that has a face at all, which is
 *   the only subset an assignment can ever apply to.
 */
export async function getCharacterMembership(
  pictureIds,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(charactersUrl("/membership", baseUrl), {
    picture_ids: pictureIds,
  });
  return res.data;
}

/**
 * Assign a character to the faces found in the given pictures.
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function addCharacterFaces(id, pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.post(charactersUrl(`/${id}/faces`, baseUrl), {
    picture_ids: pictureIds,
  });
  return res.data;
}

/**
 * Unassign a character from the faces in the given pictures.
 *
 * The ids travel in a request BODY on a DELETE, which Axios only sends when it
 * is passed as `config.data` — hence the shape below.
 *
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function removeCharacterFaces(
  id,
  pictureIds,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.delete(charactersUrl(`/${id}/faces`, baseUrl), {
    data: { picture_ids: pictureIds },
  });
  return res.data;
}

/**
 * Read one character.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the character (the response body).
 */
export async function getCharacter(id, { baseUrl = "" } = {}) {
  const res = await apiClient.get(charactersUrl(`/${id}`, baseUrl));
  return res.data;
}

/**
 * Read just a character's name.
 *
 * A deliberately narrow read: the overlay resolves a name per detected face
 * and does not need the rest of the record.
 *
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `name` is the name.
 */
export async function getCharacterName(id, { baseUrl = "" } = {}) {
  const res = await apiClient.get(charactersUrl(`/${id}/name`, baseUrl));
  return res.data;
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
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Blob>} the image data.
 */
export async function getCharacterThumbnail(
  id,
  { cacheBuster, baseUrl = "" } = {},
) {
  const path =
    cacheBuster != null
      ? `/${id}/thumbnail?cb=${cacheBuster}`
      : `/${id}/thumbnail`;
  const res = await apiClient.get(charactersUrl(path, baseUrl), {
    responseType: "blob",
  });
  return res.data;
}

/**
 * Assign a character to specific FACES (rather than to whole pictures).
 * @param {number|string} id
 * @param {Array<number|string>} faceIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function addCharacterFacesByFaceId(
  id,
  faceIds,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(charactersUrl(`/${id}/faces`, baseUrl), {
    face_ids: faceIds,
  });
  return res.data;
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
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `image_count` is the
 *   number of pictures in scope.
 */
export async function getCharacterSummary(id, params, { baseUrl = "" } = {}) {
  const res = await apiClient.get(
    charactersUrl(`/${id}/summary`, baseUrl),
    params ? { params } : undefined,
  );
  return res.data;
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
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function removeCharacterFacesByFaceId(
  id,
  faceIds,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.delete(charactersUrl(`/${id}/faces`, baseUrl), {
    data: { face_ids: faceIds },
  });
  return res.data;
}

/**
 * List the picture ids chosen as a character's reference pictures.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose
 *   `reference_picture_ids` is the ordered list.
 */
export async function getReferencePictures(id, { baseUrl = "" } = {}) {
  const res = await apiClient.get(
    charactersUrl(`/${id}/reference_pictures`, baseUrl),
  );
  return res.data;
}
