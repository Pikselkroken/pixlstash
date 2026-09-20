// The loose-file path onto the shelf - POST /model-files (shelf plan F6).
//
// For a single adapter or checkpoint that is not part of a training run and
// does not deserve a registered folder of its own. The server copies it into
// the managed store and registers it there, so the row is on the shelf as the
// call returns - no rescan. It is a **copy**: the file the user picked stays
// where it is.
//
// It shares the one shelf I/O slot with a move and an import, so either of
// those already running makes this a 409.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Copy one model file into a folder the shelf catalogues, and register it.
 *
 * @param {string} path - the file on the machine running PixlStash, absolute.
 * @param {number} [destinationFolderId] - a registered folder. Omit for the
 *   managed store, which is the ruled default destination.
 * @returns {Promise<Object>} `model_id`, `filename`, `folder_id`, `folder_path`.
 */
export async function addModelFile(path, destinationFolderId = null) {
  const body = { path };
  if (destinationFolderId != null) {
    body.destination_folder_id = destinationFolderId;
  }
  return unwrap(apiClient.post("/model-files", body));
}

/**
 * Keep one copy of a model and remove the rest (#1439).
 *
 * The KEEPER is named, never the copies to remove: that is what makes "keep
 * one" structural rather than arithmetic, and it is why no call from here can
 * empty a model. The removed copies keep their shelf rows at `state: 'removed'`,
 * so a recipe naming one still resolves to the model and a run through PixlStash
 * is substituted onto the copy that is left.
 *
 * @param {Array<{model_id: number, folder_id: number, relpath: string}>} keep -
 *   one entry per model: the copy that stays.
 * @param {Object} [options]
 * @param {boolean} [options.permanent=false] - unlink rather than trash.
 * @param {boolean} [options.dryRun=false] - plan it and remove nothing, which is
 *   how the confirmation gets the refusals and the ComfyUI warning first.
 * @returns {Promise<Object>} `merged`, `files_removed`, `permanent`, `dry_run`,
 *   `trash_name`, `comfyui_reads`, `refused`.
 */
export async function mergeModelCopies(
  keep,
  { permanent = false, dryRun = false } = {},
) {
  return unwrap(
    apiClient.post("/model-files/merge", {
      keep,
      permanent,
      dry_run: dryRun,
    }),
  );
}
