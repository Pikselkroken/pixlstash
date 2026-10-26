// Tag chips in the overlay sidebar (#2139): a click selects a chip, the
// selection bar copies, removes or clears it, and Paste adds the copied tags.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import OverlayTagsPanel from "./OverlayTagsPanel.vue";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";

enableAutoUnmount(afterEach);

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: {
      get: vi.fn(async () => ({ data: {} })),
      post: vi.fn(),
      put: vi.fn(),
      patch: vi.fn(),
      delete: vi.fn(),
    },
    appendShareToken: (u) => u,
    isReadOnly: ref(false),
    newOperationBatchId: () => "batch-test",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    setRequestClientId: vi.fn(),
  };
});

vi.mock("../../api/tags", () => ({
  listTags: vi.fn(async () => []),
  listTagPredictions: vi.fn(async () => ({ tag_predictions: [], meta: {} })),
  removeTagEverywhere: vi.fn(async () => ({})),
  confirmTagPrediction: vi.fn(async () => ({})),
  rejectTagPrediction: vi.fn(async () => ({})),
  addPictureTag: vi.fn(async () => ({})),
  removePictureTag: vi.fn(async () => ({})),
  bulkFetchTags: vi.fn(async () => []),
}));
vi.mock("../../api/pictures", () => ({
  resetPictureTags: vi.fn(async () => ({})),
  getPictureMetadata: vi.fn(async () => ({ tags: [] })),
}));
vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn(async () => ({})),
  patchUserConfig: vi.fn(async () => ({})),
}));
vi.mock("../../api/users", () => ({
  getPenalisedTags: vi.fn(async () => []),
}));
vi.mock("../../api/taggers", () => ({
  listTaggers: vi.fn(async () => ({ plugins: [] })),
}));
vi.mock("../../composables/confirmRetag", () => ({
  confirmRetag: vi.fn(async () => true),
}));

const STUBS = {
  Tooltip: true,
  "v-icon": true,
  "v-menu": true,
  "v-progress-circular": true,
};

function image(id = 7) {
  return {
    id,
    tags: [
      { id: 1, tag: "rain" },
      { id: 2, tag: "umbrella" },
      { id: 3, tag: "yellow raincoat" },
    ],
  };
}

function mountPanel(props = {}) {
  return mount(OverlayTagsPanel, {
    props: { image: image(), backendUrl: "http://test", ...props },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
}

function chips(wrapper) {
  return wrapper.findAll(".tag-drop-zone .overlay-tag");
}

beforeEach(() => {
  setActivePinia(createPinia());
});

function button(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().trim().startsWith(text));
}

describe("OverlayTagsPanel - selecting, copying and pasting tags", () => {
  it("a click selects a chip and a second click deselects it; nothing is copied", async () => {
    const wrapper = mountPanel();
    const store = useTagClipboardStore();
    const [rain, , raincoat] = chips(wrapper);
    await rain.trigger("click");
    await raincoat.trigger("click");
    expect(wrapper.text()).toContain("2 selected");
    await raincoat.trigger("click");
    expect(wrapper.text()).toContain("1 selected");
    expect(store.clipboardTags).toEqual([]);
  });

  it("Copy copies the selection in the order it was picked", async () => {
    const wrapper = mountPanel();
    const store = useTagClipboardStore();
    const [rain, , raincoat] = chips(wrapper);
    await raincoat.trigger("click");
    await rain.trigger("click");
    await button(wrapper, "Copy").trigger("click");
    expect(store.clipboardTags).toEqual(["yellow raincoat", "rain"]);
  });

  it("the clear button empties the selection", async () => {
    const wrapper = mountPanel();
    await chips(wrapper)[0].trigger("click");
    await wrapper.find('button[aria-label="Clear selection"]').trigger("click");
    expect(wrapper.find(".tag-selection-bar").exists()).toBe(false);
  });

  it("a right-click selects the chip", async () => {
    const wrapper = mountPanel();
    await chips(wrapper)[1].trigger("contextmenu");
    expect(wrapper.text()).toContain("1 selected");
  });

  it("the x on a chip removes the tag and never selects it", async () => {
    const wrapper = mountPanel();
    await chips(wrapper)[1].find(".tag-delete-btn").trigger("click");
    expect(wrapper.find(".tag-selection-bar").exists()).toBe(false);
  });

  it("the x on a selected chip removes the whole selection, and hovering it lights every selected x", async () => {
    const { removeTagEverywhere } = await import("../../api/tags");
    removeTagEverywhere.mockClear();
    const wrapper = mountPanel();
    const [rain, umbrella, raincoat] = chips(wrapper);
    await rain.trigger("click");
    await raincoat.trigger("click");
    const x = raincoat.find(".tag-delete-btn");
    await x.trigger("mouseenter");
    const hot = wrapper
      .findAll(".overlay-tag--remove-hot")
      .map((chip) => chip.text().trim());
    expect(hot).toEqual(["rain", "yellow raincoat"]);
    expect(umbrella.classes()).not.toContain("overlay-tag--remove-hot");
    expect(x.attributes("aria-label")).toBe("Remove 2 selected tags (Del)");
    await x.trigger("click");
    await vi.waitFor(() =>
      expect(removeTagEverywhere.mock.calls.map((c) => c[1])).toEqual([
        "rain",
        "yellow raincoat",
      ]),
    );
    expect(wrapper.find(".tag-selection-bar").exists()).toBe(false);
  });

  it("hovering the x of a chip outside the selection lights nothing", async () => {
    const wrapper = mountPanel();
    const [rain, umbrella] = chips(wrapper);
    await rain.trigger("click");
    await umbrella.find(".tag-delete-btn").trigger("mouseenter");
    expect(wrapper.findAll(".overlay-tag--remove-hot")).toHaveLength(0);
  });

  it("drops the selection when the picture changes", async () => {
    const wrapper = mountPanel();
    await chips(wrapper)[0].trigger("click");
    expect(wrapper.text()).toContain("1 selected");
    await wrapper.setProps({ image: image(8) });
    expect(wrapper.text()).not.toContain("selected");
  });

  it("shows Paste only while something is copied, and pastes on this picture", async () => {
    const wrapper = mountPanel();
    const store = useTagClipboardStore();
    expect(wrapper.find(".tag-paste-btn").exists()).toBe(false);
    store.clipboardTags = ["rain", "hood"];
    await wrapper.vm.$nextTick();
    const paste = vi.spyOn(store, "pasteClipboard").mockResolvedValue(null);
    expect(wrapper.find(".tag-paste-btn").text()).toContain("Paste 2");
    await wrapper.find(".tag-paste-btn").trigger("click");
    expect(paste).toHaveBeenCalledWith([7]);
  });

  it("the + opens the add field straight away", async () => {
    const wrapper = mountPanel();
    await wrapper.find('button[aria-label="Add tag (T)"]').trigger("click");
    expect(wrapper.find(".tag-add-input").exists()).toBe(true);
  });
});
