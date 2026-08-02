// Libraries resource — GET /libraries, POST /libraries/active.
//
// A library is a folder holding vault.db and its images. The server keeps one
// open at a time; switching closes it and opens another, which is why the
// switch call ends in a full page reload rather than a store update.
//
// Only two endpoints exist on purpose: creating, attaching and detaching a
// library are command-line operations, because they point the server at folders
// on disk. Nothing here accepts a path.
//
// Per the §src/api rules the URL strings live only here.

import { apiClient } from "../utils/apiClient";

/** The registry of libraries this installation knows about. */
const LIBRARIES_URL = "/libraries";

/** The active-library resource; POST switches which library is open. */
const ACTIVE_LIBRARY_URL = "/libraries/active";

/**
 * List the registered libraries.
 *
 * `path` and `cli_hint` are present only when the caller is on the server's
 * machine, its LAN, or Tailscale. A remote session gets the names and which one
 * is active, and `can_manage: false`, so the UI can disable switching rather
 * than letting the call fail.
 *
 * @returns {Promise<Object>} `{ libraries, can_manage, in_docker, cli_hint }`.
 */
export async function listLibraries() {
  const res = await apiClient.get(LIBRARIES_URL);
  return res.data;
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
  const res = await apiClient.post(ACTIVE_LIBRARY_URL, { uuid });
  return res.data;
}
