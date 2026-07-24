// Overlay smart-score refresh — the lightbox metadata panel must show the
// freshly-recomputed smart score after a tag edit or a penalised-tag settings
// change, without a full page reload.
//
// ImageOverlay.vue (~4.9k lines) is impractical to mount, so — following the
// same convention as ImageGridSmartScoreSort.test.js — this test exercises the
// exact decision rules the fix relies on, copied verbatim from three call
// sites. Keep them in sync with the source:
//   1. fetchOverlayMetadata's smart-score merge (ImageOverlay.vue).
//   2. App.vue's smart_score-signal field gate (the `pictures_changed` handler).
//   3. ImageOverlay's smartScoreUpdate watcher id-match guard.

import { describe, it, expect } from "vitest";

// (1) Verbatim copy of fetchOverlayMetadata's smart-score merge. `data` is the
// authoritative /pictures/{id}/metadata?smart_score=true response; `image` is
// the card currently shown. Returns the value written onto the displayed card.
function mergeSmartScore(image, data) {
  const existingSmartScore =
    typeof image?.smartScore === "number"
      ? image.smartScore
      : typeof image?.smart_score === "number"
        ? image.smart_score
        : null;
  // `merged` starts as { ...data, ...image }, so image's value is the default.
  let mergedSmartScore = existingSmartScore;
  if (Object.prototype.hasOwnProperty.call(data, "smartScore")) {
    const freshSmartScore = data.smartScore;
    mergedSmartScore =
      freshSmartScore !== null && freshSmartScore !== undefined
        ? freshSmartScore
        : existingSmartScore;
  }
  return mergedSmartScore;
}

// (2) Verbatim copy of App.vue's field gate for emitting the smart_score signal.
// `fields` absent/empty = full change (unknown) => always signal.
function touchesSmartScore(fields) {
  const changedFields = Array.isArray(fields) ? fields : [];
  return changedFields.length === 0 || changedFields.includes("smart_score");
}

// (3) Verbatim copy of the overlay watcher's id-match guard. Returns whether the
// open card (currentId) should re-fetch for this event's picture ids. Matches on
// id only — origin is deliberately not considered here.
function watcherShouldRefetch(payloadPictureIds, currentId) {
  const pictureIds = Array.isArray(payloadPictureIds)
    ? payloadPictureIds.map((id) => String(id))
    : [];
  if (pictureIds.length && !pictureIds.includes(String(currentId))) return false;
  return true;
}

describe("fetchOverlayMetadata smart-score merge", () => {
  it("replaces a stale displayed score with the fresh non-null server value", () => {
    // The bug: after invalidation+recompute, the server returns the corrected
    // score but the panel used to keep the old one.
    const image = { id: 7, smartScore: 0.42 };
    const data = { smartScore: 0.81 };
    expect(mergeSmartScore(image, data)).toBe(0.81);
  });

  it("accepts a fresh score of 0 (not treated as absent)", () => {
    const image = { id: 7, smartScore: 0.42 };
    const data = { smartScore: 0 };
    expect(mergeSmartScore(image, data)).toBe(0);
  });

  it("keeps the old value when the fetch returns null (recompute pending)", () => {
    // The transient window: score was NULLed, recompute not yet committed. Must
    // not flash "unscored".
    const image = { id: 7, smartScore: 0.42 };
    const data = { smartScore: null };
    expect(mergeSmartScore(image, data)).toBe(0.42);
  });

  it("keeps the old value when the response omits smartScore entirely", () => {
    const image = { id: 7, smartScore: 0.42 };
    const data = { tags: [] };
    expect(mergeSmartScore(image, data)).toBe(0.42);
  });

  it("does not regress the grid-sourced image case (no existing score, null fetch)", () => {
    // Grid cards don't carry smartScore; a null fetch leaves it unset (null),
    // never a fake 0.
    const image = { id: 7 };
    const data = { smartScore: null };
    expect(mergeSmartScore(image, data)).toBe(null);
  });

  it("populates a grid-sourced image once the fresh non-null value arrives", () => {
    const image = { id: 7 };
    const data = { smartScore: 0.66 };
    expect(mergeSmartScore(image, data)).toBe(0.66);
  });

  it("reads an existing snake_case smart_score as the fallback", () => {
    const image = { id: 7, smart_score: 0.5 };
    const data = { smartScore: null };
    expect(mergeSmartScore(image, data)).toBe(0.5);
  });
});

describe("App.vue smart_score signal field gate", () => {
  it("signals for an explicit smart_score field (interactive edit + bulk drain)", () => {
    expect(touchesSmartScore(["smart_score"])).toBe(true);
  });

  it("signals when fields are absent (full/unknown change)", () => {
    expect(touchesSmartScore(undefined)).toBe(true);
    expect(touchesSmartScore([])).toBe(true);
  });

  it("does not signal for an unrelated field-only change", () => {
    expect(touchesSmartScore(["detections"])).toBe(false);
    expect(touchesSmartScore(["score"])).toBe(false);
  });

  it("signals when smart_score is one of several changed fields", () => {
    expect(touchesSmartScore(["score", "smart_score"])).toBe(true);
  });
});

describe("overlay smartScoreUpdate watcher id match", () => {
  it("re-fetches when the open card is in the event's picture ids", () => {
    expect(watcherShouldRefetch([5, 7, 9], 7)).toBe(true);
  });

  it("re-fetches regardless of id type (string vs number)", () => {
    expect(watcherShouldRefetch(["7"], 7)).toBe(true);
  });

  it("skips when the open card is not among the changed ids", () => {
    expect(watcherShouldRefetch([5, 9], 7)).toBe(false);
  });

  it("re-fetches on an empty id list (treated as broad change)", () => {
    expect(watcherShouldRefetch([], 7)).toBe(true);
  });
});
