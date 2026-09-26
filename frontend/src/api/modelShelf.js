// Model shelf resource - /adapters and /checkpoints.
//
// Two route blocks, one table (see `pixlstash/routes/model_shelf.py`). They
// converge on the same query filtered by `file_kind`; the blocks stay apart
// because their addressing differs. Three consequences the caller must honour:
//
//   * `attachments` come back ON THE LIST as well as on the detail, so the
//     shelf never fetches them a row at a time.
//   * `file_kind='unknown'` is first-class. It is in neither list by default
//     and surfaces here under `listAdapters({ fileKind: "unknown" })`.
//     `/checkpoints` never returns one, and asking it for one is a 400.
//   * A null `base_model` is explicit, not absent: `UNASSIGNED` selects the
//     rows that record none, exactly as the project filter spells it.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** The sentinel the API uses for "records no base model". */
export const BASE_MODEL_UNASSIGNED = "UNASSIGNED";

/**
 * List adapters (or the unclassified files) on the shelf.
 *
 * @param {Object} [options]
 * @param {string} [options.fileKind="adapter"] - `adapter`, `unknown`, `vae`,
 *   `text_encoder` or `engine`. Checkpoints have their own route; anything
 *   else is a 400.
 * @param {string} [options.baseModel] - exact match, or `UNASSIGNED` for the
 *   rows that record none. Omit for all.
 * @param {string} [options.kind] - adapter algorithm, e.g. `lora` or `lokr`.
 * @param {string} [options.q] - substring of name, filename or trigger words.
 * @param {number} [options.characterId] - only adapters attached to this
 *   character. Not enforced here: the server rejects both filters at once with
 *   a 400, and duplicating that rule in the client would be a second place for
 *   it to drift from.
 * @param {number} [options.setId] - only adapters attached to this picture set.
 * @returns {Promise<Array<Object>>} the `adapters` array of the response body.
 */
export async function listAdapters({
  fileKind,
  baseModel,
  kind,
  q,
  characterId,
  setId,
} = {}) {
  const params = {};
  if (fileKind) params.file_kind = fileKind;
  if (baseModel) params.base_model = baseModel;
  if (kind) params.kind = kind;
  if (q) params.q = q;
  // Ids are compared to null, not truth-tested: the filters are meaningful for
  // any real id and a `0` would silently fall through a truthy check.
  if (characterId !== undefined && characterId !== null) {
    params.character_id = characterId;
  }
  if (setId !== undefined && setId !== null) params.set_id = setId;
  const body = await unwrap(apiClient.get("/adapters", { params }));
  return Array.isArray(body?.adapters) ? body.adapters : [];
}

/**
 * List checkpoints on the shelf.
 *
 * `sha256` is null until `MissingCheckpointHashFinder` has read the file, so
 * `id` is the identifier to hold on to for these rows.
 *
 * @param {Object} [options]
 * @param {string} [options.baseModel] - exact match, or `UNASSIGNED`.
 * @param {string} [options.q] - substring of the display name or filename.
 * @returns {Promise<Array<Object>>} the `checkpoints` array of the body.
 */
export async function listCheckpoints({ baseModel, q } = {}) {
  const params = {};
  if (baseModel) params.base_model = baseModel;
  if (q) params.q = q;
  const body = await unwrap(apiClient.get("/checkpoints", { params }));
  return Array.isArray(body?.checkpoints) ? body.checkpoints : [];
}

/**
 * Completion targets for the free-text `base_model` field.
 *
 * Both halves in one flat sorted list: the labels the server ships (so the
 * field completes on a fresh install, where nothing has been recorded yet) and
 * every distinct string this machine already records that folds to none of
 * them. The whole list, not a per-keystroke query - it is a few dozen strings
 * and the field filters it as the user types.
 *
 * @returns {Promise<Array<string>>} the `base_models` array of the body.
 */
export async function listBaseModelCompletions() {
  const body = await unwrap(apiClient.get("/models/base-models"));
  return Array.isArray(body?.base_models) ? body.base_models : [];
}

