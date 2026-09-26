// The overlay follows an explicit move to a picture created while it was open.
//
// An in-app ComfyUI i2i/upscale stacks its output next to the picture being
// viewed and ImageGrid hands the new id down as `initialImageId` (§7,
// "Grid refresh contract"). The id resolves against `allImageById` and
// `filmstripImageById`, both of which derive from `frozenAllImages` - the
// snapshot ImageOverlay takes when it opens - so a picture that did not exist
// at that moment is in neither, `setOverlayImageById` falls through to its
// no-target branch, and the overlay silently stays where it was.
//
// That is what #1256 actually looked like to a user. The `.value` typo in
// ImageGrid's handler (fixed alongside, guarded by
// components/TemplateRefValueUnwrap.test.js) meant the assignment threw before
// it ever got this far, so the two faults hid each other: fixing either one on
// its own leaves the overlay stuck.
//
// The second test is the other direction. The snapshot is frozen for a reason -
// a background refetch must not drop the open picture out from under the
// navigation - so re-capturing it on an explicit move must not turn into
// re-capturing it whenever `allImages` changes.

import { afterEach, describe, it, expect, vi } from "vitest";

// `AppInspector`'s tab band renders a `VIcon` imported from the Vuetify barrel
// (#1313 gave the lightbox pane Info | Recipe tabs), and this suite mounts the
// overlay without the Vuetify plugin. Stub the barrel, per
// `testing/vuetifyStubs.js`: nothing here needs Vuetify's real behaviour.
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Statically imported for the same reason as ImageOverlaySmartScore.test.js:
// compiling this SFC inside the first test's timeout is what makes it flake.
import ImageOverlay from "./ImageOverlay.vue";

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/metadata")) {
    return { data: { tags: [] } };
  }
  if (typeof url === "string" && url.includes("/workflow")) {
    const notFound = new Error("no workflow");
    notFound.response = { status: 404 };
    throw notFound;
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

vi.mock("../../api/stacks", () => ({
  listStackPictures: vi.fn(async () => ({ pictures: [] })),
}));

enableAutoUnmount(afterEach);

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
  // Real-but-thin panel, so the test can read the card actually on screen.
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

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const shownId = (wrapper) =>
  wrapper.findComponent({ name: "OverlayMetadataPanel" }).props("image")?.id;

async function openOverlayOn(id, allImages) {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: id,
      allImages,
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

describe("ImageOverlay follows a picture created while it was open", () => {
  it("moves to the new stack member a ComfyUI run produced", async () => {
    setActivePinia(createPinia());
    const wrapper = await openOverlayOn(7, [
      { id: 7, stack_id: "s1", stackCount: 1, tags: [] },
    ]);
    expect(shownId(wrapper)).toBe(7);

    // The WebSocket `picture_imported` insert lands first, then the runner
    // emits `update:overlayImageId` with the new member.
    await wrapper.setProps({
      allImages: [
        { id: 7, stack_id: "s1", stackCount: 2, tags: [] },
        { id: 8, stack_id: "s1", stackCount: 2, tags: [] },
      ],
    });
    await flush();
    await wrapper.setProps({ initialImageId: 8 });
    await flush();

    expect(shownId(wrapper)).toBe(8);
  });

  it("still ignores a background refetch that drops the open picture", async () => {
    setActivePinia(createPinia());
    const wrapper = await openOverlayOn(7, [
      { id: 7, tags: [] },
      { id: 9, tags: [] },
    ]);
    expect(shownId(wrapper)).toBe(7);

    // Removing the tag a filtered view is filtered on drops 7 from the grid.
    // The overlay must keep showing it, and keep it in the navigation sequence.
    await wrapper.setProps({ allImages: [{ id: 9, tags: [] }] });
    await flush();
    await flush();

    expect(shownId(wrapper)).toBe(7);
  });
});
