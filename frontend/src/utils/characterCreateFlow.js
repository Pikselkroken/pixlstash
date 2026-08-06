// Pure helpers for the "create a person from the grid context menu" flow
// (#645). Kept out of ImageGrid.vue so the default naming and the
// face-vs-picture assignment choice stay unit-testable without mounting the
// grid. See docs/frontend_architecture.md §6 (utilities are pure functions).

/**
 * Next free default person name in the "Character NNNN" series.
 *
 * Mirrors SideBar.createCharacter so both entry points name new people
 * identically.
 *
 * @param {Array<Object>} characters - existing characters (rows with `name`).
 * @returns {string} the first unused "Character NNNN" name.
 */
export function nextFreeCharacterName(characters) {
  const existingNames = new Set(
    (Array.isArray(characters) ? characters : []).map((c) => c?.name),
  );
  let num = 1;
  let name;
  do {
    name = `Character ${num.toString().padStart(4, "0")}`;
    num++;
  } while (existingNames.has(name));
  return name;
}

/**
 * Decide what the post-save assignment should send.
 *
 * A non-empty face selection wins: the user pointed at specific detections, so
 * the face-id endpoint is the right one. Otherwise the whole-picture endpoint
 * gets the selected picture ids and the backend picks the best face per
 * picture.
 *
 * @param {Object} selection - the grid selection captured at flow start.
 * @param {Array<number|string>} [selection.pictureIds] - selected picture ids.
 * @param {Array<{imageId: *, faceIdx: number, faceId: *}>} [selection.faceEntries]
 *   - the face selection entries from useMultiSelect.
 * @returns {{mode: "faces"|"pictures"|"none", ids: Array, pictureIds: Array}}
 *   `ids` is what the API call sends (face ids or picture ids); `pictureIds`
 *   is the affected pictures either way, for grid bookkeeping.
 */
export function chooseCharacterAssignment({
  pictureIds = [],
  faceEntries = [],
} = {}) {
  const entries = (Array.isArray(faceEntries) ? faceEntries : []).filter(
    (entry) => entry?.faceId !== undefined && entry?.faceId !== null,
  );
  if (entries.length) {
    return {
      mode: "faces",
      ids: entries.map((entry) => entry.faceId),
      pictureIds: [
        ...new Set(
          entries
            .map((entry) => entry.imageId)
            .filter((id) => id !== undefined && id !== null),
        ),
      ],
    };
  }
  const ids = (Array.isArray(pictureIds) ? pictureIds : []).filter(
    (id) => id !== undefined && id !== null,
  );
  if (!ids.length) {
    return { mode: "none", ids: [], pictureIds: [] };
  }
  return { mode: "pictures", ids, pictureIds: ids };
}
