// Lightbox close - reconcile a deferred smart-score re-rank without a full re-sort.
//
// Editing tags in the image lightbox under SMART_SCORE sort used to refresh twice:
//   1. the realtime-sync path opportunistically repositions the card once the
//      backend rescore lands (refreshSmartScoreForImage → repositionImageBySmartScore),
//   2. and handleLightboxChange separately raised pendingLightboxGridRefresh, which
//      closeLightbox flushed as a full reload - a complete re-score of every
//      candidate (511 pictures in the reported session) producing the same order.
//
// The deferred path now records which pictures changed and repositions just those
// on close. This pins the decision table so the fallbacks cannot be lost:
//   * a broader refresh already queued  → do nothing, the reload covers it
//   * more ids than the cap             → one reload beats N per-id fetches
//   * otherwise                         → reposition the affected cards only
//
// ImageGrid.vue (~7.4k lines) is impractical to mount, so this reproduces
// closeLightbox's branch verbatim. Keep in sync with ImageGrid.vue's closeLightbox.

import { describe, it, expect } from "vitest";

const MAX_DEFERRED_SMART_SCORE_REPOSITIONS = 25;

// Verbatim copy of closeLightbox's deferred-reconcile branch.
function reconcileOnClose({
  deferredIds,
  pendingGridImages = null,
  pendingTagFilterRefresh = false,
  pendingLightboxGridRefresh = false,
}) {
  const repositioned = [];
  let reloadQueued = pendingLightboxGridRefresh;

  const deferredSmartScoreIds = Array.from(deferredIds);
  const hasBroaderRefresh =
    pendingGridImages !== null ||
    pendingTagFilterRefresh ||
    pendingLightboxGridRefresh;
  if (deferredSmartScoreIds.length && !hasBroaderRefresh) {
    if (deferredSmartScoreIds.length > MAX_DEFERRED_SMART_SCORE_REPOSITIONS) {
      reloadQueued = true;
    } else {
      for (const id of deferredSmartScoreIds) repositioned.push(id);
    }
  }
  return { repositioned, reloadQueued };
}

describe("lightbox close smart-score reconcile", () => {
  it("repositions the edited card instead of re-sorting the library", () => {
    const result = reconcileOnClose({ deferredIds: new Set([1141]) });
    expect(result.repositioned).toEqual([1141]);
    expect(result.reloadQueued).toBe(false);
  });

  it("defers to a queued full reload rather than doing both", () => {
    const result = reconcileOnClose({
      deferredIds: new Set([1141]),
      pendingLightboxGridRefresh: true,
    });
    // The reload re-sorts everything, so repositioning first is wasted work and
    // would be discarded by the reload's resetThumbnailState anyway.
    expect(result.repositioned).toEqual([]);
    expect(result.reloadQueued).toBe(true);
  });

  it("defers to pending images staged while the lightbox was open", () => {
    const result = reconcileOnClose({
      deferredIds: new Set([1141]),
      pendingGridImages: [{ id: 1 }],
    });
    expect(result.repositioned).toEqual([]);
  });

  it("defers to a pending tag-filter refresh", () => {
    // Under an active tag filter the picture may no longer match the query, so a
    // reposition is not enough - it has to leave the grid.
    const result = reconcileOnClose({
      deferredIds: new Set([1141]),
      pendingTagFilterRefresh: true,
    });
    expect(result.repositioned).toEqual([]);
  });

  it("falls back to one reload past the cap", () => {
    const many = new Set(
      Array.from(
        { length: MAX_DEFERRED_SMART_SCORE_REPOSITIONS + 1 },
        (_, i) => i,
      ),
    );
    const result = reconcileOnClose({ deferredIds: many });
    expect(result.repositioned).toEqual([]);
    expect(result.reloadQueued).toBe(true);
  });

  it("still repositions exactly at the cap", () => {
    const atCap = new Set(
      Array.from({ length: MAX_DEFERRED_SMART_SCORE_REPOSITIONS }, (_, i) => i),
    );
    const result = reconcileOnClose({ deferredIds: atCap });
    expect(result.repositioned).toHaveLength(
      MAX_DEFERRED_SMART_SCORE_REPOSITIONS,
    );
    expect(result.reloadQueued).toBe(false);
  });

  it("does nothing when no smart-score edit was deferred", () => {
    const result = reconcileOnClose({ deferredIds: new Set() });
    expect(result.repositioned).toEqual([]);
    expect(result.reloadQueued).toBe(false);
  });
});
