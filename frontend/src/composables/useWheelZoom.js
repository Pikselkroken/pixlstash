// The zoom family's stateful core: continuous cursor-anchored wheel zoom over
// a fit→max continuum with fit and 100% as snap stops. The pure arithmetic
// (exponential step, anchor equations, delta normalization, percent format)
// lives in utils/zoomMath.js; this composable is the glue - the scale ref and
// fit measurement, the wheel handler, snap-to-stop, the floor-policy dispatch,
// pan clamping, and the settle detection that feeds the aria announcer.
//
// The FEEL is shared and non-overridable (owner ruling): ZOOM_INTENSITY, the
// per-event 0.5–2× clamp, the cursor-anchor equations, the near-stop slack,
// wheel-delta normalization, the 500 ms settle window, and the percent
// formatter are constants of the family. Per-surface parameters are the entry
// level (always fit), the max scale, the floor behavior (`rest`: hard clamp at
// fit, the surface is a destination - vs `exit`: three accumulated notches of
// deliberate resistance at the floor leave the surface, Compare's
// hysteresis), and the pan transport
// (this composable speaks transform offsets via anchorZoomOffset; a
// scroll-container surface keeps anchorZoomScroll).
//
// Pinch-ready: every anchor point below is just an {x, y} in container space,
// so a pinch centroid can drive `wheelZoom`/`snapTo` unchanged when touch
// pinch lands (named follow-up in docs/frontend_architecture.md).

import { computed, getCurrentScope, onScopeDispose, ref } from "vue";
import {
  anchorZoomOffset,
  atFitFloor,
  formatZoomPercent,
  normalizeWheelDelta,
  zoomStepScale,
  ZOOM_EXIT_GESTURE_GAP_MS,
  ZOOM_EXIT_RESISTANCE,
  ZOOM_MAX_SCALE,
} from "../utils/zoomMath";

/** Snap-stop slack: within 1% of a stop counts as being ON it (shared feel,
 * same tolerance the Compare surface ships). */
const NEAR_SCALE_SLACK = 0.01;

/** The announcer's settle window: a wheel gesture announces once, 500 ms
 * after its last scale change; snap stops announce immediately. */
export const ZOOM_SETTLE_MS = 500;

/**
 * @param {Object} [options]
 * @param {number} [options.maxScale=ZOOM_MAX_SCALE] resolution-relative
 *   ceiling; the effective ceiling is max(maxScale, fitScale) so an image
 *   whose fit exceeds it still opens legally.
 * @param {"rest"|"exit"} [options.floorPolicy="rest"] what wheeling out AT the
 *   fit floor does: `rest` clamps hard (no exit, no hysteresis); `exit`
 *   accumulates ZOOM_EXIT_RESISTANCE of outward delta (three notches of
 *   deliberate resistance; a pause restarts the count) then calls onExit.
 * @param {Function} [options.onExit] required for floorPolicy "exit".
 */
