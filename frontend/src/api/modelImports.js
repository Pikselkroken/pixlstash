// Taking a training run onto the shelf - /model-folders/{id}/runs and
// /model-imports.
//
// **Listing a run costs nothing and changes nothing.** The listing reads
// filenames and one `config.yaml` per run; it does not hash, copy, move or
// write, which is what lets the whole card grid be drawn for an output root
// before the user has decided about any of it. Do not add a call here that
// erodes that.
//
// The import is the committing half and runs the same ordering as a move: copy,
// verify by SHA-256, register the row and commit, then unlink. It shares the
// move's single job slot, so a move already running makes it a 409.

import { API_BASE_URL, apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Describe every training run under a registered `source` folder.
 *
 * @param {number} folderId - a `source` folder. Any other kind is a 400: a
 *   folder catalogued in place is a library of models, not a place runs are
 *   taken from.
 * @returns {Promise<Array<Object>>} the `runs` array.
 */
export async function listRuns(folderId) {
  const body = await unwrap(apiClient.get(`/model-folders/${folderId}/runs`));
  return Array.isArray(body?.runs) ? body.runs : [];
}

/**
 * The URL of one preview image, for an `<img src>`.
 *
 * A URL rather than a fetch: the browser's own image loading handles caching,
 * decoding and lazy loading, and a run can carry 130 samples.
 *
 * Both segments are encoded because they are **names**, not paths - the server
 * joins each to a registered path and refuses anything that resolves outside.
 *
 * @param {number} folderId
 * @param {string} runName
 * @param {string} filename - as `listRuns` named it, inside `samples/`.
 * @returns {string}
 */
export function runSampleUrl(folderId, runName, filename) {
  // An <img src> never reaches the apiClient interceptor, so the API base has
  // to be spelled out here rather than left for axios to prepend.
  return (
    `${API_BASE_URL}/model-folders/${folderId}/runs` +
    `/${encodeURIComponent(runName)}/samples/${encodeURIComponent(filename)}`
  );
}

/**
 * Import a run's checkpoints onto the shelf as one stack.
 *
 * @param {Object} options
 * @param {number} options.sourceFolderId - the registered output root.
 * @param {string} options.runName - a run inside it, by name.
 * @param {number} options.destinationFolderId - a folder the shelf catalogues.
 * @param {Array<number|null>} [options.steps] - which checkpoints, by step,
 *   with `null` for the bare final. Omit for the whole run.
 * @returns {Promise<Object>} the import report: `stack_id`, `deleted_source`
 *   and a per-file `files` array. Each file carries `sample_count` - the run's
 *   previews copied in beside that checkpoint - and a `detail` that says why
 *   they did not come when it is zero. A failed preview copy leaves the file
 *   `imported`: losing a preview must not cost the weights.
 */
export async function importRun({
  sourceFolderId,
  runName,
  destinationFolderId,
  steps,
}) {
  const body = {
    source_folder_id: sourceFolderId,
    run_name: runName,
    destination_folder_id: destinationFolderId,
  };
  if (steps) body.steps = steps;
  return unwrap(apiClient.post("/model-imports", body));
}
