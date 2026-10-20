// LoRA training resource - /training/* and /loras*, plus /workers/progress for
// the VRAM hint. Every route is owner-only on the backend (routes/training.py).
// The ai-toolkit paths are ordinary user config, so they go through api/config.js.
//
// URLs are written WITHOUT the `/api/v1` prefix: the apiClient request
// interceptor prepends it.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** The shipped and user training templates. */
export function listTrainingTemplates() {
  return unwrap(apiClient.get("/training/templates"));
}

/** Live worker and GPU usage; the panel reads its VRAM figures from it. */
export function getWorkerProgress() {
  return unwrap(apiClient.get("/workers/progress"));
}

/** Queue a training job (XOR set_id / character_id in the body). */
export function createTrainingJob(body) {
  return unwrap(apiClient.post("/training/jobs", body));
}

/** Every training job, newest first. */
export function listTrainingJobs() {
  return unwrap(apiClient.get("/training/jobs"));
}

/** One training job, including its loss history. */
export function getTrainingJob(jobId) {
  return unwrap(apiClient.get(`/training/jobs/${jobId}`));
}

/** Request a stop; the job finishes its current step and reports `stopped`. */
export function cancelTrainingJob(jobId) {
  return unwrap(apiClient.post(`/training/jobs/${jobId}/cancel`, {}));
}

/** Delete a job, its work directory and the LoRAs it produced. */
export function deleteTrainingJob(jobId) {
  return unwrap(apiClient.delete(`/training/jobs/${jobId}`));
}

/** The trained-LoRA registry. */
export function listLoras() {
  return unwrap(apiClient.get("/loras"));
}

/** One trained LoRA. */
export function getLora(loraId) {
  return unwrap(apiClient.get(`/loras/${loraId}`));
}

/** Delete a trained LoRA's file and registry row. */
export function deleteLora(loraId) {
  return unwrap(apiClient.delete(`/loras/${loraId}`));
}

/** The absolute path of a LoRA file, for "copy path". */
export function getLoraFilePath(loraId) {
  return unwrap(apiClient.get(`/loras/${loraId}/file-path`));
}
