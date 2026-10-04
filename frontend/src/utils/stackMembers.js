// stackMembers.js - who is in a picture's stack right now, by id.
//
// Read before a run is queued, so the member the run adds can be told apart
// afterwards: the lightbox Edit tab's own Run, and the Run popup it opens.

import { getPictureMetadata } from "../api/pictures";
import { listStackPictures } from "../api/stacks";

/** The source picture's stack members and their ids, or `[source]` when it has none. */
export async function stackMemberIds(sourceId) {
  const meta = await getPictureMetadata(sourceId);
  const stackId = meta?.stack_id ?? meta?.stackId ?? null;
  if (stackId == null) return { members: [], ids: new Set([String(sourceId)]) };
  const members = (await listStackPictures(stackId)) || [];
  return { members, ids: new Set(members.map((row) => String(row.id))) };
}
