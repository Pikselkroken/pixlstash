// What a workflow set, or one model, has been used for (#2168): the workflows
// its pictures came from, when it was last used, and its newest pictures.
//
// Read off the combinations `GET /models/workflow-sets` serves, each of which
// carries its own `workflows`, `last_used_at` and `latest`. A recipe binds one
// exact set of files, so every picture sits under exactly one combination and
// summing across combinations counts no picture twice. The rail's *Works in*
// and *Latest pictures*, a card's last-used chip and the set pill's *Show
// workflows* / *Show pictures* all read these, so the four cannot disagree.

/** How many pictures *Latest pictures* draws. */
export const LATEST_PICTURES = 10;

/**
 * Fold combinations into one activity.
 *
 * @param {Array<Object>} combinations
 * @returns {{workflows: Array<{id: string, name: string, picture_count: number}>,
 *   lastUsedAt: string|null, latest: Array<{picture_id: number, at: string}>,
 *   pictures: number}} workflows most pictures first, `latest` newest first.
 */
export function activityOf(combinations) {
  const byWorkflow = new Map();
  const latest = new Map();
  let lastUsedAt = null;
  let pictures = 0;
  for (const combination of combinations ?? []) {
    pictures += Number(combination?.picture_count) || 0;
    for (const flow of combination?.workflows ?? []) {
      if (!flow?.id) continue;
      const seen = byWorkflow.get(flow.id);
      if (seen) seen.picture_count += Number(flow.picture_count) || 0;
      else {
        byWorkflow.set(flow.id, {
          id: flow.id,
          name: flow.name || "",
          picture_count: Number(flow.picture_count) || 0,
        });
      }
    }
    for (const pic of combination?.latest ?? []) {
      if (pic?.picture_id != null) latest.set(pic.picture_id, pic);
    }
    const at = combination?.last_used_at;
    if (at && (!lastUsedAt || Date.parse(at) > Date.parse(lastUsedAt))) {
      lastUsedAt = at;
    }
  }
  return {
    workflows: [...byWorkflow.values()].sort(
      (a, b) => b.picture_count - a.picture_count || a.name.localeCompare(b.name),
    ),
    lastUsedAt,
    latest: [...latest.values()]
      // An unreadable date sorts last rather than making the order undefined.
      .sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0))
      .slice(0, LATEST_PICTURES),
    pictures,
  };
}

/**
 * The combinations behind one set card: an evidence group's own, or every
 * combination one of the owner's sets covers (`covered_by`, #1520).
 *
 * @param {Object} group - a `setGroups` / `handMadeGroups` entry.
 * @param {Array<Object>} combinations - the whole payload's.
 */
export function combinationsOfSet(group, combinations) {
  if (group?.set) {
    const id = group.set.id;
    return (combinations ?? []).filter((c) => c.covered_by?.includes(id));
  }
  return group?.combinations ?? [];
}

/**
 * The combinations one model has run in.
 *
 * @param {number} modelId - hub `model.id`.
 * @param {Array<Object>} combinations
 */
export function combinationsOfModel(modelId, combinations) {
  return (combinations ?? []).filter((c) =>
    (c.models ?? []).some((model) => model.id === modelId),
  );
}
