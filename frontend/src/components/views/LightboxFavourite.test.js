// H in the lightbox toggles the owner's heart, except on a picture opened from
// the Scrapheap, which shows no heart anywhere. Harness as in
// LightboxRotate.test.js.

import { afterEach, describe, it, expect, beforeEach, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Lightbox from "./Lightbox.vue";

enableAutoUnmount(afterEach);

const getMock = vi.fn(async (url) => {
  const path = String(url ?? "");
  if (path.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  if (path.includes("/metadata")) {
    return {
      data: { id: 7, format: "jpg", width: 1600, height: 1200, tags: [] },
    };
  }
  return { data: [] };
});

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: {
      get: (...a) => getMock(...a),
      post: vi.fn(async () => ({ data: {} })),
      delete: vi.fn(),
    },
    appendShareToken: (u) => u,
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
  };
});

vi.mock("../../api/operations", () => ({
  listOperations: vi.fn().mockResolvedValue([]),
  getUndoState: vi.fn().mockResolvedValue({ can_undo: true, can_redo: true }),
  undoLastOperation: vi.fn().mockResolvedValue({ operations: [] }),
  redoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoBatch: vi.fn().mockResolvedValue({ operations: [] }),
}));

const STUBS = {
  RailTags: true,
  LightboxFilmstrip: true,
  LightboxDescriptionPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  "v-icon": true,
  "v-menu": true,
  "v-tooltip": true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openLightbox(extraProps = {}) {
  const image = { id: 7, format: "jpg", tags: [], favourite: false };
  const wrapper = mount(Lightbox, {
    props: {
      open: false,
      initialImageId: image.id,
      allImages: [image],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
      ...extraProps,
    },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  return wrapper;
}

function pressH() {
  window.dispatchEvent(
    new KeyboardEvent("keydown", { key: "h", bubbles: true, cancelable: true }),
  );
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("H in the lightbox", () => {
  it("favourites the open picture", async () => {
    const wrapper = await openLightbox();
    pressH();
    expect(wrapper.emitted("apply-favourite")?.[0]?.[1]).toBe(true);
  });

  it("does nothing on a picture opened from the Scrapheap", async () => {
    const wrapper = await openLightbox({ favouritable: false });
    pressH();
    expect(wrapper.emitted("apply-favourite")).toBeUndefined();
  });
});
