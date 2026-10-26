// The lightbox filmstrip scrolls on its own. The lightbox asks the strip to
// keep the picture on screen in view (centred on every open) and draws the
// thumbs around wherever the strip has been scrolled to, never around the
// picture. The pixel rules of `reveal` are OverlayFilmstrip.test.js's.

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

const reveal = vi.fn();

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
    props: ["items", "start", "total", "virtual"],
    emits: ["select", "first-visible"],
    methods: { reveal: (...args) => reveal(...args) },
    template: "<div></div>",
  },
};

const PICTURES = Array.from({ length: 60 }, (_, i) => ({
  id: i + 1,
  tags: [],
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function openOverlayOn(id) {
  setActivePinia(createPinia());
  reveal.mockClear();
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
const drawn = (wrapper) =>
  strip(wrapper)
    .props("items")
    .map((item) => item.index);
const activeId = (wrapper) =>
  strip(wrapper)
    .props("items")
    .find((item) => item.isActive)?.id;

describe("ImageOverlay filmstrip scrolls independently", () => {
  it("opens centred on the picture", async () => {
    const wrapper = await openOverlayOn(16);
    expect(strip(wrapper).props("virtual")).toBe(true);
    expect(strip(wrapper).props("total")).toBe(60);
    expect(reveal).toHaveBeenLastCalledWith(15, { centre: true });
  });

  it("only asks the strip to keep a clicked thumb in view", async () => {
    const wrapper = await openOverlayOn(16);
    strip(wrapper).vm.$emit("select", 13);
    await flush();
    expect(activeId(wrapper)).toBe(14);
    expect(reveal).toHaveBeenLastCalledWith(13, { centre: false });
  });

  it("draws the thumbs round where the strip is scrolled, not the picture", async () => {
    const wrapper = await openOverlayOn(16);
    strip(wrapper).vm.$emit("first-visible", 30);
    await flush();
    // 7 in view from 30, and 20 either side.
    expect(strip(wrapper).props("start")).toBe(10);
    expect(drawn(wrapper)[0]).toBe(10);
    expect(drawn(wrapper).at(-1)).toBe(56);
    expect(reveal).toHaveBeenCalledTimes(1);

    strip(wrapper).vm.$emit("first-visible", 59);
    await flush();
    expect(drawn(wrapper).at(-1)).toBe(59);
    expect(drawn(wrapper)[0]).toBe(33);
  });

  it("starts centred again on the next open", async () => {
    const wrapper = await openOverlayOn(16);
    strip(wrapper).vm.$emit("select", 13);
    await flush();
    await wrapper.setProps({ open: false });
    await flush();
    await wrapper.setProps({ open: true });
    await flush();
    await flush();
    expect(reveal).toHaveBeenLastCalledWith(13, { centre: true });
  });
});
