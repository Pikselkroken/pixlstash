// The grid's rail as the selection's inspector (#2161): one head row that
// names the view, the one picture or the count; the tile's own rating strip;
// and sections whose collapsed state outlives the selection.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

const getPictureStats = vi.fn();
const getPictureMetadata = vi.fn();
const listPictureFaces = vi.fn();
vi.mock("../../api/pictures", () => ({
  getPictureStats: (...args) => getPictureStats(...args),
  getPictureMetadata: (...args) => getPictureMetadata(...args),
  listPictureFaces: (...args) => listPictureFaces(...args),
  listPicturesByIds: vi.fn(async () => []),
  pictureThumbnailUrl: (id) => `/thumbs/${id}.webp`,
}));
const bulkFetchTags = vi.fn(async () => []);
vi.mock("../../api/tags", () => ({
  bulkFetchTags: (...args) => bulkFetchTags(...args),
  listTagPredictions: vi.fn(async () => ({ tag_predictions: [], meta: {} })),
  addPictureTag: vi.fn(),
  removePictureTag: vi.fn(),
  confirmTagPrediction: vi.fn(),
  rejectTagPrediction: vi.fn(),
}));

import StatsSidebar from "./StatsSidebar.vue";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { SCRAPHEAP_PICTURES_ID } from "../../stores/useViewStore";
import { useSelectionTags } from "../../composables/useSelectionTags";
import {
  addPictureTag,
  confirmTagPrediction,
  listTagPredictions,
} from "../../api/tags";

enableAutoUnmount(afterEach);

// The lightbox's panels and the face picker are their own suites' business;
// here they only have to be there.
const STUBS = {
  "v-icon": true,
  "v-progress-circular": true,
  Tooltip: { props: ["text"], template: '<span class="tip" :data-text="text" />' },
  StatsHistogram: true,
  OverlayDescriptionPanel: true,
  OverlayTagsPanel: true,
  AddToEntityControl: true,
  TasksPanel: true,
};

const CARDS = {
  7: { id: 7, score: 3, favourite: false, width: 800, height: 600 },
  8: { id: 8, score: 3, favourite: true, width: 800, height: 600 },
  9: { id: 9, score: 1, favourite: false, width: 800, height: 600 },
};

const DETAILS = {
  7: {
    id: 7,
    description: "A woman with red hair walking in the rain, neon behind her.",
    original_file_name: "IMG_2291.jpg",
    created_at: "2026-10-12T21:14:00Z",
    tags: [],
  },
  8: {
    id: 8,
    description: null,
    original_file_name: "a8f3c1e9.png",
    created_at: "2026-10-12T21:14:00Z",
    tags: [],
  },
};

async function flush() {
  for (let i = 0; i < 4; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

function mountRail(props = {}, options = {}) {
  return mount(StatsSidebar, {
    props: {
      viewName: "All Pictures",
      pictures: [],
      ...props,
    },
    global: { stubs: STUBS },
    ...options,
  });
}

/** Select these pictures as the grid would, with their cards handed in. */
async function select(wrapper, ids) {
  useSelectionStore().selectedImageIds = ids;
  await wrapper.setProps({ pictures: ids.map((id) => CARDS[id]) });
  await flush();
}

/** Select several, then wait out RailPictures' 250 ms settle and the read. */
async function selectAndRead(wrapper, ids) {
  await select(wrapper, ids);
  await new Promise((resolve) => setTimeout(resolve, 260));
  await flush();
}

const title = (wrapper) => wrapper.find('[data-testid="rail-title"]').text();

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  useSidebarStore().statsOpen = true;
  getPictureStats.mockReset().mockResolvedValue({
    total: 111,
    tagged: 109,
    untagged: 2,
    total_tags: 1987,
    avg_tags_per_image: 17.9,
    top_tags: [],
  });
  getPictureMetadata
    .mockReset()
    .mockImplementation(async (id) => DETAILS[id] ?? { id, tags: [] });
  listPictureFaces.mockReset().mockResolvedValue([]);
});