export function useWheelZoom(options = {}) {
  const {
    maxScale = ZOOM_MAX_SCALE,
    floorPolicy = "rest",
    onExit = null,
  } = options;

  /** Current scale, 1 = actual pixels; null until measured. */
  const scale = ref(null);
  /** The floor of the continuum: the scale at which the image exactly fits. */
  const fitScale = ref(1);
  /** Pan offset in transform space (the translate() outside the scale()). */
  const offset = ref({ x: 0, y: 0 });
  const container = ref({ width: 0, height: 0 });
  const natural = ref({ width: 0, height: 0 });
  /** What the visually-hidden role="status" node announces. */
  const announcement = ref("");

  let exitAccumulator = 0;
  let exitLastOutTs = 0;
  let settleTimer = null;

  const effectiveMaxScale = computed(() => Math.max(maxScale, fitScale.value));

  /** Within the shared slack of a target scale. */
  function nearScale(target) {
    return (
      scale.value !== null &&
      Math.abs(scale.value - target) <= target * NEAR_SCALE_SLACK
    );
  }

  const atFit = computed(() => nearScale(fitScale.value));
  const atActual = computed(() => nearScale(1));
  /** Above the fit floor: pan has meaning, drag means pan not drag-out. */
  const aboveFit = computed(
    () =>
      scale.value !== null &&
      scale.value > fitScale.value * (1 + NEAR_SCALE_SLACK),
  );
  /** The button readout: whole percent of actual pixels; empty until measured
   * (the reserved label width keeps the toolbar from jumping either way). */
  const percentLabel = computed(() =>
    scale.value === null ? "" : formatZoomPercent(scale.value),
  );
  /** The CSS scale for a transform transport whose un-transformed layout is
   * the fitted image: transform scale 1 IS fit. */
  const transformScale = computed(() =>
    scale.value === null || fitScale.value <= 0
      ? 1
      : scale.value / fitScale.value,
  );

  function clampOffsetAxis(value, containerSize, naturalSize) {
    const range = Math.max(
      0,
      (naturalSize * (scale.value ?? 0) - containerSize) / 2,
    );
    return Math.max(-range, Math.min(range, value));
  }

  /** Re-clamp the pan so the image edge never crosses its viewport edge; on
   * an axis where the image fits, the range is zero and the image re-centres. */
  function reclampOffset() {
    offset.value = {
      x: clampOffsetAxis(
        offset.value.x,
        container.value.width,
        natural.value.width,
      ),
      y: clampOffsetAxis(
        offset.value.y,
        container.value.height,
        natural.value.height,
      ),
    };
  }

  /**
   * The fit measurement hook: feed it whenever the image or the viewport
   * changes size. Entry level is fit; a re-measure keeps a fit-parked scale ON
   * the (new) fit, keeps a zoomed scale and re-clamps it to the new floor and
   * ceiling, and re-clamps the pan either way.
   */
  function setMeasurements({
    containerWidth,
    containerHeight,
    naturalWidth,
    naturalHeight,
  }) {
    if (
      !(containerWidth > 0) ||
      !(containerHeight > 0) ||
      !(naturalWidth > 0) ||
      !(naturalHeight > 0)
    ) {
      return;
    }
    const wasAtFit = scale.value === null || atFit.value;
    container.value = { width: containerWidth, height: containerHeight };
    natural.value = { width: naturalWidth, height: naturalHeight };
    fitScale.value = Math.min(
      containerWidth / naturalWidth,
      containerHeight / naturalHeight,
    );
    if (wasAtFit) {
      scale.value = fitScale.value;
      offset.value = { x: 0, y: 0 };
    } else {
      scale.value = Math.max(
        fitScale.value,
        Math.min(effectiveMaxScale.value, scale.value),
      );
      reclampOffset();
    }
  }

  function announcementText() {
    const pct = formatZoomPercent(scale.value);
    return atFit.value ? `Zoom fit, ${pct}` : `Zoom ${pct}`;
  }

  function clearSettleTimer() {
    if (settleTimer !== null) {
      clearTimeout(settleTimer);
      settleTimer = null;
    }
  }

  function announceNow() {
    clearSettleTimer();
    announcement.value = announcementText();
  }

  function scheduleSettleAnnouncement() {
    clearSettleTimer();
    settleTimer = setTimeout(() => {
      settleTimer = null;
      announcement.value = announcementText();
    }, ZOOM_SETTLE_MS);
  }

  /** Apply a new scale with the cursor anchor (binding: the image point under
   * the anchor stays stationary), edge-clamped. */
  function applyScale(next, anchor) {
    const target = anchorZoomOffset({
      cursorX: anchor.x,
      cursorY: anchor.y,
      offsetX: offset.value.x,
      offsetY: offset.value.y,
      containerWidth: container.value.width,
      containerHeight: container.value.height,
      imageWidth: natural.value.width,
      imageHeight: natural.value.height,
      oldScale: scale.value,
      newScale: next,
    });
    scale.value = next;
    offset.value = { x: target.x, y: target.y };
  }

  function centreAnchor() {
    return { x: container.value.width / 2, y: container.value.height / 2 };
  }

  /**
   * One wheel event, anchored at the cursor. `cursor` is the pointer position
   * relative to the container (a pinch centroid works identically).
   * Returns true when the event meant something (so callers can decide about
   * preventDefault when they don't blanket-prevent).
   */
  function wheelZoom(event, cursor) {
    if (scale.value === null) return false;
    const deltaY = normalizeWheelDelta(event, {
      pageHeightPx: container.value.height || undefined,
    });
    if (!deltaY) return false;
    if (deltaY > 0 && atFitFloor(scale.value, fitScale.value)) {
      // Wheel-out at the floor: policy dispatch. `rest` is the hard clamp -
      // the surface is a destination, wheeling out simply rests at fit.
      if (floorPolicy === "exit") {
        // The accumulation starts only AT the floor; a pause longer than the
        // gesture gap restarts it, so a later gesture meets the full
        // resistance itself instead of inheriting a stale part-way count.
        const now = Date.now();
        if (now - exitLastOutTs > ZOOM_EXIT_GESTURE_GAP_MS) {
          exitAccumulator = 0;
        }
        exitLastOutTs = now;
        exitAccumulator += deltaY;
        if (exitAccumulator >= ZOOM_EXIT_RESISTANCE) {
          exitAccumulator = 0;
          onExit?.();
        }
      }
      return true;
    }
    // Any zoom-in movement resets the exit accumulation.
    exitAccumulator = 0;
    const next = zoomStepScale(
      scale.value,
      deltaY,
      fitScale.value,
      effectiveMaxScale.value,
    );
    if (next === scale.value) return true;
    applyScale(next, cursor ?? centreAnchor());
    scheduleSettleAnnouncement();
    return true;
  }

  /** Snap to a stop on the continuum. Announces immediately (a snap is a
   * deliberate, discrete act - no settle window). */
  function snapTo(target, anchor = null) {
    if (scale.value === null) return;
    const clamped = Math.max(
      fitScale.value,
      Math.min(effectiveMaxScale.value, target),
    );
    applyScale(clamped, anchor ?? centreAnchor());
    announceNow();
  }

  /** The two snap stops as a toggle: at fit (within slack) → 100%; anywhere
   * else → fit. Centre-anchored unless an anchor (e.g. a double-click point)
   * is given. */
  function toggleSnap(anchor = null) {
    if (scale.value === null) return;
    snapTo(atFit.value ? 1 : fitScale.value, anchor);
  }

  /** Drag-pan by a pointer delta, clamped at the edges. */
  function panBy(dx, dy) {
    if (scale.value === null) return;
    offset.value = {
      x: clampOffsetAxis(
        offset.value.x + dx,
        container.value.width,
        natural.value.width,
      ),
      y: clampOffsetAxis(
        offset.value.y + dy,
        container.value.height,
        natural.value.height,
      ),
    };
  }

  /** Back to the un-measured entry state (surface closed / reopened). */
  function reset() {
    scale.value = null;
    fitScale.value = 1;
    offset.value = { x: 0, y: 0 };
    natural.value = { width: 0, height: 0 };
    exitAccumulator = 0;
    exitLastOutTs = 0;
    clearSettleTimer();
    announcement.value = "";
  }

  if (getCurrentScope()) {
    onScopeDispose(clearSettleTimer);
  }

  return {
    // state
    scale,
    fitScale,
    offset,
    announcement,
    // derived
    effectiveMaxScale,
    atFit,
    atActual,
    aboveFit,
    percentLabel,
    transformScale,
    // actions
    nearScale,
    setMeasurements,
    wheelZoom,
    snapTo,
    toggleSnap,
    panBy,
    reset,
  };
}
