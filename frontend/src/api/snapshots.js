// Snapshots resource - /snapshots and its restore sub-resources.
//
// URLs here are written WITHOUT the `/api/v1` prefix: the apiClient request
// interceptor prepends it. The call sites this module replaced hardcoded the
// prefix, which worked (the interceptor skips URLs that already carry it) but
// duplicated a decision that belongs to the transport layer alone.
//
// Restore comes in two shapes per operation: whole-vault (no body) and a
// resource-scoped batch. They are separate endpoints, so they are separate
// functions here rather than one function with a mode flag.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** Base path of the snapshots collection. */
const SNAPSHOTS_URL = "/snapshots";

/**
 * List all snapshots, newest first.
 * @returns {Promise<Array<Object>>} the snapshot list (the response body).
 */
export async function listSnapshots() {
  return unwrap(apiClient.get(SNAPSHOTS_URL));
}

/**
 * Read the snapshot subsystem's job status.
 * @returns {Promise<Object>} the response body, whose `active_job` is the
 *   in-flight create/restore job, or null when the subsystem is idle.
 */
export async function getSnapshotStatus() {
  return unwrap(apiClient.get(`${SNAPSHOTS_URL}/status`));
}

/**
 * Create a snapshot of the current vault.
 * @param {string} [label] - optional user-facing label; omitted when falsy.
 * @returns {Promise<Object>} the created snapshot (the response body).
 */
export async function createSnapshot(label) {
  return unwrap(apiClient.post(SNAPSHOTS_URL, label ? { label } : {}));
}

/**
 * Rename a snapshot.
 * @param {number|string} id
 * @param {string} label - the new label.
 * @returns {Promise<Object>} the updated snapshot (the response body).
 */
export async function renameSnapshot(id, label) {
  return unwrap(apiClient.patch(`${SNAPSHOTS_URL}/${id}`, { label }));
}

/**
 * Delete a snapshot and its archived contents.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteSnapshot(id) {
  return unwrap(apiClient.delete(`${SNAPSHOTS_URL}/${id}`));
}

/**
 * Preview what restoring an ENTIRE snapshot would change.
 * @param {number|string} id
 * @returns {Promise<Object>} the preview (the response body).
 */
export async function previewRestore(id) {
  return unwrap(apiClient.get(`${SNAPSHOTS_URL}/${id}/restore/preview`));
}

/**
 * Preview what restoring a specific set of resources would change.
 * @param {number|string} id
 * @param {Array<Object>} resources - the resource refs to restore.
 * @returns {Promise<Object>} the preview (the response body).
 */
export async function previewRestoreBatch(id, resources) {
  return unwrap(apiClient.post(
    `${SNAPSHOTS_URL}/${id}/restore/preview/batch`,
    { resources },
  ));
}

/**
 * Ask which of the given pictures are byte-identical to their copies in a
 * snapshot.
 *
 * Used to grey out snapshots that would restore nothing for the current
 * selection.
 *
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @returns {Promise<Object>} the response body, whose `identical_ids` lists
 *   the unchanged pictures.
 */
export async function hashCompareSnapshot(id, pictureIds) {
  return unwrap(apiClient.post(`${SNAPSHOTS_URL}/${id}/hash-compare`, {
    picture_ids: pictureIds,
  }));
}

/**
 * Restore an ENTIRE snapshot over the current vault.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body (the started restore job).
 */
export async function executeRestore(id) {
  return unwrap(apiClient.post(`${SNAPSHOTS_URL}/${id}/restore`, {}));
}

/**
 * Restore a specific set of resources from a snapshot.
 *
 * `confirmRestoreDependencies` is the caller's answer to the dependency prompt
 * the preview raises; sending it false means "fail rather than pull in extra
 * resources I did not pick".
 *
 * @param {number|string} id
 * @param {Array<Object>} resources - the resource refs to restore.
 * @param {boolean} [confirmRestoreDependencies=false]
 * @returns {Promise<Object>} the response body (the started restore job).
 */
export async function executeRestoreBatch(
  id,
  resources,
  confirmRestoreDependencies = false,
) {
  return unwrap(apiClient.post(`${SNAPSHOTS_URL}/${id}/restore/batch`, {
    resources,
    confirm_restore_dependencies: confirmRestoreDependencies,
  }));
}
