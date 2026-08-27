// The bottom-edge floating layout contract (notice-surface.md §2.2).
//
// Pure functions only. The rule the spec states in its general form:
//
//   `--floating-bottom-h` is the height of the TALLEST bottom-anchored floating
//   element currently VISIBLE and INSIDE the notice column's footprint, plus
//   the gap that must sit between it and the notice stack.
//
// Keeping the arithmetic here (rather than inline in a CSS `calc(max(...))`)
// is a deliberate, reportable deviation from the spec's literal CSS sketch: the
// selection pill's height is measured, the breadcrumb only participates below
// 600px, and "which anchors count right now" is exactly the kind of rule that
// silently regresses. As a pure function it is unit-testable; as a CSS calc it
// is not.
//
// Nothing here hardcodes a pill height. The spec is explicit that 56px is a
// first-frame fallback and NOT a design token, because the pill wraps and grows
// on coarse pointers.

/** Gap between the tallest bottom-anchored element and the notice stack (--space-3). */
export const FLOATING_BOTTOM_GAP_PX = 8;

/** Viewport width at or below which the notice card widens over the breadcrumb. */
export const NARROW_VIEWPORT_MAX_PX = 600;

/**
 * First-frame fallback for the selection pill's height, used only until the
 * ResizeObserver reports a real measurement. Measured current value (40px
 * controls + 2×6px padding + 2×1px border = 54px, rounded to the spec's 56px
 * fallback), NOT a design token.
 */
export const SELBAR_FALLBACK_H_PX = 56;

/**
 * Compute the inset contributed by bottom-anchored floating chrome.
 *
 * @param {Object} options
 * @param {Array<{height: number, visible: boolean, narrowOnly?: boolean}>} options.anchors
 *   Every bottom-anchored floating element that can sit in the notice column's
 *   footprint. `narrowOnly` marks one that is only inside the footprint on
 *   narrow viewports (the grid breadcrumb: above 600px it is bottom-LEFT, well
 *   clear of the centred card, so it contributes 0).
 * @param {boolean} [options.narrow=false] - is the viewport ≤ 600px?
 * @param {number} [options.gap=FLOATING_BOTTOM_GAP_PX] - gap above the tallest.
 * @returns {number} pixels. `0` when nothing is parked there - NOT `gap`, so the
 *   stack rests at exactly `--space-5` when the bottom edge is clear.
 */
export function computeFloatingBottomInset({
  anchors = [],
  narrow = false,
  gap = FLOATING_BOTTOM_GAP_PX,
} = {}) {
  let tallest = 0;
  for (const anchor of anchors) {
    if (!anchor || !anchor.visible) continue;
    // An anchor outside the notice column's footprint contributes nothing.
    if (anchor.narrowOnly && !narrow) continue;
    const height = Number(anchor.height);
    if (!Number.isFinite(height) || height <= 0) continue;
    if (height > tallest) tallest = height;
  }
  return tallest > 0 ? tallest + gap : 0;
}

/**
 * Format an inset for assignment to a CSS custom property.
 * @param {number} px
 * @returns {string} e.g. `"62px"`.
 */
export function toPx(px) {
  const value = Number.isFinite(px) ? Math.max(0, Math.round(px)) : 0;
  return `${value}px`;
}
