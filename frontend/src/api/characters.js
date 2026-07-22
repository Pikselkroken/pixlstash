// Characters (people) resource.
//
// The entity-resource shape (list / remove) that Phase 4's `useEntityList`
// composable will be parameterised by (frontend_refactoring_plan.md §3 Phase 4):
// the sidebar's four parallel fetch/delete stacks collapse onto one generic
// lifecycle driven by modules like this one.

import { apiClient } from "../utils/apiClient";

/**
 * List characters.
 * @param {Object} [params] - optional query params.
 * @returns {Promise<Array<Object>>} the character list (the response body).
 */
export async function listCharacters(params) {
  const res = await apiClient.get(
    "/characters",
    params ? { params } : undefined,
  );
  return res.data;
}

/**
 * Delete a character by id.
 * @param {number|string} id
 * @returns {Promise<Object>} the response body.
 */
export async function deleteCharacter(id) {
  const res = await apiClient.delete(`/characters/${id}`);
  return res.data;
}
