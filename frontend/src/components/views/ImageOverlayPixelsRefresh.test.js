// Overlay pixel refresh (#1419) - when a picture's BYTES are rewritten
// somewhere other than this overlay (an undo or redo of a rotate, a rotate made
// in another tab), the open lightbox must turn the picture without being closed
// and reopened.
//
// Two defects, one visible symptom:
//
//   1. Nothing told the overlay. `pixels` is a card-content field, so
//      useGridRealtimeSync refreshes the grid card - and the overlay re-reads
//      `orientation` (which is what its `<img>` URL's `?v=o<n>` is built from)
//      only when the displayed card changes. Fixed by `wsPixelsUpdate`.
//   2. Telling it was not enough. `setOverlayImageById` re-seeds the open card
//      from the sequence FROZEN when the overlay opened, and preserved the
//      description, score and tags but not `orientation` - so the very next
//      replacement of the `allImages` array (which `applyRotatedCards` performs
//      right after a rotate) turned the picture back to how it was.
//
// Everything here is a mounted regression and the assertion is the `<img>`'s
// src, which is what the user actually sees. Assertions about copies of the
// source declared inside the test file prove nothing about the source, so there
// are none.

import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Statically imported for the same reason ImageOverlayDetectionRefresh.test.js
// does it: `vi.mock` is hoisted above every import, so a lazy import buys no
// mock ordering and only moves this 5.7k-line SFC's compile cost inside the
// first test's timeout.
import ImageOverlay from "./ImageOverlay.vue";

enableAutoUnmount(afterEach);

// The orientation the metadata endpoint currently reports for picture 7. The
// tests move it the way a rotate and its undo do.
let metadataOrientation = 6;
const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/metadata")) {
    return {
      data: {
        id: 7,
        format: "jpg",
        orientation: metadataOrientation,
        tags: [],
      },
    };
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
  VTooltip: true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

const fullImageSrc = (wrapper) =>
  wrapper.find("img.overlay-img").attributes("src") || "";

const callsMatching = (fragment) =>
  getMock.mock.calls.filter(
    ([url]) => typeof url === "string" && url.includes(fragment),
  ).length;

async function openOverlayOnCard7() {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [{ id: 7, format: "jpg", orientation: 6, tags: [] }],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
      detectionUpdate: { key: 0, pictureIds: [] },
      textUpdate: { key: 0, pictureIds: [] },
      pixelsUpdate: { key: 0, pictureIds: [] },
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
  metadataOrientation = 6;
  getMock.mockClear();
});

describe("the open lightbox follows a turn made elsewhere", () => {
  it("turns the picture back when an undo of a rotate lands on the open card", async () => {
    const wrapper = await openOverlayOnCard7();
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg?v=o6");

    metadataOrientation = 1; // the undo restored the pre-rotate orientation
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    // Orientation 1 is upright, so the cache-buster drops entirely. Asserted as
    // the whole URL rather than as "no longer says o6": a record that had lost
    // its orientation altogether would also stop saying o6, and would be a bug.
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg");
  });

  it("turns it again on the redo", async () => {
    const wrapper = await openOverlayOnCard7();

    metadataOrientation = 1;
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg");

    metadataOrientation = 6;
    await wrapper.setProps({ pixelsUpdate: { key: 2, pictureIds: [7] } });
    await flush();
    await flush();
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg?v=o6");
  });

  // Defect 2. `applyRotatedCards` hands the grid a NEW allGridImages array, and
  // the overlay re-seeds the open card whenever that prop moves. Without the
  // byte fields being preserved there, the undo above turned the picture and
  // this turned it straight back - and because it reads the SNAPSHOT taken at
  // open, not the array it is handed, even the correctly-rotated record below
  // did not save it.
  it("keeps the new orientation when the grid replaces its image array", async () => {
    const wrapper = await openOverlayOnCard7();

    metadataOrientation = 1;
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg");

    // What ImageGrid.applyRotatedCards does when it lands the rotate.
    await wrapper.setProps({
      allImages: [{ id: 7, format: "jpg", orientation: 1, tags: [] }],
    });
    await flush();
    await flush();

    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg");
  });

  // The signal is NOT gated on the payload's ids - see the watcher's comment -
  // so a frame naming other pictures still costs one metadata read. What it
  // must not do is re-read the boxes and the text: those are undone by a TURN,
  // and three of the five producers of a `pixels` event (a thumbnail
  // regeneration, a layout move and its migration) turn nothing. Re-reading the
  // text would take the user's word selection away with it.
  it("does not re-read boxes or text for a byte change that did not turn the picture", async () => {
    const wrapper = await openOverlayOnCard7();
    const detectionsBefore = callsMatching("/detections");
    const facesBefore = callsMatching("/faces");
    const textBefore = callsMatching("/text");

    // Same orientation: a thumbnail regeneration, not a turn.
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [4, 9] } });
    await flush();
    await flush();

    expect(callsMatching("/detections")).toBe(detectionsBefore);
    expect(callsMatching("/faces")).toBe(facesBefore);
    expect(callsMatching("/text")).toBe(textBefore);
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg?v=o6");
  });

  it("does re-read the boxes and the text when the picture actually turned", async () => {
    const wrapper = await openOverlayOnCard7();
    const detectionsBefore = callsMatching("/detections");
    const facesBefore = callsMatching("/faces");

    metadataOrientation = 1;
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    expect(callsMatching("/detections")).toBeGreaterThan(detectionsBefore);
    expect(callsMatching("/faces")).toBeGreaterThan(facesBefore);
  });

  it("ignores a repeated signal key", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = getMock.mock.calls.length;

    metadataOrientation = 1;
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    const afterFirst = getMock.mock.calls.length;
    expect(afterFirst).toBeGreaterThan(before);

    // Same key again: already processed.
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7, 8] } });
    await flush();
    await flush();
    expect(getMock.mock.calls.length).toBe(afterFirst);
  });

  it("ignores the signal while the overlay is closed", async () => {
    const wrapper = await openOverlayOnCard7();
    await wrapper.setProps({ open: false });
    await flush();
    const before = getMock.mock.calls.length;

    metadataOrientation = 1;
    await wrapper.setProps({ pixelsUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    expect(getMock.mock.calls.length).toBe(before);
  });
});
