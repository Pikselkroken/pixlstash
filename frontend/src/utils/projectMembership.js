/**
 * Return an entity's complete project membership, with compatibility for
 * records produced before the multi-project `project_ids` field existed.
 */
export function getEntityProjectIds(entity) {
  const raw = Array.isArray(entity?.project_ids)
    ? entity.project_ids
    : entity?.project_id == null
      ? []
      : [entity.project_id];
  return Array.from(
    new Set(raw.map(Number).filter((id) => Number.isFinite(id))),
  ).sort((a, b) => a - b);
}

export function entityBelongsToProject(entity, projectId) {
  const wanted = Number(projectId);
  return getEntityProjectIds(entity).includes(wanted);
}

/** Toggle one membership without disturbing any of the entity's other projects. */
function toggleEntityProjectId(entity, projectId) {
  const wanted = Number(projectId);
  const current = getEntityProjectIds(entity);
  return current.includes(wanted)
    ? current.filter((id) => id !== wanted)
    : [...current, wanted].sort((a, b) => a - b);
}

/** Request body for the project `+` menu's add/remove action. */
export function toggleEntityProjectPatch(entity, projectId) {
  return { project_ids: toggleEntityProjectId(entity, projectId) };
}

/** Keep the legacy primary scalar aligned for local consumers during refresh. */
export function withEntityProjectIds(entity, projectIds) {
  const ids = getEntityProjectIds({ project_ids: projectIds });
  return {
    ...entity,
    project_ids: ids,
    project_id: ids[0] ?? null,
  };
}
