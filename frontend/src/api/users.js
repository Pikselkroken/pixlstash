// Current-user resource - /users/me/*.
//
// Everything scoped to "whoever this credential is": the owner account, the
// API/share tokens it has minted, and the watermark stamped onto shared
// images. The per-user config blob is large enough to live on its own (see
// api/config.js).
//
// Share links are created from here too: a share link IS a READ-scoped token
// pinned to one resource, so `createToken` is the single place that mints one.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** Base path of the current-user resource. */
const ME_URL = "/users/me";

/**
 * Read the owner account state.
 * @returns {Promise<Object>} the response body: `username` and `has_password`
 *   (false on a fresh install that has not claimed an account yet).
 */
export async function getAuthState() {
  return unwrap(apiClient.get(`${ME_URL}/auth`));
}

/**
 * Set or change the owner password.
 *
 * @param {Object} body
 * @param {string|null} body.current_password - null when no password is set
 *   yet (the initial claim); required once one exists.
 * @param {string} body.new_password
 * @returns {Promise<Object>} the response body.
 */
export async function changePassword(body) {
  return unwrap(apiClient.post(`${ME_URL}/auth`, body));
}

/**
 * List the tokens this user has minted (API tokens and share links alike).
 * @returns {Promise<Array<Object>>} the token list (the response body).
 */
export async function listTokens() {
  return unwrap(apiClient.get(`${ME_URL}/token`));
}

/**
 * Mint a token.
 *
 * A READ token pinned to a `resource_type`/`resource_id` is what backs a share
 * link; an unpinned token is a general API credential. The plaintext token is
 * returned ONCE, in this response - it cannot be read back later.
 *
 * @param {Object} body - `scope`, optional `description`, `resource_type`,
 *   `resource_id`, `expires_at`, `include_attachments`, `watermark`.
 * @returns {Promise<Object>} the response body, whose `token` is the secret.
 */
export async function createToken(body) {
  return unwrap(apiClient.post(`${ME_URL}/token`, body));
}

/**
 * Patch a token's editable settings.
 * @param {number|string} id
 * @param {Object} body - only the keys to change (e.g. `{ watermark }`).
 * @returns {Promise<Object>} the response body.
 */
export async function patchToken(id, body) {
  return unwrap(apiClient.patch(`${ME_URL}/token/${id}`, body));
}

/**
 * Revoke a token. Any share link built on it stops working immediately.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteToken(id) {
  return unwrap(apiClient.delete(`${ME_URL}/token/${id}`));
}

/**
 * Ask which of the given pictures are currently shared by some token.
 *
 * Drives the "shared" badge, so it is re-asked as the grid scrolls: a revoked
 * token must clear the badge, not just a new share set it.
 *
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body, whose `shared_ids` is the
 *   shared subset of the ids asked about.
 */
export async function getSharedPictureIds(pictureIds) {
  return unwrap(apiClient.post(`${ME_URL}/shared-picture-ids/batch`, {
    picture_ids: pictureIds,
  }));
}

/**
 * List the resource ids of one type that this user has shared.
 * @param {string} resourceType - e.g. `"character"`, `"picture_set"`.
 * @returns {Promise<Object>} the response body.
 */
export async function getSharedResourceIds(resourceType) {
  return unwrap(apiClient.get(`${ME_URL}/shared-resource-ids`, {
    params: { resource_type: resourceType },
  }));
}

/**
 * Revoke every token sharing one specific resource.
 * @param {string} resourceType
 * @param {number|string} resourceId
 * @returns {Promise<Object>} the response body.
 */
export async function revokeTokensByResource(resourceType, resourceId) {
  return unwrap(apiClient.delete(`${ME_URL}/tokens/by-resource`, {
    params: { resource_type: resourceType, resource_id: resourceId },
  }));
}

/**
 * Upload the watermark image stamped onto shared pictures.
 *
 * Sent as multipart, so this is one of the few places the content type is set
 * explicitly rather than left to the JSON default.
 *
 * @param {File|Blob} file
 * @returns {Promise<Object>} the response body.
 */
export async function uploadWatermark(file) {
  const form = new FormData();
  form.append("file", file);
  return unwrap(apiClient.post(`${ME_URL}/watermark`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  }));
}

/**
 * Remove the uploaded watermark.
 * @returns {Promise<Object>} the response body.
 */
export async function deleteWatermark() {
  return unwrap(apiClient.delete(`${ME_URL}/watermark`));
}

/**
 * Read the user's penalised-tag list under a READ-scoped share token.
 *
 * A share session cannot read the full user config, so the smart-score
 * penalised tags are exposed on their own here; owner sessions read the same
 * data from the config blob instead.
 *
 * @returns {Promise<Object>} the response body, whose
 *   `smart_score_penalised_tags` is an array or a tag→weight map.
 */
export async function getPenalisedTags() {
  return unwrap(apiClient.get(`${ME_URL}/penalised-tags`));
}
