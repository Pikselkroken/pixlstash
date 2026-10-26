import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

const isReadOnly = ref(false);
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  get isReadOnly() {
    return isReadOnly;
  },
  newOperationBatchId: () => "cli-test",
  onSessionReset: () => () => {},
}));
vi.mock("../api/config", () => ({ patchUserConfig: vi.fn() }));
vi.mock("../api/tags", () => ({
  addPictureTag: vi.fn(),
  bulkFetchTags: vi.fn(),
}));

import { patchUserConfig } from "../api/config";
import { addPictureTag, bulkFetchTags } from "../api/tags";
import { useNoticeStore } from "./useNoticeStore";
import { splitTagText, useTagClipboardStore } from "./useTagClipboardStore";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  vi.clearAllMocks();
  patchUserConfig.mockResolvedValue({});
  addPictureTag.mockResolvedValue({});
});

function lastNotice() {
  const notices = useNoticeStore().notices;
  return notices[notices.length - 1]?.text;
}

describe("splitTagText", () => {
  it("makes one tag per item and drops prompt weights", () => {
    expect(splitTagText("rain, (umbrella:1.2),\nRain ,  , yellow raincoat")).toEqual(
      ["rain", "umbrella", "yellow raincoat"],
    );
  });
});

describe("copy", () => {
  it("remembers the tags locally and puts them on the system clipboard as text", async () => {
    const writeText = vi.fn().mockResolvedValue();
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const store = useTagClipboardStore();
    await store.copyTags(["rain", "umbrella", "Rain"]);
    expect(store.clipboardTags).toEqual(["rain", "umbrella"]);
    expect(writeText).toHaveBeenCalledWith("rain, umbrella");
    // A real tag keeps its brackets and commas; only free text is split.
    await store.copyTags(["pikachu (pokemon)", "red, white"]);
    expect(store.clipboardTags).toEqual(["pikachu (pokemon)", "red, white"]);
    expect(patchUserConfig).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("paste", () => {
  it("adds only the tags each picture is missing, as one batch", async () => {
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain", "umbrella"];
    bulkFetchTags.mockResolvedValue([
      { id: 1, tags: [{ id: 10, tag: "Rain" }] },
      { id: 2, tags: [] },
    ]);
    const result = await store.pasteClipboard([1, 2]);
    expect(addPictureTag.mock.calls).toEqual([
      [1, "umbrella", { batchId: "cli-test" }],
      [2, "rain", { batchId: "cli-test" }],
      [2, "umbrella", { batchId: "cli-test" }],
    ]);
    expect(result).toEqual({ pictureIds: [1, 2], failed: 0 });
    expect(lastNotice()).toBe("Added 2 tags to 2 pictures");
  });

  it("counts only the tags it added in the notice", async () => {
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain", "umbrella", "hood"];
    bulkFetchTags.mockResolvedValue([
      { id: 1, tags: [{ id: 10, tag: "rain" }, { id: 11, tag: "hood" }] },
    ]);
    await store.pasteClipboard([1]);
    expect(lastNotice()).toBe("Added 1 tag");
  });

  it("fetches tags in chunks the bulk endpoint accepts", async () => {
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain"];
    bulkFetchTags.mockResolvedValue([]);
    const ids = Array.from({ length: 450 }, (_, i) => i + 1);
    await store.pasteClipboard(ids);
    expect(bulkFetchTags.mock.calls.map(([chunk]) => chunk.length)).toEqual([
      200, 200, 50,
    ]);
  });

  it("counts a refused write without hiding the others", async () => {
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain"];
    bulkFetchTags.mockResolvedValue([]);
    addPictureTag
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(Object.assign(new Error("locked"), { status: 423 }));
    const result = await store.pasteClipboard([1, 2]);
    expect(result.failed).toBe(1);
    expect(lastNotice()).toContain("1 refused");
  });

  it("does nothing in a read-only session or with nothing copied", async () => {
    const store = useTagClipboardStore();
    expect(await store.pasteClipboard([1])).toBeNull();
    store.clipboardTags = ["rain"];
    isReadOnly.value = true;
    expect(await store.pasteClipboard([1])).toBeNull();
    expect(addPictureTag).not.toHaveBeenCalled();
  });

  it("never rejects when the tag read fails", async () => {
    const store = useTagClipboardStore();
    store.clipboardTags = ["rain"];
    bulkFetchTags.mockRejectedValue(new Error("offline"));
    await expect(store.pasteClipboard([1])).resolves.toBeNull();
    expect(lastNotice()).toBe("Couldn't paste the tags.");
  });
});

describe("chip size", () => {
  it("is hydrated, saved and written to the root variable", async () => {
    const store = useTagClipboardStore();
    store.hydrate({ tag_chip_size: "compact" });
    expect(store.chipSize).toBe("compact");
    await store.setChipSize("large");
    expect(patchUserConfig).toHaveBeenCalledWith({ tag_chip_size: "large" });
    await Promise.resolve();
    expect(
      document.documentElement.style.getPropertyValue("--tag-chip-font"),
    ).toBe("var(--text-sm)");
  });

  it("falls back to comfortable on an unknown value", () => {
    const store = useTagClipboardStore();
    store.hydrate({ tag_chip_size: "huge" });
    expect(store.chipSize).toBe("comfortable");
  });
});
