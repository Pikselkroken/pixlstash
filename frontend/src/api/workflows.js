// The workflow library — /workflows (see `pixlstash/routes/workflows.py`).
//
// **Card level, not topology level.** The retired shelf opened at topology and
// this module fronted its reads; F1b (#1404) deleted the shelf and, with it,
// the last caller of `listWorkflows`, `listWorkflowVariants`,
// `listWorkflowPictures` and `getWorkflowGraph`. B9 (#1410) then deleted the
// topology routes themselves and moved the cards onto `/workflows`, so the
// grid and the detail are what this module fronts, with `exportWorkflow`,
// `duplicateWorkflow`, `deleteWorkflowFile` and `dissolveStack` at the foot of
// the file for the grid's own verb menu (#1455), and the LoRA chain editor's
// `getLoraChain` / `saveLoraChain` after them (#1478). `GET /workflows/{key}/
// pictures` is served and has no caller in the app - the picture grid reaches
// a card's pictures through `GET /pictures?workflow_key=`, which filters like
// every other facet - so there is deliberately no function for it here.
//
// Every route here is owner-only: the counts are read across the whole vault,
// so a scoped session gets 403 rather than a narrowed answer.

import { apiClient, appendShareToken, API_BASE_URL } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The URL a browser loads one of a card's `covers` from.
 *
 * `_covers` (`routes/workflows.py`) sends an API-RELATIVE path in `url` -
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
 * A `covers` entry is an OBJECT since #1465 - the URL plus the stored crop
 * rectangle the cell is cropped around - so this reads `url` off it. An entry
 * without one returns "" rather than the truthy `/api/v1undefined`, which is a
 * broken-image glyph where the caller meant "no picture".
 *
 * @param {Object} cover - a `covers` entry, exactly as the payload sends it.
 * @returns {string}
 */
export function workflowCoverUrl(cover) {
  if (!cover?.url) return "";
  return appendShareToken(`${API_BASE_URL}${cover.url}`);
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
  const body = await unwrap(apiClient.get(`/workflows${query}`));
  return {
    cards: Array.isArray(body?.cards) ? body.cards : [],
    one_offs: body?.one_offs ?? 0,
    hidden: body?.hidden ?? 0,
  };
}

/**
 * One card opened — the only way to read a stack member's whole card, which
 * the grid never lists: it draws the cover alone, naming the rest in
 * `member_keys` and `members` (each member's name and what sets it apart).
 *
 * @param {string} workflowKey
 * @returns {Promise<{card: Object, notes: ?string, hidden: boolean, variants: Array<Object>, pins: ?Array<Object>}>}
 */
