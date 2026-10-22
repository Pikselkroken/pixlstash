// Compare: one picture laid over another, cut at a slider (the lightbox's
// before/after view), offered for any two pictures picked in the grid or the
// filmstrip.

import { isVideo } from "./media";

export const COMPARE_PICK_REASON = "Select two pictures to compare";
export const COMPARE_VIDEO_REASON = "Compare works on pictures, not videos";

/** The few words a greyed menu row shows for each reason; the tip says it all. */
export const COMPARE_NOTES = {
  [COMPARE_PICK_REASON]: "Select two",
  [COMPARE_VIDEO_REASON]: "Pictures only",
};

/**
 * Why Compare is greyed for these pictures, or null when it can run.
 *
 * @param {Array<object>} pictures - the picked rows, in the order picked.
 * @returns {string|null}
 */
export function compareBlockReason(pictures) {
  const picked = Array.isArray(pictures) ? pictures.filter(Boolean) : [];
  if (picked.length !== 2) return COMPARE_PICK_REASON;
  if (picked.some(isVideo)) return COMPARE_VIDEO_REASON;
  return null;
}
