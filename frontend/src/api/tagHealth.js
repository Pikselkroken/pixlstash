// Tag-health board resource - /tag_health.
//
// The board reads a server-side cache. While that cache is (re)building the
// GET keeps answering with `building: true` and a progress fraction, which is
// what the caller polls on.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** Base path of the tag-health resource. */
const TAG_HEALTH_URL = "/tag_health";

/**
 * Read the tag-health rows for a scope.
 * @param {Object} [params] - optional scope filters: `project_id`, `set_id`,
 *   `character_id`. Omit them all for the whole library.
 * @returns {Promise<Object>} the response body: `rows`, plus the cache state
 *   (`building`, `progress`, `computed_at`, `stale`).
 */
export async function getTagHealth(params) {
  return unwrap(apiClient.get(TAG_HEALTH_URL, { params }));
}

/**
 * Start a rebuild of the tag-health cache.
 *
 * Returns as soon as the rebuild is accepted; poll {@link getTagHealth} for
 * progress.
 *
 * @returns {Promise<Object>} the response body.
 */
export async function rebuildTagHealth() {
  return unwrap(apiClient.post(`${TAG_HEALTH_URL}/rebuild`));
}
