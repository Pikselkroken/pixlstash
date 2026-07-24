// Session resource — GET /session/context.
//
// Read once by `Root.vue` at boot to learn what the current credential can do
// (owner session vs. a scoped share token) before the app shell mounts.

import { apiClient } from "../utils/apiClient";

/**
 * Read the current session's context.
 * @returns {Promise<Object>} the response body, describing the session's
 *   scope and the resources it is allowed to see.
 */
export async function getSessionContext() {
  const res = await apiClient.get("/session/context");
  return res.data;
}
