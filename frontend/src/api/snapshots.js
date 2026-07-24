// Snapshots resource — /snapshots and its restore sub-resources.
//
// URLs here are written WITHOUT the `/api/v1` prefix: the apiClient request
// interceptor prepends it. The call sites this module replaced hardcoded the
// prefix, which worked (the interceptor skips URLs that already carry it) but
// duplicated a decision that belongs to the transport layer alone.
//
// Restore comes in two shapes per operation: whole-vault (no body) and a
// resource-scoped batch. They are separate endpoints, so they are separate
// functions here rather than one function with a mode flag.

import { apiClient } from "../utils/apiClient";

/** Base path of the snapshots collection. */
const SNAPSHOTS_URL = "/snapshots";

/**
 * List all snapshots, newest first.
 * @returns {Promise<Array<Object>>} the snapshot list (the response body).
 */
export async function listSnapshots() {
  const res = await apiClient.get(SNAPSHOTS_URL);
  return res.data;
}

/**
 * Read the snapshot subsystem's job status.
 * @returns {Promise<Object>} the response body, whose `active_job` is the
 *   in-flight create/restore job, or null when the subsystem is idle.
 */
export async function getSnapshotStatus() {
  const res = await apiClient.get(`${SNAPSHOTS_URL}/status`);
  return res.data;
}

/**
 * Create a snapshot of the current vault.
 * @param {string} [label] - optional user-facing label; omitted when falsy.
 * @returns {Promise<Object>} the created snapshot (the response body).
 */
export async function createSnapshot(label) {
  const res = await apiClient.post(SNAPSHOTS_URL, label ? { label } : {});
  return res.data;
}

/**
 * Rename a snapshot.
 * @param {number|string} id
 * @param {string} label - the new label.
 * @returns {Promise<Object>} the updated snapshot (the response body).
 */
export async function renameSnapshot(id, label) {
  const res = await apiClient.patch(`${SNAPSHOTS_URL}/${id}`, { label });
  return res.data;
}

/**
 * Delete a snapshot and its archived contents.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteSnapshot(id) {
  const res = await apiClient.delete(`${SNAPSHOTS_URL}/${id}`);
  return res.data;
}

/**
 * Preview what restoring an ENTIRE snapshot would change.
 * @param {number|string} id
 * @returns {Promise<Object>} the preview (the response body).
 */
export async function previewRestore(id) {
  const res = await apiClient.get(`${SNAPSHOTS_URL}/${id}/restore/preview`);
  return res.data;
}

/**
 * Preview what restoring a specific set of resources would change.
 * @param {number|string} id
 * @param {Array<Object>} resources - the resource refs to restore.
 * @returns {Promise<Object>} the preview (the response body).
 */
export async function previewRestoreBatch(id, resources) {
  const res = await apiClient.post(
    `${SNAPSHOTS_URL}/${id}/restore/preview/batch`,
    { resources },
  );
  return res.data;
}

/**
 * Restore an ENTIRE snapshot over the current vault.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body (the started restore job).
 */
export async function executeRestore(id) {
  const res = await apiClient.post(`${SNAPSHOTS_URL}/${id}/restore`, {});
  return res.data;
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
  const res = await apiClient.post(`${SNAPSHOTS_URL}/${id}/restore/batch`, {
    resources,
    confirm_restore_dependencies: confirmRestoreDependencies,
  });
  return res.data;
}
