// "About your library" findings - /insights.
//
// One GET, computed live on every call. There is no cache behind it and
// nothing to poll: "Look again" is this same request.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Read every finding about the current library.
 *
 * @returns {Promise<Object>} `total_pictures`, `folder_pictures`, `folders` and
 *   `findings` - the last ordered with what there is to look at first, and
 *   including the checks that came back clear.
 */
export async function getInsights() {
  return unwrap(apiClient.get("/insights"));
}
