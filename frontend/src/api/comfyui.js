// ComfyUI resource - /comfyui/*.
//
// PixlStash's own backend proxies ComfyUI; these are PixlStash routes, not
// calls to a ComfyUI server. Paths are relative: the shared apiClient adds the
// /api/v1 prefix and the backend origin, injects the share token on same-origin
// absolute URLs, and leaves foreign hosts alone.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Build a ComfyUI route, optionally under an explicit backend base.
 * @param {string} path - the route below `/comfyui`, e.g. `"/workflows"`.
 * @returns {string}
 */
function comfyUrl(path) {
  return `/comfyui${path}`;
}

/**
 * List the saved ComfyUI workflows.
 * @returns {Promise<Object>} the response body, whose `workflows` is the list.
 */
export async function listWorkflows() {
  return unwrap(apiClient.get(comfyUrl("/workflows")));
}

/**
 * Delete one saved workflow by its file name.
 * @param {string} name - the workflow's `name` as listed (URL-encoded here).
 * @returns {Promise<Object>} the response body.
 */
export async function deleteWorkflow(name) {
  return unwrap(apiClient.delete(
    comfyUrl(`/workflows/${encodeURIComponent(name)}`),
  ));
}

/**
 * Import a workflow graph, optionally replacing one of the same name.
 *
 * `overwrite` is the caller's answer to the "already exists" prompt; sending
 * it false means the server refuses rather than silently replacing a workflow.
 *
 * @param {Object} body
 * @param {string} body.name
 * @param {Object} body.workflow - the graph, with placeholders already applied.
 * @param {boolean} [body.overwrite=false]
 * @returns {Promise<Object>} the response body.
 */
export async function importWorkflow(
  { name, workflow, overwrite = false },
) {
  return unwrap(apiClient.post(comfyUrl("/workflows/import"), {
    name,
    workflow,
    overwrite,
  }));
}

/**
 * Run an image-to-image workflow over a set of pictures.
 *
 * @param {Object} body
 * @param {Array<number|string>} body.picture_ids
 * @param {string} body.workflow_name
 * @param {string} [body.caption]
 * @param {string} [body.client_id] - ties progress events back to this tab.
 * @param {boolean} [body.stack] - stack the outputs with their source.
 * @returns {Promise<Object>} the response body, whose `prompts` are the queued
 *   ComfyUI prompt ids.
 */
export async function runImageToImage(body) {
  return unwrap(apiClient.post(comfyUrl("/run_i2i"), body));
}

/**
 * Read the ComfyUI workflow embedded in a generated picture.
 *
 * Rejects with a 404 when the picture carries no workflow, which is the normal
 * case for imported photos rather than an error.
 *
 * @param {number|string} pictureId
 * @returns {Promise<Object>} the response body: the graph plus its summary,
 *   prompt, models and LoRAs.
 */
export async function getPictureWorkflow(pictureId) {
  return unwrap(apiClient.get(
    comfyUrl(`/pictures/${pictureId}/workflow`),
  ));
}

/**
 * Read whether a picture carries a replayable ComfyUI recipe.
 *
 * The response is
 * `{available, reason, summary, positive_prompt, seed, models, loras,
 * node_count, node_classes, source_is_imported, source_label, seed_inputs,
 * preflight}`. A picture with no recipe is a normal answer, not an error: the
 * call resolves with `available: false` and `reason: "no_prompt_chunk"` for
 * imported photos, so callers should read `available` rather than rely on a
 * rejection.
 *
 * `preflight` reports whether the recipe's models and LoRAs are present on the
 * ComfyUI server. `preflight.checked === false` means ComfyUI could not be
 * reached at all - it does NOT mean the recipe passed its checks.
 *
 * `node_classes` is the distinct list of ComfyUI node classes the graph would
 * execute. It is read from the file, so it is populated even when the
 * pre-flight could not run, which is exactly when the user has nothing else to
 * judge the graph by. `source_is_imported` / `source_label` say whether the
 * file came from outside this PixlStash instance, and by which route.
 *
 * @param {number|string} pictureId
 * @returns {Promise<Object>} the response body described above.
 */
export async function getPictureRecipe(pictureId) {
  return unwrap(apiClient.get(
    comfyUrl(`/pictures/${pictureId}/recipe`),
  ));
}

/**
 * Re-run a picture's own recipe to generate variants of it.
 *
 * The graph itself is never sent by the client: the backend re-extracts it from
 * the picture on every call, so a run always replays what the picture actually
 * carries rather than a copy the client may have gone stale on.
 *
 * @param {Object} body
 * @param {number|string} body.picture_id - the picture whose recipe to replay.
 * @param {string} body.seed_mode - how the seed is chosen for the variants.
 * @param {number} [body.seed] - the explicit seed, when `seed_mode` needs one.
 * @param {string} [body.client_id] - ties progress events back to this tab.
 * @param {boolean} [body.stack] - stack the outputs with their source.
 * @param {boolean} [body.allow_unchecked] - the user's explicit acknowledgement
 *   that they want to run a graph the server could not inspect. The backend
 *   refuses the run with a 400 without it whenever `preflight.checked` is
 *   false, so this must only ever be sent for a run the user acknowledged, and
 *   never as a constant.
 * @returns {Promise<Object>} the response body:
 *   `{status, prompts: [{picture_id, prompt_id}]}`.
 */
export async function runRecipe(body) {
  return unwrap(apiClient.post(comfyUrl("/run_recipe"), body));
}

/**
 * Run a text-to-image workflow.
 *
 * @param {Object} body - the prompt, workflow name, and the view context
 *   (`set_id`, `project_id`, `character_id`) the outputs should land in.
 * @returns {Promise<Object>} the response body, whose `prompts` are the queued
 *   ComfyUI prompt ids.
 */
export async function runTextToImage(body) {
  return unwrap(apiClient.post(comfyUrl("/run_t2i"), body));
}

/**
 * Ask the backend to abort the in-flight ComfyUI run.
 * @returns {Promise<Object>} the response body.
 */
export async function abortRun() {
  return unwrap(apiClient.post(comfyUrl("/abort")));
}
