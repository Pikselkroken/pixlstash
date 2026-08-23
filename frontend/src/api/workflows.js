// The workflow library — /workflows (see `pixlstash/routes/workflows.py`).
//
// The list opens at TOPOLOGY level: one row per graph, whatever it was bound
// to, because the owner's library holds ~192 of those against ~617 recipes and
// the second number is a list nobody reads. A row's recipes are its variants
// and are fetched only when the row is expanded.
//
// Every route here is owner-only: the counts are read across the whole vault,
// so a scoped session gets 403 rather than a narrowed answer.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The whole library, plus how far the extraction pass has read.
 *
 * @returns {Promise<{scan: {pictures: number, scanned: number}, workflows: Array<Object>}>}
 */
export async function listWorkflows() {
  const body = await unwrap(apiClient.get("/workflows"));
  return {
    scan: body?.scan ?? { pictures: 0, scanned: 0 },
    workflows: Array.isArray(body?.workflows) ? body.workflows : [],
  };
}

/**
 * The recipes filed under one topology — the row's expansion.
 *
 * @param {string} topologyHash
 * @returns {Promise<Array<Object>>}
 */
export async function listWorkflowVariants(topologyHash) {
  const body = await unwrap(
    apiClient.get(`/workflows/${encodeURIComponent(topologyHash)}/variants`),
  );
  return Array.isArray(body) ? body : [];
}

/**
 * Picture ids for the inspector's tiles, newest first.
 *
 * @param {string} topologyHash
 * @param {number} [limit=6]
 * @returns {Promise<Array<number>>}
 */
export async function listWorkflowPictures(topologyHash, limit = 6) {
  const body = await unwrap(
    apiClient.get(`/workflows/${encodeURIComponent(topologyHash)}/pictures`, {
      params: { limit },
    }),
  );
  return Array.isArray(body) ? body : [];
}

/**
 * One recipe's stored graph.
 *
 * **Not runnable in ComfyUI** — the stored document has its parameters, seeds
 * and prompts nulled and names its assets by an opaque reference, which is what
 * makes a workflow survive a purge of the pictures it made. `runnable` says so
 * in the payload; anything offering this as a download has to say so too.
 *
 * @param {string} structuralHash
 * @returns {Promise<{structural_hash: string, document: Object, runnable: boolean}>}
 */
export async function getWorkflowGraph(structuralHash) {
  return unwrap(
    apiClient.get(
      `/workflows/recipes/${encodeURIComponent(structuralHash)}/graph`,
    ),
  );
}
