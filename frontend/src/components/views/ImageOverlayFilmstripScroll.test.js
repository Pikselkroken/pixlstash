// The lightbox filmstrip scrolls on its own. A click on a thumb leaves the
// strip where it is, the wheel scrolls it without changing the picture, and
// it follows the picture on screen only when that picture would leave view:
// one slot when stepping past an edge, centred on a longer jump.
//
// The strip draws 7 thumbs plus 3 either side as a buffer, so away from the
// ends of the 30 pictures the first thumb it is handed is the first visible
// one minus 3.

import { afterEach, describe, it, expect, vi } from "vitest";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import ImageOverlay from "./ImageOverlay.vue";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn(async (url) => {
      if (typeof url === "string" && url.includes("/workflow")) {
        const notFound = new Error("no workflow");
        notFound.response = { status: 404 };
        throw notFound;
      }
      return { data: url.includes("/metadata") ? { tags: [] } : [] };
    }),
    post: vi.fn(),
    delete: vi.fn(),
  },
  appendShareToken: (u) => u,
  isReadOnly: { value: false },
}));

vi.mock("../../api/stacks", () => ({
  listStackPictures: vi.fn(async () => ({ pictures: [] })),
}));

enableAutoUnmount(afterEach);

const STUBS = {
  OverlayTagsPanel: true,
  // Closing the lightbox calls into the description panel.
  OverlayDescriptionPanel: {
    template: "<div></div>",
    methods: { cancelEditDescription() {}, resetCopyState() {} },
  },
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  ProgressOverlay: true,
  OverlayFilmstrip: {
    name: "OverlayFilmstrip",
    props: ["items"],
    emits: ["select", "scroll-by"],
    methods: { resetWheel: () => resetWheel() },
    template: "<div></div>",
  },
};

const resetWheel = vi.fn();
const PICTURES = Array.from({ length: 30 }, (_, i) => ({
  id: i + 1,
  tags: [],
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function openOverlayOn(id) {
  setActivePinia(createPinia());
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: id,
      allImages: PICTURES,
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

const strip = (wrapper) => wrapper.findComponent({ name: "OverlayFilmstrip" });
const firstVisible = (wrapper) => strip(wrapper).props("items")[0].index + 3;
const activeId = (wrapper) =>
  strip(wrapper)
    .props("items")
    .find((item) => item.isActive)?.id;

async function press(key) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key }));
  await flush();
}

describe("ImageOverlay filmstrip scrolls independently", () => {
  it("opens centred and stays put when a thumb is clicked", async () => {
    const wrapper = await openOverlayOn(16);
    expect(firstVisible(wrapper)).toBe(12);

    strip(wrapper).vm.$emit("select", 13);
    await flush();

    expect(activeId(wrapper)).toBe(14);
    expect(firstVisible(wrapper)).toBe(12);
  });

  it("scrolls on the wheel without changing the picture, and stops at the end", async () => {
    const wrapper = await openOverlayOn(16);
    for (let i = 0; i < 4; i += 1) strip(wrapper).vm.$emit("scroll-by", 1);
    await flush();
    expect(firstVisible(wrapper)).toBe(16);
    expect(activeId(wrapper)).toBe(16);

    // 23 is the last top that still fills the strip: the wheel stops there.
    resetWheel.mockClear();
    for (let i = 0; i < 7; i += 1) strip(wrapper).vm.$emit("scroll-by", 1);
    expect(resetWheel).not.toHaveBeenCalled();
    strip(wrapper).vm.$emit("scroll-by", 1);
    expect(resetWheel).toHaveBeenCalledTimes(1);
  });

  it("follows one slot when stepping past an edge, and centres on a jump", async () => {
    const wrapper = await openOverlayOn(16);
    // Picture 16 is index 15: scroll it two slots above the top edge, then
    // step down onto the slot just above it.
    for (let i = 0; i < 5; i += 1) strip(wrapper).vm.$emit("scroll-by", 1);
    await flush();
    expect(firstVisible(wrapper)).toBe(17);
    await press("ArrowRight");
    expect(activeId(wrapper)).toBe(17);
    expect(firstVisible(wrapper)).toBe(16);

    // Picture 10 is index 9, far above the view: centred on it.
    await wrapper.setProps({ initialImageId: 10 });
    await flush();
    expect(activeId(wrapper)).toBe(10);
    expect(firstVisible(wrapper)).toBe(6);
  });

  it("starts centred again on the next open", async () => {
    const wrapper = await openOverlayOn(16);
    for (let i = 0; i < 2; i += 1) strip(wrapper).vm.$emit("scroll-by", 1);
    await flush();
    expect(firstVisible(wrapper)).toBe(14);

    await wrapper.setProps({ open: false });
    await flush();
    await wrapper.setProps({ open: true });
    await flush();
    await flush();
    expect(activeId(wrapper)).toBe(16);
    expect(firstVisible(wrapper)).toBe(12);
  });

  it("ignores the wheel before the strip has a picture to place", async () => {
    // 99 is in no list, so the strip draws nothing yet.
    const wrapper = await openOverlayOn(99);
    expect(strip(wrapper).props("items")).toEqual([]);
    for (let i = 0; i < 13; i += 1) strip(wrapper).vm.$emit("scroll-by", 1);
    await flush();

    await wrapper.setProps({ initialImageId: 16 });
    await flush();
    expect(activeId(wrapper)).toBe(16);
    expect(firstVisible(wrapper)).toBe(12);
  });
});
