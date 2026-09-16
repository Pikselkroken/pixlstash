// The open lightbox keeps a picture's text current (#1197): when a text read
// lands for the picture on screen (`pictures_changed` with `ocr_text`), the
// overlay reads /pictures/{id}/text again; a frame for other pictures does not.

import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import ImageOverlay from "./ImageOverlay.vue";
import { useSearchStore } from "../../stores/useSearchStore";

enableAutoUnmount(afterEach);

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/text")) {
    return { data: { state: "read", lines: [] } };
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

const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
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
