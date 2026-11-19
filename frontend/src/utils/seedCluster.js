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

export const SEED_REASONS = {
  readOnly: "Face clusters need owner access",
  noPerson: "Open a person to start a cluster from these",
  noSelection: "Select pictures first",
};

/** The few words a greyed menu row shows for each reason; the tip says it all. */
export const SEED_NOTES = {
  [SEED_REASONS.readOnly]: "Owner only",
  [SEED_REASONS.noPerson]: "In a person",
  [SEED_REASONS.noSelection]: "Select first",
};

/**
 * Why both grid menus grey "Start a cluster from these…", or null.
 *
 * @param {{personId: number|null, readOnly: boolean, count: number}} state
 * @returns {string|null}
 */
export function seedClusterReason({ personId, readOnly, count }) {
  // The face-clusters route is owner-only.
  if (readOnly) return SEED_REASONS.readOnly;
  if (personId == null) return SEED_REASONS.noPerson;
  if (!count) return SEED_REASONS.noSelection;
  return null;
}