describe("the rail's head row", () => {
  it("names the view and shows its stats while nothing is selected", async () => {
    const wrapper = mountRail();
    await flush();
    expect(title(wrapper)).toBe("All Pictures");
    expect(wrapper.find(".rail-clear").exists()).toBe(false);
    const tiles = wrapper.findAll(".stats-tile").map((t) => t.text());
    expect(tiles).toEqual(["111Pictures", "1,987Tags", "17.9Tags / picture"]);
    expect(
      wrapper.findAll("button.inspector-tab").map((t) => t.text()),
    ).toEqual(["Tags", "Pictures", "Tasks"]);
  });

  it("names one picture by its description, never by its file name", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    expect(title(wrapper)).toContain("A woman with red hair walking in the rain");
    expect(title(wrapper)).not.toContain("IMG_2291");
    // The first clause is the title; the whole description is the tip.
    expect(title(wrapper)).not.toContain("neon");
    expect(
      wrapper.find('[data-testid="rail-title"] .tip').attributes("data-text"),
    ).toBe(DETAILS[7].description);
    // The file name is a property instead.
    expect(wrapper.find(".rail-picture__props").text()).toContain(
      "IMG_2291.jpg",
    );
    expect(wrapper.findAll("button.inspector-tab")).toHaveLength(0);
  });

  it("falls back to who and when, not the file name, without a description", async () => {
    listPictureFaces.mockResolvedValue([
      { id: 1, frame_index: 0, bbox: [0, 0, 10, 10], character_id: 4 },
    ]);
    useEntityNamesStore().mergeCharacterNames([{ id: 4, name: "Aria" }]);
    const wrapper = mountRail();
    await select(wrapper, [8]);
    expect(title(wrapper)).toMatch(/^Aria · \S/);
    expect(title(wrapper)).not.toContain("a8f3c1e9");
  });

  it("shows no file name while the picture's details are still being read", async () => {
    let answer;
    getPictureMetadata.mockImplementation(
      () => new Promise((resolve) => (answer = resolve)),
    );
    const wrapper = mountRail();
    await select(wrapper, [8]);
    expect(title(wrapper)).toBe("");
    answer(DETAILS[8]);
    await flush();
    expect(title(wrapper)).not.toBe("");
    expect(title(wrapper)).not.toContain("a8f3c1e9");
  });

  it("counts several and clears them", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7, 8, 9]);
    expect(title(wrapper)).toBe("3 pictures");
    await wrapper.find(".rail-clear").trigger("click");
    expect(useSelectionStore().selectedImageIds).toEqual([]);
    await flush();
    expect(title(wrapper)).toBe("All Pictures");
  });
});

describe("the rail's rating strip", () => {
  it("rates and favourites through the grid, and shows what the tile shows", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    const stars = () => wrapper.findAll(".rail-picture__strip .star-slot v-icon-stub");

    await stars()[3].trigger("click");
    expect(wrapper.emitted("set-score")).toEqual([[CARDS[7], 4]]);

    await wrapper.find(".star-overlay__heart").trigger("click");
    expect(wrapper.emitted("toggle-favourite")).toEqual([[[7]]]);

    // The grid answers by replacing the card; the strip follows the new one,
    // as the tile does (the H key lands here the same way).
    const after = { ...CARDS[7], favourite: true };
    await wrapper.setProps({ pictures: [after], scoreOf: () => 4 });
    expect(
      wrapper.find(".star-overlay__heart").attributes("aria-pressed"),
    ).toBe("true");
    const lit = stars().filter((s) =>
      String(s.attributes("color")).includes("accent"),
    );
    expect(lit).toHaveLength(4);
  });

  it("draws no heart in the Scrapheap, as the tile does not", async () => {
    const wrapper = mountRail();
    useSelectionStore().selectedCharacter = String(SCRAPHEAP_PICTURES_ID);
    await select(wrapper, [7]);
    expect(wrapper.find(".star-overlay__heart").exists()).toBe(false);
  });
});

