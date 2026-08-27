// Moving model files between registered folders - /model-moves.
//
// Three routes over ONE job, machine-wide, because a move is not a
// request-shaped operation: a folder of 1,806 adapters is 438 GB, so the
// copying runs on a thread and the client watches. Two things follow for the
// caller:
//
//   * **Validation is not deferred.** The whole batch is planned inside the
//     POST and refused before the first byte if the destination is unusable, a
//     path escapes its folder, or the copy would not fit. A mistake is an
//     immediate 4xx, never a job that dies on file 1,500 having moved 1,499.
//   * **A second move while one runs is a 409.** One disk, one queue.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Start moving registered copies into another folder.
 *
 * @param {number} destinationFolderId - a registered folder. A `source` folder
 *   is refused: it is an ai-toolkit output root, taken from, never written into.
 * @param {Array<{folder_id: number, relpath: string}>} items - the copies, each
 *   named by its `model_file` primary key so it is one COPY and never a model
 *   that happens to have several. Both fields come straight off a shelf row's
 *   `locations[]`.
 * @returns {Promise<Object>} the first status snapshot.
 */
export async function startModelMove(destinationFolderId, items) {
  return unwrap(
    apiClient.post("/model-moves", {
      destination_folder_id: destinationFolderId,
      items,
    }),
  );
}

/**
 * Move a folder PixlStash owns to another host path, files and all.
 *
 * A relocation IS a move - of every file one folder holds - so it runs the same
 * job and is watched through the same status route. Two folders qualify, and
 * the list response says which: offer this on the rows whose `relocatable` is
 * true, never on `movable === "root_only"`, which is also what the InsightFace
 * packs say and they have no relocate route yet.
 *
 * @param {number} folderId - the folder to move, from `GET /model-folders`.
 * @param {string} path - an absolute host path. Created if it does not exist.
 * @returns {Promise<Object>} the first status snapshot.
 */
export async function relocateModelFolder(folderId, path) {
  return unwrap(
    apiClient.post(`/model-folders/${folderId}/relocate`, { path }),
  );
}

/**
 * How the current or last move is going.
 *
 * `status` is `running`, `finished`, or `idle` when none has ever run. The last
 * finished job is kept, so a client that was not watching can still read the
 * outcome.
 *
 * @returns {Promise<Object>} the status snapshot.
 */
export async function getModelMoveStatus() {
  return unwrap(apiClient.get("/model-moves"));
}

/**
 * Stop the queue between files.
 *
 * **Rolls nothing back.** The files already moved stay moved; that is the
 * ruling, and it is the only answer that does not need its own crash-window
 * argument for an undo the shelf does not have.
 *
 * @returns {Promise<Object>} the status snapshot after the request.
 */
export async function cancelModelMove() {
  return unwrap(apiClient.delete("/model-moves"));
}
