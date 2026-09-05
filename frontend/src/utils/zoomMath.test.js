// The blink-zoom's continuous-wheel arithmetic. The cursor anchor is a
// BINDING requirement (the image point under the pointer stays stationary
// through every scale change), so its invariant is pinned here as pure math
// rather than riding on jsdom's non-existent layout.

import { describe, it, expect } from "vitest";
import {
  anchorZoomOffset,
  anchorZoomScroll,
  atFitFloor,
  formatZoomPercent,
  normalizeWheelDelta,
  zoomStepScale,
  ZOOM_EXIT_GESTURE_GAP_MS,
  ZOOM_EXIT_RESISTANCE,
  ZOOM_MAX_SCALE,
} from "./zoomMath";

describe("zoomStepScale", () => {
  it("zooms in on wheel up and out on wheel down, exponentially", () => {
    const up = zoomStepScale(1, -100, 0.5);
    expect(up).toBeCloseTo(Math.exp(0.2), 5);
    const down = zoomStepScale(up, 100, 0.5);
    expect(down).toBeCloseTo(1, 5);
  });

  it("clamps to the fit floor and the ceiling", () => {
    expect(zoomStepScale(0.55, 500, 0.5)).toBe(0.5);
    expect(zoomStepScale(ZOOM_MAX_SCALE, -500, 0.5)).toBe(ZOOM_MAX_SCALE);
  });

  // A wild device delta may at most halve or double in one event, so a
  // single burst can never teleport the scale across the continuum.
  it("caps one event's effect at a doubling either way", () => {
    expect(zoomStepScale(1, -100000, 0.5)).toBe(2);
    expect(zoomStepScale(1, 100000, 0.1)).toBe(0.5);
  });
});

describe("the exit-resistance tunables", () => {
  // Owner requirement (2026-07-30): reaching fit exited too easily at one
  // notch; leaving must now cost THREE deliberate standard notches. The
  // accumulator semantics (counts only AT the floor, any zoom-in resets it,
  // a pause restarts it) are pinned on the consumers - useWheelZoom's exit
  // policy and the Compare dialog.
  it("prices the exit at three standard 120-delta wheel notches", () => {
    expect(ZOOM_EXIT_RESISTANCE).toBe(3 * 120);
  });

  it("keeps the gesture gap between momentum tails and a human pause", () => {
    // Longer than intra-gesture event gaps, far shorter than a deliberate
    // stop-and-decide; a value outside this band breaks one or the other.
    expect(ZOOM_EXIT_GESTURE_GAP_MS).toBeGreaterThanOrEqual(300);
    expect(ZOOM_EXIT_GESTURE_GAP_MS).toBeLessThanOrEqual(1000);
  });
});

describe("atFitFloor", () => {
  it("recognises the floor with rounding slack, and only the floor", () => {
    expect(atFitFloor(0.5, 0.5)).toBe(true);
    expect(atFitFloor(0.5004, 0.5)).toBe(true);
    expect(atFitFloor(0.51, 0.5)).toBe(false);
  });
});

