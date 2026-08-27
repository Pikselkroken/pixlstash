// Rotate a photo in place - the copy and the gates, as pure functions.
//
// Four surfaces offer the same action (the lightbox toolbar, its `[` / `]`
// shortcuts, the grid context menu and the selection bar's overflow) and every
// one of them has to answer the same two questions before it renders: *can this
// picture be rotated at all*, and *what does the control say when it cannot*.
// Answering them four times is how a control ends up enabled on one surface and
// greyed on another, so they are answered once here and tested without a
// mounted view - the same argument `keepCoverOnly.js` makes for its own copy.
//
// The context menu and the selection menu are held to a stricter rule still:
// #403's parity spec fails the build if a multi-picture selection can reach an
// action from one of them and not the other, which is what caught this pair
// being wired to the context menu alone.
//
// **In-place rotate is an EXIF edit, not a re-encode.** The backend rewrites the
// orientation tag and copies every other byte through, which is why it is
// instant and lossless. What it needs in exchange is that every renderer agrees
// on the tag - and the browser only does for JPEG. Measured 2026-08-18: both
// Chromium 148 and Firefox 150 ignore a PNG's `eXIf` orientation exactly as
// they ignore WebP's. That is NOT what gates this list, though: the media route
// serves anything the browser will not turn already transposed
// (`BROWSER_ORIENTED_FORMATS` in `routes/pictures/_serving.py`), so PNG is
// offered and renders correctly on both halves. WebP stays out, and
// TIFF/BMP/GIF/video are out for the ordinary reasons.
// `supports_in_place_rotation` on the server is the authority; this is the
// client half, which exists only so the control is greyed *before* the
// round-trip rather than after it.

import { MediaFormat } from "./media";

/** Wire values for the endpoint's `direction`, mirroring `orientation.py`. */
export const ROTATE_CW = "cw";
export const ROTATE_CCW = "ccw";

/**
 * The operation type the server records for a rotate.
 *
 * Used to arm the receipt's second sentence, which is consumed by the FIRST
 * receipt whose op type matches - a mismatch drops the note rather than
 * carrying it onto an unrelated action, so a drift here is silent. Keep it in
 * step with the backend's operation log.
 */
export const ROTATE_OP_TYPE = "pictures.rotate";

/** Formats whose bytes can carry a rotation the whole stack agrees on. */
const IN_PLACE_ROTATABLE_FORMATS = new Set(["jpg", "jpeg", "png"]);

/**
 * Why this picture cannot be rotated in place, worded for a tooltip.
 *
 * Both sentences point at the route that DOES work rather than stopping at the
 * refusal: the Filters menu still makes a rotated copy of anything, so a user
 * told only "no" would have to rediscover that on their own.
 */
export const ROTATE_FORMAT_REASON =
  "PNG and JPEG only - use Filters > Rotate to make a rotated copy";
export const ROTATE_REFERENCE_FOLDER_REASON =
  "Reference-folder files are never written to - use Filters > Rotate to make a rotated copy";

/**
 * Can this picture's own file be rotated in place?
 *
 * @param {Object|null} image - a grid or overlay picture record; needs `format`
 *   and `reference_folder_id`, both of which ride the grid listing.
 * @returns {boolean}
 */
export function canRotateInPlace(image) {
  if (!image) return false;
  if (image.reference_folder_id != null) return false;
  return IN_PLACE_ROTATABLE_FORMATS.has(MediaFormat(image));
}

/**
 * Why the rotate control is unavailable for these pictures, or `null`.
 *
 * **One rotatable picture is enough.** A mixed selection stays enabled and the
 * response's `unsupported_picture_ids` reports what was left alone afterwards,
 * which is the rule Delete and Keep cover only already follow: refusing a whole
 * selection because one member is a WebP would be a worse outcome than doing
 * the work that can be done and saying so.
 *
 * @param {Array<Object>} images - the picture records the action would target.
 * @returns {string|null} a tooltip sentence, or `null` when the action is live.
 */
export function rotateBlockReason(images) {
  const targets = Array.isArray(images) ? images.filter(Boolean) : [];
  if (!targets.length) return null;
  if (targets.some(canRotateInPlace)) return null;
  // Nothing here can rotate. Name the reason that actually applies; a selection
  // that mixes both refusals falls back to the format sentence, which is the
  // one a user can act on by picking different pictures.
  const everyOneIsReference = targets.every(
    (image) => image?.reference_folder_id != null,
  );
  return everyOneIsReference
    ? ROTATE_REFERENCE_FOLDER_REASON
    : ROTATE_FORMAT_REASON;
}

/**
 * The menu item's label, which has to state how much it is about to act on.
 *
 * A bare "Rotate left" over a 12-picture selection reads as an action on the
 * one picture under the cursor, which is the wrong picture in exactly the case
 * where being wrong costs the most.
 *
 * @param {string} direction - {@link ROTATE_CW} or {@link ROTATE_CCW}.
 * @param {number} count - pictures the action would target.
 * @returns {string} e.g. `"Rotate left"` or `"Rotate 12 photos left"`.
 */
export function rotateMenuLabel(direction, count) {
  const side = direction === ROTATE_CW ? "right" : "left";
  const total = Number(count) || 0;
  if (total <= 1) return `Rotate ${side}`;
  return `Rotate ${total.toLocaleString()} photos ${side}`;
}

/**
 * The receipt's second sentence: what the rotate did NOT touch.
 *
 * On the same pill as what it did, never a notice of its own - two surfaces for
 * one action means the user reads the reassuring half and dismisses the half
 * that needed a decision. The two buckets are the server's own and are
 * disjoint: `unsupported_picture_ids` is a file this rotate will not write (a
 * format whose orientation tag cannot be spliced, or a reference-folder
 * original), which the Filters route can still serve; `skipped_picture_ids` is
 * a picture that was not there to turn, which is a different problem with a
 * different fix. A LOCKED selection reaches neither bucket - the endpoint
 * refuses the whole request, which surfaces as an error rather than a note.
 *
 * @param {Object|null} result - the endpoint's response body.
 * @returns {string} `""` when everything asked for was rotated.
 */
export function rotateSkipNote(result) {
  const countOf = (value) => (Array.isArray(value) ? value.length : 0);
  const unsupported = countOf(result?.unsupported_picture_ids);
  const skipped = countOf(result?.skipped_picture_ids);
  if (!unsupported && !skipped) return "";
  const parts = [];
  if (unsupported) {
    parts.push(
      `${unsupported} left as ${unsupported === 1 ? "it is" : "they are"} ` +
        `(Filters > Rotate makes a rotated copy of those)`,
    );
  }
  if (skipped) {
    parts.push(`${skipped} skipped (no longer in the library)`);
  }
  return `${parts.join("; ")}.`;
}