describe("the rail's sections", () => {
  it("stay collapsed across a change of selection", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    const propsToggle = () =>
      wrapper
        .findAll(".section-head__chevron")
        .find((b) => b.attributes("aria-label")?.endsWith("Properties"));
    await propsToggle().trigger("click");
    expect(wrapper.find(".rail-picture__props").exists()).toBe(false);

    // Another picture, then several, then one again: each remounts the body.
    await select(wrapper, [8]);
    expect(wrapper.find(".rail-picture__props").exists()).toBe(false);
    await select(wrapper, [7, 8]);
    await select(wrapper, [7]);
    expect(wrapper.find(".rail-picture__props").exists()).toBe(false);
    expect(propsToggle().attributes("aria-expanded")).toBe("false");

    await propsToggle().trigger("click");
    expect(wrapper.find(".rail-picture__props").exists()).toBe(true);
  });
});

describe("keys for chips selected in the rail", () => {
  // The grid listens on window in the bubble phase; this stands in for it.
  function mountWithChips(hasSelection) {
    const panel = {
      removeSelection: vi.fn(),
      clearSelection: vi.fn(),
      copySelection: vi.fn(),
    };
    const TagsStub = {
      setup(_, { expose }) {
        expose({ hasSelection, ...panel });
        return () => null;
      },
    };
    const wrapper = mount(StatsSidebar, {
      props: { viewName: "All Pictures", pictures: [] },
      global: { stubs: { ...STUBS, OverlayTagsPanel: TagsStub } },
    });
    return { wrapper, panel };
  }

  function press(key, init = {}) {
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key, bubbles: true, ...init }),
    );
  }

  let grid;
  beforeEach(() => {
    grid = vi.fn();
    window.addEventListener("keydown", grid);
  });
  afterEach(() => window.removeEventListener("keydown", grid));

  it("Delete removes the selected tags and never reaches the grid", async () => {
    const { wrapper, panel } = mountWithChips(true);
    await select(wrapper, [7]);
    press("Delete");
    expect(panel.removeSelection).toHaveBeenCalledOnce();
    expect(grid).not.toHaveBeenCalled();
    press("Escape");
    expect(panel.clearSelection).toHaveBeenCalledOnce();
    press("c", { ctrlKey: true });
    expect(panel.copySelection).toHaveBeenCalledOnce();
    expect(grid).not.toHaveBeenCalled();
  });

  it("still takes them while a tooltip is showing", async () => {
    const { wrapper, panel } = mountWithChips(true);
    await select(wrapper, [7]);
    const tip = document.createElement("div");
    tip.className = "v-overlay v-tooltip v-overlay--active";
    document.body.appendChild(tip);
    try {
      press("Delete");
      expect(panel.removeSelection).toHaveBeenCalledOnce();
      expect(grid).not.toHaveBeenCalled();
    } finally {
      tip.remove();
    }
  });

  it("leaves the keys to the grid while no chip is selected", async () => {
    const { wrapper, panel } = mountWithChips(false);
    await select(wrapper, [7]);
    press("Delete");
    expect(panel.removeSelection).not.toHaveBeenCalled();
    expect(grid).toHaveBeenCalledOnce();
  });
});

describe("under the lightbox", () => {
  it("drops its copies of the lightbox's panels until the lightbox closes", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    expect(wrapper.find(".rail-picture").exists()).toBe(true);
    await wrapper.setProps({ covered: true });
    expect(wrapper.find(".rail-picture").exists()).toBe(false);
    await select(wrapper, [7, 8]);
    expect(wrapper.find(".rail-pictures").exists()).toBe(false);
    await wrapper.setProps({ covered: false });
    expect(wrapper.find(".rail-pictures").exists()).toBe(true);
  });
});

