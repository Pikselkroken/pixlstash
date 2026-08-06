// ComfyUI resource — /comfyui/*.
//
// PixlStash's own backend proxies ComfyUI; these are PixlStash routes, not
// calls to a ComfyUI server. Some call sites address them through an explicit
// backend base (a `backendUrl` prop) rather than a relative path, so every
// function here takes an optional `baseUrl` prefix and passes the resulting
// URL to the shared apiClient, which injects the share token on same-origin
// absolute URLs and leaves foreign hosts alone.

import { apiClient } from "../utils/apiClient";

/**
 * Build a ComfyUI route, optionally under an explicit backend base.
 * @param {string} path - the route below `/comfyui`, e.g. `"/workflows"`.
 * @param {string} [baseUrl=""] - backend base; empty for a relative URL.
 * @returns {string}
 */
function comfyUrl(path, baseUrl = "") {
  return `${baseUrl}/comfyui${path}`;
}

/**
 * List the saved ComfyUI workflows.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""] - explicit backend base, if the caller
 *   has one.
 * @returns {Promise<Object>} the response body, whose `workflows` is the list.
 */
export async function listWorkflows({ baseUrl = "" } = {}) {
  const res = await apiClient.get(comfyUrl("/workflows", baseUrl));
  return res.data;
}

/**
 * Delete one saved workflow by its file name.
 * @param {string} name - the workflow's `name` as listed (URL-encoded here).
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function deleteWorkflow(name, { baseUrl = "" } = {}) {
  const res = await apiClient.delete(
    comfyUrl(`/workflows/${encodeURIComponent(name)}`, baseUrl),
  );
  return res.data;
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
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function importWorkflow(
  { name, workflow, overwrite = false },
  { baseUrl = "" } = {},
) {
  const res = await apiClient.post(comfyUrl("/workflows/import", baseUrl), {
    name,
    workflow,
    overwrite,
  });
  return res.data;
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
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `prompts` are the queued
 *   ComfyUI prompt ids.
 */
export async function runImageToImage(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(comfyUrl("/run_i2i", baseUrl), body);
  return res.data;
}

/**
 * Read the ComfyUI workflow embedded in a generated picture.
 *
 * Rejects with a 404 when the picture carries no workflow, which is the normal
 * case for imported photos rather than an error.
 *
 * @param {number|string} pictureId
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: the graph plus its summary,
 *   prompt, models and LoRAs.
 */
export async function getPictureWorkflow(pictureId, { baseUrl = "" } = {}) {
  const res = await apiClient.get(
    comfyUrl(`/pictures/${pictureId}/workflow`, baseUrl),
  );
  return res.data;
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
 * reached at all — it does NOT mean the recipe passed its checks.
 *
 * `node_classes` is the distinct list of ComfyUI node classes the graph would
 * execute. It is read from the file, so it is populated even when the
 * pre-flight could not run, which is exactly when the user has nothing else to
 * judge the graph by. `source_is_imported` / `source_label` say whether the
 * file came from outside this PixlStash instance, and by which route.
 *
 * @param {number|string} pictureId
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""] - explicit backend base, if the caller
 *   has one.
 * @returns {Promise<Object>} the response body described above.
 */
export async function getPictureRecipe(pictureId, { baseUrl = "" } = {}) {
  const res = await apiClient.get(
    comfyUrl(`/pictures/${pictureId}/recipe`, baseUrl),
  );
  return res.data;
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
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{status, prompts: [{picture_id, prompt_id}]}`.
 */
export async function runRecipe(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(comfyUrl("/run_recipe", baseUrl), body);
  return res.data;
}

/**
 * Run a text-to-image workflow.
 *
 * @param {Object} body - the prompt, workflow name, and the view context
 *   (`set_id`, `project_id`, `character_id`) the outputs should land in.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `prompts` are the queued
 *   ComfyUI prompt ids.
 */
export async function runTextToImage(body, { baseUrl = "" } = {}) {
  const res = await apiClient.post(comfyUrl("/run_t2i", baseUrl), body);
  return res.data;
}

/**
 * Ask the backend to abort the in-flight ComfyUI run.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function abortRun({ baseUrl = "" } = {}) {
  const res = await apiClient.post(comfyUrl("/abort", baseUrl));
  return res.data;
}
