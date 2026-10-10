// The workflow library — /workflows (see `pixlstash/routes/workflows.py`).
//
// **Card level, not topology level.** The retired shelf opened at topology and
// this module fronted its reads; F1b (#1404) deleted the shelf and, with it,
// the last caller of `listWorkflows`, `listWorkflowVariants`,
// `listWorkflowPictures` and `getWorkflowGraph`. B9 (#1410) then deleted the
// topology routes themselves and moved the cards onto `/workflows`, so the
// grid and the detail are what this module fronts, with `exportWorkflow`,
// `duplicateWorkflow` and `deleteWorkflow` at the foot of the file for the
// grid's own verb menu (#1455), and the LoRA chain editor's `getLoraChain` /
// `saveLoraChain` after them (#1478). Since #1623 every route is addressed by
// a workflow `id` (opaque: `auto:<core and families digest>` or `manual:<uuid hex>`), never by a
// card key. `GET /workflows/{id}/pictures` is served and has no caller in the
// app - the picture grid reaches a workflow's pictures through
// `GET /pictures?workflow=`, which filters like every other facet - so there
// is deliberately no function for it here.
//
// Every route here is owner-only: the counts are read across the whole vault,
// so a scoped session gets 403 rather than a narrowed answer.

import { apiClient, appendShareToken, API_BASE_URL } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** `/workflows/{id}` and *tail*, the id encoded. */
function wf(workflowId, tail = "") {
  return `/workflows/${encodeURIComponent(workflowId)}${tail}`;
}

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
 * The Workflows grid: one card per workflow, plus what it left out (v1.12 B3).
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
 * One workflow opened: the card plus its notes, variants, pins and model
 * fixes. Only this read (and a write's answer) fills `card.default_recipe`;
 * the grid sends it null.
 *
 * @param {string} workflowId
 * @returns {Promise<{card: Object, notes: ?string, hidden: boolean, variants: Array<Object>, pins: ?Array<Object>}>}
 */
export async function getWorkflowCard(workflowId) {
  return unwrap(apiClient.get(wf(workflowId)));
}

/**
 * Edit a card's own attributes (v1.12 B4). Answers with the card re-read.
 *
 * `null` for `name` or `notes` clears it, which is not the same as leaving
 * the field out — so callers pass only what they mean to change.
 *
 * @param {string} workflowId
 * @param {{name?: ?string, notes?: ?string, hidden?: boolean}} changes
 * @returns {Promise<Object>} the same shape `getWorkflowCard` returns
 */
export async function patchWorkflowCard(workflowId, changes) {
  return unwrap(apiClient.patch(wf(workflowId), changes));
}

/**
 * The LoRAs of a workflow's pictures: the ones in every picture (`shared`),
 * the ones that change (`varying`, most pictures first) and the pictures that
 * loaded none of those (`without`).
 *
 * Each LoRA is named by `asset`, the stored graphs' reference, which is what
 * the picture grid's `workflow_lora` filter takes.
 * `cover` is the workflow's cover picture; `cover_asset` then says which
 * changing LoRA it loaded, for the top of the pile.
 *
 * @param {string} workflowId
 * @param {{cover?: number}} [options]
 * @returns {Promise<{keys: string[], pictures: number, shared: Object[],
 *   varying: Object[], without: ?Object, cover_asset: ?string}>}
 */
export async function getLoraSummary(workflowId, { cover } = {}) {
  return unwrap(
    apiClient.get(wf(workflowId, "/lora-summary"), {
      params: cover ? { cover } : {},
    }),
  );
}

/**
 * Replace a card's whole set of parameter overrides. Answers with the detail.
 *
 * Whole, not one: an empty list is "reset everything", and resetting one
 * value means sending the others back unchanged.
 *
 * @param {string} workflowId
 * @param {Array<{slot_label: string, input_name: string, value: *}>} defaults
 * @returns {Promise<Object>}
 */
export async function setWorkflowDefaults(workflowId, defaults) {
  return unwrap(apiClient.put(wf(workflowId, "/defaults"), { defaults }));
}

/**
 * Put one of a workflow's LoRAs in or out of its default recipe (#1653).
 *
 * `asset` is the `asset:` reference `lora-summary` names it by; `include`
 * true adds it (at `strength`, else the strength its pictures used most),
 * false keeps it out, null drops the edit. A drop may name the edit by its
 * `sha256` instead, which still works after the file left the shelf.
 * Answers with the opened workflow.
 *
 * @param {string} workflowId
 * @param {{asset?: string, sha256?: string, include: boolean|null, strength?: number}} edit
 */
export async function setWorkflowDefaultLora(workflowId, edit) {
  return unwrap(apiClient.put(wf(workflowId, "/default-lora"), edit));
}

/**
 * Replace a model a card's workflow loads (a missing checkpoint), or undo it.
 *
 * `was` is the file as the graph names it, `now` a shelf model's filename or
 * `null` to load the original again. The card keeps its pictures, and a
 * picture made with the replacement is filed on it. Answers with the card.
 *
 * @param {string} workflowId
 * @param {{was: string, now: ?string}} fix
 * @returns {Promise<Object>}
 */
export async function setWorkflowModelFix(workflowId, fix) {
  return unwrap(apiClient.put(wf(workflowId, "/model-fix"), fix));
}

/**
 * Replace a card's whole set of pinned parameters.
 *
 * `null` forgets the choice, so the client's default pins apply again; `[]`
 * is somebody who unpinned everything, and the two are kept apart.
 *
 * @param {string} workflowId
 * @param {?Array<{slot_label: string, input_name: string}>} pins
 * @returns {Promise<{pins: ?Array<Object>}>}
 */
export async function setWorkflowPins(workflowId, pins) {
  return unwrap(apiClient.put(wf(workflowId, "/pins"), { pins }));
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
 * @param {string} workflowId
 * @param {Array<{slot_label: string, input_name: string,
 *   mode: "selection"|"picker"|"fixed", pixel_sha?: ?string,
 *   picture_id?: ?number}>} inputs
 * @returns {Promise<{inputs: Array<Object>}>}
 */
export async function setWorkflowInputs(workflowId, inputs) {
  return unwrap(apiClient.put(wf(workflowId, "/inputs"), { inputs }));
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
 * Save this workflow with the repairs a run on this ComfyUI makes (#1661):
 * the owner's model replacements, same-model renames, LoRAs this ComfyUI
 * lacks loaded through the ComfyUI-PixlStash loader, and missing seed or text
 * nodes replaced. Written as a NEW workflow; the original is not changed.
 * 409 when nothing needs fixing, 503 when ComfyUI cannot be asked.
 *
 * @param {string} workflowId
 * @returns {Promise<{name: string, workflow_id: ?string, changes: Array<string>}>}
 */
export async function saveFixedWorkflow(workflowId) {
  return unwrap(apiClient.post(wf(workflowId, "/fixed-copy")));
}

/**
 * Run a workflow card.
 *
 * Exactly ONE source: `picture_ids` ("run what made these"), `saved_recipe_id`
 * ("run this look") or `workflow_id` ("run this workflow"). `target` is a
 * workflow id that overrides which workflow actually runs. `models` pins the
 * checkpoint (`[{address, filename}]`) and `skip_stages` turns stages off.
 * `prompt` / `negative` / `loras` / `values` are overrides applied to the graph
 * at run time and are never written back into it. `inputs` fills the card's
 * picture inputs and is usually empty: the server fills the one input a
 * selection can only mean on its own (#1457). `replay: true` says the one
 * picture named is the workflow's own output, so its picture input takes what
 * that picture was made from. `stack` files each new picture behind the one it
 * was made from.
 *
 * @param {Object} body
 * @returns {Promise<{status: string, runs: number, groups: Array<Object>, prompts: Array<Object>}>}
 */
export async function runWorkflowCard(body) {
  return unwrap(apiClient.post("/workflows/run", body));
}

/**
 * This card as a ComfyUI file somebody else can open (v1.12 B8).
 *
 * Scrubbed by the server - prompts and seeds blanked, recipe LoRA slots
 * emptied, model names this machine does not hold left out - so what comes
 * back is the graph, not a run of it.
 *
 * @param {string} workflowId
 * @returns {Promise<{filename: string, workflow: Object, removed: Array<string>, source: string}>}
 */
export async function exportWorkflow(workflowId) {
  return unwrap(apiClient.get(wf(workflowId, "/export")));
}

/**
 * Copy this workflow as a new MANUAL workflow (`… (copy)`).
 *
 * Unscrubbed, unlike the export: the copy stays on this machine and is meant
 * to run. The answer's `workflow_id` is the new manual workflow.
 *
 * @param {string} workflowId
 * @returns {Promise<{name: string, workflow_id: string}>}
 */
export async function duplicateWorkflow(workflowId) {
  return unwrap(apiClient.post(wf(workflowId, "/duplicate")));
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
 * With `replacing` (the Workflow tab's "Replace with…", #1596): also
 * `replacements`, the shelf models that go with the workflow's checkpoint (for
 * a checkpoint: that share the missing one's base model, where anything says
 * which and any does) and that the loader naming that file can load, and
 * `replacements_reason` when there are none.
 *
 * @param {string} workflowId
 * @param {{checkpointId?: number, replacing?: string, slotKind?: string}} [options]
 * @returns {Promise<Object>}
 */
export async function readModelSwap(
  workflowId,
  { checkpointId, replacing, slotKind } = {},
) {
  const params = {};
  if (checkpointId != null) params.checkpoint_id = checkpointId;
  if (replacing != null) params.replacing = replacing;
  if (slotKind != null) params.slot_kind = slotKind;
  return unwrap(apiClient.get(wf(workflowId, "/model-swap"), { params }));
}

/**
 * What cloning this workflow onto each workflow set would write.
 *
 * One plan per set asked: the files it takes from the set (`swaps`), each model
 * loader before and after (`loaders`, with the node `pack` a new loader class
 * comes from and whether ComfyUI has it), whether the set's checkpoint has the
 * workflow's base model so its LoRAs are kept, and `fit` / `reason`. Asked once
 * per open, for every set on the shelf, and again for one set when the owner
 * picks which of its files a loader takes (`picks`, the plan's `takes` with
 * the choice made); nothing is written.
 *
 * @param {string} workflowId
 * @param {Array<{key: string, checkpoint_ids: Array<number>, model_ids: Array<number>, picks?: Object<string, number>}>} sets
 * @returns {Promise<{base_filename: ?string, base_model: ?string, plans: Array<Object>}>}
 */
export async function planSetClones(workflowId, sets) {
  return unwrap(apiClient.post(wf(workflowId, "/set-clone-plans"), { sets }));
}

/**
 * Write a copy of this workflow with model files replaced, as a new card.
 *
 * `swaps` maps the graph's filename to the one to load instead; every loader
 * naming it is rewritten. `verified` in the answer is false when ComfyUI was
 * not reachable and the names were written unchecked.
 *
 * `loras`, when given, is the chain to write it with in `PUT …/lora-chain`'s
 * shape (read against the original's chain; `{entries: []}` clears it), and
 * `loaders` in the answer names each loader whose node class changed for a file
 * of another type.
 *
 * @param {string} workflowId
 * @param {{name: string, swaps: Object<string, string>, loras?: Object}} body
 * @returns {Promise<{name: string, workflow_id: ?string, swapped: Array, unswapped: Array, loaders: Array, verified: boolean}>}
 */
export async function cloneWorkflowWithModels(workflowId, body) {
  return unwrap(apiClient.post(wf(workflowId, "/clone-with-models"), body));
}

/**
 * Delete a MANUAL workflow; its document goes to the system trash.
 *
 * Its pictures stay, back on the automatic workflow their graph is in, and
 * its saved recipes become unfiled. An automatic workflow answers 409 (hide
 * it instead), which is why every caller gates on `manual`.
 *
 * @param {string} workflowId
 * @returns {Promise<{deleted: string, workflow_id: string}>} `deleted` is its name.
 */
export async function deleteWorkflow(workflowId) {
  return unwrap(apiClient.delete(wf(workflowId)));
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
 * @param {string} workflowId
 * @returns {Promise<{workflow_id: string, editable: boolean, refusal: ?string,
 *   source: ?Object, sink: ?Object, loaders: Array<Object>,
 *   added_loader_class: ?string, lanes: Array<Object>, branch_note: ?string}>}
 *   Where the model forks, `loaders` is the trunk every pass reads and `lanes`
 *   holds one `{source, sampler, sink, loaders, added_loader_class}` per pass;
 *   empty for a straight chain. `branch_note` says why loaders past a further
 *   branch are left as they are.
 */
export async function getLoraChain(workflowId) {
  return unwrap(apiClient.get(wf(workflowId, "/lora-chain")));
}

/**
 * The inputs of a workflow's graph a parameter can be made of, by node.
 *
 * Every literal input a run can set by address, less what PixlStash already
 * has a control for (prompts, seeds, models, LoRAs, picture inputs, the save
 * node). `exposed` marks the ones that are parameters already; `kind` and
 * `options` are ComfyUI's, and null where it did not answer.
 *
 * @param {string} workflowId
 * @returns {Promise<{nodes: Array<{node_id: string, title: string,
 *   class_type: string, inputs: Array<{slot_label: string, input_name: string,
 *   value: (boolean|number|string), kind: ?string, options: ?Array<string>,
 *   exposed: boolean}>}>}>} A 409 when the workflow has no graph.
 */
export async function getWorkflowFormInputs(workflowId) {
  return unwrap(apiClient.get(wf(workflowId, "/form-inputs")));
}

/**
 * Save a LoRA chain as the owner left it (#1478): as a new workflow, or with
 * `overwrite: true` over the one it edits.
 *
 * `entries` is the whole chain in apply order: an existing loader by
 * `node_id` (kept, maybe moved or re-weighted), a new one by the shelf
 * `sha256` with `node_id: null`. For a forked chain `entries` is the trunk
 * and `lanes` one such list per lane, in the read's order; an existing loader
 * may sit in any of them. An existing loader left out of all is deleted. A
 * write answers 201 with the new card's `workflow_id` and leaves the original
 * alone; an overwrite answers 200 with the same `workflow_id` and
 * `overwritten: true`; `dry_run: true` answers 200 with only the `changes`
 * the confirm step lists.
 *
 * @param {string} workflowId
 * @param {{entries: Array<{node_id: ?string, sha256?: string, strength?: number}>,
 *   lanes?: Array<Array<Object>>, name: ?string, dry_run: boolean,
 *   overwrite?: boolean}} body
 * @returns {Promise<{dry_run: boolean, name: ?string, workflow_id: ?string,
 *   overwritten?: boolean,
 *   changes: Array<{kind: string, node_id: ?string, text: string}>}>}
 */
export async function saveLoraChain(workflowId, body) {
  return unwrap(apiClient.put(wf(workflowId, "/lora-chain"), body));
}
