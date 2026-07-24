// Current-user resource — /users/me/*.
//
// Everything scoped to "whoever this credential is": the owner account, the
// API/share tokens it has minted, and the watermark stamped onto shared
// images. The per-user config blob is large enough to live on its own (see
// api/config.js).
//
// Share links are created from here too: a share link IS a READ-scoped token
// pinned to one resource, so `createToken` is the single place that mints one.

import { apiClient } from "../utils/apiClient";

/** Base path of the current-user resource. */
const ME_URL = "/users/me";

/**
 * Read the owner account state.
 * @returns {Promise<Object>} the response body: `username` and `has_password`
 *   (false on a fresh install that has not claimed an account yet).
 */
export async function getAuthState() {
  const res = await apiClient.get(`${ME_URL}/auth`);
  return res.data;
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
  const res = await apiClient.post(`${ME_URL}/auth`, body);
  return res.data;
}

/**
 * List the tokens this user has minted (API tokens and share links alike).
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} the token list (the response body).
 */
export async function listTokens({ baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}${ME_URL}/token`);
  return res.data;
}

/**
 * Mint a token.
 *
 * A READ token pinned to a `resource_type`/`resource_id` is what backs a share
 * link; an unpinned token is a general API credential. The plaintext token is
 * returned ONCE, in this response — it cannot be read back later.
 *
 * @param {Object} body - `scope`, optional `description`, `resource_type`,
 *   `resource_id`, `expires_at`, `include_attachments`, `watermark`.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `token` is the secret.
 */
export async function createToken(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(`${baseUrl}${ME_URL}/token`, body);
  return res.data;
}

/**
 * Patch a token's editable settings.
 * @param {number|string} id
 * @param {Object} body - only the keys to change (e.g. `{ watermark }`).
 * @returns {Promise<Object>} the response body.
 */
export async function patchToken(id, body) {
  const res = await apiClient.patch(`${ME_URL}/token/${id}`, body);
  return res.data;
}

/**
 * Revoke a token. Any share link built on it stops working immediately.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteToken(id) {
  const res = await apiClient.delete(`${ME_URL}/token/${id}`);
  return res.data;
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
  const res = await apiClient.post(`${ME_URL}/watermark`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return res.data;
}

/**
 * Remove the uploaded watermark.
 * @returns {Promise<Object>} the response body.
 */
export async function deleteWatermark() {
  const res = await apiClient.delete(`${ME_URL}/watermark`);
  return res.data;
}
