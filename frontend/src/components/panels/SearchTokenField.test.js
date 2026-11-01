// The toolbar search field: tokens are every active filter, typing lists
// every Filter menu entry by name, Enter searches the words, Tab adds the
// highlighted filter and Shift+Tab its opposite, and pasted comma text makes
// tokens.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
vi.mock("../../api/pictures", () => ({
  listComfyuiModels: vi.fn(),
  listComfyuiLoras: vi.fn(),
}));
vi.mock("../../api/workflows", () => ({ listWorkflowCards: vi.fn() }));
vi.mock("../../api/folders", () => ({
  listImportFolders: vi.fn().mockResolvedValue([]),
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
import { listComfyuiLoras, listComfyuiModels } from "../../api/pictures";
import { listWorkflowCards } from "../../api/workflows";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";

const VOCABULARY = [
  { tag: "looking at viewer", count: 1204 },
  { tag: "looking back", count: 88 },
  { tag: "rain", count: 310 },
  { tag: "umbrella", count: 150 },
  { tag: "blurry background", count: 77 },
  { tag: "puddle", count: 1 },
];

let wrapper;

let clock = 0;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.mocked(listTags).mockResolvedValue(VOCABULARY);
  vi.mocked(listComfyuiModels).mockResolvedValue([
    { value: "greyscaleXL.safetensors", name: null },
  ]);
  vi.mocked(listComfyuiLoras).mockResolvedValue([
    { value: "greyscale-film-v2.safetensors", name: null },
  ]);
  vi.mocked(listWorkflowCards).mockResolvedValue({ cards: [] });
  // Past the shared list's 30 s cache, so each test reads its own mocks.
  clock += 60_000;
  vi.spyOn(Date, "now").mockReturnValue(clock);
});

afterEach(() => {
  vi.restoreAllMocks();
  wrapper?.unmount();
});

async function mountField(props = {}) {
  wrapper = mount(SearchTokenField, {
    props,
    global: { stubs: { "v-icon": true } },
  });
  await wrapper.vm.focus();
  await flushPromises();
  return wrapper;
}

function optionTexts() {
  return wrapper.findAll('[role="option"]').map((o) => o.text());
}

function input() {
  return wrapper.find("input.stf-input");
}

async function type(text) {
  await input().setValue(text);
  await flushPromises();
}

async function press(key, mods = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    cancelable: true,
    ...mods,
  });
  input().element.dispatchEvent(event);
  await flushPromises();
  return event;
}

