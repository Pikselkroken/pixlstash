// Which GPU PixlStash runs on - GET /compute/gpus and PATCH /compute/gpu.
//
// Choosing one stages the change; the server restarts to use it, so the caller
// says so (desktop restarts itself, headless shows "Restart PixlStash").

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Every GPU this machine has, what uses each, and which one PixlStash uses.
 *
 * @returns {Promise<{gpus: Array<Object>, selected: string, resolved: ?string,
 *   auto_reason: string, suggestions: string[], restart_required: boolean}>}
 */
export async function getComputeGpus() {
  return unwrap(apiClient.get("/compute/gpus"));
}

/**
 * Choose the GPU: `"auto"` or a GPU's uuid. 422 for an unknown uuid or an
 * integrated GPU.
 *
 * @param {"auto" | string} gpu
 * @returns {Promise<{status: string, selected: string, restart_required: boolean}>}
 */
export async function setComputeGpu(gpu) {
  return unwrap(apiClient.patch("/compute/gpu", { gpu }));
}
