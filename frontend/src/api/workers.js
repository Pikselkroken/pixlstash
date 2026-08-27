// Background-worker progress resource - GET /workers/progress.
//
// This is a poll endpoint: `useTasksStore` is the app's single poller and
// fans the result out to the Tasks tab, the activity light, and the thumbnail
// upgrade banner. Settings reads it once to show current worker state.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Read the current progress of every background worker.
 * @returns {Promise<Object>} the response body: per-worker progress records.
 */
export async function getWorkerProgress() {
  return unwrap(apiClient.get("/workers/progress"));
}
