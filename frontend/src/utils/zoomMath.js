// Pure zoom arithmetic for the continuous cursor-anchored wheel model shared
// by every zoom surface (the "zoom family": Compare's blink-zoom, the
// lightbox's ImageOverlay, and any surface added later).
//
// The wheel means ZOOM for the whole gesture (owner requirement): wheel over
// a candidate opens the zoom and continued wheeling keeps magnifying, one
// continuous motion. These functions are pure so the invariants - the
// exponential step, the fit floor and ceiling, and above all the CURSOR
// ANCHOR (the image point under the pointer stays stationary through every
// scale change, a binding requirement) - are pinned by unit tests instead of
// riding on jsdom's non-existent layout.
//
// Two anchor solvers share one equation, one per pan transport:
// `anchorZoomScroll` for a scroll-container surface (Compare) and
// `anchorZoomOffset` for a translate+scale transform surface (ImageOverlay).

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
 * The OUTWARD delta, accumulated while already AT the fit floor, that exits
 * an `exit`-policy zoom surface back to its parent (Compare's blink-zoom).
 * Accumulation is the hysteresis: reaching fit stops there (the clamp
 * swallows the remainder of that tick), and only further deliberate wheeling
 * leaves - a trackpad's momentum crumbs cannot blow through, and the boundary
 * cannot flap because reopening takes a wheel over a thumbnail again.
 *
 * Three standard 120-delta notches (owner requirement, 2026-07-30: the old
 * single notch exited too easily - reaching fit should meet deliberate
 * resistance before the view closes). The accumulator starts counting only
 * once the scale sits on the fit floor, and ANY zoom-in movement resets it.
 */
export const ZOOM_EXIT_RESISTANCE = 360;

/**
 * A pause between wheel-out events at the floor longer than this starts the
 * exit accumulation over: a later, separate gesture must meet the full
 * resistance itself instead of inheriting a stale part-way count. Longer than
 * any intra-gesture event gap (including trackpad momentum tails), far
 * shorter than a human "I stopped, then decided to leave".
 */
export const ZOOM_EXIT_GESTURE_GAP_MS = 600;

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
 * across a scale change - the binding anchor requirement, and the standard
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

/**
 * The transform-space twin of `anchorZoomScroll`, for a surface whose pan
 * transport is a `translate(offset) scale(…)` transform on a container-centred
 * image (ImageOverlay) rather than a scroll container.
 *
 * Same equation, different variable: with the image centred in the container,
 * a point p (natural px, measured from the image centre) renders at
 *   visual = containerCentre + offset + p * scale
 * so the cursor pins  p = (cursor − centre − offset) / scale  and the new
 * offset re-solves that for the new scale. The offset is clamped so the image
 * edge never crosses its viewport edge - while the image is smaller than the
 * container on an axis the range is zero and the image re-centres, which is
 * also what re-clamping on zoom-out relies on.
 *
 * @param {Object} args
 * @param {number} args.cursorX - pointer x, relative to the container.
 * @param {number} args.cursorY - pointer y, relative to the container.
 * @param {number} args.offsetX - current translate x.
 * @param {number} args.offsetY - current translate y.
 * @param {number} args.containerWidth
 * @param {number} args.containerHeight
 * @param {number} args.imageWidth - the image's NATURAL width.
 * @param {number} args.imageHeight - the image's NATURAL height.
 * @param {number} args.oldScale - scale before the step (1 = actual pixels).
 * @param {number} args.newScale - scale after the step (already clamped).
 * @returns {{x: number, y: number}} the new translate offsets.
 */
export function anchorZoomOffset({
  cursorX,
  cursorY,
  offsetX,
  offsetY,
  containerWidth,
  containerHeight,
  imageWidth,
  imageHeight,
  oldScale,
  newScale,
}) {
  const axis = (cursor, offset, container, natural) => {
    const fromCentre = cursor - container / 2;
    const point = (fromCentre - offset) / oldScale;
    const target = fromCentre - point * newScale;
    const range = Math.max(0, (natural * newScale - container) / 2);
    return Math.max(-range, Math.min(range, target));
  };
  return {
    x: axis(cursorX, offsetX, containerWidth, imageWidth),
    y: axis(cursorY, offsetY, containerHeight, imageHeight),
  };
}

/**
 * A wheel event's deltaY in PIXELS regardless of the device's delta mode.
 * Line-mode wheels (classic mice on Firefox/Linux report DOM_DELTA_LINE) and
 * page-mode wheels would otherwise feed tiny 1–3 unit deltas into arithmetic
 * tuned for pixel deltas, making the same physical notch zoom ~50× weaker.
 *
 * @param {WheelEvent} event
 * @param {Object} [opts]
 * @param {number} [opts.lineHeightPx=16] - px per line for DOM_DELTA_LINE.
 * @param {number} [opts.pageHeightPx=800] - px per page for DOM_DELTA_PAGE.
 * @returns {number} deltaY in pixels; 0 for a missing/degenerate event.
 */
export function normalizeWheelDelta(
  event,
  { lineHeightPx = 16, pageHeightPx = 800 } = {},
) {
  if (!event) return 0;
  const raw = Number(event.deltaY ?? 0);
  if (!Number.isFinite(raw) || raw === 0) return 0;
  if (event.deltaMode === 1) return raw * lineHeightPx;
  if (event.deltaMode === 2) return raw * pageHeightPx;
  return raw;
}

/**
 * The zoom family's one readout format: whole percent of ACTUAL pixels
 * (100% = 1:1), the photo-tool convention.
 *
 * @param {number} scale - 1 = actual pixels.
 * @returns {string} e.g. "37%"; empty string for a non-finite scale.
 */
export function formatZoomPercent(scale) {
  if (!Number.isFinite(scale)) return "";
  return `${Math.round(scale * 100)}%`;
}
