// The sidebar tree's per-row picture counts, read off the shared entity lists
// (`useEntityListsStore`, `include_counts=true` - issue #651) instead of one
// `/{id}/summary` request per row.
//
// Why this is a module and not four lines inside `SideBar.vue`: every character
// row carries BOTH count scopes (`image_count` for the whole vault,
// `project_image_count` already narrowed to that character's own project, or to
// "in no project" when it has none), so picking between them is the entire
// definition of "which number does this view mode show". Getting it backwards
// is a silent failure - the tree renders a plausible wrong number and nobody
// notices - and `SideBar.vue` is ~10k lines, far past the size where a
// component test could pin it. Pure function here, unit test next door.

/** The `projectViewMode` value whose counts are scoped to a project. */
export const PROJECT_VIEW_MODE = "project";

/**
 * The character-row count writes for one sidebar refresh.
 *
 * @param {Array<Object>} rows - character rows from `useEntityListsStore`.
 * @param {string} viewMode - the sidebar's `projectViewMode`.
 * @returns {Array<{id: number|string, count: number}>} one entry per row that
 *   actually carries a count, in list order.
 */
export function characterCountUpdates(rows, viewMode) {
  return countUpdates(
    rows,
    viewMode === PROJECT_VIEW_MODE ? "project_image_count" : "image_count",
  );
}

/**
 * The project-row count writes for one sidebar refresh.
 *
 * Projects have a single scope, so there is no view-mode choice here. The
 * "unassigned" bucket is not a row in this list and is counted separately.
 *
 * @param {Array<Object>} rows - project rows from `useEntityListsStore`.
 * @returns {Array<{id: number|string, count: number}>}
 */
export function projectCountUpdates(rows) {
  return countUpdates(rows, "image_count");
}

/**
 * Read one count field off each row, skipping the rows that have no answer.
 *
 * A missing field (older backend) or an explicit `null` (a list read made
 * without `include_counts`) must LEAVE THE PREVIOUS NUMBER in place rather than
 * blanking the row; a real `0` is an answer and still writes.
 */
function countUpdates(rows, field) {
  if (!Array.isArray(rows)) return [];
  const updates = [];
  for (const row of rows) {
    if (row == null || row.id == null) continue;
    const count = row[field];
    if (count == null) continue;
    updates.push({ id: row.id, count });
  }
  return updates;
}