/**
 * Write curated columns onto one or more models.
 *
 * Three of the shelf's verbs land here - Rename, Set base model, Set kind -
 * because all three write one column and differ in nothing else. **Only the
 * keys present in `changes` are sent**, so setting a base model across a
 * selection cannot blank the names in it, and an explicit `null` is a *clear*
 * rather than "leave it alone".
 *
 * @param {Array<number>} ids - hub `model.id` values. Ids rather than hashes:
 *   an unhashed 24 GB checkpoint has no hash to be addressed by.
 * @param {Object} changes - any of `display_name` (one id only), `base_model`,
 *   `kind`, `file_kind`, `capabilities`. The last is the COMPLETE feature set
 *   for every id sent, so `[]` clears it; the server replaces rather than
 *   merges, or there would be no way to take one off.
 * @returns {Promise<{updated: Array<number>, fields: Array<string>}>}
 */
export async function editModels(ids, changes) {
  return unwrap(apiClient.patch("/models", { ids, ...changes }));
}

/**
 * Set which characters and sets use one adapter.
 *
 * **This REPLACES the adapter's whole attachment set**, so a caller adding one
 * entity has to send the ones already there with it. That is why Assign is N
 * calls rather than one: the route is per-adapter by design, because the hash
 * is what an imported file arrives with and an id is not.
 *
 * @param {string} sha256 - the adapter's interop hash. A checkpoint 400s here,
 *   and a row the hash worker has not reached yet has none to address.
 * @param {Array<{entity_type: string, entity_id: number}>} attachments - the
 *   complete set. Empty detaches from everything. Send ONLY these two keys: the
 *   request model forbids extras, while the response model allows them, so
 *   echoing a row's `attachments` back verbatim would start failing the day the
 *   server adds a field to the response.
 * @returns {Promise<{sha256: string, attachments: Array<Object>}>}
 */
export async function setAdapterAttachments(sha256, attachments) {
  return unwrap(
    apiClient.put(
      `/adapters/${encodeURIComponent(sha256)}/attachments`,
      attachments.map((att) => ({
        entity_type: att.entity_type,
        entity_id: att.entity_id,
      })),
    ),
  );
}

/**
 * Forget models whose files are gone.
 *
 * The one shelf call that destroys curation, so its caller confirms first. The
 * server gates on each row's state rather than on the caller: anything with a
 * `present` or `unreachable` copy comes back under `refused` with a reason
 * instead of failing the call, which is what the receipt reports.
 *
 * @param {Array<number>} ids - hub `model.id` values.
 * @returns {Promise<{forgotten: Array<number>, refused: Array<Object>}>}
 */
export async function forgetModels(ids) {
  return unwrap(apiClient.post("/models/forget", { ids }));
}

/**
 * What deleting these models would leave behind: the VAEs and text encoders
 * nothing else would use (`orphaned`), the ones a kept model still uses
 * (`shared`), the ones a same-named file makes impossible to tell (`unknown`),
 * the deleted support files a kept model still runs with (`in_use`), and the
 * ids no recipe names at all (`no_evidence`). Read from the recipes the hub
 * has seen; changes nothing.
 *
 * @param {Array<number>} ids - hub `model.id` values about to be deleted.
 * @returns {Promise<{orphaned: Array<Object>, shared: Array<Object>,
 *   unknown: Array<Object>, in_use: Array<Object>, no_evidence: Array<number>}>}
 */
export async function fetchModelCompanions(ids) {
  return unwrap(apiClient.post("/models/companions", { ids }));
}

/**
 * Which shelf models a kept picture proves ran together.
 *
 * One entry per **combination**: the exact set of files one or more recipes
 * bound together, with the pictures they made. A model appears in every
 * combination it has run in, so membership overlaps and is stored nowhere - the
 * grouping is derived per request from the recipes the hub has read.
 *
 * **Co-occurrence is evidence; its absence is not.** The ids no recipe in this
 * library names come back under `no_set` rather than being dropped, and a
 * member the evidence could only reach through a basename two shelf rows share
 * is flagged `ambiguous` and still listed. Folding combinations into stacks is
 * the CLIENT's (see `utils/workflowSets.js`): the server groups nothing, so the
 * toolbar can price every fold setting without another request.
 *
 * `hand_made` is the owner's own sets (#1520), newest first; a combination one
 * of them covers carries its id in `covered_by`. A set may carry a merge
 * `offer` and the `declined` models kept out of it (#1523).
 *
 * @returns {Promise<{combinations: Array<Object>, no_set: Array<number>,
 *   hand_made: Array<Object>}>}
 */
export async function fetchWorkflowSets() {
  const body = await unwrap(apiClient.get("/models/workflow-sets"));
  return {
    combinations: Array.isArray(body?.combinations) ? body.combinations : [],
    no_set: Array.isArray(body?.no_set) ? body.no_set : [],
    hand_made: Array.isArray(body?.hand_made) ? body.hand_made : [],
  };
}

