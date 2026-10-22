// Downloads of the models a workflow is missing - POST /model-downloads.
//
// A missing model the run pre-flight marks `downloadable` is one ComfyUI's
// template list links; the server looks the link up from the name, so only
// the name is sent. Progress is `downloads` on GET /workers/progress, which
// `useTasksStore` polls.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Start downloading one missing model into the download folder.
 *
 * @param {string} file - the name as the workflow's loader spells it.
 * @returns {Promise<Object>} the download: `id`, `file`, `folder_path`, `status`.
 */
export async function startModelDownload(file) {
  return unwrap(apiClient.post("/model-downloads", { file }));
}

/**
 * Stop a running download; what it had fetched is discarded.
 *
 * @param {string} id
 * @returns {Promise<Object>} `{cancelled}`, false when it was no longer running.
 */
export async function cancelModelDownload(id) {
  return unwrap(
    apiClient.post(`/model-downloads/${encodeURIComponent(id)}/cancel`),
  );
}
