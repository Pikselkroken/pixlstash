// "Start a cluster from these…": a person's face cluster seeded from the
// pictures selected in their view (FaceClustersDialog's "Your cluster").

/**
 * The person whose grid this is, or null for All pictures, Unassigned or the
 * Scrapheap: only a person's own view says whose faces to rank.
 *
 * @param {string|number|null} selectedCharacter - `selectionStore.selectedCharacter`.
 * @returns {number|null}
 */
export function seedPersonId(selectedCharacter) {
  const id = Number(selectedCharacter);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/**
 * Why both grid menus grey "Start a cluster from these…", or null.
 *
 * @param {{personId: number|null, readOnly: boolean, count: number}} state
 * @returns {string|null}
 */
export function seedClusterReason({ personId, readOnly, count }) {
  // The face-clusters route is owner-only.
  if (readOnly) return "Face clusters need owner access";
  if (personId == null) return "Open a person to start a cluster from these";
  if (!count) return "Select pictures first";
  return null;
}
