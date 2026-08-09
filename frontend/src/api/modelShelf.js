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
