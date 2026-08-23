/**
 * Where a picture imported "here" should be filed.
 *
 * Pasting or dropping while looking at a set or a character should put the
 * picture in what you are looking at. `openStagingSession` has always accepted
 * `setId` / `characterId` / `projectId` and applies them server-side on commit,
 * but neither caller supplied them: the paste handler passed only the project,
 * and the grid's drop handler passed a `selectedCharacterId` key that
 * `ImageImporter.startImport` does not read, so the association was silently
 * dropped on both paths.
 *
 * Shared by both callers rather than resolved twice, and kept a pure function
 * of the selection so it can be tested without mounting anything.
 *
 * Virtual views are not destinations. "All Pictures", "Unassigned" and the
 * scrapheap are filters over the library, not containers, so an import made
 * while looking at one is filed nowhere in particular -- which is what those
 * views mean.
 *
 * A multi-selection is ambiguous and therefore declined: with two sets on
 * screen there is no answer to which one the picture joins, and picking the
 * first would be a coin toss the user cannot see. Filing it nowhere is
 * recoverable by hand; filing it in the wrong set quietly is not.
 */

import {
  ALL_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
} from "../stores/useViewStore";

/** Ids that name a filter over the library rather than something to join. */
const VIRTUAL_VIEW_IDS = new Set([
  ALL_PICTURES_ID,
  UNASSIGNED_PICTURES_ID,
  SCRAPHEAP_PICTURES_ID,
]);

/** The one id in `ids`, or null when there are none or more than one. */
function soleId(ids, single) {
  const list = Array.isArray(ids) && ids.length ? ids : [];
  const candidates = list.length ? list : single != null ? [single] : [];
  if (candidates.length !== 1) return null;
  const id = candidates[0];
  if (
    id == null ||
    VIRTUAL_VIEW_IDS.has(id) ||
    VIRTUAL_VIEW_IDS.has(String(id))
  )
    return null;
  return id;
}

/**
 * @param {object} selection The selection store (or a plain stand-in).
 * @returns {{setId: (string|number|null), characterId: (string|number|null)}}
 */
export function resolveImportTarget(selection = {}) {
  return {
    setId: soleId(selection.selectedSetIds, selection.selectedSet),
    characterId: soleId(
      selection.selectedCharacterIds,
      selection.selectedCharacter,
    ),
  };
}
