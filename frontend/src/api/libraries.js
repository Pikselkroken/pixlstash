// Libraries resource - the registry: list, inspect, add, rename, detach, switch.
//
// A library is a folder holding vault.db and its images. The server keeps one
// open at a time; switching closes it and opens another, which is why the
// switch call ends in a full page reload rather than a store update.
//
// Adding a library takes a host path, so those calls are on the locality tier
// and are refused for a remote session - which is why `can_manage` from the
// listing gates the whole management surface rather than each button guessing.
//
// Per the §src/api rules the URL strings live only here.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

// Public, deployment-independent fallback for remote owners. Unlike cli_hint
// it contains no host path, interpreter path, or container name.
export const LIBRARIES_DOCUMENTATION_URL =
  "https://github.com/Pikselkroken/pixlstash#multiple-libraries";

/** The registry of libraries this installation knows about. */
const LIBRARIES_URL = "/libraries";

/** The active-library resource; POST switches which library is open. */
const ACTIVE_LIBRARY_URL = "/libraries/active";

/** Asks what a folder is, so one picker can answer without a mode to choose. */
const INSPECT_LIBRARY_URL = "/libraries/inspect";

/**
 * List the registered libraries.
 *
 * `path` and `cli_hint` are present only when the caller is on the server's
 * machine, its LAN, or Tailscale. A remote session gets the names and which one
 * is active, and `can_manage: false`, so the UI can disable switching rather
 * than letting the call fail. Every library entry includes
 * `active_share_links`, owner metadata used to warn before its resource-scoped
 * links become inactive.
 *
 * @returns {Promise<Object>} `{ libraries, can_manage, in_docker, cli_hint }`.
 */
export async function listLibraries() {
  return unwrap(apiClient.get(LIBRARIES_URL));
}

/**
 * Switch the active library.
 *
 * On success every connected client is told to reload, because picture ids do
 * not mean the same thing in another library. If the target cannot be opened
 * the server answers 409 and stays on the library it was already using, so the
 * caller can surface the error without having lost anything.
 *
 * @param {string} uuid - The library's stable id (never its row number: a stale
 *   client holding a row number could otherwise switch to a different library).
 * @returns {Promise<Object>} `{ status, library, active_share_links }`.
 */
export async function setActiveLibrary(uuid) {
  return unwrap(apiClient.post(ACTIVE_LIBRARY_URL, { uuid }));
}

/**
 * Ask what a folder is before offering to add it.
 *
 * Answers one of five verdicts - `attached`, `overlaps`, `vault`, `pictures`,
 * `empty` - each with a `headline` and a `detail` written by the server, so the
 * picker renders the registry's own words for a refusal rather than re-deriving
 * the rule. `can_add` is the only thing the UI branches on.
 *
 * @param {string} path - absolute folder path on the server's machine.
 * @returns {Promise<Object>} the verdict body.
 */
export async function inspectLibraryPath(path) {
  return unwrap(apiClient.get(INSPECT_LIBRARY_URL, { params: { path } }));
}

/**
 * Add a library at `path`.
 *
 * Attaches when the folder already holds a vault and starts a fresh library
 * when it does not; no file is moved, renamed or copied either way. The folder
 * must already exist - the picker's `New folder` makes one. The server
 * re-inspects the path, so a folder that became covered since the picker asked
 * is still refused (409).
 *
 * @param {string} path
 * @param {string} [name] - defaults to the folder's own name.
 * @returns {Promise<Object>} the created library, in the listing's shape.
 */
export async function addLibrary(path, name) {
  const body = { path };
  if (name) body.name = name;
  return unwrap(apiClient.post(LIBRARIES_URL, body));
}

/**
 * Rename a library. Changes the label only; nothing on disk is renamed.
 *
 * @param {string} uuid
 * @param {string} name
 * @returns {Promise<Object>} the updated library.
 */
export async function renameLibrary(uuid, name) {
  return unwrap(apiClient.patch(`${LIBRARIES_URL}/${uuid}`, { name }));
}

/**
 * Stop using a library. Removes no file.
 *
 * The row is kept rather than deleted, so the share links pointing at it
 * survive - inert - until the same folder is added again. The active library is
 * refused (409); switch away first.
 *
 * @param {string} uuid
 * @returns {Promise<Object>} `{ status, library, inert_share_links }`.
 */
export async function detachLibrary(uuid) {
  return unwrap(apiClient.delete(`${LIBRARIES_URL}/${uuid}`));
}
