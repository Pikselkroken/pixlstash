// The workflow library — /workflows (see `pixlstash/routes/workflows.py`).
//
// **Card level, not topology level.** The retired shelf opened at topology and
// this module fronted its reads; F1b (#1404) deleted the shelf and, with it,
// the last caller of `listWorkflows`, `listWorkflowVariants`,
// `listWorkflowPictures` and `getWorkflowGraph`. They are gone from here
// rather than kept warm for a screen that does not exist: the ROUTES stay, so
// F6's Export and F7's pictures link re-add the three lines they need against
// whatever shape those steps actually want.
//
// Every route here is owner-only: the counts are read across the whole vault,
// so a scoped session gets 403 rather than a narrowed answer.

import { apiClient, appendShareToken, API_BASE_URL } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The URL a browser loads one of a card's `covers` from.
 *
 * `_cover_urls` (`routes/workflows.py`) sends an API-RELATIVE path -
 * `/pictures/thumbnails/{id}.webp?v={version}` - and an `<img src>` bypasses
 * Axios entirely, so nothing prepends `/api/v1` and nothing appends the share
 * token. Used verbatim, the browser asks the PAGE origin for a path no route
 * serves and every cover on the Workflows grid breaks.
 *
 * Here rather than in the component for `pictureThumbnailUrl`'s stated reason:
 * the path is part of a contract, and a second spelling of it elsewhere is
 * exactly the drift the api layer exists to prevent. This is the second
 * spelling of THAT path, so the two live one file apart on purpose.
 *
 * @param {string} cover - a `covers` entry, exactly as the payload sends it.
 * @returns {string}
 */
export function workflowCoverUrl(cover) {
  return appendShareToken(`${API_BASE_URL}${cover}`);
}

/**
 * The Workflows grid: one card per stack, plus what it left out (v1.12 B3).
 *
 * `one_offs` and `hidden` are counted over the same sets whatever the two
 * flags say, so the Filters panel can label a ticked checkbox with the number
 * it is letting in (F7).
 *
 * @param {{includeHidden?: boolean, includeOneOffs?: boolean}} [options]
 * @returns {Promise<{cards: Array<Object>, one_offs: number, hidden: number}>}
 */
export async function listWorkflowCards({
  includeHidden = false,
  includeOneOffs = false,
} = {}) {
  const params = new URLSearchParams();
  if (includeHidden) params.set("include_hidden", "true");
  if (includeOneOffs) params.set("include_one_offs", "true");
  const query = params.size ? `?${params}` : "";
  const body = await unwrap(apiClient.get(`/workflows/cards${query}`));
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
    apiClient.patch(`/workflows/${encodeURIComponent(workflowKey)}`, changes),
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

/**
 * Set a stack's member order; `keys[0]` becomes the cover (v1.12 F2).
 *
 * The route refuses anything but a COMPLETE ordered list of what the stack
 * holds: a key left out would be deleted from the stack with no record that it
 * left, and a key added is `POST /workflows/stacks`' gesture rather than this
 * one. So callers reorder the list they already have and send all of it.
 *
 * @param {string} stackId — `stack_id` from the card, or `auto:<core hash>`.
 * @param {Array<string>} keys
 * @returns {Promise<{stack_id: ?string, keys: Array<string>}>}
 */
export async function reorderStack(stackId, keys) {
  return unwrap(
    apiClient.put(`/workflows/stacks/${encodeURIComponent(stackId)}/order`, {
      keys,
    }),
  );
}

/**
 * Stand one card on its own. Its stack dissolves if that leaves one card.
 *
 * @param {string} workflowKey
 * @returns {Promise<{stack_id: ?string, keys: Array<string>}>}
 */
export async function unstackWorkflow(workflowKey) {
  return unwrap(
    apiClient.post(`/workflows/${encodeURIComponent(workflowKey)}/unstack`),
  );
}
