// Folders resource — /reference-folders, /import-folders, and /filesystem/*.
//
// Two folder KINDS share one shape: reference folders (watched, read in place,
// optionally sidecar-synced) and import folders (watched, contents ingested).
// The editor is written once against both, so the CRUD functions here take the
// kind as their first argument rather than being duplicated per kind.
//
// `/filesystem/*` is the server-side directory picker that backs the browse
// dialog. It is grouped here because it exists only to choose a folder path.

import { apiClient } from "../utils/apiClient";

/** Folder kinds, mapped to their collection paths. */
const FOLDER_URLS = {
  reference: "/reference-folders",
  import: "/import-folders",
};

/**
 * Resolve a folder kind to its collection path.
 * @param {"reference"|"import"} kind
 * @returns {string}
 */
function folderUrl(kind) {
  const url = FOLDER_URLS[kind];
  if (!url) throw new Error(`Unknown folder kind: ${kind}`);
  return url;
}

/**
 * List the reference folders.
 * @returns {Promise<Object>} the response body, whose `folders` is the list.
 */
export async function listReferenceFolders() {
  const res = await apiClient.get(FOLDER_URLS.reference);
  return res.data;
}

/**
 * List the import folders.
 * @returns {Promise<Object>} the response body, whose `folders` is the list.
 */
export async function listImportFolders() {
  const res = await apiClient.get(FOLDER_URLS.import);
  return res.data;
}

/**
 * Create a folder of the given kind.
 * @param {"reference"|"import"} kind
 * @param {Object} body - `{ folder }` plus the kind's options (label,
 *   host_path, delete_after_import, sync_tags, ...).
 * @returns {Promise<Object>} the created folder (the response body).
 */
export async function createFolder(kind, body) {
  const res = await apiClient.post(folderUrl(kind), body);
  return res.data;
}

/**
 * Patch a folder of the given kind.
 * @param {"reference"|"import"} kind
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @returns {Promise<Object>} the updated folder (the response body).
 */
export async function patchFolder(kind, id, body) {
  const res = await apiClient.patch(`${folderUrl(kind)}/${id}`, body);
  return res.data;
}

/**
 * Stop watching a folder of the given kind.
 * @param {"reference"|"import"} kind
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteFolder(kind, id) {
  const res = await apiClient.delete(`${folderUrl(kind)}/${id}`);
  return res.data;
}

/**
 * Ask the server which sidecar convention a candidate reference path uses.
 *
 * Used to pre-fill the sync toggles and suffixes while the user is still
 * typing the path, so the answer is only meaningful for the path that was
 * asked about: callers re-check the current path before applying it.
 *
 * @param {string} path
 * @returns {Promise<Object>} the response body: `found_tags`, `tags_suffix`,
 *   `found_descriptions`, `description_suffix`.
 */
export async function detectSidecars(path) {
  const res = await apiClient.get(`${FOLDER_URLS.reference}/detect-sidecars`, {
    params: { path },
  });
  return res.data;
}

/**
 * List one server-side directory for the folder picker.
 * @param {string|null} [path] - omit or pass null for the default root.
 * @param {Object} [options]
 * @param {boolean} [options.showHidden=false] - include dot-entries.
 * @returns {Promise<Object>} the response body: `entries` and the resolved
 *   `path` (which may differ from the requested one).
 */
export async function browseFilesystem(path, { showHidden = false } = {}) {
  const res = await apiClient.get("/filesystem/browse", {
    params: { path: path ?? undefined, show_hidden: showHidden },
  });
  return res.data;
}

/**
 * Create a directory on the server, for the picker's "new folder" action.
 * @param {string} path - the full path to create.
 * @returns {Promise<Object>} the response body, whose `path` is the created
 *   directory as the server resolved it.
 */
export async function createFilesystemFolder(path) {
  const res = await apiClient.post("/filesystem/folders", { path });
  return res.data;
}