describe("SearchTokenField", () => {
  it("rests as a field, and opens with the caret in it", async () => {
    wrapper = mount(SearchTokenField, {
      global: { stubs: { "v-icon": true } },
    });
    expect(wrapper.find("input.stf-input").exists()).toBe(false);
    await wrapper.find("button.stf-rest").trigger("click");
    await flushPromises();
    expect(wrapper.find("input.stf-input").exists()).toBe(true);
    expect(wrapper.emitted("update:open")[0]).toEqual([true]);
  });

  it("lists the words search first, then matching tags with their counts", async () => {
    await mountField();
    await type("look");
    const text = optionTexts();
    expect(text[0]).toContain("Search descriptions and text for “look”");
    expect(text[1]).toContain("looking at viewer");
    expect(text[1]).toContain("1,204 pictures");
    expect(text[2]).toContain("looking back");
    const highlighted = wrapper.find('[aria-selected="true"]');
    expect(highlighted.text()).toContain("looking at viewer");
    // The row carries only the Tab key; the foot says what ⇧Tab adds.
    expect(highlighted.find(".stf-row-keys").text()).toBe("Tab");
    expect(input().attributes("aria-activedescendant")).toBe(
      "search-token-option-0",
    );
  });

  it("says what the count counts, one picture as one, and what ⇧Tab adds", async () => {
    await mountField();
    await type("pud");
    expect(optionTexts()[1]).toContain("1 picture");
    expect(optionTexts()[1]).not.toContain("1 pictures");
    expect(wrapper.find(".stf-foot").text()).toContain("⇧Tab without tag");
  });

  it("finds any filter by name, under the Filter menu's kinds", async () => {
    await mountField();
    await type("gre");
    const sections = wrapper.findAll(".stf-sec").map((s) => s.text());
    expect(sections).toEqual(["Checkpoint", "LoRA"]);
    expect(wrapper.text()).toContain("greyscale-film-v2");
    expect(wrapper.text()).toContain("Content › LoRA");

    await type("fa");
    const kinds = wrapper.findAll(".stf-sec").map((s) => s.text());
    expect(kinds).toEqual(["Faces", "Show", "Problems"]);
    expect(wrapper.text()).toContain("Has face");
    expect(wrapper.text()).toContain("Favourites only");
    expect(wrapper.text()).toContain("Face tags, no face");
  });

  it("Enter searches the words, whichever row is highlighted", async () => {
    await mountField();
    await type("look");
    await press("Enter");
    const search = useSearchStore();
    expect(search.searchQuery).toBe("look");
    expect(useFilterStore().tagFilter).toEqual([]);
    expect(wrapper.find("input.stf-input").exists()).toBe(false);
  });

  it("Tab on the highlighted tag adds a Has filter and clears the text", async () => {
    await mountField();
    await type("look");
    const event = await press("Tab");
    const filters = useFilterStore();
    expect(filters.tagFilter).toEqual(["looking at viewer"]);
    expect(filters.tagRejectedFilter).toEqual([]);
    expect(useSearchStore().searchInput).toBe("");
    expect(event.defaultPrevented).toBe(true);
    expect(wrapper.find(".stf-token").text()).toContain("looking at viewer");
  });

  it("Shift+Tab adds a Lacks filter", async () => {
    await mountField();
    const filters = useFilterStore();
    await type("look");
    await press("Tab", { shiftKey: true });
    expect(filters.tagRejectedFilter).toEqual(["looking at viewer"]);
    expect(filters.tagFilter).toEqual([]);
  });

  it("Tab adds any kind of filter; one with no opposite does the same on Shift+Tab", async () => {
    await mountField();
    const filters = useFilterStore();
    await type("has fa");
    await press("Tab", { shiftKey: true });
    expect(filters.faceBboxFilter).toBe("with_face");
    await type("greyscale-film");
    await press("Tab");
    expect(filters.comfyuiLoraFilter).toEqual(["greyscale-film-v2.safetensors"]);
    // Both show as tokens, as the strip shows them.
    const tokens = wrapper.findAll(".stf-token").map((t) => t.text());
    expect(tokens.join(" ")).toContain("has face");
    expect(tokens.join(" ")).toContain("greyscale-film-v2");
  });

  it("Tab with nothing to add moves focus as usual", async () => {
    await mountField();
    await type("zzqx");
    const event = await press("Tab");
    expect(event.defaultPrevented).toBe(false);
  });

  it("adding Has moves the same tag out of Lacks, and a Has tag is not listed again", async () => {
    await mountField();
    const filters = useFilterStore();
    filters.tagRejectedFilter = ["umbrella"];
    await type("umb");
    await press("Tab");
    expect(filters.tagFilter).toEqual(["umbrella"]);
    expect(filters.tagRejectedFilter).toEqual([]);
    await type("umb");
    expect(optionTexts().some((t) => t.includes("umbrella"))).toBe(false);
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

  it("Backspace in an empty field removes the last token, of any kind", async () => {
    await mountField();
    const filters = useFilterStore();
    filters.tagFilter = ["rain"];
    filters.comfyuiModelFilter = ["greyscaleXL.safetensors"];
    await flushPromises();
    await press("Backspace");
    expect(filters.comfyuiModelFilter).toEqual([]);
    expect(filters.tagFilter).toEqual(["rain"]);
  });

  it("clicking a tag token copies that tag", async () => {
    await mountField();
    const store = useTagClipboardStore();
    const copy = vi.spyOn(store, "copyTags").mockResolvedValue();
    useFilterStore().tagFilter = ["rain"];
    await flushPromises();
    await wrapper.find(".stf-token-body").trigger("mousedown");
    await wrapper.find(".stf-token-body").trigger("click");
    expect(copy).toHaveBeenCalledWith(["rain"]);
  });

  it("a group token shows its words and copies nothing (#2469)", async () => {
    await mountField();
    const store = useTagClipboardStore();
    const copy = vi.spyOn(store, "copyTags").mockResolvedValue();
    useFilterStore().tagFilter = ["__group:none"];
    await flushPromises();
    expect(wrapper.find(".stf-token-body").text()).toContain("any Ungrouped tag");
    await wrapper.find(".stf-token-body").trigger("mousedown");
    await wrapper.find(".stf-token-body").trigger("click");
    expect(copy).not.toHaveBeenCalled();
  });

  it("an empty field lists recent searches; a click runs one, Tab leaves", async () => {
    const search = useSearchStore();
    search.addToSearchHistory("red hair");
    await mountField();
    expect(wrapper.find(".stf-sec").text()).toBe("Recent");
    const tab = await press("Tab");
    expect(tab.defaultPrevented).toBe(false);
    expect(search.searchQuery).toBe("");
    await wrapper.find('[role="option"]').trigger("click");
    expect(search.searchQuery).toBe("red hair");
  });

  it("closes when focus leaves it, and Escape closes an empty field", async () => {
    await mountField();
    await input().trigger("focusout", { relatedTarget: document.body });
    expect(wrapper.find("input.stf-input").exists()).toBe(false);
    expect(wrapper.emitted("update:open").at(-1)).toEqual([false]);

    await wrapper.vm.focus();
    await press("Escape");
    expect(wrapper.find("input.stf-input").exists()).toBe(false);
  });

  // Opened from code (F), so a close it makes itself hands focus back to the
  // resting field rather than dropping it to the body.
  it.each([
    ["Escape", ""],
    ["Enter", "rain"],
  ])("%s returns focus to the resting field", async (key, text) => {
    wrapper = mount(SearchTokenField, {
      attachTo: document.body,
      global: { stubs: { "v-icon": true } },
    });
    await wrapper.vm.focus();
    await flushPromises();
    if (text) await type(text);
    await press(key);
    await flushPromises();
    expect(document.activeElement).toBe(
      wrapper.find("button.stf-rest").element,
    );
  });

  it("Escape with text hides the list first, then Tab moves focus", async () => {
    await mountField();
    await type("look");
    await press("Escape");
    expect(wrapper.find(".stf-drop").exists()).toBe(false);
    expect(wrapper.find("input.stf-input").exists()).toBe(true);
    const tab = await press("Tab");
    expect(tab.defaultPrevented).toBe(false);
    expect(useFilterStore().tagFilter).toEqual([]);
  });

  it("names its filters to a screen reader at rest, Lacks as lacks", async () => {
    const filters = useFilterStore();
    filters.tagFilter = ["rain"];
    filters.tagRejectedFilter = ["umbrella"];
    wrapper = mount(SearchTokenField, {
      global: { stubs: { "v-icon": true } },
    });
    const rest = wrapper.find("button.stf-rest");
    expect(rest.text()).toContain("rain · lacks umbrella");
    expect(rest.attributes("aria-label")).toBe("Search");
    const description = wrapper.find(
      `#${rest.attributes("aria-describedby")}`,
    );
    expect(description.text()).toBe("rain · lacks umbrella");
  });
});