// ── Hand-made workflow sets (#1520) ─────────────────────────────────────────
//
// A per-hub menu of models that work together, stored by hash so a member that
// leaves the shelf stays in the set and reconnects when the file returns. None
// of these calls touches a file or a shelf row.

/**
 * Make a set. Every argument is optional: the grid's tile makes an empty one.
 *
 * @param {{name?: string|null, members?: Array<Object>}} [body] - a member is
 *   `{model_id, slot?}`, or `{sha256, slot, label?}` to put back one that was
 *   removed or deleted (the undo), whether or not its file is on the shelf now.
 * @returns {Promise<Object>} the set, as `hand_made` entries are shaped.
 */
export async function createWorkflowSet(body = {}) {
  return unwrap(apiClient.post("/models/workflow-sets", body));
}

/** Rename a set. `null` clears the name; the set is then shown by its checkpoint. */
export async function renameWorkflowSet(id, name) {
  return unwrap(apiClient.patch(`/models/workflow-sets/${id}`, { name }));
}

/**
 * Delete a set. Files and shelf rows are untouched.
 *
 * @returns {Promise<{deleted: Object}>} the set as it was, which is the undo.
 */
export async function deleteWorkflowSet(id) {
  return unwrap(apiClient.delete(`/models/workflow-sets/${id}`));
}

/**
 * Add members to a set. One already in it is skipped, not an error.
 *
 * @returns {Promise<{set: Object, added: Array<string>}>} `added` is the hashes
 *   actually written, which is what an undo takes back out.
 */
export async function addWorkflowSetMembers(id, members) {
  return unwrap(
    apiClient.post(`/models/workflow-sets/${id}/members`, { members }),
  );
}

/**
 * Take members off a set, by hash. The files stay on the shelf.
 *
 * @returns {Promise<{set: Object, removed: Array<Object>}>} `removed` carries
 *   `{sha256, slot, label}`, which is what an undo puts back.
 */
export async function removeWorkflowSetMembers(id, sha256) {
  return unwrap(
    apiClient.post(`/models/workflow-sets/${id}/members/remove`, { sha256 }),
  );
}

/**
 * Replace the models kept out of a set's merge offer (#1523): Keep separate
 * adds to the list, Offer again empties it.
 *
 * @returns {Promise<{set: Object, previous: Array<string>}>} `previous` is the
 *   list before, which is what an undo puts back.
 */
export async function setWorkflowSetDeclines(id, sha256) {
  return unwrap(
    apiClient.put(`/models/workflow-sets/${id}/declines`, { sha256 }),
  );
}

/**
 * Delete models from disk, and their shelf rows with them.
 *
 * The one shelf call that destroys the owner's bytes, so its caller confirms
 * first. Every registered copy of each model goes, or none of it does: the
 * server refuses a model whose copy sits somewhere it will not unlink from (its
 * own engine folders, the InsightFace packs, the shared HuggingFace cache) or
 * on a drive that is not plugged in, and reports the refusal rather than
 * failing the call.
 *
 * @param {Array<number>} ids - hub `model.id` values. A stack passes its
 *   members: a run is deleted whole or not at all.
 * @param {Object} [options]
 * @param {boolean} [options.permanent=false] - false moves the files to the
 *   machine's trash, which is the undo. True unlinks them, and nothing gets
 *   them back; the shelf sends it only for Shift+Delete.
 * @returns {Promise<{deleted: Array<number>, files_removed: number,
 *   permanent: boolean, refused: Array<{id: number, reason: string}>}>}
 */
export async function deleteModels(ids, { permanent = false } = {}) {
  return unwrap(apiClient.post("/model-files/delete", { ids, permanent }));
}

/**
 * Ask the server host to show a model's folder in its desktop file manager.
 *
 * The one shelf call that acts on the machine rather than on the library, so
 * it is loopback-only at the gate: a shelf opened over the LAN cannot drive
 * the server's desktop, and neither can a headless one - that comes back 500
 * rather than pretending a window opened somewhere.
 *
 * @param {number} id - hub `model.id`. A collapsed stack passes its cover, so
 *   the cover's folder is the one that opens.
 * @returns {Promise<{status: string}>}
 */
export async function openModelLocation(id) {
  return unwrap(apiClient.post(`/models/${id}/open-location`));
}
