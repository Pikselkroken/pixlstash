// Collapsing loose adapters into stacks - /model-stacks.
//
// `createStack` is the only call that builds one, from a selection the owner
// made. The rest edit a stack that exists: `unstackStack` breaks one up, and
// `setStackCover` / `removeStackMember` act on one file inside it - the two
// gestures the expanded strip is for.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Collapse models into one stack.
 *
 * **Order is recomputed server-side**, so the caller cannot choose the cover by
 * reordering `modelIds`: the newest version leads, then its bare final, else
 * its highest step.
 *
 * `fuse` is what makes stacking two stacks fuse them: it admits models that are
 * already stacked and absorbs their stacks **whole**, so members not named in
 * `modelIds` come along too and the emptied stacks are removed. Leave it off
 * for the proposals flow, which must keep refusing a row something else stacked
 * between the dry run and the press.
 *
 * @param {Array<number>} modelIds - hub `model.id` values, at least two.
 * @param {string|null} [name] - what to call the stack. When fusing, null
 *   inherits the first name among the absorbed stacks.
 * @param {{fuse?: boolean}} [options]
 * @returns {Promise<{stack_id: number, member_count: number}>}
 */
export async function createStack(
  modelIds,
  name = null,
  { fuse = false } = {},
) {
  return unwrap(
    apiClient.post("/model-stacks", { model_ids: modelIds, name, fuse }),
  );
}

/**
 * Break a stack apart, leaving its members loose on the shelf.
 *
 * **Nothing on disk is touched** - two hub columns are cleared and one row is
 * deleted. The released files reappear as the individual adapters they were,
 * which also means detection can offer to regroup them: this undoes a grouping,
 * it does not record a refusal.
 *
 * @param {number} stackId - hub `adapter_stack.id`.
 * @returns {Promise<{released: number}>}
 */
export async function unstackStack(stackId) {
  return unwrap(apiClient.delete(`/model-stacks/${stackId}`));
}

/**
 * Choose which member the shelf draws for a run.
 *
 * The one call that lets a person pick the cover. `createStack` deliberately
 * recomputes the order from the filenames, which is right for a heuristic and
 * wrong once the owner knows the run's best checkpoint is not the file the
 * trainer wrote last.
 *
 * The choice sticks: nothing recomputes a stack's order after it is built, so
 * it survives a re-scan and a re-import.
 *
 * @param {number} stackId - hub `adapter_stack.id`.
 * @param {number} modelId - hub `model.id`, already a member of that stack.
 * @returns {Promise<{stack_id: number, model_ids: Array<number>}>} the members
 *   in their new order, cover first.
 */
export async function setStackCover(stackId, modelId) {
  return unwrap(
    apiClient.patch(`/model-stacks/${stackId}/cover`, { model_id: modelId }),
  );
}

/**
 * Take one model out of a stack, leaving it loose on the shelf.
 *
 * The single-file counterpart to {@link unstackStack}, for the checkpoint that
 * turned out to be a different subject. **Nothing on disk is touched.** The
 * survivors are renumbered so the run keeps a cover, and removing the cover
 * promotes whichever member was behind it.
 *
 * A stack of one is not a stack, so taking out the second-to-last member
 * dissolves the whole thing and both files go loose - which is what
 * `dissolved` reports.
 *
 * @param {number} stackId - hub `adapter_stack.id`.
 * @param {number} modelId - hub `model.id`, already a member of that stack.
 * @returns {Promise<{released: number, dissolved: boolean}>}
 */
export async function removeStackMember(stackId, modelId) {
  return unwrap(
    apiClient.delete(`/model-stacks/${stackId}/members/${modelId}`),
  );
}
