// Model-folder registry resource: /model-folders.
//
// A model folder is a HUB row, not a vault one: a folder of LoRAs is a fact
// about this disk, so it is registered once for the machine rather than once
// per library (`pixlstash/routes/model_folders.py`). Three consequences the
// caller must honour:
//
//   * The READ is owner-only but the WRITES are §16.3 host-capability. A remote
//     owner gets the list and a 403 on every mutator, so the UI takes its
//     enabled/disabled state from `useLibrariesStore().canManage` rather than
//     letting a button fail on click.
//   * Exactly one `managed` folder always exists and DELETE on it answers 409,
//     not 403: the caller is authorized and the target's state refuses. It is
//     PixlStash's own storage, so there is no association to dissolve.
//   * Forgetting a folder is a tombstone. The `model_file` rows go, the `model`
//     rows keep their names, triggers and attachments, and re-adding the folder
//     re-links by content. That is what makes it cheap to undo, and why the
//     caller must capture the row's fields BEFORE the delete.
//
// Per the §src/api rules the URL strings live only here.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** The registry of folders the shelf catalogues. */
const MODEL_FOLDERS_URL = "/model-folders";

/** The folder kind PixlStash owns: exactly one exists and it cannot be forgotten. */
export const MANAGED_KIND = "managed";

/**
 * An ai-toolkit output root: taken from on import, never catalogued in place.
 *
 * The server permits several, but ai-toolkit writes everything under one root,
 * so the UI treats it as a single setting - registering one hides the offer to
 * register another, and the training-runs view reads that one.
 */
export const SOURCE_KIND = "source";

/** The folder kinds a caller may register (`managed`/`foreign` are ours). */
export const CREATABLE_KINDS = ["user", "source"];

/**
 * List every registered model folder.
 *
 * Owner-only, and readable from anywhere the owner is: a remote session gets
 * the same list it would get locally and only loses the ability to change it.
 *
 * @returns {Promise<Array<Object>>} the `folders` array. Each entry carries
 *   `id`, `path`, `kind` (`user`/`managed`/`foreign`/`source`), `owner`,
 *   `movable`, `relocatable` (whether `POST .../relocate` will move it whole -
 *   offer Move on exactly these, never on `movable === "root_only"`, which the
 *   InsightFace packs also say and cannot be moved yet), `host_path`,
 *   `delete_after_import`, `last_checked`, `created_at`, `file_count` (copies
 *   registered under it, in any state) and `present_bytes` (bytes of the
 *   `present` ones only, so zero is an answer rather than an unknown).
 */
export async function listModelFolders() {
  const body = await unwrap(apiClient.get(MODEL_FOLDERS_URL));
  return Array.isArray(body?.folders) ? body.folders : [];
}

/**
 * Capacity of the drives the registered folders sit on.
 *
 * A separate call from {@link listModelFolders} because it is a separate route,
 * and it is a separate route because it stats the filesystem: an offline
 * network mount can make it slow, while the folder list answers from the
 * database. Call it for the drive bands and let it fail on its own without
 * taking the list with it.
 *
 * @returns {Promise<Array<Object>>} the `devices` array. Each entry carries
 *   `device_id` (null when the drive could not be measured), `mount_point`,
 *   `total_bytes` and `free_bytes` (both null when unmeasurable),
 *   `shelf_bytes` and `folder_ids`.
 */
export async function listModelFolderDevices() {
  const body = await unwrap(apiClient.get(`${MODEL_FOLDERS_URL}/devices`));
  return Array.isArray(body?.devices) ? body.devices : [];
}

/**
 * Register a folder for the shelf to catalogue.
 *
 * Registering does not scan; call {@link rescanModelFolder} afterwards. A path
 * that is already registered answers 409, which the picker avoids by disabling
 * the rows it was given in `registeredPaths`.
 *
 * @param {Object} options
 * @param {string} options.path - absolute host path, chosen by the owner.
 * @param {string} [options.kind="user"] - `user` to catalogue in place, or
 *   `source` for an ai-toolkit output root that is taken from instead.
 * @param {string} [options.hostPath] - Docker bind source. REQUIRED when the
 *   server runs in Docker; the API answers 400 without it.
 * @param {boolean} [options.deleteAfterImport] - `source` folders only.
 * @returns {Promise<Object>} the created folder.
 */
export async function createModelFolder({
  path,
  kind = "user",
  hostPath,
  deleteAfterImport,
} = {}) {
  const body = { path, kind };
  if (hostPath) body.host_path = hostPath;
  if (deleteAfterImport !== undefined) {
    body.delete_after_import = deleteAfterImport;
  }
  return unwrap(apiClient.post(MODEL_FOLDERS_URL, body));
}

/**
 * Forget a registered folder.
 *
 * Nothing on disk is touched and no curation is lost, so this needs no
 * confirmation prompt. The managed store answers 409 instead, and so does a
 * folder asked for while a move or an import is running - that job is writing
 * the very location rows this drops, so it is a "try again in a moment", not a
 * refusal of the gesture. The store shows the server's `detail` either way.
 *
 * @param {number|string} id
 * @returns {Promise<Object>} the response body, whose `tombstoned_files` is how
 *   many location rows were dropped.
 */
export async function forgetModelFolder(id) {
  return unwrap(apiClient.delete(`${MODEL_FOLDERS_URL}/${id}`));
}

/**
 * Start a scan of a registered folder.
 *
 * Returns 202 as soon as the thread is started, NOT when the scan finishes: a
 * folder of 1,800 adapters is minutes of hashing and there is no progress
 * channel. The only completion signal is `last_checked` advancing on a later
 * {@link listModelFolders}, which is what the store polls for.
 *
 * @param {number|string} id
 * @returns {Promise<Object>} the response body, whose `status` is `started`,
 *   `already_running`, or `skipped` for a `source` folder.
 */
export async function rescanModelFolder(id) {
  return unwrap(apiClient.post(`${MODEL_FOLDERS_URL}/${id}/rescan`, {}));
}
