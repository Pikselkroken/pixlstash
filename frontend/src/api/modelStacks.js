// Collapsing loose adapters into stacks — /model-stacks.
//
// Two calls and the split between them is the contract: **detection proposes,
// it never applies.** `listStackProposals` reads the shelf and writes nothing,
// so the whole dry run is drawn before the owner decides; `createStack` is the
// only half that writes and is reached only after they have seen it.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * Groups of loose adapters that look like one subject.
 *
 * `tier` is `step_group` (one version, files differing only by a training step)
 * or `version_group` (`Foxglove` beside `Foxglove_v2` — several training runs
 * of one subject). Prefix grouping (`JimmyVehicle` beside `JimmyVehicle2`) needs
 * counter-evidence and is not offered.
 *
 * @returns {Promise<Array<Object>>} the `proposals` array, each carrying
 *   `tier`, `key`, `name`, `folder_id`, `total_size` and cover-first `members`,
 *   whose entries carry `step` and `version`.
 */
export async function listStackProposals() {
  const body = await unwrap(apiClient.get("/model-stacks/proposals"));
  return Array.isArray(body?.proposals) ? body.proposals : [];
}

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
 * **Nothing on disk is touched** — two hub columns are cleared and one row is
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
