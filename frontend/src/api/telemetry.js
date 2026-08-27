// Telemetry install-ID resource - GET/POST /telemetry/install-id.
//
// The four consent flags are ordinary user settings and ride /users/me/config
// (see api/config.js). Only the install ID has its own routes, because it is a
// property of the installation rather than of the user row: it lives beside the
// server config so a snapshot restore or a library switch cannot change it.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

const INSTALL_ID_URL = "/telemetry/install-id";

/**
 * Fetch this installation's anonymous install ID, creating one if absent.
 *
 * @returns {Promise<{available: boolean, install_id: string|null,
 *   created_date: string|null, is_new_install: boolean|null}>}
 *   ``available: false`` means the ID could not be stored (a read-only config
 *   directory, typically). No ID is invented in that case, because one that is
 *   regenerated every boot would inflate install counts rather than measure
 *   them.
 */
export async function getInstallId() {
  return unwrap(apiClient.get(INSTALL_ID_URL));
}

/**
 * Replace the stored install ID with a fresh, unlinkable one.
 *
 * The previous ID is overwritten and nothing on disk ties the two together.
 *
 * @returns {Promise<Object>} The new identity, same shape as getInstallId.
 */
export async function recreateInstallId() {
  return unwrap(apiClient.post(`${INSTALL_ID_URL}/recreate`));
}
