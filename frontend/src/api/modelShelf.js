// Model shelf resource — /adapters and /checkpoints.
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
 * @param {string} [options.fileKind="adapter"] - `adapter` or `unknown`.
 *   Checkpoints have their own route; anything else is a 400.
 * @param {string} [options.baseModel] - exact match, or `UNASSIGNED` for the
 *   rows that record none. Omit for all.
 * @param {string} [options.kind] - adapter algorithm, e.g. `lora` or `lokr`.
 * @param {string} [options.q] - substring of name, filename or trigger words.
 * @returns {Promise<Array<Object>>} the `adapters` array of the response body.
 */
export async function listAdapters({ fileKind, baseModel, kind, q } = {}) {
  const params = {};
  if (fileKind) params.file_kind = fileKind;
  if (baseModel) params.base_model = baseModel;
  if (kind) params.kind = kind;
  if (q) params.q = q;
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
 * Write curated columns onto one or more models.
 *
 * Three of the shelf's verbs land here — Rename, Set base model, Set kind —
 * because all three write one column and differ in nothing else. **Only the
 * keys present in `changes` are sent**, so setting a base model across a
 * selection cannot blank the names in it, and an explicit `null` is a *clear*
 * rather than "leave it alone".
 *
 * @param {Array<number>} ids - hub `model.id` values. Ids rather than hashes:
 *   an unhashed 24 GB checkpoint has no hash to be addressed by.
 * @param {Object} changes - any of `display_name` (one id only), `base_model`,
 *   `kind`, `file_kind`.
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
