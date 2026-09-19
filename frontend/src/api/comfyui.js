// ComfyUI resource - /comfyui/*.
//
// PixlStash's own backend proxies ComfyUI; these are PixlStash routes, not
// calls to a ComfyUI server. Paths are relative: the shared apiClient adds the
// /api/v1 prefix and the backend origin, injects the share token on same-origin
// absolute URLs, and leaves foreign hosts alone.

import { apiClient } from "../utils/apiClient";
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
  return unwrap(
    apiClient.delete(comfyUrl(`/workflows/${encodeURIComponent(name)}`)),
  );
}

/**
 * Each picture input of a saved workflow and how it is filled.
 *
 * `mode` is `selection` (the grid's selection fills it; at most one), `picker`
 * (asked at run time) or `fixed` (one picture, `picture_id`, chosen at setup).
 * `picture_missing` is a fixed input whose picture has left this library.
 *
 * `lora_slots` is every LoRA a run can swap (`by` is `filename` for a core
 * loader, `digest` for a ComfyUI-PixlStash one); empty means the graph has no
 * LoRA loader to swap, and a run adds one only with `insert_lora_loader: true`
 * (see `getLoraInsertion`).
 *
 * @param {string} name - the workflow's `name` as listed.
 * @returns {Promise<{workflow: string, inputs: Array<Object>}>}
 */
export async function getWorkflowInputs(name) {
  return unwrap(
    apiClient.get(comfyUrl(`/workflows/${encodeURIComponent(name)}/inputs`)),
  );
}

/**
 * Where a LoRA loader would be added to a saved workflow that has none (#1376).
 *
 * `plan` is `{model, clip, rewires, pixlstash_loader}`: the node the loader
 * takes the model from (and the CLIP, `null` for a model-only loader), every
 * input it would rewire, and whether the loader may be the ComfyUI-PixlStash
 * one, which leaves the pictures un-replayable by "Generate variants". It is `null` when no loader can be added, and `reason` says why;
 * `has_lora_loader` is true when there is a loader to swap instead. Owner-only,
 * since it asks the owner's ComfyUI.
 *
 * @param {string} name - the workflow's `name` as listed.
 * @returns {Promise<{plan: Object|null, reason: string|null, has_lora_loader: boolean}>}
 */
export async function getLoraInsertion(name) {
  return unwrap(
    apiClient.get(
      comfyUrl(`/workflows/${encodeURIComponent(name)}/lora-insertion`),
    ),
  );
}

/**
 * Run a saved workflow, filling each picture input by its mode.
 *
 * A workflow with a Selection input runs once per id in `picture_ids`; one
 * without takes no `picture_ids` and runs once.
 *
 * @param {string} name - the workflow's `name` as listed.
 * @param {Object} body - `{picture_ids?, pictures?: [{node_id, picture_id}],
 *   caption?, values?, seed_mode?, seed?, stack?, client_id?, set_id?,
 *   project_id?, character_id?, adapter_sha256?}`. `adapter_sha256` puts that
 *   shelf LoRA into every LoRA slot of the graph; a workflow with none refuses
 *   the run.
 * @returns {Promise<{status: string, workflow: string,
 *   prompts: Array<{picture_id: ?number, prompt_id: string}>}>}
 */
export async function runWorkflow(name, body) {
  return unwrap(
    apiClient.post(
      comfyUrl(`/workflows/${encodeURIComponent(name)}/run`),
      body,
    ),
  );
}

/**
 * Import a workflow file as it is, UI or API format.
 *
 * A copy of a workflow already stored comes back `matched` under the stored
 * name. A name taken by a different workflow is refused (409) unless
 * `overwrite` replaces it or `keepBoth` stores this one as "name (2)".
 *
 * @param {Object} body
 * @param {string} body.name
 * @param {Object} body.workflow - the parsed file, unchanged.
 * @param {boolean} [body.overwrite=false]
 * @param {boolean} [body.keepBoth=false]
 * @returns {Promise<{name: string, matched: boolean, topology_hash: ?string}>}
 */
export async function importWorkflow({
  name,
  workflow,
  overwrite = false,
  keepBoth = false,
}) {
  return unwrap(
    apiClient.post(comfyUrl("/workflows/import"), {
      name,
      workflow,
      overwrite,
      keep_both: keepBoth,
    }),
  );
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
  return unwrap(apiClient.get(comfyUrl(`/pictures/${pictureId}/workflow`)));
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
 * `lora_insertion` is `{plan, reason}` where `lora_slots` is empty: where a
 * LoRA loader would be added (#1376), or why none can be.
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
export async function getPictureRecipe(pictureId, { preflight = true } = {}) {
  const url = comfyUrl(`/pictures/${pictureId}/recipe`);
  // `preflight: false` skips the backend's ComfyUI `/object_info` read, so the
  // answer costs one file read and no network. The lightbox's Recipe tab asks
  // that way because it re-reads on every filmstrip step; the Remix dialog
  // keeps the pre-flight, because it is about to run the thing and needs to
  // know whether it can. The default call passes no config at all, so it stays
  // exactly the request it has always been.
  return unwrap(
    preflight
      ? apiClient.get(url)
      : apiClient.get(url, { params: { preflight: false } }),
  );
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
 * @param {string} [body.adapter_sha256] - a shelf LoRA to put into the graph.
 * @param {boolean} [body.insert_lora_loader] - add a loader where the graph has
 *   none, as `getPictureRecipe`'s `lora_insertion.plan` showed. Needs
 *   `adapter_sha256`; without the flag such a graph is refused.
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
