// Collapsing loose adapters into training runs — /model-stacks.
//
// Two calls and the split between them is the contract: **detection proposes,
// it never applies.** `listStackProposals` reads the shelf and writes nothing,
// so the whole dry run is drawn before the owner decides; `createStack` is the
// only half that writes and is reached only after they have seen it.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Groups of loose adapters that look like one training run.
 *
 * Tier 1 only: files differing solely by a training step, which needs no
 * judgement. Prefix grouping (`JimmyCarr` beside `JimmyCarr2`) is tier 2 and
 * is not offered yet.
 *
 * @returns {Promise<Array<Object>>} the `proposals` array, each carrying
 *   `tier`, `key`, `name`, `folder_id`, `total_size` and cover-first `members`.
 */
export async function listStackProposals() {
  const body = await unwrap(apiClient.get("/model-stacks/proposals"));
  return Array.isArray(body?.proposals) ? body.proposals : [];
}

/**
 * Collapse models into one stack.
 *
 * **Order is recomputed server-side**, so the caller cannot choose the cover by
 * reordering `modelIds`: the bare final leads, else the highest step.
 *
 * @param {Array<number>} modelIds - hub `model.id` values, at least two.
 * @param {string|null} [name] - what to call the stack.
 * @returns {Promise<{stack_id: number, member_count: number}>}
 */
export async function createStack(modelIds, name = null) {
  return unwrap(apiClient.post("/model-stacks", { model_ids: modelIds, name }));
}
