// Character sheets - see `pixlstash/routes/character_sheets.py`.
//
// A sheet is drawn with an ordinary `POST /workflows/run` of a MOSS-Image
// workflow, `hold` set so it lands in the Scrapheap rather than the library;
// these calls are what is particular to sheets: the prompt and canvas to run
// with, the ranking of what was drawn, the cut of the kept sheets into
// pictures on the character, and the draws themselves, stored on the
// character so the Sheets screen comes back to them after a close or a reload.
// The sheets themselves stay in the Scrapheap.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The layout prompt, negative and canvas for one sheet of the character.
 *
 * @param {{description: string, body?: string, top?: string, trousers?: string,
 *   shoes?: string, framing?: string, subject?: string, trigger?: string,
 *   reference?: boolean, layout?: "moss"|"prose"}} body `reference` names
 *   the person in the run's reference picture; `prose` is the sheet as one
 *   sentence, for an edit model.
 * @returns {Promise<{prompt: string, negative: string, width: number, height: number}>}
 */
export async function buildSheetPrompt(body) {
  return unwrap(apiClient.post("/character-sheets/prompt", body));
}

/**
 * Score drawn sheets by their own faces, and against the character's.
 *
 * @param {number} characterId
 * @param {number[]} pictureIds
 * @param {number[]} [referencePictureIds] the pictures the sheets were drawn
 *   from: closeness is measured against their faces, not the character's.
 * @returns {Promise<{sheets: Array<Object>, references: number}>}
 */
export async function rankSheets(characterId, pictureIds, referencePictureIds) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/rank`, {
      picture_ids: pictureIds,
      ...(referencePictureIds
        ? { reference_picture_ids: referencePictureIds }
        : {}),
    }),
  );
}

/**
 * Cut the chosen sheets into their six panels each, filed on the character.
 *
 * @param {number} characterId
 * @param {number[]} pictureIds
 * @param {number} [drawId] the draw they were drawn in, which records the cut.
 * @returns {Promise<{picture_ids: number[]}>} sheet by sheet, passport photos first.
 */
export async function useSheets(characterId, pictureIds, drawId) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/use`, {
      picture_ids: pictureIds,
      ...(drawId != null ? { draw_id: drawId } : {}),
    }),
  );
}

/**
 * A draw: one or more sheet runs drawn with the same settings.
 *
 * @typedef {Object} SheetDraw
 * @property {number} id
 * @property {number} character_id
 * @property {string} created_at ISO time.
 * @property {Object} settings what the screen drew with, as it stored it.
 * @property {?number} drawn_from the reference picture, when drawn from one.
 * @property {Array<{key: string, status: string, message: string,
 *   picture_ids: number[]}>} runs in the order they were started.
 * @property {number[]} kept the sheets ticked to use.
 * @property {number[]} used the pictures cut from its sheets.
 * @property {number[]} used_sheets the sheets already cut.
 * @property {boolean} seen opened on the Sheets screen since it ended.
 */

/**
 * The character's draws, newest first, with every run's ending read in.
 *
 * @param {number} characterId
 * @returns {Promise<{draws: SheetDraw[]}>}
 */
export async function listSheetDraws(characterId) {
  return unwrap(apiClient.get(`/characters/${characterId}/sheets/draws`));
}

/**
 * Store a new draw on the character.
 *
 * @param {number} characterId
 * @param {{settings: Object, drawn_from: ?number, run_keys: string[]}} body
 * @returns {Promise<SheetDraw>}
 */
export async function createSheetDraw(characterId, body) {
  return unwrap(apiClient.post(`/characters/${characterId}/sheets/draws`, body));
}

/**
 * Add runs to a draw, or set its kept sheets or its seen mark.
 *
 * @param {number} characterId
 * @param {number} drawId
 * @param {{add_run_keys?: string[], kept?: number[], seen?: boolean}} patch
 * @returns {Promise<SheetDraw>}
 */
export async function updateSheetDraw(characterId, drawId, patch) {
  return unwrap(
    apiClient.patch(`/characters/${characterId}/sheets/draws/${drawId}`, patch),
  );
}

/**
 * Forget a draw. Its sheets stay in the Scrapheap, its cut pictures on the
 * character.
 *
 * @param {number} characterId
 * @param {number} drawId
 */
export async function deleteSheetDraw(characterId, drawId) {
  return unwrap(
    apiClient.delete(`/characters/${characterId}/sheets/draws/${drawId}`),
  );
}

/**
 * Every character with a draw: how many, the newest, and every run key, for
 * the sidebar's Sheets rows and the Tasks panel's links.
 *
 * @returns {Promise<{characters: Array<{character_id: number, draws: number,
 *   newest: SheetDraw, run_keys: string[]}>}>}
 */
export async function listSheetDrawSummary() {
  return unwrap(apiClient.get("/character-sheets/draws"));
}
