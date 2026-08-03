// Stacks resource — /stacks.
//
// A stack groups near-duplicate pictures behind one grid tile. Creating a
// stack takes the member ids; dissolving one removes every member, which is
// why "dissolve" is a members-DELETE rather than a stack-DELETE: the pictures
// survive, only the grouping goes.

import { apiClient } from "../utils/apiClient";

/**
 * Build a stacks route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/stacks`.
 * @param {string} [baseUrl=""]
 * @returns {string}
 */
function stacksUrl(path = "", baseUrl = "") {
  return `${baseUrl}/stacks${path}`;
}

/**
 * Read one stack.
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, whose `picture_ids` are the
 *   stack's members.
 */
export async function getStack(id, { baseUrl = "" } = {}) {
  const res = await apiClient.get(stacksUrl(`/${id}`, baseUrl));
  return res.data;
}

/**
 * List a stack's member pictures.
 *
 * @param {number|string} id
 * @param {Object} [options]
 * @param {string} [options.fields="grid"] - projection; the grid only needs
 *   the grid fields, and asking for the full record is markedly slower.
 * @param {string} [options.sort] - omitted for the stack's own order.
 * @param {boolean} [options.descending] - omitted unless a sort is given.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Array<Object>>} the member pictures (the response body).
 */
export async function listStackPictures(
  id,
  { fields = "grid", sort, descending, baseUrl = "" } = {},
) {
  const params = { fields };
  if (sort) params.sort = sort;
  if (typeof descending === "boolean") {
    params.descending = descending ? "true" : "false";
  }
  const res = await apiClient.get(stacksUrl(`/${id}/pictures`, baseUrl), {
    params,
  });
  return res.data;
}

/**
 * Create a stack from the given pictures.
 *
 * The order of `pictureIds` becomes the stack's order, so callers sort before
 * calling rather than relying on the server.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the created stack (the response body).
 */
export async function createStack(pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.post(stacksUrl("", baseUrl), {
    picture_ids: pictureIds,
  });
  return res.data;
}

/**
 * Persist a stack's member order.
 * @param {number|string} id
 * @param {Array<number>} pictureIds - the members in their new order.
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function setStackOrder(id, pictureIds, { baseUrl = "" } = {}) {
  const res = await apiClient.patch(stacksUrl(`/${id}/order`, baseUrl), {
    picture_ids: pictureIds,
  });
  return res.data;
}

/**
 * Remove pictures from a stack; removing all of them dissolves it.
 *
 * The ids travel in a request BODY on a DELETE, which Axios only sends when it
 * is passed as `config.data`.
 *
 * @param {number|string} id
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body.
 */
export async function removeStackMembers(
  id,
  pictureIds,
  { baseUrl = "" } = {},
) {
  const res = await apiClient.delete(stacksUrl(`/${id}/members`, baseUrl), {
    data: { picture_ids: pictureIds },
  });
  return res.data;
}

/**
 * Build the Keep-cover-only request body from a selection.
 *
 * The unit is the stack: `stack_ids` and `picture_ids` are unioned server-side,
 * a named picture pulls in its whole stack, and a loose picture contributes
 * nothing. Empty lists are omitted rather than sent as `[]`, so the server's
 * "send at least one of them" check reads a genuinely absent selection as
 * absent instead of as an empty one.
 *
 * @param {Object} [selection]
 * @param {Array<number|string>} [selection.stackIds]
 * @param {Array<number|string>} [selection.pictureIds]
 * @returns {Object} the request body.
 */
function keepCoverOnlyBody({ stackIds, pictureIds } = {}) {
  const body = {};
  const stacks = (Array.isArray(stackIds) ? stackIds : [])
    .map(Number)
    .filter((id) => Number.isFinite(id));
  const pictures = (Array.isArray(pictureIds) ? pictureIds : [])
    .map(Number)
    .filter((id) => Number.isFinite(id));
  if (stacks.length) body.stack_ids = stacks;
  if (pictures.length) body.picture_ids = pictures;
  return body;
}

/**
 * Dry-run collapsing the selected stacks to their covers.
 *
 * The **authoritative** source for every figure the confirm dialog renders: the
 * server derives the whole report from one read over the same selection through
 * the same planner the mutation uses, so the dialog's headline and its rows
 * cannot describe two different moments. Do not compose a second request to
 * fill in a row, and do not derive a bucket by subtracting one figure from
 * another.
 *
 * Writes nothing and deletes nothing.
 *
 * @param {Object} [selection]
 * @param {Array<number|string>} [selection.stackIds] - stacks named directly.
 * @param {Array<number|string>} [selection.pictureIds] - the grid selection;
 *   each resolves to its stack.
 * @param {string} [selection.baseUrl=""]
 * @returns {Promise<Object>} `{ stacks_selected, stacks_eligible,
 *   stacks_skipped_locked, stacks_skipped_character_on_copy,
 *   stacks_skipped_single_member, pictures_moving, picture_ids_moving,
 *   covers_kept, covers_gaining_metadata, reference_folder_pictures_moving,
 *   bytes_held_by_copies, originals_deleted_from_disk,
 *   scrapheap_retention_days, unknown_stack_ids, stacks }`.
 */
export async function previewKeepCoverOnly({
  stackIds,
  pictureIds,
  baseUrl = "",
} = {}) {
  const res = await apiClient.post(
    stacksUrl("/keep-cover-only/preview", baseUrl),
    keepCoverOnlyBody({ stackIds, pictureIds }),
  );
  return res.data;
}

/**
 * Collapse the selected stacks to their covers.
 *
 * Each stack keeps its **current** cover and every other live member is
 * soft-deleted to the Scrapheap, where it can be restored: the same soft delete
 * the grid's Delete performs, never a second permanent path. Nothing leaves
 * disk. The whole call is one operation under one `batch_id`, so a single
 * `Ctrl+Z` puts every stack back.
 *
 * Call {@link previewKeepCoverOnly} first and show the user what it reports.
 *
 * @param {Object} [selection]
 * @param {Array<number|string>} [selection.stackIds]
 * @param {Array<number|string>} [selection.pictureIds]
 * @param {string} [selection.batchId] - client-namespaced `cli-…` batch id, so
 *   one gesture stays a single undo. Omit and the server mints one.
 * @param {string} [selection.baseUrl=""]
 * @returns {Promise<Object>} `{ status, stacks_collapsed, stack_ids_collapsed,
 *   pictures_moved, picture_ids_moved, cover_picture_ids,
 *   covers_gaining_metadata, tags_added, scores_lifted,
 *   reference_folder_pictures_moved, originals_deleted_from_disk,
 *   stacks_skipped_locked, stacks_skipped_character_on_copy,
 *   stacks_skipped_single_member, unknown_stack_ids }`.
 */
export async function keepCoverOnly({
  stackIds,
  pictureIds,
  batchId,
  baseUrl = "",
} = {}) {
  const body = keepCoverOnlyBody({ stackIds, pictureIds });
  if (batchId) body.batch_id = batchId;
  const res = await apiClient.post(stacksUrl("/keep-cover-only", baseUrl), body);
  return res.data;
}
