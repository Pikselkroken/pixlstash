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
