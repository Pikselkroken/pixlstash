// The toolbar search field: tokens are the Has / Lacks tag filters, typing
// lists tags and a words search, and pasted comma text makes tokens.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  isReadOnly: ref(false),
  onSessionReset: () => () => {},
  newOperationBatchId: () => "cli-test",
}));
vi.mock("../../api/config", () => ({ patchUserConfig: vi.fn() }));
vi.mock("../../api/tags", () => ({
  addPictureTag: vi.fn(),
  bulkFetchTags: vi.fn(),
  listTags: vi.fn(),
  removePictureTag: vi.fn(),
}));
vi.mock("../../api/pictureSets", () => ({
  addPictureToSet: vi.fn(),
  getPictureSetMembership: vi.fn(),
  removePictureFromSet: vi.fn(),
}));
vi.mock("../../api/characters", () => ({
  addCharacterFaces: vi.fn(),
  getCharacterMembership: vi.fn(),
  removeCharacterFaces: vi.fn(),
}));
vi.mock("../../stores/useEntityListsStore", () => ({
  useEntityListsStore: () => ({ pictureSets: [], characters: [], invalidate: vi.fn() }),
}));

import SearchTokenField from "./SearchTokenField.vue";
import { listTags } from "../../api/tags";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";

const VOCABULARY = [
  { tag: "looking at viewer", count: 1204 },
  { tag: "looking back", count: 88 },
  { tag: "rain", count: 310 },
  { tag: "umbrella", count: 150 },
  { tag: "blurry background", count: 77 },
];

let wrapper;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.mocked(listTags).mockResolvedValue(VOCABULARY);
});

async function mountField() {
  wrapper = mount(SearchTokenField, {
    global: { stubs: { "v-icon": true } },
  });
  await flushPromises();
  return wrapper;
}

function input() {
  return wrapper.find("input.stf-input");
}

async function type(text) {
  await input().setValue(text);
  await flushPromises();
}

async function press(key, mods = {}) {
  await input().trigger("keydown", { key, ...mods });
  await flushPromises();
}

describe("SearchTokenField", () => {
  it("typing lists matching tags with their counts, first row highlighted", async () => {
    await mountField();
    await type("look");
    const options = wrapper.findAll('[role="option"]');
    const text = options.map((o) => o.text());
    expect(text[0]).toContain("looking at viewer");
    expect(text[0]).toContain("1204");
    expect(text[1]).toContain("looking back");
    expect(options[0].attributes("aria-selected")).toBe("true");
    expect(input().attributes("aria-activedescendant")).toBe(
      "search-token-option-0",
    );
    expect(text.at(-1)).toContain("Search descriptions and text for “look”");
  });

  it("Enter on the highlighted tag adds a Has filter and clears the text", async () => {
    await mountField();
    await type("look");
    await press("Enter");
    const filters = useFilterStore();
    expect(filters.tagFilter).toEqual(["looking at viewer"]);
    expect(filters.tagRejectedFilter).toEqual([]);
    expect(useSearchStore().searchInput).toBe("");
    expect(wrapper.text()).toContain("looking at viewer");
  });

  it("Shift+Enter adds a Lacks filter", async () => {
    await mountField();
    const filters = useFilterStore();
    await type("look");
    await press("Enter", { shiftKey: true });
    expect(filters.tagRejectedFilter).toEqual(["looking at viewer"]);
    expect(filters.tagFilter).toEqual([]);
  });

  it("adding Has moves the same tag out of Lacks, and a Has tag is not listed again", async () => {
    await mountField();
    const filters = useFilterStore();
    filters.tagRejectedFilter = ["umbrella"];
    await type("umb");
    await press("Enter");
    expect(filters.tagFilter).toEqual(["umbrella"]);
    expect(filters.tagRejectedFilter).toEqual([]);
    await type("umb");
    const again = wrapper.findAll('[role="option"]').map((o) => o.text());
    expect(again.some((t) => t.includes("umbrella"))).toBe(false);
  });

  it("Ctrl+Enter runs a text search and adds no token", async () => {
    await mountField();
    await type("look");
    await press("Enter", { ctrlKey: true });
    const search = useSearchStore();
    expect(search.searchQuery).toBe("look");
    expect(useFilterStore().tagFilter).toEqual([]);
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("pasting comma text makes one Has token per item", async () => {
    await mountField();
    const event = new Event("paste", { cancelable: true });
    Object.defineProperty(event, "clipboardData", {
      value: { getData: () => "rain, umbrella" },
    });
    input().element.dispatchEvent(event);
    await flushPromises();
    expect(event.defaultPrevented).toBe(true);
    expect(useFilterStore().tagFilter).toEqual(["rain", "umbrella"]);
  });

  it("Backspace in an empty field removes the last token", async () => {
    await mountField();
    const filters = useFilterStore();
    filters.tagFilter = ["rain"];
    filters.tagRejectedFilter = ["blurry background"];
    await press("Backspace");
    expect(filters.tagRejectedFilter).toEqual([]);
    expect(filters.tagFilter).toEqual(["rain"]);
  });

  it("clicking a token copies that tag", async () => {
    await mountField();
    const store = useTagClipboardStore();
    const copy = vi.spyOn(store, "copyTags").mockResolvedValue();
    useFilterStore().tagFilter = ["rain"];
    await flushPromises();
    await wrapper.find(".stf-token-body").trigger("mousedown");
    await wrapper.find(".stf-token-body").trigger("click");
    expect(copy).toHaveBeenCalledWith(["rain"]);
  });
});
