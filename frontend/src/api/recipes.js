// Saved recipes — /recipes (see `pixlstash/routes/recipes.py`, v1.12 B6).
//
// A *saved recipe* is the look a person keeps: a prompt, its LoRAs and the
// parameters they changed, filed under one workflow. It is not a
// `workflow_recipe` row, which is a **variant** of a card and is the hub's own
// vocabulary. Owner-only, like everything under /workflows.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * `workflow_id=a&workflow_id=b`, which is what FastAPI's `list[...]` reads.
 *
 * Axios' default array form is `workflow_id[]=`, a key the server does not
 * know: the list is dropped in silence, so `/recipes/used` answers `[]` for
 * every workflow and `/recipes` answers every recipe in the library.
 */
const REPEATED_KEYS = { indexes: null };

/** One id or several, as the list the query repeats. */
function workflowIds(workflowId) {
  return (Array.isArray(workflowId) ? workflowId : [workflowId]).filter(Boolean);
}

/**
 * Keep the look a Run popup is showing.
 *
 * `overrides` is addressed `"<slot_label>/<input_name>"`, which is the form
 * `POST /workflows/run` unpacks back into `values` when the recipe is run.
 *
 * @param {Object} recipe - `{workflow_id, name, prompt, negative, loras,
 *   overrides, seed, keep_seed, source_picture_id, models?}`. `models` is
 *   `[{address, filename}]`, the checkpoint the run pinned, when it pinned one.
 * @returns {Promise<Object>} the saved row.
 */
export async function createSavedRecipe(recipe) {
  return unwrap(apiClient.post("/recipes", recipe));
}

/**
 * The saved recipes of one workflow (or several), in their kept order.
 *
 * Without an id this is every recipe in the library, and the `pictures`
 * credit is then 0 for all of them.
 *
 * @param {string|Array<string>} [workflowId]
 * @returns {Promise<Array<Object>>}
 */
export async function listSavedRecipes(workflowId) {
  const ids = workflowIds(workflowId);
  const body = await unwrap(
    apiClient.get("/recipes", {
      params: ids.length ? { workflow_id: ids } : {},
      paramsSerializer: REPEATED_KEYS,
    }),
  );
  return Array.isArray(body) ? body : [];
}

/**
 * The saved recipes on no workflow this machine holds (theirs was deleted, or
 * never filed): the Workflows view's "Unfiled recipes". `pictures` is 0.
 *
 * @returns {Promise<Array<Object>>}
 */
export async function listUnfiledRecipes() {
  const body = await unwrap(
    apiClient.get("/recipes", { params: { unfiled: true } }),
  );
  return Array.isArray(body) ? body : [];
}

/**
 * Make a saved recipe a manual workflow of its own: the graph Run would build
 * for it. Works with ComfyUI down, and for a recipe whose workflow is gone.
 * 404 no such recipe, 409 no graph left to build on.
 *
 * @param {number} recipeId
 * @returns {Promise<{workflow_id: string, name: string}>}
 */
export async function extractRecipeWorkflow(recipeId) {
  return unwrap(apiClient.post(`/recipes/${recipeId}/extract-workflow`));
}

/**
 * Every look this workflow's own pictures were made with, saved or not.
 *
 * **A saved recipe is a look somebody kept; this is every look they ran.** A
 * library that has never pressed Save still has hundreds, which is why the
 * Recipes tab lists these beside the saved ones instead of showing an empty
 * panel to somebody with a full library. A look a saved recipe already keeps
 * stays in, with `saved` set: a clone does not take the original off the list.
 *
 * `loras` are file names with no strength - a picture row stores none - and
 * `cover_picture_id` is where the Save dialog reads the strengths back from.
 *
 * @param {string|Array<string>} workflowId - one workflow, or a selection.
 * @returns {Promise<Array<{prompt: string, loras: Array<Object>, pictures: number, cover_picture_id: ?number, saved: boolean}>>}
 */
export async function listUsedLooks(workflowId) {
  const ids = workflowIds(workflowId);
  if (!ids.length) return [];
  const body = await unwrap(
    apiClient.get("/recipes/used", {
      params: { workflow_id: ids },
      paramsSerializer: REPEATED_KEYS,
    }),
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
 * @param {Object} changes - e.g. `{name}`. `workflow_id` is not settable.
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
