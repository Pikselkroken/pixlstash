// The blink-zoom's continuous-wheel arithmetic. The cursor anchor is a
// BINDING requirement (the image point under the pointer stays stationary
// through every scale change), so its invariant is pinned here as pure math
// rather than riding on jsdom's non-existent layout.

import { describe, it, expect } from "vitest";
import {
  anchorZoomScroll,
  atFitFloor,
  zoomStepScale,
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

describe("atFitFloor", () => {
  it("recognises the floor with rounding slack, and only the floor", () => {
    expect(atFitFloor(0.5, 0.5)).toBe(true);
    expect(atFitFloor(0.5004, 0.5)).toBe(true);
    expect(atFitFloor(0.51, 0.5)).toBe(false);
  });
});

describe("anchorZoomScroll — the point under the cursor stays put", () => {
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

  // At the edges the clamp wins over the anchor — the required behaviour:
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
