// Folders resource - /reference-folders, /import-folders, and /filesystem/*.
//
// Two folder KINDS share one shape: reference folders (watched, read in place,
// optionally sidecar-synced) and import folders (watched, contents ingested).
// The editor is written once against both, so the CRUD functions here take the
// kind as their first argument rather than being duplicated per kind.
//
// `/filesystem/*` is the server-side directory picker that backs the browse
// dialog. It is grouped here because it exists only to choose a folder path.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

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
  return unwrap(apiClient.get(FOLDER_URLS.reference));
}

/**
 * List the import folders.
 * @returns {Promise<Object>} the response body, whose `folders` is the list.
 */
export async function listImportFolders() {
  return unwrap(apiClient.get(FOLDER_URLS.import));
}

/**
 * Create a folder of the given kind.
 * @param {"reference"|"import"} kind
 * @param {Object} body - `{ folder }` plus the kind's options (label,
 *   host_path, delete_after_import, sync_tags, ...).
 * @returns {Promise<Object>} the created folder (the response body).
 */
export async function createFolder(kind, body) {
  return unwrap(apiClient.post(folderUrl(kind), body));
}

/**
 * Patch a folder of the given kind.
 * @param {"reference"|"import"} kind
 * @param {number|string} id
 * @param {Object} body - only the keys to change.
 * @returns {Promise<Object>} the updated folder (the response body).
 */
export async function patchFolder(kind, id, body) {
  return unwrap(apiClient.patch(`${folderUrl(kind)}/${id}`, body));
}

/**
 * Stop watching a folder of the given kind.
 * @param {"reference"|"import"} kind
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteFolder(kind, id) {
  return unwrap(apiClient.delete(`${folderUrl(kind)}/${id}`));
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
  return unwrap(apiClient.get(`${FOLDER_URLS.reference}/detect-sidecars`, {
    params: { path },
  }));
}

/**
 * List one server-side directory for the folder picker.
 * @param {string|null} [path] - omit or pass null for the default root.
 * @param {Object} [options]
 * @param {boolean} [options.showHidden=false] - include dot-entries.
 * @param {boolean} [options.includeModelFiles=false] - also list model files,
 *   for the shelf's `Add file` picker. Off for every other caller, which is
 *   choosing a directory and would only have its subfolders buried.
 * @returns {Promise<Object>} the response body: `entries` and the resolved
 *   `path` (which may differ from the requested one).
 */
export async function browseFilesystem(
  path,
  { showHidden = false, includeModelFiles = false } = {},
) {
  return unwrap(apiClient.get("/filesystem/browse", {
    params: {
      path: path ?? undefined,
      show_hidden: showHidden,
      include_model_files: includeModelFiles,
    },
  }));
}

/**
 * Create a directory on the server, for the picker's "new folder" action.
 * @param {string} path - the full path to create.
 * @returns {Promise<Object>} the response body, whose `path` is the created
 *   directory as the server resolved it.
 */
export async function createFilesystemFolder(path) {
  return unwrap(apiClient.post("/filesystem/folders", { path }));
}

/**
 * Move a reference folder's contents to a new location on disk.
 *
 * The server moves the files AND rewrites the stored picture paths, so the
 * library keeps pointing at them; the response reports both counts.
 *
 * @param {number|string} id
 * @param {string} destinationFolder
 * @returns {Promise<Object>} the response body: `moved_picture_ids`,
 *   `moved_entry_count`, `rewritten_count`.
 */
export async function relocateReferenceFolder(id, destinationFolder) {
  return unwrap(apiClient.post(
    `${FOLDER_URLS.reference}/${id}/relocate`,
    { destination_folder: destinationFolder },
  ));
}

/**
 * Move specific pictures into a reference folder.
 *
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.destinationSubpath] - a folder beneath the
 *   reference root; omitted to drop them at the root.
 * @returns {Promise<Object>} the response body, whose `moved_picture_ids` are
 *   the pictures that actually moved.
 */
export async function movePicturesToReferenceFolder(
  id,
  pictureIds,
  { destinationSubpath } = {},
) {
  const body = { picture_ids: pictureIds };
  if (destinationSubpath) body.destination_subpath = destinationSubpath;
  return unwrap(apiClient.post(
    `${FOLDER_URLS.reference}/${id}/move-pictures`,
    body,
  ));
}
