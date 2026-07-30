// Pure zoom arithmetic for the Compare blink-zoom's continuous wheel model.
//
// The wheel means ZOOM for the whole gesture (owner requirement): wheel over
// a candidate opens the zoom and continued wheeling keeps magnifying, one
// continuous motion. These functions are pure so the invariants — the
// exponential step, the fit floor and ceiling, and above all the CURSOR
// ANCHOR (the image point under the pointer stays stationary through every
// scale change, a binding requirement) — are pinned by unit tests instead of
// riding on jsdom's non-existent layout.

/**
 * How hard one unit of wheel delta zooms. A standard 100-delta notch works
 * out to e^0.2 ≈ 1.22× per notch; a trackpad's small deltas scale down
 * proportionally, which is what keeps the same gesture smooth on both.
 */
export const ZOOM_INTENSITY = 0.002;

/** The ceiling, as a multiple of actual pixels (100% = 1:1). Past ~8× the
 * comparison shows interpolation, not the pictures. */
export const ZOOM_MAX_SCALE = 8;

/**
 * One full wheel notch of OUTWARD delta, accumulated while already at the
 * fit floor, is what closes the zoom back to Compare. Accumulation is the
 * hysteresis: reaching fit stops there (the clamp swallows the remainder of
 * that tick), and only a further deliberate notch leaves — a trackpad's
 * momentum crumbs cannot blow through, and the boundary cannot flap because
 * reopening takes a wheel over a thumbnail again.
 */
export const ZOOM_CLOSE_NOTCH = 120;

/**
 * The next scale for one wheel event: exponential in the delta (wheel up,
 * negative deltaY, zooms in), clamped per event so a wild device delta can
 * at most halve or double, then clamped to the [fit, max] continuum.
 *
 * @param {number} scale - the current scale (1 = actual pixels).
 * @param {number} deltaY - the wheel event's deltaY.
 * @param {number} fitScale - the floor: the scale at which the image fits.
 * @param {number} [maxScale=ZOOM_MAX_SCALE]
 * @returns {number}
 */
export function zoomStepScale(scale, deltaY, fitScale, maxScale = ZOOM_MAX_SCALE) {
  const factor = Math.min(2, Math.max(0.5, Math.exp(-deltaY * ZOOM_INTENSITY)));
  const next = scale * factor;
  return Math.max(fitScale, Math.min(maxScale, next));
}

/**
 * Whether a scale sits at the fit floor (within rounding slack).
 * @param {number} scale
 * @param {number} fitScale
 * @returns {boolean}
 */
export function atFitFloor(scale, fitScale) {
  return scale <= fitScale * 1.001;
}

/**
 * The scroll offsets that keep the image point under the cursor stationary
 * across a scale change — the binding anchor requirement, and the standard
 * map/photo-viewer behaviour.
 *
 * The image is centred by auto margins while smaller than the container, so
 * the cursor→image mapping accounts for the centring margin on each axis:
 *   imagePoint = (scroll + cursor − margin) / scale
 * and the new scroll re-solves that equation for the new scale, clamped to
 * the scrollable range (the point can leave the anchor only when the clamp
 * at an edge forces it, which is the required edge behaviour).
 *
 * @param {Object} args
 * @param {number} args.cursorX - pointer x, relative to the container.
 * @param {number} args.cursorY - pointer y, relative to the container.
 * @param {number} args.scrollLeft
 * @param {number} args.scrollTop
 * @param {number} args.containerWidth
 * @param {number} args.containerHeight
 * @param {number} args.imageWidth - the image's NATURAL width.
 * @param {number} args.imageHeight - the image's NATURAL height.
 * @param {number} args.oldScale
 * @param {number} args.newScale
 * @returns {{left: number, top: number}}
 */
export function anchorZoomScroll({
  cursorX,
  cursorY,
  scrollLeft,
  scrollTop,
  containerWidth,
  containerHeight,
  imageWidth,
  imageHeight,
  oldScale,
  newScale,
}) {
  const axis = (cursor, scroll, container, natural) => {
    const oldMargin = Math.max(0, (container - natural * oldScale) / 2);
    const newMargin = Math.max(0, (container - natural * newScale) / 2);
    const imagePoint = (scroll + cursor - oldMargin) / oldScale;
    const target = imagePoint * newScale + newMargin - cursor;
    const range = Math.max(0, natural * newScale - container);
    return Math.max(0, Math.min(range, target));
  };
  return {
    left: axis(cursorX, scrollLeft, containerWidth, imageWidth),
    top: axis(cursorY, scrollTop, containerHeight, imageHeight),
  };
}
