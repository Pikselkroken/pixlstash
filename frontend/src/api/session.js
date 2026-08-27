// Session resource - GET /session/context.
//
// Read once by `Root.vue` at boot to learn what the current credential can do
// (owner session vs. a scoped share token) before the app shell mounts.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Read the current session's context.
 * @returns {Promise<Object>} the response body, describing the session's
 *   scope and the resources it is allowed to see.
 */
export async function getSessionContext() {
  return unwrap(apiClient.get("/session/context"));
}

/**
 * List the sort mechanisms this server offers.
 * @returns {Promise<Object|Array<Object>>} the response body: either a bare
 *   array or an object nesting the list under `sort_mechanisms`/`options`,
 *   depending on server version.
 */
export async function listSortMechanisms() {
  return unwrap(apiClient.get(`/sort_mechanisms`));
}
