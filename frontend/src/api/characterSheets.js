// Character sheets - see `pixlstash/routes/character_sheets.py`.
//
// A sheet is drawn with an ordinary `POST /workflows/run` of a MOSS-Image
// workflow, `hold` set so it lands in the Scrapheap rather than the library;
// these calls are what is particular to sheets: the prompt and canvas to run
// with, the ranking of what was drawn, the cut of the kept sheets into
// pictures on the character, and the discarding of the sheets themselves.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The layout prompt, negative and canvas for one sheet of the character.
 *
 * @param {{description: string, body?: string, top?: string, trousers?: string,
 *   shoes?: string, framing?: string, subject?: string, trigger?: string}} body
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
 * @returns {Promise<{sheets: Array<Object>, references: number}>}
 */
export async function rankSheets(characterId, pictureIds) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/rank`, {
      picture_ids: pictureIds,
    }),
  );
}

/**
 * Cut the chosen sheets into their six panels each, filed on the character.
 *
 * @param {number} characterId
 * @param {number[]} pictureIds
 * @returns {Promise<{picture_ids: number[]}>} sheet by sheet, passport photos first.
 */
export async function useSheets(characterId, pictureIds) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/use`, {
      picture_ids: pictureIds,
    }),
  );
}

/**
 * Destroy sheets drawn held: they were never the library's. Pictures that are
 * not held sheets are left alone by the server.
 *
 * @param {number} characterId
 * @param {number[]} pictureIds
 * @returns {Promise<{picture_ids: number[]}>} the sheets destroyed.
 */
export async function discardSheets(characterId, pictureIds) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/discard`, {
      picture_ids: pictureIds,
    }),
  );
}
