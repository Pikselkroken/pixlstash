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
 * Put a stored workflow, user or built-in, on its Workflows card.
 *
 * A built-in has no card until something asks, so this is how the Run popup
 * gets a key to open on. Idempotent; owner-only.
 *
 * @param {string} name - the workflow's `name` as listed.
 * @returns {Promise<{name: string, workflow_key: string}>}
 */
export async function cardForWorkflow(name) {
  return unwrap(
    apiClient.post(comfyUrl(`/workflows/${encodeURIComponent(name)}/card`)),
  );
}

/**
 * Where a LoRA loader would be added to a saved workflow that has none (#1376).
 *
 * `plan` is `{model, clip, rewires, pixlstash_loader}`: the node the loader
 * takes the model from (and the CLIP, `null` for a model-only loader), every
 * input it would rewire, and whether the loader may be the ComfyUI-PixlStash
 * one, which needs the node pack installed. It is `null` when no loader can be added, and `reason` says why;
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
 * Start pulling every workflow the configured ComfyUI has saved (#1440).
 *
 * Answers at once with `{status: "started"|"already_running", task_id}`; the
 * pull runs as a server task. Read how it went from {@link getWorkflowPull}.
 *
 * @returns {Promise<{status: string, task_id: ?string}>}
 */
export async function startWorkflowPull() {
  return unwrap(apiClient.post(comfyUrl("/workflows/pull")));
}

/**
 * The most recent pull since the server started.
 *
 * `status` is `idle`, `pending`, `running`, `completed` or `failed`;
 * `summary` is set once it completed and `error` once it failed.
 *
 * @returns {Promise<{status: string, task_id: ?string, comfyui_url: ?string,
 *   error: ?string, summary: ?Object}>}
 */
export async function getWorkflowPull() {
  return unwrap(apiClient.get(comfyUrl("/workflows/pull")));
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
 * A picture carrying only ComfyUI's **editor** graph and not the API one the
 * server ran answers here too: the editor graph is rebuilt into an API prompt
 * against `/object_info` and the answer is the same shape, with
 * `converted_from_editor_graph: true`. A rebuild that could not be exact is
 * not approximated - it answers `available: false` with `reason:
 * "editor_graph"`, its prompt and models still filled in, and
 * `conversion_problems` holding one sentence per thing that could not be read.
 * That is the one answer `preflight: false` still costs a ComfyUI read for,
 * because without the node list there is nothing to report at all.
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
 * Ask the backend to abort the in-flight ComfyUI run.
 * @returns {Promise<Object>} the response body.
 */
export async function abortRun() {
  return unwrap(apiClient.post(comfyUrl("/abort")));
}
