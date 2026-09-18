// The open lightbox keeps a picture's text current (#1197): when a text read
// lands for the picture on screen (`pictures_changed` with `ocr_text`), the
// overlay reads /pictures/{id}/text again; a frame for other pictures does not.

import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";

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

import ImageOverlay from "./ImageOverlay.vue";
import { useSearchStore } from "../../stores/useSearchStore";

enableAutoUnmount(afterEach);

// Picture id -> its /text body (or a promise of one); unlisted has no words.
let textBodies = {};

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/text")) {
    const id = url.match(/\/pictures\/(\d+)\/text/)?.[1];
    return { data: (await textBodies[id]) ?? { state: "read", lines: [] } };
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

const revealWord = vi.fn();

const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: {
    name: "OverlayDescriptionPanel",
    methods: {
      cancelEditDescription() {},
      resetCopyState() {},
      revealWord(index) {
        revealWord(index);
      },
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

const textCalls = () =>
  getMock.mock.calls
    .map(([url]) => url)
    .filter((url) => typeof url === "string" && url.includes("/text"));

async function openOverlayOnCard7() {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [{ id: 7, tags: [] }],
      backendUrl: "http://test",
      textUpdate: { key: 0, pictureIds: [] },
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
  getMock.mockClear();
  revealWord.mockClear();
  textBodies = {};
});

describe("ImageOverlay - text in the picture", () => {
  it("reads the open picture's text, with the active text search", async () => {
    useSearchStore().searchQuery = "coffee";
    await openOverlayOnCard7();
    expect(textCalls().at(-1)).toBe("/pictures/7/text?query=coffee");
  });

  it("re-reads when a text read lands for the open picture", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = textCalls().length;
    expect(before).toBeGreaterThan(0);

    await wrapper.setProps({ textUpdate: { key: 1, pictureIds: [7] } });
    await flush();
    expect(textCalls().length).toBe(before + 1);
  });

  it("ignores a text read for other pictures", async () => {
    const wrapper = await openOverlayOnCard7();
    const before = textCalls().length;

    await wrapper.setProps({ textUpdate: { key: 1, pictureIds: [8, 9] } });
    await flush();
    expect(textCalls().length).toBe(before);
  });
});

describe("ImageOverlay - word boxes over the picture", () => {
  const COFFEE = {
    state: "read",
    lines: [
      [
        { text: "BAKERY", box: [0.1, 0.1, 0.2, 0.1], matched: false },
        { text: "COFFEE", box: [0.4, 0.1, 0.2, 0.1], matched: true },
      ],
    ],
  };

  /** Give the jsdom <img> a size, so the overlay counts as laid out. */
  async function layOut(wrapper) {
    const img = wrapper.find(".overlay-img");
    for (const [property, value] of [
      ["complete", true],
      ["naturalWidth", 800],
      ["naturalHeight", 600],
      ["clientWidth", 400],
      ["clientHeight", 300],
    ]) {
      Object.defineProperty(img.element, property, {
        configurable: true,
        value,
      });
    }
    await img.trigger("load");
    await flush();
  }

  async function openOnCoffee() {
    // A search matching picture 7's text opens it on the Text tab.
    useSearchStore().searchQuery = "coffee";
    textBodies = { 7: COFFEE };
    const wrapper = mount(ImageOverlay, {
      props: {
        open: false,
        initialImageId: 7,
        allImages: [
          { id: 7, format: "jpg", tags: [] },
          { id: 8, format: "jpg", tags: [] },
        ],
        backendUrl: "http://test",
        textUpdate: { key: 0, pictureIds: [] },
      },
      global: { stubs: STUBS },
      attachTo: document.body,
    });
    await wrapper.setProps({ open: true });
    await flush();
    await flush();
    await layOut(wrapper);
    return wrapper;
  }

  it("draws one box per word, in pixels of the displayed picture", async () => {
    const wrapper = await openOnCoffee();
    const boxes = wrapper.findAll(".picture-text-box");
    expect(boxes).toHaveLength(2);
    const line = boxes[1].find(".picture-text-box-line");
    expect(line.attributes()).toMatchObject({
      x: "160",
      y: "30",
      width: "80",
      height: "30",
    });
    // The search match lands selected.
    expect(boxes[1].classes()).toContain("picture-text-box--selected");
  });

  it("clicking a box selects its word and reveals it in the sidebar", async () => {
    const wrapper = await openOnCoffee();
    await wrapper.findAll(".picture-text-box")[0].trigger("click");
    expect(revealWord).toHaveBeenCalledWith(0);
    const boxes = wrapper.findAll(".picture-text-box");
    expect(boxes[0].classes()).toContain("picture-text-box--selected");
    expect(boxes[1].classes()).not.toContain("picture-text-box--selected");
  });

  it("the boxes go when the picture changes", async () => {
    const wrapper = await openOnCoffee();
    expect(wrapper.findAll(".picture-text-box")).toHaveLength(2);
    // Picked by hand, so the next picture opens on Text too.
    wrapper
      .findComponent({ name: "OverlayDescriptionPanel" })
      .vm.$emit("pick-tab", "text");
    // Picture 8's text is still on its way: nothing of 7's may stay drawn.
    textBodies[8] = new Promise(() => {});

    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
    );
    await flush();
    await flush();
    expect(getMock.mock.calls.map(([url]) => url)).toContain(
      "/pictures/8/text?query=coffee",
    );
    await layOut(wrapper);
    expect(wrapper.findAll(".picture-text-box")).toHaveLength(0);
  });
});
