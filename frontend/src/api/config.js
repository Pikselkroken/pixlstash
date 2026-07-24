// User config resource — GET/PATCH /users/me/config.
//
// This endpoint is the most duplicated string in the frontend (20+ call sites
// across settings sections, App.vue, stores, and toolbar panels). Centralising
// it here is the pattern-setter for the Phase 1 API layer: one place owns the
// URL, so a contract change (or the §13 error normalisation) happens once.

import { apiClient } from "../utils/apiClient";

/** Path of the per-user config blob. */
const CONFIG_URL = "/users/me/config";

/**
 * Fetch the current user's configuration blob.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""] - explicit backend base, for the call
 *   sites that address the backend absolutely.
 * @returns {Promise<Object>} the config object (the response body).
 */
export async function getUserConfig({ baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}${CONFIG_URL}`);
  return res.data;
}

/**
 * Patch a partial slice of the current user's configuration.
 * @param {Object} partial - only the keys to change.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the updated config (the response body).
 */
export async function patchUserConfig(partial, { baseUrl = "" } = {}) {
  const res = await apiClient.patch(`${baseUrl}${CONFIG_URL}`, partial);
  return res.data;
}
