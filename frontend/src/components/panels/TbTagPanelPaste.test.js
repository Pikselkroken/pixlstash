// The tag dialog (T) and copied tags (#2139): Paste shows while something is
// copied, Ctrl+V pastes from anywhere in the open dialog but a text field, and
// comma-separated text in the add field becomes one tag per item.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { enableAutoUnmount, mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const tagsApi = vi.hoisted(() => ({
  listTags: vi.fn(async () => []),
  addPictureTag: vi.fn(async () => ({})),
  removePictureTag: vi.fn(async () => ({})),
  bulkFetchTags: vi.fn(async () => []),
  removeTagEverywhere: vi.fn(async () => ({})),
  listTagPredictions: vi.fn(async () => []),
  confirmTagPrediction: vi.fn(async () => ({})),
  rejectTagPrediction: vi.fn(async () => ({})),
}));

vi.mock("../../api/tags", () => tagsApi);
vi.mock("../../api/pictures", () => ({
  resetPicturesTags: vi.fn(async () => ({})),
}));
vi.mock("../../api/taggers", () => ({
  listTaggers: vi.fn(async () => ({ plugins: [] })),
}));
vi.mock("../../api/config", () => ({ getUserConfig: vi.fn(async () => ({})) }));
vi.mock("../../api/users", () => ({ getPenalisedTags: vi.fn(async () => []) }));

import TbTagPanel from "./TbTagPanel.vue";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";

enableAutoUnmount(afterEach);

const STUBS = {
  "v-icon": true,
  "v-btn": true,
  "v-progress-circular": true,
  Tooltip: true,
};

beforeEach(() => {
  setActivePinia(createPinia());
  Object.values(tagsApi).forEach((mock) => mock.mockReset());
  tagsApi.listTags.mockResolvedValue([]);
  tagsApi.bulkFetchTags.mockResolvedValue([]);
  tagsApi.listTagPredictions.mockResolvedValue({ tag_predictions: [], meta: {} });
  tagsApi.addPictureTag.mockResolvedValue({});
});

async function mountPanel(open = true) {
  const wrapper = mount(TbTagPanel, {
    props: {
      backendUrl: "http://backend",
      open,
      selectedCount: 2,
      selectedImageIds: [1, 2],
      allGridImages: [{ id: 1 }, { id: 2 }],
    },
    attachTo: document.body,
    global: { stubs: STUBS },
  });
  await flushPromises();
  return wrapper;
}

function ctrlV(target = window) {
  const event = new KeyboardEvent("keydown", {
    key: "v",
    code: "KeyV",
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  target.dispatchEvent(event);
  return event;
}

const pasteButton = (wrapper) =>
  wrapper.findAll("button").find((b) => b.text().includes("Paste"));

describe("TbTagPanel - pasting copied tags", () => {
  it("shows Paste only while something is copied and pastes on all selected", async () => {
    const wrapper = await mountPanel();
    const store = useTagClipboardStore();
    expect(pasteButton(wrapper)).toBeUndefined();
    store.clipboardTags = ["rain", "hood"];
    await wrapper.vm.$nextTick();
    const paste = vi.spyOn(store, "pasteClipboard").mockResolvedValue(null);
    expect(pasteButton(wrapper).text()).toContain("Paste 2 tags");
    await pasteButton(wrapper).trigger("click");
    expect(paste).toHaveBeenCalledWith([1, 2]);
  });

  it("Ctrl+V pastes from anywhere in the open dialog but its text field", async () => {
    const wrapper = await mountPanel();
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain"];
    const paste = vi.spyOn(store, "pasteClipboard").mockResolvedValue(null);

    const inField = ctrlV(wrapper.find("input.tag-menu-input").element);
    expect(inField.defaultPrevented).toBe(false);
    expect(paste).not.toHaveBeenCalled();

    const elsewhere = ctrlV();
    expect(elsewhere.defaultPrevented).toBe(true);
    expect(paste).toHaveBeenCalledWith([1, 2]);
  });

  it("Ctrl+V does nothing while the dialog is closed", async () => {
    await mountPanel(false);
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain"];
    const paste = vi.spyOn(store, "pasteClipboard").mockResolvedValue(null);
    ctrlV();
    expect(paste).not.toHaveBeenCalled();
  });

  it("comma-separated text in the add field adds one tag per item", async () => {
    const wrapper = await mountPanel();
    await wrapper.find("input.tag-menu-input").setValue("rain, (hood:1.2)");
    await wrapper.find("input.tag-menu-input").trigger("keydown", { key: "Enter" });
    await flushPromises();
    // One gesture, one undo step: every request carries the same batch id.
    const [[, , { batchId }]] = tagsApi.addPictureTag.mock.calls;
    expect(batchId).toEqual(expect.any(String));
    expect(tagsApi.addPictureTag.mock.calls).toEqual([
      [1, "rain", { batchId }],
      [1, "hood", { batchId }],
      [2, "rain", { batchId }],
      [2, "hood", { batchId }],
    ]);
  });
});
