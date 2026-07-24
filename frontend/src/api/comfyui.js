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
