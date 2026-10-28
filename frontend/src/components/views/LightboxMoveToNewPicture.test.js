// The lightbox follows an explicit move to a picture created while it was open.
//
// An in-app ComfyUI i2i/upscale stacks its output next to the picture being
// viewed and ImageGrid hands the new id down as `initialImageId` (§7,
// "Grid refresh contract"). The id resolves against `allImageById` and
// `filmstripImageById`, both of which derive from `frozenAllImages` - the
// snapshot Lightbox takes when it opens - so a picture that did not exist
// at that moment is in neither, `setLightboxImageById` falls through to its
// no-target branch, and the lightbox silently stays where it was.
//
// That is what #1256 actually looked like to a user. The `.value` typo in
// ImageGrid's handler (fixed alongside, guarded by
// components/TemplateRefValueUnwrap.test.js) meant the assignment threw before
// it ever got this far, so the two faults hid each other: fixing either one on
// its own leaves the lightbox stuck.
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

// Statically imported for the same reason as LightboxSmartScore.test.js:
// compiling this SFC inside the first test's timeout is what makes it flake.
import Lightbox from "./Lightbox.vue";

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
  LightboxTagsPanel: true,
  LightboxFilmstrip: true,
  LightboxDescriptionPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  ProgressOverlay: true,
  // Real-but-thin panel, so the test can read the card actually on screen.
  LightboxMetadataPanel: {
    name: "LightboxMetadataPanel",
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
  wrapper.findComponent({ name: "LightboxMetadataPanel" }).props("image")?.id;

async function openLightboxOn(id, allImages) {
  const wrapper = mount(Lightbox, {
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

describe("Lightbox follows a picture created while it was open", () => {
  it("moves to the new stack member a ComfyUI run produced", async () => {
    setActivePinia(createPinia());
    const wrapper = await openLightboxOn(7, [
      { id: 7, stack_id: "s1", stackCount: 1, tags: [] },
    ]);
    expect(shownId(wrapper)).toBe(7);

    // The WebSocket `picture_imported` insert lands first, then the runner
    // emits `update:lightboxImageId` with the new member.
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
    const wrapper = await openLightboxOn(7, [
      { id: 7, tags: [] },
      { id: 9, tags: [] },
    ]);
    expect(shownId(wrapper)).toBe(7);

    // Removing the tag a filtered view is filtered on drops 7 from the grid.
    // The lightbox must keep showing it, and keep it in the navigation sequence.
    await wrapper.setProps({ allImages: [{ id: 9, tags: [] }] });
    await flush();
    await flush();

    expect(shownId(wrapper)).toBe(7);
  });
});

// A run started from the grid ends while the lightbox is open (#1862). The
// grid holds `picture_imported` inserts back until the lightbox closes, so the
// result is in no list the lightbox has: `showRunResult` reads it by id, as
// *Show it* does. Only the lightbox knows what the reader is on by then.
describe("Lightbox steps to a finished run's picture", () => {
  const RESULT = { id: 31, stack_id: "s1", stackCount: 2, tags: [] };
  const rowsById = () =>
    getMock.mockImplementation(async (url, config) => {
      const asked = `${url} ${JSON.stringify(config?.params ?? {})}`;
      if (url.includes("/metadata")) return { data: { tags: [] } };
      if (asked.includes("31")) return { data: [RESULT] };
      return { data: [] };
    });

  it("moves when the reader is still on the picture the run was made from", async () => {
    setActivePinia(createPinia());
    rowsById();
    const wrapper = await openLightboxOn(7, [
      { id: 7, stack_id: "s1", stackCount: 1, tags: [] },
      { id: 9, tags: [] },
    ]);
    wrapper.vm.showRunResult(31, 7, "s1");
    await flush();
    await flush();
    expect(shownId(wrapper)).toBe(31);
    expect(wrapper.emitted("show-picture")?.at(-1)).toEqual([31]);
  });

  it("leaves a reader who has moved on to another picture where they are", async () => {
    setActivePinia(createPinia());
    rowsById();
    const wrapper = await openLightboxOn(9, [
      { id: 7, stack_id: "s1", stackCount: 1, tags: [] },
      { id: 9, tags: [] },
    ]);
    wrapper.vm.showRunResult(31, 7, "s1");
    await flush();
    await flush();
    expect(shownId(wrapper)).toBe(9);
    expect(wrapper.emitted("show-picture")).toBeUndefined();
  });
});
