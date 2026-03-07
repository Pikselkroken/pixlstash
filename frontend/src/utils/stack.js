/**
 * Pure utility functions for working with image stack data.
 * These functions operate only on plain image/stack objects and have no
 * dependency on Vue reactive state or component props.
 */

export function getPictureStackId(img) {
  const stackId = img?.stack_id ?? img?.stackId ?? null;
  if (stackId === null || stackId === undefined) return null;
  return String(stackId);
}

export function normalizeStackIdValue(stackId) {
  if (stackId === null || stackId === undefined) return null;
  const asNumber = Number(stackId);
  if (Number.isNaN(asNumber)) return String(stackId);
  return asNumber;
}

export function getStackPositionValue(img) {
  if (!img) return null;
  const raw = img.stack_position ?? img.stackPosition ?? null;
  if (raw === null || raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

export function getStackSmartScoreValue(img) {
  const raw = img?.smartScore ?? img?.smart_score ?? null;
  if (raw === null || raw === undefined) return 0;
  const value = Number(raw);
  return Number.isFinite(value) ? value : 0;
}

export function getStackCreatedAtTs(img) {
  if (!img?.created_at) return 0;
  const ts = new Date(img.created_at).getTime();
  return Number.isFinite(ts) ? ts : 0;
}

export function compareStackOrder(a, b) {
  const posA = getStackPositionValue(a);
  const posB = getStackPositionValue(b);
  if (posA !== null || posB !== null) {
    if (posA === null) return 1;
    if (posB === null) return -1;
    if (posA !== posB) return posA - posB;
  }
  const scoreA = Number(a?.score ?? 0);
  const scoreB = Number(b?.score ?? 0);
  if (scoreA !== scoreB) return scoreB - scoreA;
  const smartA = getStackSmartScoreValue(a);
  const smartB = getStackSmartScoreValue(b);
  if (smartA !== smartB) return smartB - smartA;
  const dateA = getStackCreatedAtTs(a);
  const dateB = getStackCreatedAtTs(b);
  if (dateA !== dateB) return dateB - dateA;
  const idA = Number(a?.id ?? 0);
  const idB = Number(b?.id ?? 0);
  return idA - idB;
}

export function sortStackMembers(members) {
  if (!Array.isArray(members)) return [];
  return members.slice().sort(compareStackOrder);
}

export function selectNewestStackMember(members) {
  if (!Array.isArray(members) || members.length === 0) return null;
  return members.reduce((best, current) => {
    if (!best) return current;
    const bestTs = getStackCreatedAtTs(best);
    const currentTs = getStackCreatedAtTs(current);
    if (currentTs !== bestTs) {
      return currentTs > bestTs ? current : best;
    }
    const bestId = Number(best?.id ?? 0);
    const currentId = Number(current?.id ?? 0);
    return currentId > bestId ? current : best;
  }, null);
}

export function buildStackLeaderMap(images) {
  const byStack = new Map();
  for (const img of images) {
    const stackId = getPictureStackId(img);
    if (!stackId || img?.id == null) continue;
    if (!byStack.has(stackId)) {
      byStack.set(stackId, []);
    }
    byStack.get(stackId).push(img);
  }
  const leaders = new Map();
  for (const [stackId, members] of byStack.entries()) {
    const ordered = sortStackMembers(members);
    const leader = ordered[0];
    if (leader?.id != null) {
      leaders.set(stackId, String(leader.id));
    }
  }
  return leaders;
}

export function getStackBadgeCount(img) {
  const count = Number(img?.stackCount ?? img?.stack_count ?? 0);
  return Number.isFinite(count) ? count : 0;
}

export function shouldShowStackBadge(img) {
  return getStackBadgeCount(img) > 1;
}

export function stackBadgeTitle(img) {
  const count = getStackBadgeCount(img);
  if (count <= 1) return '';
  return `Stack of ${count} images`;
}

export function buildStackReorderedMembers(stackItems, orderedIds, stackCount) {
  const byId = new Map(
      stackItems.filter((item) => item && item.id != null)
          .map((item) => [String(item.id), item]),
  );
  return orderedIds
      .map((id, idx) => {
        const item = byId.get(String(id));
        if (!item) return null;
        const next = {...item, stack_position: idx};
        if (idx === 0 && stackCount > 0) {
          next.stackCount = stackCount;
        } else {
          if (next.stackCount !== undefined) delete next.stackCount;
          if (next.stack_count !== undefined) delete next.stack_count;
        }
        return next;
      })
      .filter(Boolean);
}

export function applyStackOrderToList(source, stackId, orderedMembers) {
  if (!Array.isArray(source) || !source.length) return source;
  const result = [];
  let inserted = false;
  for (const item of source) {
    if (getPictureStackId(item) !== stackId) {
      result.push(item);
      continue;
    }
    if (inserted) continue;
    result.push(...orderedMembers);
    inserted = true;
  }
  return result;
}