describe("the Tasks deep link over a selection", () => {
  it("shows the Tasks tab until the selection next changes", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    expect(wrapper.find(".rail-picture").exists()).toBe(true);

    useSidebarStore().showTasksTab();
    await flush();
    expect(wrapper.find(".rail-picture").exists()).toBe(false);
    expect(wrapper.find("button.inspector-tab--active").text()).toBe("Tasks");
    // The picture is still selected in the grid, so the rail can still clear it.
    expect(wrapper.find(".rail-clear").exists()).toBe(true);

    await select(wrapper, [8]);
    expect(wrapper.find(".rail-picture").exists()).toBe(true);
  });
});

describe("the selection's tag reads", () => {
  it("drop an answer for a selection that has since changed", async () => {
    const answers = [];
    bulkFetchTags.mockImplementation(
      () => new Promise((resolve) => answers.push(resolve)),
    );
    let ids = [1, 2];
    const tags = useSelectionTags(() => ids);
    const first = tags.fetchSelectedImageTags();
    ids = [3, 4];
    const second = tags.fetchSelectedImageTags();
    answers[1]([{ id: 3, tags: [{ id: 9, tag: "new" }] }, { id: 4, tags: [] }]);
    await second;
    answers[0]([{ id: 1, tags: [{ id: 8, tag: "old" }] }, { id: 2, tags: [] }]);
    await first;
    expect(tags.fetchedTagData.value.map((row) => row.id)).toEqual([3, 4]);
    bulkFetchTags.mockReset().mockResolvedValue([]);
  });

  it("do not stay loading when the selection empties mid-read", async () => {
    bulkFetchTags.mockImplementation(() => new Promise(() => {}));
    let ids = [1, 2];
    const tags = useSelectionTags(() => ids);
    void tags.fetchSelectedImageTags();
    expect(tags.tagDataLoading.value).toBe(true);
    ids = [];
    await tags.fetchSelectedImageTags();
    expect(tags.tagDataLoading.value).toBe(false);
    bulkFetchTags.mockReset().mockResolvedValue([]);
  });

  it("read as loading, not as the new selection's tags, until it is read", async () => {
    bulkFetchTags.mockResolvedValue([
      { id: 1, tags: [{ id: 9, tag: "old" }] },
      { id: 2, tags: [] },
    ]);
    const ids = ref([1, 2]);
    const tags = useSelectionTags(() => ids.value);
    await tags.fetchSelectedImageTags();
    expect(tags.tagDataLoading.value).toBe(false);
    // Before any read goes out (the rail waits for the selection to settle).
    ids.value = [3, 4];
    expect(tags.tagDataLoading.value).toBe(true);
    bulkFetchTags.mockImplementation(() => new Promise(() => {}));
    void tags.fetchSelectedImageTags();
    expect(tags.fetchedTagData.value).toEqual([]);
    bulkFetchTags.mockReset().mockResolvedValue([]);
  });

  it("report a partial failure and still refresh for what changed", async () => {
    bulkFetchTags.mockResolvedValue([
      { id: 1, tags: [] },
      { id: 2, tags: [] },
    ]);
    confirmTagPrediction
      .mockReset()
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error("refused"));
    const applied = vi.fn();
    const tags = useSelectionTags(() => [1, 2], { onApplied: applied });
    bulkFetchTags.mockClear();
    await tags.confirmPredictionOnAll({ tag: "rain", ids: [1, 2] });
    expect(applied).toHaveBeenCalledWith({
      tag: "rain",
      pictureIds: [1],
      action: "add",
    });
    expect(bulkFetchTags).toHaveBeenCalled();
    expect(tags.tagError.value).toMatch(/^1 of 2 pictures were not changed/);
    bulkFetchTags.mockReset().mockResolvedValue([]);
  });

  it("add a tag to the rest past the read cap too", async () => {
    const ids = Array.from({ length: 105 }, (_, i) => i + 1);
    bulkFetchTags.mockResolvedValue(
      ids.slice(0, 100).map((id) => ({
        id,
        tags: id === 1 ? [{ id: 900, tag: "rain" }] : [],
      })),
    );
    const tags = useSelectionTags(() => ids);
    await tags.fetchSelectedImageTags();
    addPictureTag.mockClear();
    await tags.addTagToRemaining(tags.tagsOnSome.value[0]);
    const tagged = addPictureTag.mock.calls.map(([id]) => id);
    expect(tagged).toHaveLength(104);
    expect(tagged).toContain(105);
    expect(tagged).not.toContain(1);
    bulkFetchTags.mockReset().mockResolvedValue([]);
  });

  it("add as one undo step: every request shares one batch id", async () => {
    bulkFetchTags.mockResolvedValue([
      { id: 1, tags: [{ id: 900, tag: "rain" }] },
      { id: 2, tags: [] },
      { id: 3, tags: [] },
    ]);
    const tags = useSelectionTags(() => [1, 2, 3]);
    await tags.fetchSelectedImageTags();
    const batchesOf = () =>
      new Set(addPictureTag.mock.calls.map(([, , opts]) => opts?.batchId));
    addPictureTag.mockClear();
    await tags.addTagToRemaining(tags.tagsOnSome.value[0]);
    expect(addPictureTag).toHaveBeenCalledTimes(2);
    expect([...batchesOf()]).toEqual([expect.any(String)]);
    addPictureTag.mockClear();
    await tags.addTagsToAll(["sun", "sea"]);
    expect(addPictureTag).toHaveBeenCalledTimes(6);
    expect([...batchesOf()]).toEqual([expect.any(String)]);
    bulkFetchTags.mockReset().mockResolvedValue([]);
  });
});

