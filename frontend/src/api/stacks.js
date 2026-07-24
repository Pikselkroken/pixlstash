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
