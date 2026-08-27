// Overlay smart-score refresh - the lightbox metadata panel must show the
// freshly-recomputed smart score after a tag edit or a penalised-tag settings
// change, without a full page reload.
//
// Two layers of coverage:
//   A. Isolated decision rules copied verbatim from the source call sites (fast,
//      no component mount). Keep them in sync with the source:
//        1. fetchOverlayMetadata's smart-score merge (ImageOverlay.vue).
//        2. App.vue's smart_score-signal field gate (`pictures_changed` handler).
//        3. ImageOverlay's smartScoreUpdate watcher trigger rule.
//   B. A mounted-component regression that drives the ACTUAL ImageOverlay through
//      the real signal sequence - this is what catches the coalescing / bulk-drain
//      batch / registry-demotion cases the isolated copies cannot model.

import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Imported statically, not lazily inside the tests. `vi.mock` is hoisted
// above every import, so a lazy import buys no mock ordering. It only moves
// the cost of compiling this 5.7k-line SFC (~7s on a loaded machine) inside
// the first test's 5s timeout, which is what made this file flake in the full
// suite while passing on its own.
import ImageOverlay from "./ImageOverlay.vue";

// A test that fails mid-way must not leave a mounted overlay behind: its
// window-level keydown listener would answer every later test in this file.
enableAutoUnmount(afterEach);

// ---- Mounted-component harness (layer B) -----------------------------------
// A controllable /pictures/{id}/metadata?smart_score=true response.
let metadataSmartScore = 0.42;
const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/metadata")) {
    return { data: { id: 7, smartScore: metadataSmartScore, tags: [] } };
  }
  if (typeof url === "string" && url.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  return { data: [] };
});

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: { get: (...a) => getMock(...a), post: vi.fn(), delete: vi.fn() },
  appendShareToken: (u) => u,
  isReadOnly: { value: false },
}));


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

// (3) The overlay watcher's trigger rule. A distinct smart_score signal re-fetches
// the open card REGARDLESS of whether the payload's pictureIds names it: a
// bulk-drain event batches a whole task's ids (and can omit the open card), a
// registry-demoted interactive rescore rides that same bulk path, and Vue
// coalesces rapid signals - so the payload cannot be trusted to always name the
// open card. Fires only when the signal key advances and a card is open.
function watcherShouldRefetch(nextKey, lastKey, open, currentId) {
  if (!nextKey || nextKey === lastKey) return false;
  if (!open || currentId == null) return false;
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

describe("overlay smartScoreUpdate watcher trigger", () => {
  it("re-fetches when the signal key advances and a card is open", () => {
    expect(watcherShouldRefetch(2, 1, true, 7)).toBe(true);
  });

  it("re-fetches even when the payload's ids omit the open card", () => {
    // The regression: bulk-drain batch / registry-demoted rescore / coalesced
    // signal that names other pictures. The open card must still refresh.
    expect(watcherShouldRefetch(5, 4, true, 7)).toBe(true);
  });

  it("skips a repeated key (already processed)", () => {
    expect(watcherShouldRefetch(3, 3, true, 7)).toBe(false);
  });

  it("skips the zero/absent key", () => {
    expect(watcherShouldRefetch(0, 0, true, 7)).toBe(false);
  });

  it("skips when the overlay is closed", () => {
    expect(watcherShouldRefetch(2, 1, false, 7)).toBe(false);
  });

  it("skips when no card is loaded", () => {
    expect(watcherShouldRefetch(2, 1, true, null)).toBe(false);
  });
});

// ---- Layer B: mounted-component regression ---------------------------------
// Drives the real ImageOverlay through the signal sequence. The bug: after a
// penalised-tag edit the overlay panel kept the stale score until a full reload
// whenever the smart_score signal that landed did not name the open card.
describe("ImageOverlay mounted smart-score refresh", () => {
  const STUBS = {
    OverlayTagsPanel: true,
    OverlayFilmstrip: true,
    OverlayDescriptionPanel: true,
    AddToEntityControl: true,
    CharacterEditor: true,
    StarRatingOverlay: true,
    PluginParametersUI: true,
    ComfyUiRunner: true,
    ProgressOverlay: true,
    // Real-but-thin panel so we can read the `image` prop it renders from.
    OverlayMetadataPanel: {
      name: "OverlayMetadataPanel",
      props: [
        "image",
        "comfyMetadata",
        "dateFormat",
        "backendUrl",
        "videoDuration",
      ],
      template: "<div class='meta'></div>",
    },
  };

  const flush = () => new Promise((r) => setTimeout(r, 0));

  async function openOverlayOnCard7() {
    const wrapper = mount(ImageOverlay, {
      props: {
        open: false,
        initialImageId: 7,
        allImages: [{ id: 7, smartScore: 0.42, tags: [] }],
        backendUrl: "http://test",
        tagUpdate: { key: 0, pictureIds: [] },
        descriptionUpdate: { key: 0, pictureIds: [] },
        smartScoreUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
    });
    await wrapper.setProps({ open: true });
    await flush();
    await flush();
    return wrapper;
  }

  const scoreOf = (wrapper) =>
    wrapper
      .findComponent({ name: "OverlayMetadataPanel" })
      .props("image").smartScore;

  beforeEach(() => {
    setActivePinia(createPinia());
    metadataSmartScore = 0.42;
    getMock.mockClear();
  });

  it("updates the panel when the signal names the open card", async () => {
    const wrapper = await openOverlayOnCard7();
    expect(scoreOf(wrapper)).toBe(0.42);

    metadataSmartScore = 0.81;
    await wrapper.setProps({ smartScoreUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    expect(scoreOf(wrapper)).toBe(0.81);
  });

  it("keeps the old score during the transient NULL, then adopts the committed value", async () => {
    const wrapper = await openOverlayOnCard7();

    // Tag edit: metadata read while the recompute is still pending -> NULL.
    metadataSmartScore = null;
    await wrapper.setProps({ tagUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    expect(scoreOf(wrapper)).toBe(0.42); // no "unscored" flash

    // Recompute commits; the signal arrives.
    metadataSmartScore = 0.81;
    await wrapper.setProps({ smartScoreUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    expect(scoreOf(wrapper)).toBe(0.81);
  });

  it("updates the open card even when the signal's pictureIds omit it (bulk-drain batch / registry demotion)", async () => {
    const wrapper = await openOverlayOnCard7();
    expect(scoreOf(wrapper)).toBe(0.42);

    // Card 7 was rescored in the DB, but the drain event carries a different
    // batch's ids. Pre-fix this skipped the re-fetch and left the panel stale.
    metadataSmartScore = 0.81;
    await wrapper.setProps({
      smartScoreUpdate: { key: 1, pictureIds: [101, 102, 103] },
    });
    await flush();
    await flush();

    expect(scoreOf(wrapper)).toBe(0.81);
  });

  it("survives Vue watcher coalescing (a later signal for other pictures overwrites the one that named the card)", async () => {
    const wrapper = await openOverlayOnCard7();
    expect(scoreOf(wrapper)).toBe(0.42);

    metadataSmartScore = 0.81;
    // Two writes before a flush -> Vue coalesces to the latest ([99]).
    wrapper.setProps({ smartScoreUpdate: { key: 1, pictureIds: [7] } });
    await wrapper.setProps({ smartScoreUpdate: { key: 2, pictureIds: [99] } });
    await flush();
    await flush();

    expect(scoreOf(wrapper)).toBe(0.81);
  });
});
