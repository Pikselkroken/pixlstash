// Overlay turn refresh (#1419) - when a picture's BYTES are rewritten
// somewhere other than this overlay (an undo or redo of a rotate, a rotate made
// in another tab), the open lightbox must turn the picture without being closed
// and reopened.
//
// Two defects, one visible symptom:
//
//   1. Nothing told the overlay. The grid's card refresh is deferred under an
//      open lightbox, and the overlay re-reads `orientation` (which is what its
//      `<img>` URL's `?v=o<n>` is built from) only when the displayed card
//      changes. Fixed by `wsOrientationUpdate`.
//   2. Telling it was not enough. The overlay re-seeds the open card from the
//      sequence FROZEN when the overlay opened, and that snapshot kept the
//      pre-rotate `orientation` - so the very next replacement of the
//      `allImages` array (which `applyRotatedCards` performs right after a
//      rotate) turned the picture back to how it was. Fixed on `develop` in
//      parallel with this branch, from the local-rotate side: the frozen
//      snapshot is patched, and the re-seed preserves the byte fields for rows
//      the patch cannot reach. ImageOverlayRotate.test.js owns both.
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
      orientationUpdate: { key: 0, pictureIds: [] },
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
    await wrapper.setProps({ orientationUpdate: { key: 1, pictureIds: [7] } });
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
    await wrapper.setProps({ orientationUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg");

    metadataOrientation = 6;
    await wrapper.setProps({ orientationUpdate: { key: 2, pictureIds: [7] } });
    await flush();
    await flush();
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg?v=o6");
  });

  // Defect 2, end to end. `applyRotatedCards` hands the grid a NEW
  // allGridImages array, and the overlay re-seeds the open card whenever that
  // prop moves - from the snapshot frozen at open, not from the array it is
  // handed, so a correctly-rotated record does not save it either.
  //
  // TWO mechanisms keep the turn now, and they are redundant for the case here,
  // so this test fails only when BOTH are gone (verified): `fetchOverlayMetadata`
  // patches the frozen snapshot's orientation, and `setOverlayImageById`
  // preserves the byte fields for a row that patch never reaches. Each is
  // pinned individually by ImageOverlayRotate.test.js, which owns them. What
  // this one holds is the whole #1419 chain - socket signal through to the
  // `<img>` - which neither of those exercises.
  it("keeps the new orientation when the grid replaces its image array", async () => {
    const wrapper = await openOverlayOnCard7();

    metadataOrientation = 1;
    await wrapper.setProps({ orientationUpdate: { key: 1, pictureIds: [7] } });
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

  // The signal names the pictures it turned and the watcher is gated on them, so
  // a frame about other pictures costs nothing at all - not even a metadata
  // read. `pictureText.refresh()` clears the viewer's word selection, so firing
  // on somebody else's turn is a visible loss, and a 200-id rotate or a 64-id
  // background batch names other pictures most of the time.
  it("does nothing at all for a turn that names other pictures", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = getMock.mock.calls.length;

    metadataOrientation = 1; // would be picked up by a wrongly-ungated refresh
    await wrapper.setProps({
      orientationUpdate: { key: 1, pictureIds: [4, 9] },
    });
    await flush();
    await flush();

    expect(getMock.mock.calls.length).toBe(before);
    expect(fullImageSrc(wrapper)).toBe("http://test/pictures/7.jpg?v=o6");
  });

  it("re-reads the boxes and the text when the open picture is turned", async () => {
    const wrapper = await openOverlayOnCard7();
    const detectionsBefore = callsMatching("/detections");
    const facesBefore = callsMatching("/faces");

    metadataOrientation = 1;
    await wrapper.setProps({ orientationUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    expect(callsMatching("/detections")).toBeGreaterThan(detectionsBefore);
    expect(callsMatching("/faces")).toBeGreaterThan(facesBefore);
  });

  it("ignores a repeated signal key", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = getMock.mock.calls.length;

    metadataOrientation = 1;
    await wrapper.setProps({ orientationUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();
    const afterFirst = getMock.mock.calls.length;
    expect(afterFirst).toBeGreaterThan(before);

    // Same key again: already processed.
    await wrapper.setProps({
      orientationUpdate: { key: 1, pictureIds: [7, 8] },
    });
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
    await wrapper.setProps({ orientationUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    await flush();

    expect(getMock.mock.calls.length).toBe(before);
  });
});
