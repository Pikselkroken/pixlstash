// Tagger plugins resource — /taggers and /tagger/label-thresholds.
//
// `/taggers` returns both the installed plugins and the user's per-plugin
// settings in one body; the settings are written back through the user config
// (see api/config.js), which is why there is no PATCH here.

import { apiClient } from "../utils/apiClient";

/**
 * List the installed tagger plugins together with their current settings.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""] - explicit backend base, for the call
 *   sites that address the backend absolutely.
 * @returns {Promise<Object>} the response body: `plugins` and `settings`.
 */
export async function listTaggers({ baseUrl = "" } = {}) {
  const res = await apiClient.get(`${baseUrl}/taggers`);
  return res.data;
}

/**
 * Read the active tagger's per-label confidence thresholds.
 *
 * @param {number} [offset] - preview the thresholds at this offset instead of
 *   the saved one. Omitted when null/undefined so the server uses the saved
 *   value.
 * @returns {Promise<Array<Object>>} the threshold rows (the response body).
 */
export async function getLabelThresholds(offset) {
  const res = await apiClient.get("/tagger/label-thresholds", {
    params: offset != null ? { offset } : {},
  });
  return res.data;
}
