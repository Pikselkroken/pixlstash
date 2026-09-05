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
