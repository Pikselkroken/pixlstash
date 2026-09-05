// Folder-structure resource - v1.11 Phases 2 and 3: read a folder tree,
// propose what each level is, and commit an accepted mapping.
//
// Both halves are task-id-polling shells (integration_architecture.md §11):
// the POST starts work in the background and returns a task id, the GET
// polls status. Neither ever writes a file - the read proposes, and the
// commit's only filesystem-adjacent effect is registering the root for
// in-place indexing, the same mechanism reference folders already use.
//
// Per the §src/api rules the URL strings live only here.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

const READ_URL = "/folder-structure/read";
const READ_STATUS_URL = "/folder-structure/read/status";
const COMMIT_URL = "/folder-structure/commit";
const COMMIT_STATUS_URL = "/folder-structure/commit/status";

/**
 * Start reading a folder tree. Writes nothing; two minutes is typical for a
 * real library, so the caller polls `getFolderStructureReadStatus`.
 *
 * @param {string} path - absolute folder path on the server's machine.
 * @param {{matchExisting?: boolean}} [options] - `matchExisting: false` skips
 *   matching folder names against the active library's projects, people and
 *   sets. "Add a library" reads the folder BEFORE the library exists, while
 *   some other library is active, so matching there would name the wrong
 *   library's entities.
 * @returns {Promise<Object>} `{ task_id }`.
 */
export async function startFolderStructureRead(
  path,
  { matchExisting = true } = {},
) {
  return unwrap(
    apiClient.post(READ_URL, { path, match_existing: matchExisting }),
  );
}

/**
 * Poll a read's progress. `result` is null until `status` is `completed`
 * (or `cancelled`, which keeps whatever was found).
 *
 * @param {string} taskId
 * @returns {Promise<Object>} `{ task_id, status, stage, processed, total, progress, error, result }`.
 */
export async function getFolderStructureReadStatus(taskId) {
  return unwrap(
    apiClient.get(READ_STATUS_URL, { params: { task_id: taskId } }),
  );
}

/**
 * Ask a running read to stop at its next folder boundary. The partial result
 * is kept, so the screen can still show what was found.
 *
 * @param {string} taskId
 * @returns {Promise<Object>} `{ status }`.
 */
export async function cancelFolderStructureRead(taskId) {
  return unwrap(apiClient.delete(READ_URL, { params: { task_id: taskId } }));
}

/**
 * Commit an accepted mapping over a settled read. Registers the read's root
 * for in-place indexing and creates the accepted projects/people/sets/tags -
 * no file is moved, renamed or copied.
 *
 * @param {string} taskId - the settled read's task id.
 * @param {Array<{relative_path: string, kind: string, match_id?: number}>} assignments -
 *   one entry per folder the owner accepted as something; a folder left "just
 *   a folder" is simply absent.
 * @param {string} [label] - defaults to the folder's own name.
 * @param {"reference"|"local_import"} [mode] - `"reference"` (default)
 *   registers the scanned root as an external reference folder, indexed in
 *   place. `"local_import"` instead imports its pictures as ordinary managed
 *   pictures of the active library - only valid when the scanned root is
 *   inside that library's own image root (v1.11 Phase 3, "Bring them in" on
 *   a freshly created library).
 * @param {Object|null} [readResult] - the read's own result, for a caller that
 *   has one but no task the server still remembers. Exactly one of `taskId` and
 *   `readResult` reaches the server.
 * @returns {Promise<Object>} `{ task_id }`.
 */
export async function startFolderStructureCommit(
  taskId,
  assignments,
  label,
  mode = "reference",
  readResult = null,
) {
  // A read lives in one server process's memory. The desktop's first run reads
  // the library folder while the GPU runtime downloads and then restarts the
  // backend onto it, so the task that produced the answer is gone by the time
  // the owner answers the questions - "Task not found", with the result sitting
  // right here. Send the result instead when that is all we have.
  const body = taskId
    ? { task_id: taskId, assignments, mode }
    : { read_result: readResult, assignments, mode };
  if (label) body.label = label;
  return unwrap(apiClient.post(COMMIT_URL, body));
}

/**
 * Poll a commit's progress. `result` is null until `status` is `completed`.
 * `abandoned` and `deferred` are the owner's own two ways of stopping one -
 * see `stopFolderStructureCommit`. In reference mode the folder's own scan
 * runs to completion regardless of what the screen does next.
 *
 * @param {string} taskId
 * @returns {Promise<Object>} `{ task_id, status, stage, processed, total, progress, error, result }`.
 */
export async function getFolderStructureCommitStatus(taskId) {
  return unwrap(
    apiClient.get(COMMIT_STATUS_URL, { params: { task_id: taskId } }),
  );
}

/**
 * Stop a running commit.
 *
 * Neither stop un-indexes anything: no file is touched either way and every
 * picture already indexed stays indexed. The commit unwinds at its next chunk
 * boundary, so a poll immediately afterwards can still say `running`.
 *
 * @param {string} taskId
 * @param {"abort"|"defer"} [stop] - `"abort"` gives up on the import;
 *   `"defer"` is "organise later": keep everything indexed, do not apply the
 *   folder mapping.
 * @returns {Promise<Object>} `{ status }`.
 */
export async function stopFolderStructureCommit(taskId, stop = "abort") {
  return unwrap(
    apiClient.delete(COMMIT_URL, { params: { task_id: taskId, stop } }),
  );
}
