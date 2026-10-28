// Keys in the lightbox (#2139): a bare digit rates and Ctrl+2 does not;
// Ctrl+V pastes the copied tags wherever focus is, except a text field.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import Lightbox from "./Lightbox.vue";

enableAutoUnmount(afterEach);

const getMock = vi.fn(async () => ({ data: [] }));

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: { get: (...a) => getMock(...a), post: vi.fn(), delete: vi.fn() },
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

const pasteClipboard = vi.fn();
const clearSelection = vi.fn();
let hasSelection = false;

// The Tags panel, reduced to what the lightbox's keys reach into.
const TagsPanelStub = {
  template: "<div />",
  setup(_, { expose }) {
    expose({
      pasteClipboard,
      clearSelection,
      get hasSelection() {
        return hasSelection;
      },
    });
  },
};

const STUBS = {
  LightboxTagsPanel: TagsPanelStub,
  LightboxFilmstrip: true,
  LightboxDescriptionPanel: true,
  LightboxMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  "v-icon": true,
  "v-menu": true,
  "v-tooltip": true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openLightbox() {
  const wrapper = mount(Lightbox, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [{ id: 7, format: "jpg", tags: [], score: 0 }],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
    },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  return wrapper;
}

function press(key, init = {}, target = window) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  target.dispatchEvent(event);
  return event;
}

beforeEach(() => {
  setActivePinia(createPinia());
  pasteClipboard.mockReset();
  clearSelection.mockReset();
  hasSelection = false;
});

describe("Lightbox - star keys", () => {
  it("a bare digit rates the picture and Ctrl+2 does not", async () => {
    const wrapper = await openLightbox();
    press("2", { code: "Digit2", ctrlKey: true });
    expect(wrapper.emitted("apply-score")).toBeUndefined();
    press("2", { code: "Digit2" });
    expect(wrapper.emitted("apply-score")).toHaveLength(1);
  });
});

describe("Lightbox - Ctrl+V", () => {
  it("pastes the copied tags with nothing in particular focused", async () => {
    await openLightbox();
    const event = press("v", { code: "KeyV", ctrlKey: true });
    expect(pasteClipboard).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it("still pastes while some text on the page is selected", async () => {
    await openLightbox();
    const text = document.createElement("p");
    text.textContent = "a description";
    document.body.appendChild(text);
    window.getSelection().selectAllChildren(text);
    try {
      press("v", { code: "KeyV", ctrlKey: true });
    } finally {
      window.getSelection().removeAllRanges();
      text.remove();
    }
    expect(pasteClipboard).toHaveBeenCalledTimes(1);
  });

  it("leaves a text field its own paste", async () => {
    await openLightbox();
    const input = document.createElement("input");
    document.body.appendChild(input);
    try {
      const event = press("v", { code: "KeyV", ctrlKey: true }, input);
      expect(event.defaultPrevented).toBe(false);
    } finally {
      input.remove();
    }
    expect(pasteClipboard).not.toHaveBeenCalled();
  });
});

describe("Lightbox - Escape with tags selected", () => {
  it("clears the tag selection", async () => {
    hasSelection = true;
    await openLightbox();
    press("Escape");
    expect(clearSelection).toHaveBeenCalledTimes(1);
  });
});
