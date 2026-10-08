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
 * @returns {Promise<{name: string, workflow_id: string}>}
 */
export async function cardForWorkflow(name) {
  return unwrap(
    apiClient.post(comfyUrl(`/workflows/${encodeURIComponent(name)}/card`)),
  );
}

/**
 * Import a workflow file as it is, UI or API format.
 *
 * Every import is a NEW manual workflow (identical copies are allowed), and
 * the answer names it: open it by `workflow_id`.
 *
 * @param {Object} body
 * @param {string} body.name
 * @param {Object} body.workflow - the parsed file, unchanged.
 * @returns {Promise<{status: string, name: string, matched: boolean, workflow_id: string}>}
 */
export async function importWorkflow({ name, workflow }) {
  return unwrap(
    apiClient.post(comfyUrl("/workflows/import"), { name, workflow }),
  );
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

/**
 * Whether the configured ComfyUI can open a PixlStash workflow link: it needs
 * the ComfyUI-PixlStash node's `open_workflow.js`. Owner-only.
 * @returns {Promise<{can_open_workflows: ?boolean}>} `null` when ComfyUI
 *   cannot be asked.
 */
export async function getPixlstashNode() {
  return unwrap(apiClient.get(comfyUrl("/pixlstash-node")));
}

/**
 * Ask the backend whether a ComfyUI answers at `url`. Owner-only.
 *
 * Always resolves for a well-formed URL, reachable or not; rejects (400) only
 * for a malformed one. Save the reply's `url`, which is normalised.
 *
 * @param {string} url - e.g. `"http://127.0.0.1:8188/"`.
 * @returns {Promise<{reachable: boolean, url: string, version: ?string,
 *   detail: ?string}>}
 */
export async function probeComfyui(url) {
  return unwrap(apiClient.post(comfyUrl("/probe"), { url }));
}

/**
 * Whether this PixlStash is linked to the saved ComfyUI. Owner-only.
 *
 * @returns {Promise<{linked: boolean, comfyui_url: ?string,
 *   pixlstash_url: ?string, where: ?("this_computer"|"local_network"),
 *   linked_at: ?string}>}
 */
export async function getComfyuiLink() {
  return unwrap(apiClient.get(comfyUrl("/link")));
}

/**
 * Link the saved ComfyUI: mint a full-access token and write it, with
 * PixlStash's URL, into ComfyUI's settings. Owner-only.
 *
 * Always resolves 200 whether or not it linked. `steps` is exactly four, in
 * order `reach`, `nodes`, `link`, `check`, each `{id, state, detail, reason}`
 * with `state` one of `done`, `failed`, `needs_you`, `not_run`.
 * `can_install_pack` is true when this PixlStash carries the nodes and the
 * saved ComfyUI is on this computer, so {@link installComfyuiPack} can run.
 *
 * @returns {Promise<{linked: boolean, link: Object, steps: Array<Object>,
 *   can_install_pack: boolean}>}
 */
export async function linkComfyui() {
  return unwrap(apiClient.post(comfyUrl("/link")));
}

/**
 * Revoke the ComfyUI token and clear ComfyUI's copy, best-effort. Owner-only.
 * @returns {Promise<{linked: false}>}
 */
export async function unlinkComfyui() {
  return unwrap(apiClient.delete(comfyUrl("/link")));
}

/**
 * Install the ComfyUI-PixlStash nodes this PixlStash carries into the saved
 * ComfyUI, which must be on this computer. Owner-only, local caller only.
 *
 * Older copies go to the system trash (`replaced`). `restart` is `"requested"`
 * when ComfyUI-Manager was asked to restart ComfyUI, `"manual"` when the
 * person has to (`detail` says why). Rejects 409 when it cannot install.
 *
 * @returns {Promise<{installed_to: string, version: string,
 *   replaced: Array<string>, restart: ("requested"|"manual"),
 *   detail: ?string}>}
 */
export async function installComfyuiPack() {
  return unwrap(apiClient.post(comfyUrl("/pack/install")));
}
