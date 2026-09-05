// Tagger plugins resource - /taggers, /taggers/plugin-diagnostics and
// /tagger/label-thresholds.
//
// `/taggers` returns both the installed plugins and the user's per-plugin
// settings in one body; the settings are written back through the user config
// (see api/config.js), which is why there is no PATCH here. Because it carries
// those settings it is owner-only, and the installation diagnostics beside it
// are local-owner-only - both calls have to tolerate a 403.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * List the installed tagger plugins together with their current settings.
 * @returns {Promise<Object>} the response body: `plugins` and `settings`.
 */
export async function listTaggers() {
  return unwrap(apiClient.get(`/taggers`));
}

/**
 * Read the plugin installation diagnostics: the scanned host folders and the
 * plugins that failed to import.
 *
 * Local owner only - a remote or share-scoped caller gets 403, which the
 * caller is expected to treat as "nothing to show" rather than an error. Both
 * halves name paths on the server's disk, which is why they are not on
 * `/taggers`.
 * @returns {Promise<Object>} the body: `plugin_dirs`, `load_errors`, and
 *   deployment-aware CLI hints for finding, searching, listing, and installing
 *   plugins.
 */
export async function listTaggerPluginDiagnostics() {
  return unwrap(apiClient.get(`/taggers/plugin-diagnostics`));
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
  return unwrap(apiClient.get("/tagger/label-thresholds", {
    params: offset != null ? { offset } : {},
  }));
}
