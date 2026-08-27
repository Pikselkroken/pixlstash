// Overlay detection refresh - after a Segment run finishes, the open lightbox
// must show the new object boxes without being closed and reopened.
//
// The bug: `detections` is a card-content field, so useGridRealtimeSync refreshes
// the grid card in place - and defers even that while the overlay is open (§9.1).
// The overlay reads its boxes from /pictures/{id}/detections on card change only,
// so nothing re-fetched them for the card already on screen.
//
// Two layers of coverage, mirroring ImageOverlaySmartScore.test.js:
//   A. Isolated decision rules copied from the source call sites:
//        1. App.vue's detections-signal field gate (`pictures_changed` handler).
//        2. ImageOverlay's detectionUpdate watcher trigger rule.
//   B. A mounted-component regression driving the real ImageOverlay through the
//      signal, asserting the detections endpoint is actually re-read.

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
// A controllable /pictures/{id}/detections response.
let detectionRows = [];
const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/detections")) {
    return { data: detectionRows };
  }
  if (typeof url === "string" && url.includes("/metadata")) {
    return { data: { id: 7, tags: [] } };
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


// (1) Verbatim copy of App.vue's field gate for emitting the detections signal.
// The backend always stamps a finished DetectionTask `fields: ["detections"]`
// (vault.py), so this matches the explicit field only - an absent/empty `fields`
// list is some other change and must not trigger a detections re-read.
function touchesDetections(fields) {
  const changedFields = Array.isArray(fields) ? fields : [];
  return changedFields.includes("detections");
}

// (2) The overlay watcher's trigger rule: fire on every distinct key while a
// card is open, without gating on the payload's ids (two detection tasks
// completing in one Vue flush coalesce to the later payload, which can omit the
// open card).
function watcherShouldRefetch(nextKey, lastKey, open, currentId) {
  if (!nextKey || nextKey === lastKey) return false;
  if (!open || currentId == null) return false;
  return true;
}

describe("App.vue detections signal field gate", () => {
  it("signals for the explicit detections field a DetectionTask emits", () => {
    expect(touchesDetections(["detections"])).toBe(true);
  });

  it("signals when detections is one of several changed fields", () => {
    expect(touchesDetections(["score", "detections"])).toBe(true);
  });

  it("does not signal for an unrelated field-only change", () => {
    expect(touchesDetections(["smart_score"])).toBe(false);
  });

  it("does not signal when fields are absent (not a detection completion)", () => {
    expect(touchesDetections(undefined)).toBe(false);
    expect(touchesDetections([])).toBe(false);
  });
});

describe("overlay detectionUpdate watcher trigger", () => {
  it("re-fetches when the signal key advances and a card is open", () => {
    expect(watcherShouldRefetch(2, 1, true, 7)).toBe(true);
  });

  it("re-fetches even when the payload's ids omit the open card (coalesced signals)", () => {
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
describe("ImageOverlay mounted detection refresh", () => {
  const STUBS = {
    OverlayTagsPanel: true,
    OverlayFilmstrip: true,
    // Thin real component: the overlay's close watcher calls back into this
    // panel's exposed methods, so a bare `true` stub throws on close.
    OverlayDescriptionPanel: {
      name: "OverlayDescriptionPanel",
      methods: {
        cancelEditDescription() {},
        resetCopyState() {},
      },
      template: "<div class='description-panel'></div>",
    },
    OverlayMetadataPanel: true,
    AddToEntityControl: true,
    CharacterEditor: true,
    StarRatingOverlay: true,
    PluginParametersUI: true,
    ComfyUiRunner: true,
    ProgressOverlay: true,
  };

  const flush = () => new Promise((r) => setTimeout(r, 0));

  const detectionCalls = () =>
    getMock.mock.calls.filter(
      ([url]) => typeof url === "string" && url.includes("/detections"),
    );

  async function openOverlayOnCard7() {
    const wrapper = mount(ImageOverlay, {
      props: {
        open: false,
        initialImageId: 7,
        allImages: [{ id: 7, tags: [] }],
        backendUrl: "http://test",
        tagUpdate: { key: 0, pictureIds: [] },
        descriptionUpdate: { key: 0, pictureIds: [] },
        smartScoreUpdate: { key: 0, pictureIds: [] },
        detectionUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
    });
    await wrapper.setProps({ open: true });
    await flush();
    await flush();
    return wrapper;
  }

  beforeEach(() => {
    setActivePinia(createPinia());
    detectionRows = [];
    getMock.mockClear();
  });

  it("re-reads the detections endpoint when a Segment run lands on the open card", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = detectionCalls().length;
    expect(before).toBeGreaterThan(0); // fetched once on open

    // Segment finished: the backend now has boxes for this picture.
    detectionRows = [
      { frame_index: 0, bbox: [0, 0, 10, 10], label: "cat" },
      { frame_index: 0, bbox: [20, 20, 30, 30], label: "hat" },
    ];
    await wrapper.setProps({ detectionUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    const calls = detectionCalls();
    expect(calls.length).toBe(before + 1);
    expect(calls.at(-1)[0]).toContain("/pictures/7/detections");
  });

  it("re-reads even when the signal's pictureIds omit the open card (coalesced signals)", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = detectionCalls().length;

    await wrapper.setProps({
      detectionUpdate: { key: 1, pictureIds: [101, 102] },
    });
    await flush();
    await flush();

    expect(detectionCalls().length).toBe(before + 1);
  });

  it("does not re-read for a repeated signal key", async () => {
    const wrapper = await openOverlayOnCard7();

    await wrapper.setProps({ detectionUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    const afterFirst = detectionCalls().length;

    // Same key again (e.g. an unrelated prop re-render): no second fetch.
    await wrapper.setProps({ detectionUpdate: { key: 1, pictureIds: [7] } });
    await flush();

    expect(detectionCalls().length).toBe(afterFirst);
  });

  it("does not re-read while the overlay is closed", async () => {
    const wrapper = await openOverlayOnCard7();
    await wrapper.setProps({ open: false });
    await flush();
    const before = detectionCalls().length;

    await wrapper.setProps({ detectionUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    expect(detectionCalls().length).toBe(before);
  });
});
