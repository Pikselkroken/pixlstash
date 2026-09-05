// Pure geometry helpers for the square (uniform-grid) thumbnail mode.
//
// Thumbnail v2 stores ONE aspect-ratio-preserving bitmap per picture (the whole
// frame, short edge ~384px) plus a stored face-weighted square rectangle within
// that bitmap. Justified mode shows the whole bitmap; square mode sprite-crops it
// to the stored rectangle so the face framing is preserved (rather than a naive
// object-fit:cover centre-crop, which loses it).
//
// All functions are pure and unit-tested (squareCrop.test.js). They operate in
// BITMAP pixel space: (square_crop_x, square_crop_y) is the top-left of the crop
// square within the thumbnail_width × thumbnail_height bitmap, and
// square_crop_side is its side length (= min(w, h) except the extreme-panorama
// cap). faces[].bbox / detections[].bbox arrive already mapped into this same
// bitmap pixel space.

/**
 * Normalise the stored square-crop rectangle for a grid image.
 *
 * Returns null (→ caller falls back to object-fit:cover centring) when the AR
 * bitmap dims or the crop origin are missing - i.e. a picture that is still
 * being (re)processed during the one-time thumbnail-v2 upgrade regen.
 *
 * @param {Object} img - Grid image object.
 * @returns {{tw:number, th:number, cx:number, cy:number, side:number}|null}
 */
export function squareCropParams(img) {
  if (!img) return null;
  // Nullable while unprocessed. Reject null/undefined explicitly - Number(null)
  // is 0 (would masquerade as a valid crop origin), so a bare Number() check is
  // not enough to trigger the fallback.
  if (img.square_crop_x == null || img.square_crop_y == null) return null;
  const tw = Number(img.thumbnail_width);
  const th = Number(img.thumbnail_height);
  const cx = Number(img.square_crop_x);
  const cy = Number(img.square_crop_y);
  if (
    !(tw > 0) ||
    !(th > 0) ||
    !Number.isFinite(cx) ||
    !Number.isFinite(cy)
  ) {
    return null;
  }
  // square_crop_side is normally min(w, h). Derive it if the backend omitted it.
  let side = Number(img.square_crop_side);
  if (!(side > 0)) side = Math.min(tw, th);
  return { tw, th, cx, cy, side };
}

/**
 * Inline `<img>` style that sprite-crops the AR bitmap into a square cell.
 *
 * The cell (container) is overflow:hidden and square (side = S in CSS pixels).
 * The <img> is scaled by S/side and translated by -crop*S/side, expressed as
 * percentages so the exact pixel size S never has to be known here:
 *   width  = thumbnail_width  / side * 100%   (relative to container width S)
 *   height = thumbnail_height / side * 100%   (relative to container height S)
 *   left   = -square_crop_x   / side * 100%   (percentage left is relative to S)
 *   top    = -square_crop_y   / side * 100%
 * The container being square, its width and height both equal S, so the same
 * `side` denominator maps both axes correctly.
 *
 * @param {Object} img - Grid image object.
 * @returns {Object|null} Inline style object, or null to fall back to CSS cover.
 */
export function squareCropImgStyle(img) {
  const params = squareCropParams(img);
  if (!params) return null;
  const { tw, th, cx, cy, side } = params;
  return {
    width: `${(tw / side) * 100}%`,
    height: `${(th / side) * 100}%`,
    left: `${(-cx / side) * 100}%`,
    top: `${(-cy / side) * 100}%`,
    // Both dims are explicit and match the bitmap AR, so aspect-ratio must not
    // fight them and object-fit is a no-op - set them defensively.
    aspectRatio: "auto",
    objectFit: "fill",
    // Rounded corners frame the cell (container), not this oversized img.
    borderRadius: "0",
  };
}

/**
 * Map a bbox (in AR-bitmap pixel space) into rendered cell pixels for square
 * mode: subtract the crop offset then scale by S/side. Boxes partly outside the
 * crop produce out-of-cell coordinates and are clipped by the cell's
 * overflow:hidden at the container edge.
 *
 * @param {number[]} bbox - [x0, y0, x1, y1] in bitmap pixels.
 * @param {{cx:number, cy:number, side:number}} params - Crop rectangle.
 * @param {number} cellSize - Rendered cell size S in CSS pixels (container width).
 * @returns {{left:number, top:number, width:number, height:number}}
 */
export function squareCropBboxRect(bbox, params, cellSize) {
  const { cx, cy, side } = params;
  const scale = cellSize / side;
  return {
    left: (bbox[0] - cx) * scale,
    top: (bbox[1] - cy) * scale,
    width: (bbox[2] - bbox[0]) * scale,
    height: (bbox[3] - bbox[1]) * scale,
  };
}

/**
 * Map a bbox (in AR-bitmap pixel space) into rendered cell pixels for the
 * object-fit:cover fallback (used in justified mode with the whole bitmap, and
 * in square mode before the crop rectangle has been populated). Mirrors CSS
 * `object-fit: cover` + `object-position: top center`: scale to fill, centre
 * horizontally, top-anchor vertically.
 *
 * @param {number[]} bbox - [x0, y0, x1, y1] in bitmap pixels.
 * @param {number} naturalWidth - Bitmap width in pixels.
 * @param {number} naturalHeight - Bitmap height in pixels.
 * @param {number} containerWidth - Rendered container width in CSS pixels.
 * @param {number} containerHeight - Rendered container height in CSS pixels.
 * @returns {{left:number, top:number, width:number, height:number}}
 */
export function coverBboxRect(
  bbox,
  naturalWidth,
  naturalHeight,
  containerWidth,
  containerHeight,
) {
  const scale = Math.max(
    containerWidth / naturalWidth,
    containerHeight / naturalHeight,
  );
  const displayWidth = naturalWidth * scale;
  const offsetX = (containerWidth - displayWidth) / 2;
  const offsetY = 0; // object-position: top
  return {
    left: offsetX + bbox[0] * scale,
    top: offsetY + bbox[1] * scale,
    width: (bbox[2] - bbox[0]) * scale,
    height: (bbox[3] - bbox[1]) * scale,
  };
}
