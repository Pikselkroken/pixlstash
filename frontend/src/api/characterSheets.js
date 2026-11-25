// Character sheets - see `pixlstash/routes/character_sheets.py`.
//
// A sheet is drawn with an ordinary `POST /workflows/run` of a MOSS-Image
// (or Krea 2) workflow, `hold` set so it lands in the Scrapheap rather than the library;
// these calls are what is particular to sheets: the prompt and canvas to run
// with, the ranking of what was drawn, the cut of the kept sheets into
// pictures on the character. The sheets themselves stay in the Scrapheap.

import { apiClient } from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/**
 * The layout prompt, negative and canvas for one sheet of the character.
 *
 * @param {{description: string, body?: string, top?: string, trousers?: string,
 *   shoes?: string, framing?: string, subject?: string, trigger?: string,
 *   reference?: boolean, layout?: "moss"|"prose"|"regions"}} body `reference`
 *   names the person in the run's reference picture; `prose` is the sheet as
 *   one sentence, for an edit model or Krea 2; `regions` is the layout for a
 *   sheet copy of another text-to-image workflow.
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
 * @param {Record<number, number[][]>} [boxes] the photo boxes drawn by hand,
 *   keyed by sheet picture id: six `[left, top, right, bottom]` fractions each.
 *   A sheet without one is cut automatically; none sends no `boxes` at all.
 * @returns {Promise<{picture_ids: number[]}>} sheet by sheet, passport photos first.
 */
export async function useSheets(characterId, pictureIds, boxes = {}) {
  return unwrap(
    apiClient.post(`/characters/${characterId}/sheets/use`, {
      picture_ids: pictureIds,
      ...(Object.keys(boxes).length ? { boxes } : {}),
    }),
  );
}

/**
 * Score drawn outfit, location or prop sheets by how their four panels agree.
 * No faces to wait for: a sheet is scored as soon as it is imported.
 *
 * @param {"outfit"|"location"|"prop"} kind
 * @param {number[]} pictureIds
 * @returns {Promise<{sheets: Array<Object>}>}
 */
export async function rankItemSheets(kind, pictureIds) {
  return unwrap(
    apiClient.post("/item-sheets/rank", { kind, picture_ids: pictureIds }),
  );
}

/**
 * Cut the chosen item sheets into their four panels each, filed on nobody.
 *
 * @param {"outfit"|"location"|"prop"} kind
 * @param {number[]} pictureIds
 * @param {Record<number, number[][]>} [boxes] four hand-drawn boxes a sheet.
 * @returns {Promise<{picture_ids: number[]}>} sheet by sheet, row by row.
 */
export async function useItemSheets(kind, pictureIds, boxes = {}) {
  return unwrap(
    apiClient.post("/item-sheets/use", {
      kind,
      picture_ids: pictureIds,
      ...(Object.keys(boxes).length ? { boxes } : {}),
    }),
  );
}
