// Pure geometry helpers for cropping a thumbnail around its stored rectangle:
// the square (uniform-grid) thumbnail mode, and since #1465 any cell shape,
// which is what the workflow cards' 6:5 / 4:5 / 3:5 covers need.
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
  if (!(tw > 0) || !(th > 0) || !Number.isFinite(cx) || !Number.isFinite(cy)) {
    return null;
  }
  // square_crop_side is normally min(w, h). Derive it if the backend omitted it.
  let side = Number(img.square_crop_side);
  if (!(side > 0)) side = Math.min(tw, th);
  return { tw, th, cx, cy, side };
}

/**
 * The crop rectangle for a cell of ANY aspect ratio, in bitmap pixels.
 *
 * The stored rectangle is square; a cell mostly is not (the workflow card's
 * covers are 6:5, 4:5 and 3:5). So the square is treated as the picture's
 * region of interest and the cell's ratio is fitted AROUND it: the smallest
 * rectangle of that ratio containing the square, shrunk to the bitmap where it
 * does not fit, then centred on the square's centre and clamped to the bitmap
 * edges. The centre is what survives the shrink, which is the point - it is
 * where `FaceUtils.square_crop_rect` put the faces.
 *
 * `ratio` 1 reproduces the stored rectangle exactly: w = h = side, and the
 * centring is a no-op because a stored square is already inside the bitmap.
 *
 * @param {{tw:number, th:number, cx:number, cy:number, side:number}} params
 * @param {number} ratio - The cell's width / height.
 * @returns {{x:number, y:number, w:number, h:number}|null}
 */
export function cropRectForRatio(params, ratio) {
  if (!(ratio > 0)) return null;
  const { tw, th, cx, cy, side } = params;
  let w = ratio >= 1 ? side * ratio : side;
  let h = w / ratio;
  if (w > tw) {
    w = tw;
    h = tw / ratio;
  }
  if (h > th) {
    h = th;
    w = th * ratio;
  }
  return {
    x: Math.min(Math.max(cx + side / 2 - w / 2, 0), tw - w),
    y: Math.min(Math.max(cy + side / 2 - h / 2, 0), th - h),
    w,
    h,
  };
}

/**
 * Inline `<img>` style that sprite-crops the AR bitmap into a cell of `ratio`.
 *
 * The cell (container) is overflow:hidden, W wide and H = W/ratio tall, and
 * the `<img>` inside it is absolutely positioned. The img is scaled by W/w and
 * translated by -x, expressed as percentages so the cell's pixel size never
 * has to be known here (a percentage `left` resolves against W and a
 * percentage `top` against H, which is exactly the two denominators below):
 *   width  = thumbnail_width  / w * 100%
 *   height = thumbnail_height / h * 100%
 *   left   = -x / w * 100%
 *   top    = -y / h * 100%
 * `w` never exceeds `tw` and `h` never exceeds `th`, so the img covers the cell
 * on both axes however far the cell's real ratio has drifted from `ratio` (a
 * gap between the grid's tracks puts it a fraction of a percent out).
 *
 * @param {Object} img - Anything carrying the bitmap and crop fields: a grid
 *   image, or one of a workflow card's `covers`.
 * @param {number} [ratio=1] - The cell's width / height.
 * @returns {Object|null} Inline style object, or null to fall back to CSS cover.
 */
export function cropImgStyle(img, ratio = 1) {
  const params = squareCropParams(img);
  if (!params) return null;
  const rect = cropRectForRatio(params, ratio);
  if (!rect) return null;
  const { tw, th } = params;
  return {
    width: `${(tw / rect.w) * 100}%`,
    height: `${(th / rect.h) * 100}%`,
    left: `${(-rect.x / rect.w) * 100}%`,
    top: `${(-rect.y / rect.h) * 100}%`,
    // Both dims are explicit and match the bitmap AR, so aspect-ratio must not
    // fight them and object-fit is a no-op - set them defensively.
    aspectRatio: "auto",
    objectFit: "fill",
    // Rounded corners frame the cell (container), not this oversized img.
    borderRadius: "0",
  };
}

/**
 * The square case, which is what the uniform grid's cells are.
 *
 * @param {Object} img - Grid image object.
 * @returns {Object|null} Inline style object, or null to fall back to CSS cover.
 */
export function squareCropImgStyle(img) {
  return cropImgStyle(img, 1);
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