describe("the face crop", () => {
  it("fills its square from a wide face box", async () => {
    listPictureFaces.mockResolvedValue([
      // 200 wide, 100 tall, in an 800 x 600 picture.
      { id: 1, frame_index: 0, bbox: [100, 100, 300, 200], character_id: null },
    ]);
    const wrapper = mountRail();
    await select(wrapper, [7]);
    const style = wrapper.find(".rail-faces__crop").attributes("style");
    // Scaled by the shorter side (32 / 100): the picture is 256 x 192 px and
    // the face's 64 px width is centred in the 32 px box.
    expect(style).toContain("background-size: 256px 192px");
    expect(style).toContain("background-position: -48px -32px");
  });
});

describe("adding a tag to several", () => {
  it("sends it once for a double Enter, as an add", async () => {
    const wrapper = mountRail();
    await selectAndRead(wrapper, [7, 8, 9]);
    await wrapper.find('[aria-label="Add a tag to all 3"]').trigger("click");
    const input = wrapper.find(".rail-pictures__input");
    await input.setValue("rain");
    addPictureTag.mockClear();
    input.trigger("keydown", { key: "Enter" });
    input.trigger("keydown", { key: "Enter" });
    await flush();
    expect(addPictureTag).toHaveBeenCalledTimes(3);
    expect(wrapper.emitted("tags-applied")).toEqual([
      [{ tag: "rain", pictureIds: [7, 8, 9], action: "add" }],
    ]);
  });

  it("re-reads the rejected tags too: an add confirms a rejected one", async () => {
    const wrapper = mountRail();
    await selectAndRead(wrapper, [7, 8, 9]);
    await wrapper.find('[aria-label="Add a tag to all 3"]').trigger("click");
    const input = wrapper.find(".rail-pictures__input");
    await input.setValue("rain");
    listTagPredictions.mockClear();
    await input.trigger("keydown", { key: "Enter" });
    await flush();
    expect(listTagPredictions.mock.calls.map(([id]) => id)).toEqual([7, 8, 9]);
  });

  it("opens a collapsed Tags section and focuses the field", async () => {
    const wrapper = mountRail({}, { attachTo: document.body });
    await selectAndRead(wrapper, [7, 8, 9]);
    await wrapper.find('[aria-label="Collapse Tags"]').trigger("click");
    expect(wrapper.find(".rail-pictures__zone").exists()).toBe(false);
    await wrapper.find('[aria-label="Add a tag to all 3"]').trigger("click");
    await flush();
    const input = wrapper.find(".rail-pictures__input");
    expect(input.exists()).toBe(true);
    expect(document.activeElement).toBe(input.element);
  });
});
