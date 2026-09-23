// Saved recipes — /recipes (see `pixlstash/routes/recipes.py`, v1.12 B6).
//
// A *saved recipe* is the look a person keeps: a prompt, its LoRAs and the
// parameters they changed, filed under one workflow card. It is not a
// `workflow_recipe` row, which is a **variant** of a card and is the hub's own
// vocabulary. Owner-only, like everything under /workflows.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Keep the look a Run popup is showing.
 *
 * `overrides` is addressed `"<slot_label>/<input_name>"`, which is the form
 * `POST /workflows/run` unpacks back into `values` when the recipe is run.
 *
 * @param {Object} recipe - `{workflow_key, name, prompt, negative, loras,
 *   overrides, seed, keep_seed, source_picture_id}`.
 * @returns {Promise<Object>} the saved row.
 */
export async function createSavedRecipe(recipe) {
  return unwrap(apiClient.post("/recipes", recipe));
}

/**
 * The saved recipes of one workflow's whole stack, in their kept order.
 *
 * A recipe runs on any workflow in the stack it was saved from (backend
 * decision D10), so the key of a *member* answers with the stack's recipes and
 * not with that member's own - which is what the Recipes tab shows for a
 * selected member. Without a key this is every recipe in the library, and the
 * `pictures` credit is then 0 for all of them.
 *
 * @param {string} [workflowKey]
 * @returns {Promise<Array<Object>>}
 */
export async function listSavedRecipes(workflowKey) {
  const keys = (Array.isArray(workflowKey) ? workflowKey : [workflowKey]).filter(
    Boolean,
  );
  const body = await unwrap(
    apiClient.get("/recipes", {
      params: keys.length ? { workflow_key: keys } : {},
    }),
  );
  return Array.isArray(body) ? body : [];
}

/**
 * Every look this workflow's own pictures were made with, saved or not.
 *
 * **A saved recipe is a look somebody kept; this is every look they ran.** A
 * library that has never pressed Save still has hundreds, which is why the
 * Recipes tab lists these beside the saved ones instead of showing an empty
 * panel to somebody with a full library. A look a saved recipe already keeps
 * stays in, with `bookmarked` set: a bookmark does not take a look off the list.
 *
 * `loras` are file names with no strength - a picture row stores none - and
 * `cover_picture_id` is where the Save dialog reads the strengths back from.
 *
 * @param {string|Array<string>} workflowKey - one card, or a selection.
 * @returns {Promise<Array<{prompt: string, loras: Array<Object>, pictures: number, cover_picture_id: ?number, bookmarked: boolean}>>}
 */
export async function listUsedLooks(workflowKey) {
  const keys = (Array.isArray(workflowKey) ? workflowKey : [workflowKey]).filter(
    Boolean,
  );
  if (!keys.length) return [];
  const body = await unwrap(
    apiClient.get("/recipes/used", { params: { workflow_key: keys } }),
  );
  return Array.isArray(body) ? body : [];
}

/**
 * Write the order of the recipes a tab is showing.
 *
 * The complete ordered list, not a move: the route refuses the whole write if
 * one id is unknown, so a half-applied order is never left behind.
 *
 * @param {Array<number>} recipeIds
 * @returns {Promise<Array<number>>} the order as it landed.
 */
export async function reorderSavedRecipes(recipeIds) {
  const body = await unwrap(
    apiClient.put("/recipes/order", { recipe_ids: recipeIds }),
  );
  return Array.isArray(body?.recipe_ids) ? body.recipe_ids : [];
}

/**
 * Change the named fields of one recipe; the rest stand.
 *
 * @param {number} recipeId
 * @param {Object} changes - e.g. `{name}`. `workflow_key` is not settable.
 * @returns {Promise<Object>} the recipe as it now reads.
 */
export async function editSavedRecipe(recipeId, changes) {
  return unwrap(apiClient.patch(`/recipes/${recipeId}`, changes));
}

/**
 * Forget one recipe. The pictures it made are untouched.
 *
 * @param {number} recipeId
 * @returns {Promise<Object>} `{deleted}`.
 */
export async function deleteSavedRecipe(recipeId) {
  return unwrap(apiClient.delete(`/recipes/${recipeId}`));
}

/**
 * One recipe as a file, with the plain list of what that file gives away.
 *
 * Nothing is withheld - a recipe *is* the prompt and the LoRA names - so
 * `shares` is the sentence-by-sentence list the export dialog prints before
 * the owner agrees to it.
 *
 * @param {number} recipeId
 * @returns {Promise<{filename: string, recipe: Object, shares: Array<string>}>}
 */
export async function exportSavedRecipe(recipeId) {
  return unwrap(apiClient.get(`/recipes/${recipeId}/export`));
}