export async function getWorkflowCard(workflowKey) {
  return unwrap(apiClient.get(`/workflows/${encodeURIComponent(workflowKey)}`));
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
 * Replace a card's whole picture-input setup, in the open library (#1457).
 *
 * **Whole, so write back only a set that was read**: a row left out is a row
 * deleted. The read is the run pre-flight's `RunGroup.picture_inputs`, which is
 * every input of the card with its stored mode laid over it. A `fixed` input
 * may name its picture by `picture_id`; the server stores that picture's
 * content, so the client never has to hold a `pixel_sha` it did not read.
 *
 * @param {string} workflowKey
 * @param {Array<{slot_label: string, input_name: string,
 *   mode: "selection"|"picker"|"fixed", pixel_sha?: ?string,
 *   picture_id?: ?number}>} inputs
 * @returns {Promise<{inputs: Array<Object>}>}
 */
export async function setWorkflowInputs(workflowKey, inputs) {
  return unwrap(
    apiClient.put(`/workflows/${encodeURIComponent(workflowKey)}/inputs`, {
      inputs,
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

/**
 * What a run would do, doing none of it (v1.12 B7).
 *
 * The same body `runWorkflowCard` takes. Every card the request resolves to
 * comes back as a group with the reasons it would not run; `reasons` empty is
 * the only thing that means "this would run".
 *
 * @param {Object} body - see `runWorkflowCard`.
 * @returns {Promise<{ok: boolean, runs: number, groups: Array<Object>}>}
 */
export async function preflightWorkflowRun(body) {
  return unwrap(apiClient.post("/workflows/run/preflight", body));
}

/**
 * Run a workflow card.
 *
 * Exactly ONE source: `picture_ids` ("run what made these"), `saved_recipe_id`
 * ("run this look") or `workflow_key` ("run this card"). `target` overrides
 * which card actually runs, which is how a stack's other member is chosen.
 * `prompt` / `negative` / `loras` / `values` are overrides applied to the graph
 * at run time and are never written back into it. `inputs` fills the card's
 * picture inputs and is usually empty: the server fills the one input a
 * selection can only mean on its own (#1457). `stack` files each new picture
 * behind the one it was made from.
 *
 * @param {Object} body
 * @returns {Promise<{status: string, runs: number, groups: Array<Object>, prompts: Array<Object>}>}
 */
export async function runWorkflowCard(body) {
  return unwrap(apiClient.post("/workflows/run", body));
}

/**
 * Dissolve a whole stack: every member stands on its own afterwards.
 *
 * The plural counterpart of {@link unstackWorkflow}, and addressed by the
 * STACK rather than by a card - `stack_id` off any member is what names it.
 *
 * @param {string} stackId
 * @returns {Promise<{stack_id: ?string, keys: Array<string>}>}
 */
export async function dissolveStack(stackId) {
  return unwrap(
    apiClient.post(`/workflows/stacks/${encodeURIComponent(stackId)}/unstack`),
  );
}

/**
 * This card as a ComfyUI file somebody else can open (v1.12 B8).
 *
 * Scrubbed by the server - prompts and seeds blanked, recipe LoRA slots
 * emptied, model names this machine does not hold left out - so what comes
 * back is the graph, not a run of it.
 *
 * @param {string} workflowKey
 * @returns {Promise<{filename: string, workflow: Object, removed: Array<string>, source: string}>}
 */
export async function exportWorkflow(workflowKey) {
  return unwrap(
    apiClient.get(`/workflows/${encodeURIComponent(workflowKey)}/export`),
  );
}

/**
 * Write this workflow into the user's workflow folder under a free name.
 *
 * Unscrubbed, unlike the export: the copy stays on this machine and is meant
 * to run. A card the library only knows from its pictures gets a file of its
 * own this way, so the answer's `workflow_key` can be a NEW card.
 *
 * @param {string} workflowKey
 * @returns {Promise<{name: string, workflow_key: ?string}>}
 */
export async function duplicateWorkflow(workflowKey) {
  return unwrap(
    apiClient.post(`/workflows/${encodeURIComponent(workflowKey)}/duplicate`),
  );
}

/**
 * What the Clone with new models dialog draws for one card.
 *
 * Without `checkpointId`: the model files the card's graph names (each
 * resolved to a shelf row where one fits) and the shelf's checkpoints, VAEs
 * and text encoders. With it: also the VAEs and text encoders recipes have run
 * beside that checkpoint, each saying which step of the widening answered
 * (`via`; `declared` when nothing has and only the file layout fits), and the LoRAs and ControlNets trained on another family (`flags`).
 * Call it on open and on each checkpoint choice, never per keystroke: the
 * server reads the whole shelf and every recipe to answer.
 *
 * @param {string} workflowKey
 * @param {{checkpointId?: number}} [options]
 * @returns {Promise<Object>}
 */
export async function readModelSwap(workflowKey, { checkpointId } = {}) {
  return unwrap(
    apiClient.get(`/workflows/${encodeURIComponent(workflowKey)}/model-swap`, {
      params: checkpointId == null ? {} : { checkpoint_id: checkpointId },
    }),
  );
}

/**
 * Write a copy of this workflow with model files replaced, as a new card.
 *
 * `swaps` maps the graph's filename to the one to load instead; every loader
 * naming it is rewritten. `verified` in the answer is false when ComfyUI was
 * not reachable and the names were written unchecked.
 *
 * @param {string} workflowKey
 * @param {{name: string, swaps: Object<string, string>}} body
 * @returns {Promise<{name: string, workflow_key: ?string, swapped: Array, unswapped: Array, verified: boolean}>}
 */
export async function cloneWorkflowWithModels(workflowKey, body) {
  return unwrap(
    apiClient.post(
      `/workflows/${encodeURIComponent(workflowKey)}/clone-with-models`,
      body,
    ),
  );
}

/**
 * Send this card's workflow file to the system trash.
 *
 * Only a card with a file has one: a workflow the library knows from its
 * pictures answers 409, which is why every caller gates on `imported`. The
 * card and its pictures stay either way - this deletes a file, not a card.
 *
 * @param {string} workflowKey
 * @returns {Promise<{deleted: string, workflow_key: string}>}
 */
export async function deleteWorkflowFile(workflowKey) {
  return unwrap(
    apiClient.delete(`/workflows/${encodeURIComponent(workflowKey)}`),
  );
}

/**
 * A workflow's LoRA chain, in the order the chain applies it (#1478).
 *
 * Source rail on top, the loaders from source to sink, the sink rail under
 * them, plus what "Add a LoRA" would insert. **Always a 200 when the card has
 * a graph**, ComfyUI or not: with ComfyUI unreachable the chain is read from
 * the graph's own links and comes back `editable: false` with the reason in
 * `refusal`, so the dialog opens read-only rather than failing. 404 is no such
 * card and 409 a card with no graph.
 *
 * @param {string} workflowKey
 * @returns {Promise<{workflow_key: string, editable: boolean, refusal: ?string,
 *   source: ?Object, sink: ?Object, loaders: Array<Object>,
 *   added_loader_class: ?string, branch_note: ?string}>} `branch_note` says
 *   why the list stops before some of the workflow's loaders (the model
 *   branches at the chain's end); null for a straight chain.
 */
export async function getLoraChain(workflowKey) {
  return unwrap(
    apiClient.get(`/workflows/${encodeURIComponent(workflowKey)}/lora-chain`),
  );
}

/**
 * Save a LoRA chain as the owner left it, as a NEW workflow (#1478).
 *
 * `entries` is the whole chain in apply order: an existing loader by
 * `node_id` (kept, maybe moved or re-weighted), a new one by the shelf
 * `sha256` with `node_id: null`. An existing loader left out is deleted. The
 * original file is never modified: a write answers 201 with the new card's
 * `workflow_key`, and `dry_run: true` answers 200 with only the `changes` the
 * confirm step lists.
 *
 * @param {string} workflowKey
 * @param {{entries: Array<{node_id: ?string, sha256?: string, strength?: number}>,
 *   name: ?string, dry_run: boolean}} body
 * @returns {Promise<{dry_run: boolean, name: ?string, workflow_key: ?string,
 *   changes: Array<{kind: string, node_id: ?string, text: string}>}>}
 */
export async function saveLoraChain(workflowKey, body) {
  return unwrap(
    apiClient.put(
      `/workflows/${encodeURIComponent(workflowKey)}/lora-chain`,
      body,
    ),
  );
}
