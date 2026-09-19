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

/**
 * The Workflows grid: one card per stack, plus what it left out (v1.12 B3).
 *
 * @returns {Promise<{cards: Array<Object>, one_offs: number, hidden: number}>}
 */
export async function listWorkflowCards() {
  const body = await unwrap(apiClient.get("/workflows/cards"));
  return {
    cards: Array.isArray(body?.cards) ? body.cards : [],
    one_offs: body?.one_offs ?? 0,
    hidden: body?.hidden ?? 0,
  };
}

/**
 * One card opened — the only way to read a stack member, which the grid never
 * lists: it draws the cover alone and names the rest in `member_keys`.
 *
 * @param {string} workflowKey
 * @returns {Promise<{card: Object, notes: ?string, hidden: boolean, variants: Array<Object>, pins: ?Array<Object>}>}
 */
export async function getWorkflowCard(workflowKey) {
  return unwrap(
    apiClient.get(`/workflows/cards/${encodeURIComponent(workflowKey)}`),
  );
}

/**
 * Edit a card's own attributes (v1.12 B4). Answers with the card re-read.
 *
 * `null` for `name` or `notes` clears it, which is not the same as leaving
 * the field out — so callers pass only what they mean to change.
 *
 * @param {string} workflowKey
 * @param {{name?: ?string, notes?: ?string, hidden?: boolean}} changes
 * @returns {Promise<Object>} the same shape `getWorkflowCard` returns
 */
export async function patchWorkflowCard(workflowKey, changes) {
  return unwrap(
    apiClient.patch(
      `/workflows/${encodeURIComponent(workflowKey)}`,
      changes,
    ),
  );
}

/**
 * Mark a card's LoRA slots structural or recipe.
 *
 * **This re-keys the card**, and may split it into several or merge it into
 * another: `key` in the answer is where the card addressed here now lives,
 * and the caller has to follow it rather than keep the key it sent.
 *
 * @param {string} workflowKey
 * @param {Object<string, string>} marks `{slot_label: "structural"|"recipe"}`
 * @returns {Promise<{key: string, moved: Object}>}
 */
export async function setWorkflowSlots(workflowKey, marks) {
  return unwrap(
    apiClient.put(`/workflows/${encodeURIComponent(workflowKey)}/slots`, {
      marks,
    }),
  );
}

/**
 * Replace a card's whole set of parameter overrides. Answers with the detail.
 *
 * Whole, not one: an empty list is "reset everything", and resetting one
 * value means sending the others back unchanged.
 *
 * @param {string} workflowKey
 * @param {Array<{slot_label: string, input_name: string, value: *}>} defaults
 * @returns {Promise<Object>}
 */
export async function setWorkflowDefaults(workflowKey, defaults) {
  return unwrap(
    apiClient.put(`/workflows/${encodeURIComponent(workflowKey)}/defaults`, {
      defaults,
    }),
  );
}

/**
 * Replace a card's whole set of pinned parameters.
 *
 * `null` forgets the choice, so the client's default pins apply again; `[]`
 * is somebody who unpinned everything, and the two are kept apart.
 *
 * @param {string} workflowKey
 * @param {?Array<{slot_label: string, input_name: string}>} pins
 * @returns {Promise<{pins: ?Array<Object>}>}
 */
export async function setWorkflowPins(workflowKey, pins) {
  return unwrap(
    apiClient.put(`/workflows/${encodeURIComponent(workflowKey)}/pins`, {
      pins,
    }),
  );
}

/**
 * Put several cards in one stack. `keys[0]` becomes the cover.
 *
 * @param {Array<string>} keys
 * @returns {Promise<{stack_id: ?string, keys: Array<string>}>}
 */
export async function stackWorkflows(keys) {
  return unwrap(apiClient.post("/workflows/stacks", { keys }));
}