describe("anchorZoomScroll - the point under the cursor stays put", () => {
  /** Where the cursor lands in image coordinates for a given state. */
  function imagePointUnderCursor({
    cursor,
    scroll,
    container,
    image,
    scale,
  }) {
    const margin = (axisContainer, axisImage) =>
      Math.max(0, (axisContainer - axisImage * scale) / 2);
    return {
      x: (scroll.left + cursor.x - margin(container.w, image.w)) / scale,
      y: (scroll.top + cursor.y - margin(container.h, image.h)) / scale,
    };
  }

  const container = { w: 800, h: 600 };
  const image = { w: 1000, h: 750 };

  it("holds the invariant across a zoom-in step", () => {
    const cursor = { x: 400, y: 300 };
    const before = imagePointUnderCursor({
      cursor,
      scroll: { left: 100, top: 50 },
      container,
      image,
      scale: 1,
    });
    const next = anchorZoomScroll({
      cursorX: cursor.x,
      cursorY: cursor.y,
      scrollLeft: 100,
      scrollTop: 50,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: 1,
      newScale: 1.25,
    });
    expect(next).toEqual({ left: 225, top: 137.5 });
    const after = imagePointUnderCursor({
      cursor,
      scroll: { left: next.left, top: next.top },
      container,
      image,
      scale: 1.25,
    });
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  // The image starts CENTRED (smaller than the viewport, auto margins): the
  // anchor must account for the margin, or the first in-tick jumps.
  it("holds the invariant when zooming out of the centred state", () => {
    const small = { w: 400, h: 300 };
    const cursor = { x: 400, y: 300 }; // dead centre of the container
    const before = imagePointUnderCursor({
      cursor,
      scroll: { left: 0, top: 0 },
      container,
      image: small,
      scale: 1,
    });
    expect(before).toEqual({ x: 200, y: 150 }); // the image's own centre
    const next = anchorZoomScroll({
      cursorX: cursor.x,
      cursorY: cursor.y,
      scrollLeft: 0,
      scrollTop: 0,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: small.w,
      imageHeight: small.h,
      oldScale: 1,
      newScale: 3,
    });
    expect(next).toEqual({ left: 200, top: 150 });
    const after = imagePointUnderCursor({
      cursor,
      scroll: { left: next.left, top: next.top },
      container,
      image: small,
      scale: 3,
    });
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  // At the edges the clamp wins over the anchor - the required behaviour:
  // scroll never goes negative or past the content.
  it("clamps to the scrollable range at the edges", () => {
    const next = anchorZoomScroll({
      cursorX: 0,
      cursorY: 0,
      scrollLeft: 0,
      scrollTop: 0,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: 2,
      newScale: 1.1,
    });
    expect(next.left).toBeGreaterThanOrEqual(0);
    expect(next.left).toBeLessThanOrEqual(image.w * 1.1 - container.w);
    expect(next.top).toBeGreaterThanOrEqual(0);
    expect(next.top).toBeLessThanOrEqual(image.h * 1.1 - container.h);
  });

  it("returns zero scroll once the image fits again", () => {
    const next = anchorZoomScroll({
      cursorX: 700,
      cursorY: 500,
      scrollLeft: 400,
      scrollTop: 300,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: 2,
      newScale: 0.5,
    });
    expect(next).toEqual({ left: 0, top: 0 });
  });
});

describe("anchorZoomOffset - the transform-space twin", () => {
  const container = { w: 800, h: 600 };
  const image = { w: 1000, h: 750 };

  /** Where the cursor lands in image coordinates (natural px from the image
   * centre) for a translate+scale transform on a container-centred image. */
  function imagePointUnderCursor({ cursor, offset, scale }) {
    return {
      x: (cursor.x - container.w / 2 - offset.x) / scale,
      y: (cursor.y - container.h / 2 - offset.y) / scale,
    };
  }

  it("holds the invariant across a zoom-in step", () => {
    const cursor = { x: 250, y: 450 };
    const offset = { x: -60, y: 40 };
    const before = imagePointUnderCursor({ cursor, offset, scale: 1 });
    const next = anchorZoomOffset({
      cursorX: cursor.x,
      cursorY: cursor.y,
      offsetX: offset.x,
      offsetY: offset.y,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: 1,
      newScale: 1.25,
    });
    const after = imagePointUnderCursor({ cursor, offset: next, scale: 1.25 });
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  // The overlay enters at fit - offset zero, image centred. The first in-tick
  // must anchor from that centred state without a jump.
  it("holds the invariant zooming out of the centred fit state", () => {
    const fit = 0.8; // 1000×750 fits 800×600 exactly at 0.8
    const cursor = { x: 600, y: 150 };
    const before = imagePointUnderCursor({
      cursor,
      offset: { x: 0, y: 0 },
      scale: fit,
    });
    const next = anchorZoomOffset({
      cursorX: cursor.x,
      cursorY: cursor.y,
      offsetX: 0,
      offsetY: 0,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: fit,
      newScale: fit * 1.5,
    });
    const after = imagePointUnderCursor({
      cursor,
      offset: next,
      scale: fit * 1.5,
    });
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  // At the edges the clamp wins over the anchor: the image edge never crosses
  // its viewport edge, on either side of either axis.
  it("clamps the offset so the image edge never crosses the viewport edge", () => {
    const next = anchorZoomOffset({
      cursorX: 0,
      cursorY: 0,
      offsetX: 300,
      offsetY: 200,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: 2,
      newScale: 1.1,
    });
    const rangeX = (image.w * 1.1 - container.w) / 2;
    const rangeY = (image.h * 1.1 - container.h) / 2;
    expect(Math.abs(next.x)).toBeLessThanOrEqual(rangeX);
    expect(Math.abs(next.y)).toBeLessThanOrEqual(rangeY);
  });

  it("re-centres (zero offset) once the image fits again", () => {
    const next = anchorZoomOffset({
      cursorX: 700,
      cursorY: 500,
      offsetX: -250,
      offsetY: 180,
      containerWidth: container.w,
      containerHeight: container.h,
      imageWidth: image.w,
      imageHeight: image.h,
      oldScale: 2,
      newScale: 0.5,
    });
    expect(next).toEqual({ x: 0, y: 0 });
  });
});

describe("normalizeWheelDelta", () => {
  it("passes pixel-mode deltas through unchanged", () => {
    expect(normalizeWheelDelta({ deltaY: -100, deltaMode: 0 })).toBe(-100);
    expect(normalizeWheelDelta({ deltaY: 53, deltaMode: 0 })).toBe(53);
  });

  // The live Compare drift this promotion fixes: a line-mode wheel's deltaY
  // of ±3 fed raw into pixel-tuned arithmetic zooms ~50× weaker per notch.
  it("scales line-mode deltas to pixels", () => {
    expect(normalizeWheelDelta({ deltaY: -3, deltaMode: 1 })).toBe(-48);
    expect(
      normalizeWheelDelta({ deltaY: -3, deltaMode: 1 }, { lineHeightPx: 20 }),
    ).toBe(-60);
  });

  it("scales page-mode deltas to pixels", () => {
    expect(normalizeWheelDelta({ deltaY: 1, deltaMode: 2 })).toBe(800);
    expect(
      normalizeWheelDelta({ deltaY: -1, deltaMode: 2 }, { pageHeightPx: 600 }),
    ).toBe(-600);
  });

  it("returns 0 for missing or degenerate events", () => {
    expect(normalizeWheelDelta(null)).toBe(0);
    expect(normalizeWheelDelta({ deltaY: 0, deltaMode: 0 })).toBe(0);
    expect(normalizeWheelDelta({ deltaY: NaN, deltaMode: 0 })).toBe(0);
    expect(normalizeWheelDelta({ deltaMode: 0 })).toBe(0);
  });
});

describe("formatZoomPercent", () => {
  it("rounds to a whole percent of actual pixels", () => {
    expect(formatZoomPercent(1)).toBe("100%");
    expect(formatZoomPercent(0.374)).toBe("37%");
    expect(formatZoomPercent(2.396)).toBe("240%");
    expect(formatZoomPercent(8)).toBe("800%");
  });

  it("is empty for a non-finite scale", () => {
    expect(formatZoomPercent(null)).toBe("");
    expect(formatZoomPercent(NaN)).toBe("");
    expect(formatZoomPercent(undefined)).toBe("");
  });
});
