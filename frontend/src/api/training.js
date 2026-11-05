// LoRA training resource - /training/*. Every route is owner-only on the backend (routes/training.py).
// The ai-toolkit paths are ordinary user config, so they go through api/config.js.
//
// URLs are written WITHOUT the `/api/v1` prefix: the apiClient request
// interceptor prepends it.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";
import { downloadExport } from "./pictures";

/** The training recipes, each saying whether the saved checkout can run it. */
export function listTrainingPresets() {
  return unwrap(apiClient.get("/training/presets"));
}

/**
 * Ask pixlstash.dev for newer recipes; the backend fetches and verifies them.
 * `automatic` is the daily check, a no-op unless the owner turned it on.
 */
export function checkTrainingPresets(automatic = false) {
  return unwrap(
    apiClient.post("/training/presets/check", null, { params: { automatic } }),
  );
}

/**
 * What a run of a set or character would hold, and the checks before Start.
 * `set_id` with `character_id` means that character's reference set.
 */
export function getTrainingDataset(params) {
  return unwrap(apiClient.get("/training/dataset", { params }));
}

/**
 * A character's pictures ranked for a LoRA training set: stills and clips,
 * each a best-first list a set takes a prefix of. `include` lets a set-aside
 * reason back in: {other_people, near_duplicates, low_quality, low_likeness}.
 */
export function fetchCharacterSetSuggestion(characterId, include = {}) {
  const params = { character_id: characterId };
  for (const [reason, on] of Object.entries(include)) {
    if (on) params[`include_${reason}`] = true;
  }
  return unwrap(apiClient.get("/training/character_set_suggestion", { params }));
}

/** The sample prompts written from the set's or character's captions. */
export function getTrainingSamplePrompts(params) {
  return unwrap(apiClient.get("/training/sample-prompts", { params }));
}

/** Preview what a run would use on this machine, without queueing it. */
export function resolveTraining(body) {
  return unwrap(apiClient.post("/training/resolve", body));
}

/**
 * Queue a training job: set_id or character_id, or both for a character's
 * reference set.
 */
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

/**
 * What the Training runs view draws for one job: phase, checkpoint columns,
 * prompt rows with the sample in each cell, loss, cover, speed and memory.
 */
export function getTrainingJobLive(jobId) {
  return unwrap(apiClient.get(`/training/jobs/${jobId}/live`));
}

/**
 * Make the checkpoint at `step` the run's stack cover (and so its set's LoRA).
 * Before the run is on the shelf the step is kept and applied at its import.
 */
export function setTrainingJobCover(jobId, step) {
  return unwrap(apiClient.put(`/training/jobs/${jobId}/cover`, { step }));
}

/**
 * What a run trained on, as a zip: its images, their frozen captions and
 * snapshot.json. Resolves to `{ blob, filename }`.
 */
export function exportTrainingSnapshot(jobId) {
  return downloadExport(`/training/jobs/${jobId}/snapshot/export`);
}

/** The steps a run kept that it can be trained on from, newest first. */
export function getTrainLongerOptions(jobId) {
  return unwrap(apiClient.get(`/training/jobs/${jobId}/train-longer`));
}

/** Queue the same run to train on from `fromStep`, `moreSteps` further. */
export function trainJobLonger(jobId, fromStep, moreSteps) {
  return unwrap(
    apiClient.post(`/training/jobs/${jobId}/train-longer`, {
      from_step: fromStep,
      more_steps: moreSteps,
    }),
  );
}

/** Request a stop; the job finishes its current step and reports `stopped`. */
export function cancelTrainingJob(jobId) {
  return unwrap(apiClient.post(`/training/jobs/${jobId}/cancel`, {}));
}

/** Let PixlStash quit a local ComfyUI so this queued run fits (#2558). */
export function quitComfyuiForTrainingJob(jobId) {
  return unwrap(apiClient.post(`/training/jobs/${jobId}/quit-comfyui`, {}));
}

/** Delete a job and its work directory; its LoRA stays on the shelf. */
export function deleteTrainingJob(jobId) {
  return unwrap(apiClient.delete(`/training/jobs/${jobId}`));
}

/** Find an ai-toolkit checkout on this computer; `searched` lists where it looked. */
export function discoverAiToolkit() {
  return unwrap(apiClient.get("/training/ai-toolkit/discover"));
}

/**
 * Run the trainer's own start-up on a checkout and its python. A path that is
 * not a checkout is refused with 422 and `detail`.
 */
export function checkAiToolkit({ repo_path, python }) {
  return unwrap(
    apiClient.post("/training/ai-toolkit/check", { repo_path, python }),
  );
}

/**
 * Save the checkout and the Python that runs its trainer; both null disconnects.
 * Host-local: the server refuses a remote owner unless host operations are on.
 */
export function putAiToolkitConfig({ repo_path, python }) {
  return unwrap(
    apiClient.put("/training/ai-toolkit/config", { repo_path, python }),
  );
}

/** The saved install's version, and its dataset and runs folders. */
export function getAiToolkitStatus() {
  return unwrap(apiClient.get("/training/ai-toolkit/status"));
}
