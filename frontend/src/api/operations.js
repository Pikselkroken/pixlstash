// Operations resource: the append-only operation log behind undo and redo.
//
// Every user-visible change is recorded as one operation, newest first. The log
// is the undo/redo stack today and the audit / activity feed later, so a caller
// reading it is reading history, not a queue it may reorder.
//
// All of these routes are OWNER_ONLY on the backend: a share or read-only
// session must never enumerate the owner's whole change history, and must never
// revert a change. Guarding that is the caller's job, not this module's; the
// module is pure transport and a call made in a read-only session will simply
// come back 403.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * List recorded operations, newest first.
 *
 * Returns what changed, how many targets it touched, who did it, where it came
 * from and whether it is still reversible. The before/after payloads are
 * omitted here; fetch a single operation to see them.
 *
 * @param {Object} [options]
 * @param {number} [options.limit=50] - how many operations to return.
 * @param {string} [options.status] - filter to one status: `"applied"`,
 *   `"undone"` or `"superseded"`.
 * @param {string} [options.batchId] - filter to all operations of one bulk
 *   action.
 * @param {string} [options.opType] - filter to one operation type.
 *   that do not talk to the default backend.
 * @returns {Promise<Array<Object>>} the operations (the response body).
 */
export async function listOperations({
  limit = 50,
  status,
  batchId,
  opType,
  signal,
} = {}) {
  const params = { limit };
  if (status) params.status = status;
  if (batchId) params.batch_id = batchId;
  if (opType) params.op_type = opType;
  const config = signal ? { params, signal } : { params };
  return unwrap(apiClient.get(`/operations`, config));
}

/**
 * Read what undo and redo would do next.
 *
 * Lets the UI label and enable its undo affordances without fetching the whole
 * log.
 *
 * @returns {Promise<Object>} the response body:
 *   `{ can_undo, can_redo, next_undo, next_redo }`, where the two operations
 *   are `null` when there is nothing to act on.
 */
export async function getUndoState({ signal } = {}) {
  const url = `/operations/undo-state`;
  const res = signal
    ? await apiClient.get(url, { signal })
    : await apiClient.get(url);
  return res.data;
}

/**
 * Undo the newest reversible operation, or a named one.
 *
 * Restores the recorded *before* state. If the operation belongs to a batch
 * (one bulk action) the whole batch is reverted, so a partially-undone bulk
 * action cannot exist. The server answers 409 when there is nothing to undo or
 * the named operation is not reversible, and 423 when a locked picture set
 * freezes one of the targets.
 *
 * @param {Object} [options]
 * @param {number|null} [options.operationId=null] - undo this specific
 *   operation instead of the newest reversible one. Sent as a body only when
 *   given.
 * @returns {Promise<Object>} the response body:
 *   `{ operations, picture_ids, picture_count }`.
 */
export async function undoLastOperation({
  operationId = null,
  signal,
} = {}) {
  const url = `/operations/undo`;
  let res;
  if (operationId === null) {
    res = signal
      ? await apiClient.post(url, undefined, { signal })
      : await apiClient.post(url);
  } else {
    const body = { operation_id: operationId };
    res = signal
      ? await apiClient.post(url, body, { signal })
      : await apiClient.post(url, body);
  }
  return res.data;
}

/**
 * Re-apply the most recently undone operation.
 *
 * Restores the recorded *after* state of that operation and its whole batch.
 * Recording any new operation invalidates the redo stack, so redo only ever
 * replays onto the history it was undone from. 409 when there is nothing to
 * redo.
 *
 * @returns {Promise<Object>} the response body:
 *   `{ operations, picture_ids, picture_count }`.
 */
export async function redoOperation({ signal } = {}) {
  const url = `/operations/redo`;
  const res = signal
    ? await apiClient.post(url, undefined, { signal })
    : await apiClient.post(url);
  return res.data;
}

/**
 * Undo one specific operation, addressed by path.
 *
 * Same semantics as {@link undoLastOperation} with an `operationId`: undoing
 * any member of a batch reverts the whole batch.
 *
 * @param {number|string} operationId - the operation to revert.
 * @returns {Promise<Object>} the response body:
 *   `{ operations, picture_ids, picture_count }`.
 */
export async function undoOperation(
  operationId,
  { signal } = {},
) {
  const url = `/operations/${operationId}/undo`;
  const res = signal
    ? await apiClient.post(url, undefined, { signal })
    : await apiClient.post(url);
  return res.data;
}

/**
 * Undo one whole bulk action by its batch id.
 *
 * The single-call revert behind a bulk action's report ("Collapsed 2,700
 * groups. Undo"). Reverts every still-applied, reversible operation carrying
 * this batch id, newest first. 409 when the batch has nothing left to undo.
 *
 * @param {string} batchId - the batch id shared by the bulk action's
 *   operations. URL-encoded here, since it is a server-issued opaque string.
 * @returns {Promise<Object>} the response body:
 *   `{ operations, picture_ids, picture_count }`.
 */
export async function undoBatch(batchId, { signal } = {}) {
  const url = `/operations/batches/${encodeURIComponent(batchId)}/undo`;
  const res = signal
    ? await apiClient.post(url, undefined, { signal })
    : await apiClient.post(url);
  return res.data;
}
