// The lightbox filmstrip scrolls on its own. The lightbox asks the strip to
// keep the picture on screen in view (centred on every open) and draws the
// thumbs around wherever the strip has been scrolled to, never around the
// picture. The pixel rules of `reveal` are LightboxFilmstrip.test.js's.

import { afterEach, describe, it, expect, vi } from "vitest";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Lightbox from "./Lightbox.vue";

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
  LightboxTagsPanel: true,
  // Closing the lightbox calls into the description panel.
  LightboxDescriptionPanel: {
    template: "<div></div>",
    methods: { cancelEditDescription() {}, resetCopyState() {} },
  },
  LightboxMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  ProgressOverlay: true,
  LightboxFilmstrip: {
    name: "LightboxFilmstrip",
    props: ["items", "offsets", "widths"],
    emits: ["select", "visible-range"],
    methods: { reveal: (...args) => reveal(...args) },
    template: "<div></div>",
  },
};

const PICTURES = Array.from({ length: 60 }, (_, i) => ({
  id: i + 1,
  tags: [],
}));

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function openLightboxOn(id, allImages = PICTURES) {
  setActivePinia(createPinia());
  reveal.mockClear();
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

const strip = (wrapper) => wrapper.findComponent({ name: "LightboxFilmstrip" });
const drawn = (wrapper) =>
  strip(wrapper)
    .props("items")
    .map((item) => item.index);
const activeId = (wrapper) =>
  strip(wrapper)
    .props("items")
    .find((item) => item.isActive)?.id;

describe("Lightbox filmstrip scrolls independently", () => {
  it("opens centred on the picture", async () => {
    const wrapper = await openLightboxOn(16);
    expect(strip(wrapper).props("offsets")).toHaveLength(60);
    expect(reveal).toHaveBeenLastCalledWith(15, { centre: true });
  });

  it("says where the picture sits in the view's order", async () => {
    const wrapper = await openLightboxOn(16);
    expect(wrapper.find(".lightbox-position").text()).toBe("16 of 60");
    strip(wrapper).vm.$emit("select", 59);
    await flush();
    expect(wrapper.find(".lightbox-position").text()).toBe("60 of 60");
  });

  it("sizes each thumb to its picture's shape, 2px apart", async () => {
    const wrapper = await openLightboxOn(1, [
      { id: 1, tags: [], width: 1600, height: 900 },
      // Not yet probed: a square.
      { id: 2, tags: [] },
      // Turned a quarter: drawn as it shows, taller than wide.
      { id: 3, tags: [], width: 1600, height: 900, orientation: 6 },
      // A sliver is clamped so it can still be hit, a panorama so it does not
      // take the strip.
      { id: 4, tags: [], width: 100, height: 1000 },
      { id: 5, tags: [], width: 4000, height: 500 },
    ]);
    expect(strip(wrapper).props("widths")).toEqual([100, 56, 32, 24, 140]);
    expect(strip(wrapper).props("offsets")).toEqual([0, 102, 160, 194, 220]);
  });

  it("lays the members of a stack opened here flush, so the run reads as one", async () => {
    setActivePinia(createPinia());
    const stack = [
      { id: 1, tags: [] },
      { id: 2, tags: [], stack_id: 5, stack_position: 0, stack_count: 2 },
      { id: 3, tags: [], stack_id: 5, stack_position: 1, stack_count: 2 },
      { id: 4, tags: [] },
    ];
    const wrapper = mount(Lightbox, {
      props: {
        open: false,
        initialImageId: 2,
        initialExpandedStackIds: [5],
        allImages: stack,
        backendUrl: "http://test",
      },
      global: { stubs: STUBS },
    });
    await wrapper.setProps({ open: true });
    await flush();
    await flush();
    expect(drawn(wrapper)).toEqual([0, 1, 2, 3]);
    // 2px between pictures, none inside the stack.
    expect(strip(wrapper).props("offsets")).toEqual([0, 58, 114, 172]);
  });

  it("only asks the strip to keep a clicked thumb in view", async () => {
    const wrapper = await openLightboxOn(16);
    strip(wrapper).vm.$emit("select", 13);
    await flush();
    expect(activeId(wrapper)).toBe(14);
    expect(reveal).toHaveBeenLastCalledWith(13, { centre: false });
  });

  it("draws the thumbs round where the strip is scrolled, not the picture", async () => {
    const wrapper = await openLightboxOn(16);
    strip(wrapper).vm.$emit("visible-range", { first: 30, last: 36 });
    await flush();
    // 30 to 36 in view, and 20 either side.
    expect(drawn(wrapper)[0]).toBe(10);
    expect(drawn(wrapper).at(-1)).toBe(56);
    expect(reveal).toHaveBeenCalledTimes(1);

    strip(wrapper).vm.$emit("visible-range", { first: 55, last: 59 });
    await flush();
    expect(drawn(wrapper).at(-1)).toBe(59);
    expect(drawn(wrapper)[0]).toBe(35);
  });

  it("starts centred again on the next open", async () => {
    const wrapper = await openLightboxOn(16);
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

describe("Lightbox stack chip", () => {
  const STACK = [
    { id: 1, tags: [] },
    { id: 2, tags: [], stack_id: 5, stack_position: 0, stack_count: 3 },
    { id: 3, tags: [], stack_id: 5, stack_position: 1, stack_count: 3 },
    { id: 4, tags: [], stack_id: 5, stack_position: 2, stack_count: 3 },
    { id: 9, tags: [] },
  ];
  const chipText = (wrapper) =>
    wrapper.find(".lightbox-stack-chip-text").text().replace(/\s+/g, " ");
  const step = (wrapper, i) =>
    wrapper.findAll(".lightbox-stack-chip-step")[i].trigger("click");

  it("steps through a folded stack, wrapping at either end", async () => {
    const wrapper = await openLightboxOn(2, STACK);
    expect(chipText(wrapper)).toBe("1 of 3 in this stack");
    // Folded: the filmstrip shows the cover alone.
    expect(strip(wrapper).props("offsets")).toHaveLength(3);

    await step(wrapper, 1);
    await flush();
    expect(activeId(wrapper)).toBe(3);
    // Unfolded, so the member stepped to is on the strip.
    expect(strip(wrapper).props("offsets")).toHaveLength(5);
    expect(chipText(wrapper)).toBe("2 of 3 in this stack");

    await step(wrapper, 1);
    await step(wrapper, 1);
    await flush();
    expect(activeId(wrapper)).toBe(2);

    await step(wrapper, 0);
    await flush();
    expect(activeId(wrapper)).toBe(4);
    expect(chipText(wrapper)).toBe("3 of 3 in this stack");
  });
});
