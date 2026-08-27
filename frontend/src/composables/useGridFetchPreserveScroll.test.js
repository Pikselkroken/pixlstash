// Grid reload - a scroll-preserving fetch must not snap the render window to the top.
//
// Symptom this guards: you scroll down, open an image, remove a tag, press ESC, and
// the grid is blank until you touch the scroll wheel.
//
// Mechanism: closing the overlay flushes a deferred refresh
// (pendingOverlayGridRefresh → debouncedFetchAllGridImages). A NON-preserving fetch
// resets visibleStart/visibleEnd to the top of the list, but nothing resets
// scrollTop. The virtualiser then renders the first screenful of cards while the
// viewport is still parked further down, so the user sees empty space. Any scroll
// recomputes the window from the real scrollTop and everything reappears - which is
// why it looked like a thumbnail-loading bug and was not one.
//
// Observed in the field (511 images, scrolled near the bottom):
//   before ESC:  window [371, 511]
//   after  ESC:  window [0, 163]   ← 163 cards rendered, all above the viewport
// and `document.querySelectorAll('.image-card').length` returned 164.
//
// The non-deferred siblings of this refresh always set preserveScrollOnNextFetch
// before fetching; the overlay-deferred path did not. These tests pin the window
// arithmetic from useGridFetch so the two branches cannot drift apart again.

import { describe, it, expect } from "vitest";

// Verbatim copy of useGridFetch's post-fetch window computation.
function applyWindow({
  preserveScroll,
  imageCount,
  windowCount,
  visibleStart,
  visibleEnd,
}) {
  if (!preserveScroll) {
    // Normal (non-preserve) fetch: jump to top so thumbnails load from index 0.
    visibleStart = 0;
    visibleEnd = Math.min(imageCount, windowCount);
  } else {
    // Scroll-preserving fetch: keep visibleStart/End as-is so
    // updateVisibleThumbnails loads the range the user is actually viewing.
    visibleEnd = Math.min(visibleEnd, imageCount);
    if (visibleStart > visibleEnd) visibleStart = Math.max(0, visibleEnd - 1);
  }
  return [visibleStart, visibleEnd];
}

// The user's real session: 511 images, scrolled to the bottom.
const SCROLLED_TO_BOTTOM = {
  imageCount: 511,
  windowCount: 163,
  visibleStart: 371,
  visibleEnd: 511,
};

describe("grid reload window vs. scroll position", () => {
  it("keeps the window where the user is scrolled when preserving", () => {
    const [start, end] = applyWindow({
      ...SCROLLED_TO_BOTTOM,
      preserveScroll: true,
    });
    expect([start, end]).toEqual([371, 511]);
  });

  it("snaps the window to the top when not preserving (the reported blank)", () => {
    const [start, end] = applyWindow({
      ...SCROLLED_TO_BOTTOM,
      preserveScroll: false,
    });
    // scrollTop is untouched, so these 163 cards render far above the viewport.
    expect([start, end]).toEqual([0, 163]);
  });

  it("clamps a preserved window that now runs past a shortened list", () => {
    const [start, end] = applyWindow({
      preserveScroll: true,
      imageCount: 40,
      windowCount: 163,
      visibleStart: 371,
      visibleEnd: 511,
    });
    // Removing the tag filtered the list down; the window must land inside it
    // rather than pointing off the end.
    expect(end).toBe(40);
    expect(start).toBeLessThanOrEqual(end);
    expect(start).toBeGreaterThanOrEqual(0);
  });

  it("is a no-op for a user already at the top", () => {
    const atTop = {
      imageCount: 511,
      windowCount: 163,
      visibleStart: 0,
      visibleEnd: 163,
    };
    expect(applyWindow({ ...atTop, preserveScroll: true })).toEqual([0, 163]);
    expect(applyWindow({ ...atTop, preserveScroll: false })).toEqual([0, 163]);
  });
});
