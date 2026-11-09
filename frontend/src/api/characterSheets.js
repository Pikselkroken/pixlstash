// Character sheets - see `pixlstash/routes/character_sheets.py`.
//
// A sheet is drawn with an ordinary `POST /workflows/run` of a MOSS-Image
// workflow; these three calls are what is particular to sheets: the prompt and
// canvas to run with, the ranking of what was drawn, and the cut of the chosen
// sheet into pictures on the character.

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
 * Cut the chosen sheet into its six panels, filed on the character.
 *
 * @param {number} characterId
 * @param {number} pictureId
 * @returns {Promise<{picture_ids: number[]}>} passport photos first.
 */
export async function useSheet(characterId, pictureId) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/${pictureId}/use`),
  );
}
