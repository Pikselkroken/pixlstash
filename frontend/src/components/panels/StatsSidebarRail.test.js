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
const openPictureLocation = vi.fn();
vi.mock("../../api/pictures", () => ({
  getPictureStats: (...args) => getPictureStats(...args),
  getPictureMetadata: (...args) => getPictureMetadata(...args),
  listPictureFaces: (...args) => listPictureFaces(...args),
  openPictureLocation: (...args) => openPictureLocation(...args),
  pictureThumbnailUrl: (id) => `/thumbs/${id}.webp`,
  resetPicturesTags: vi.fn(async () => ({})),
}));
vi.mock("../../api/config", () => ({ getUserConfig: vi.fn(async () => ({})) }));
vi.mock("../../api/users", () => ({ getPenalisedTags: vi.fn(async () => ({})) }));
vi.mock("../../api/taggers", () => ({ listTaggers: vi.fn(async () => ({})) }));
const bulkFetchTags = vi.fn(async () => []);
vi.mock("../../api/tags", () => ({
  bulkFetchTags: (...args) => bulkFetchTags(...args),
  listTags: vi.fn(async () => []),
  listTagPredictions: vi.fn(async () => ({ tag_predictions: [], meta: {} })),
  addPictureTag: vi.fn(),
  removePictureTag: vi.fn(),
  confirmTagPrediction: vi.fn(),
  rejectTagPrediction: vi.fn(),
}));

import StatsSidebar from "./StatsSidebar.vue";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useFilterStore } from "../../stores/useFilterStore";
import { useTagGroupsStore } from "../../stores/useTagGroupsStore";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { SCRAPHEAP_PICTURES_ID } from "../../stores/useViewStore";
import { useSelectionTags } from "../../composables/useSelectionTags";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { sessionContext } from "../../utils/apiClient";
import { fitWords, pictureTitle } from "../../composables/useRailPicture";
import {
  addPictureTag,
  confirmTagPrediction,
  listTagPredictions,
  rejectTagPrediction,
} from "../../api/tags";

enableAutoUnmount(afterEach);

// The lightbox's panels and the face picker are their own suites' business;
// here they only have to be there.
const STUBS = {
  "v-icon": true,
  "v-progress-circular": true,
  Tooltip: { props: ["text"], template: '<span class="tip" :data-text="text" />' },
  StatsHistogram: true,
  LightboxDescriptionPanel: true,
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
    file_path: "2026/10/a1.jpg",
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

/** Select several, then wait out RailTags' 250 ms settle and the read. */
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
    ).toEqual(["Tags", "Pictures"]);
  });

  it("names one picture by its description, never by its file name", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    // The first sentence, without its article; the whole is the tip.
    expect(title(wrapper)).toBe(
      "Woman with red hair walking in the rain, neon behind her",
    );
    expect(
      wrapper.find('[data-testid="rail-title"] .tip').attributes("data-text"),
    ).toBe(DETAILS[7].description);
    // The file name is a property instead, folded until asked for.
    await wrapper.find('[aria-label="Expand Properties"]').trigger("click");
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

  it("ends in a close button, with or without a selection", async () => {
    const wrapper = mountRail();
    await flush();
    let buttons = wrapper.findAll(".inspector-head button");
    expect(buttons.map((b) => b.classes("rail-close"))).toEqual([true]);
    await select(wrapper, [7, 8, 9]);
    buttons = wrapper.findAll(".inspector-head button");
    expect(buttons.at(-1).classes()).toContain("rail-close");
    expect(buttons.at(-2).classes()).toContain("rail-clear");
  });

  it("closes the rail, keeps the selection and focuses the grid's toggle", async () => {
    // The lightbox's toggle comes first, so a first-match query would win it.
    document.body.innerHTML = `
      <span class="lightbox-topbar-sidebar-toggle">
        <button class="tb-stats-btn" id="lightbox-toggle"></button>
      </span>
      <button class="tb-stats-btn" id="grid-toggle"></button>
      <div id="rail"></div>`;
    const wrapper = mountRail({}, { attachTo: "#rail" });
    await select(wrapper, [7, 8, 9]);
    await wrapper.find(".rail-close").trigger("click");
    await flush();
    expect(useSidebarStore().statsOpen).toBe(false);
    expect(useSelectionStore().selectedImageIds).toEqual([7, 8, 9]);
    expect(document.activeElement?.id).toBe("grid-toggle");
    wrapper.unmount();
    document.body.innerHTML = "";
  });
});

describe("the head's title and the description", () => {
  const selectText = vi.fn();
  const DescriptionStub = {
    props: ["highlight"],
    template: '<div class="desc-stub" :data-highlight="highlight" />',
    methods: { selectText },
  };
  const mountWithDescription = () =>
    mountRail({}, { global: { stubs: { ...STUBS, LightboxDescriptionPanel: DescriptionStub } } });
  const highlight = (wrapper) =>
    wrapper.find(".desc-stub").attributes("data-highlight");

  it("pointing at the title marks its words, and a click selects them", async () => {
    selectText.mockReset();
    const wrapper = mountWithDescription();
    await select(wrapper, [7]);
    const quote = wrapper.find(".rail-title__quote");
    expect(highlight(wrapper)).toBe("");
    await quote.trigger("mouseenter");
    expect(highlight(wrapper)).toBe(
      "Woman with red hair walking in the rain, neon behind her",
    );
    await quote.trigger("mouseleave");
    expect(highlight(wrapper)).toBe("");
    await quote.trigger("click");
    expect(selectText).toHaveBeenCalledWith(
      "Woman with red hair walking in the rain, neon behind her",
    );
  });

  it("a title that is not the description's words is plain text", async () => {
    const wrapper = mountWithDescription();
    await select(wrapper, [8]);
    expect(wrapper.find(".rail-title__quote").exists()).toBe(false);
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
  it("stay open across a change of selection once opened", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    const propsToggle = () =>
      wrapper
        .findAll(".section-head__chevron")
        .find((b) => b.attributes("aria-label")?.endsWith("Properties"));
    // Collapsed state is the session's, so an earlier test may have opened it.
    if (propsToggle().attributes("aria-expanded") === "false") {
      await propsToggle().trigger("click");
    }
    expect(wrapper.find(".rail-picture__props").exists()).toBe(true);

    // Another picture, then several, then one again: each remounts the body.
    await select(wrapper, [8]);
    expect(wrapper.find(".rail-picture__props").exists()).toBe(true);
    await select(wrapper, [7, 8]);
    await select(wrapper, [7]);
    expect(propsToggle().attributes("aria-expanded")).toBe("true");

    await propsToggle().trigger("click");
    expect(wrapper.find(".rail-picture__props").exists()).toBe(false);
  });
});

describe("the File property's Show in folder", () => {
  afterEach(() => {
    sessionContext.value = null;
  });

  // Properties folds by default and keeps its state across tests: open it.
  async function openProperties(wrapper) {
    const toggle = wrapper.find('[aria-label="Expand Properties"]');
    if (toggle.exists()) await toggle.trigger("click");
  }

  it("opens the selected picture's folder", async () => {
    openPictureLocation.mockReset().mockResolvedValue({ status: "ok" });
    const wrapper = mountRail();
    await select(wrapper, [7]);
    await openProperties(wrapper);
    const button = wrapper.find('.rail-picture__file button[aria-label="Show in folder"]');
    expect(button.find(".tip").attributes("data-text")).toBe("Show in folder");
    await button.trigger("click");
    expect(openPictureLocation).toHaveBeenCalledWith(7);
  });

  it("is not offered in a read-only session", async () => {
    sessionContext.value = { is_owner: false, scope: "READ" };
    const wrapper = mountRail();
    await select(wrapper, [7]);
    await openProperties(wrapper);
    expect(wrapper.find(".rail-picture__props").text()).toContain(
      "IMG_2291.jpg",
    );
    expect(wrapper.find('[aria-label="Show in folder"]').exists()).toBe(false);
  });
});

describe("several pictures' ratings", () => {
  it("count each rating among the loaded cards", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7, 8, 9]);
    expect(wrapper.find(".rail-hist").attributes("aria-label")).toBe(
      "unrated: 0, ★1: 1, ★2: 0, ★3: 2, ★4: 0, ★5: 0",
    );
    // The tallest column is the most common rating.
    const bars = wrapper.findAll(".rail-hist__bar");
    expect(bars[3].attributes("style")).toContain("height: 28px");
    expect(bars[1].attributes("style")).toContain("height: 14px");
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

// The task list is the title bar's pill: a notice's *Show* opens that, and
// neither opens this rail nor takes it off the selection.
describe("the Tasks deep link", () => {
  it("leaves a shut rail shut", async () => {
    const sidebar = useSidebarStore();
    sidebar.statsOpen = false;
    mountRail();
    sidebar.showTasksTab();
    await flush();
    expect(sidebar.statsOpen).toBe(false);
    expect(sidebar.tasksPillRequest).toBe(1);
  });

  it("leaves the selection on screen", async () => {
    const wrapper = mountRail();
    await select(wrapper, [7]);
    useSidebarStore().showTasksTab();
    await flush();
    expect(wrapper.find(".rail-picture").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("No active tasks");
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

  it("count a picture that took one tag of several as changed", async () => {
    addPictureTag.mockReset().mockImplementation(async (id, tag) => {
      if (id === 2 && tag === "sea") throw new Error("refused");
      return {};
    });
    const applied = vi.fn();
    const tags = useSelectionTags(() => [1, 2, 3], { onApplied: applied });
    expect(await tags.addTagsToAll(["sun", "sea"])).toBe(false);
    expect(applied).toHaveBeenCalledWith({
      tag: "sun, sea",
      pictureIds: [1, 2, 3],
      action: "add",
    });
    expect(tags.tagError.value).toMatch(/^1 of 6 tags were not added/);
    addPictureTag.mockReset();
  });

  it("skip a locked set's picture in a transient warning, not the rail's error", async () => {
    const locked = Object.assign(new Error("locked"), {
      response: { status: 423, data: { detail: { code: "pictures_locked" } } },
    });
    addPictureTag.mockReset().mockImplementation(async (id) => {
      if (id === 1) throw locked;
      return {};
    });
    const applied = vi.fn();
    const tags = useSelectionTags(() => [1, 2, 3], { onApplied: applied });
    expect(await tags.addTagsToAll(["sun"])).toBe(true);
    expect(applied).toHaveBeenCalledWith({
      tag: "sun",
      pictureIds: [2, 3],
      action: "add",
    });
    expect(tags.tagError.value).toBe("");
    const notices = useNoticeStore();
    expect(notices.notices).toHaveLength(1);
    expect(notices.notices[0]).toMatchObject({
      level: "warning",
      text: "Tagged 2 pictures; 1 is in a locked set and was skipped.",
      action: null,
    });
    expect(notices.notices[0].timeout).toBeGreaterThan(0);
    // A second partial refusal collapses onto the same card.
    await tags.addTagsToAll(["sea"]);
    expect(notices.notices).toHaveLength(1);
    expect(notices.notices[0].count).toBe(2);
    expect(notices.notices[0].text).toBe(
      "Tagged 2 pictures; 1 is in a locked set and was skipped.",
    );
    addPictureTag.mockReset();
  });

  it("say nothing was tagged when every picture is locked, and not twice for a drop", async () => {
    const locked = Object.assign(new Error("locked"), {
      response: { status: 423 },
    });
    addPictureTag.mockReset().mockRejectedValue(locked);
    const tags = useSelectionTags(() => [1, 2]);
    expect(await tags.addTagsToAll(["sun", "sea"])).toBe(true);
    const notices = useNoticeStore();
    expect(notices.notices.map((n) => n.text)).toEqual([
      "Nothing was tagged; 2 pictures are in a locked set.",
    ]);
    // The reject after a removal is the same gesture: it reports nothing.
    notices.clear();
    rejectTagPrediction.mockReset().mockRejectedValue(locked);
    await tags.rejectTagOnAll({
      name: "sun",
      tagsByImageId: new Map([[1, 10]]),
    });
    expect(notices.notices).toHaveLength(0);
    expect(tags.tagError.value).toBe("");
    addPictureTag.mockReset();
    rejectTagPrediction.mockReset();
  });

  it("say a failed rejected-tag read failed, rather than show none", async () => {
    listTagPredictions.mockImplementation(async (id) => {
      if (id === 2) throw new Error("offline");
      return { tag_predictions: [], meta: {} };
    });
    const tags = useSelectionTags(() => [1, 2]);
    await tags.fetchSelectedImagePredictions();
    expect(tags.predictionReadFailed.value).toBe(true);
    listTagPredictions.mockImplementation(async () => ({
      tag_predictions: [],
      meta: {},
    }));
    await tags.fetchSelectedImagePredictions();
    expect(tags.predictionReadFailed.value).toBe(false);
  });

  it("say a failed read failed, rather than show no tags", async () => {
    bulkFetchTags.mockRejectedValueOnce(new Error("offline"));
    const tags = useSelectionTags(() => [1, 2]);
    await tags.fetchSelectedImageTags();
    expect(tags.tagDataLoading.value).toBe(false);
    expect(tags.tagReadFailed.value).toBe(true);
    bulkFetchTags.mockResolvedValueOnce([{ id: 1, tags: [] }, { id: 2, tags: [] }]);
    await tags.fetchSelectedImageTags();
    expect(tags.tagReadFailed.value).toBe(false);
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

describe("the faces", () => {
  it("crop fills its square from a wide face box", async () => {
    listPictureFaces.mockResolvedValue([
      // 200 wide, 100 tall, in an 800 x 600 picture.
      { id: 1, frame_index: 0, bbox: [100, 100, 300, 200], character_id: null },
    ]);
    const wrapper = mountRail();
    await select(wrapper, [7]);
    const style = wrapper.find(".rail-face__crop").attributes("style");
    // Scaled by the shorter side (42 / 100): the picture is 336 x 252 px and
    // the face's 84 px width is centred in the 42 px box.
    expect(style).toContain("background-size: 336px 252px");
    expect(style).toContain("background-position: -63px -42px");
    expect(wrapper.find(".rail-face__name").text()).toBe("Unknown");
  });

  it("drop their names past three and end in a +N tile", async () => {
    listPictureFaces.mockResolvedValue(
      [1, 2, 3, 4, 5].map((id) => ({
        id,
        frame_index: 0,
        bbox: [id * 10, 0, id * 10 + 50, 50],
        character_id: null,
      })),
    );
    const wrapper = mountRail();
    await select(wrapper, [7]);
    expect(wrapper.findAll(".rail-face")).toHaveLength(4);
    expect(wrapper.find(".rail-face__name").exists()).toBe(false);
    expect(wrapper.find(".rail-face__more").text()).toBe("+2");
    // The panel has a row, and a picker, for every one of them.
    await wrapper.find('[aria-label="Edit faces"]').trigger("click");
    expect(wrapper.findAll(".faces-panel__row")).toHaveLength(5);
    expect(wrapper.find(".faces-panel__count").text()).toBe("5");
    // The pencil shuts it again.
    await wrapper.find('[aria-label="Edit faces"]').trigger("click");
    expect(wrapper.find(".faces-panel").exists()).toBe(false);
  });
});

describe("adding a tag to several", () => {
  it("sends it once for a double Enter, as an add", async () => {
    const wrapper = mountRail();
    await selectAndRead(wrapper, [7, 8, 9]);
    const input = wrapper.find(".rail-tags__input");
    await input.setValue("rain");
    addPictureTag.mockClear();
    input.trigger("keydown", { key: "Enter" });
    input.trigger("keydown", { key: "Enter" });
    await flush();
    expect(addPictureTag).toHaveBeenCalledTimes(3);
    expect(wrapper.emitted("tags-applied")).toEqual([
      [{ tag: "rain", pictureIds: [7, 8, 9], action: "add" }],
    ]);
    // The field empties for the next tag.
    expect(input.element.value).toBe("");
  });

  it("re-reads the rejected tags too: an add confirms a rejected one", async () => {
    const wrapper = mountRail();
    await selectAndRead(wrapper, [7, 8, 9]);
    const input = wrapper.find(".rail-tags__input");
    await input.setValue("rain");
    listTagPredictions.mockClear();
    await input.trigger("keydown", { key: "Enter" });
    await flush();
    expect(listTagPredictions.mock.calls.map(([id]) => id)).toEqual([7, 8, 9]);
  });
});

describe("T", () => {
  it("opens a shut rail and puts the caret in its tag field", async () => {
    const sidebar = useSidebarStore();
    sidebar.statsOpen = false;
    const wrapper = mountRail({}, { attachTo: document.body });
    await select(wrapper, [7, 8, 9]);
    expect(wrapper.find(".rail-tags__input").exists()).toBe(false);
    sidebar.requestTagField();
    await flush();
    const input = wrapper.find(".rail-tags__input");
    expect(input.exists()).toBe(true);
    expect(document.activeElement).toBe(input.element);
    expect(sidebar.tagFieldPending).toBe(false);
  });
});

describe("the head's title", () => {
  const measure = (text) => text.length * 10;

  it("cuts after the last whole word that fits", () => {
    expect(fitWords("Elderly man with white hair at a desk", 300, measure)).toBe(
      "Elderly man with white hair…",
    );
    expect(fitWords("Short", 300, measure)).toBe("Short");
    // Unmeasured (no layout yet): whole, for CSS to clip.
    expect(fitWords("Elderly man with white hair", 0, measure)).toBe(
      "Elderly man with white hair",
    );
  });

  it("drops a trailing comma before the ellipsis", () => {
    expect(fitWords("Woman with red hair, neon behind", 210, measure)).toBe(
      "Woman with red hair…",
    );
  });

  it("names a picture by its first sentence, without its article", () => {
    const image = { description: "An old man. He smiles." };
    expect(pictureTitle(image, [], String).title).toBe("Old man");
    expect(pictureTitle({ description: "Theatre seats" }, [], String).title).toBe(
      "Theatre seats",
    );
  });
});

describe("stats narrowed by a tag group (#2469)", () => {
  it("re-read when a move changes the group, and only when a group term is in the query", async () => {
    const filters = useFilterStore();
    const tagGroups = useTagGroupsStore();
    const wrapper = mountRail();
    await flush();

    filters.tagFilter = ["hat"];
    await flush();
    const plain = getPictureStats.mock.calls.length;
    tagGroups.refilterGroupTerms();
    await flush();
    expect(getPictureStats.mock.calls.length).toBe(plain);

    filters.tagFilter = ["__group:3"];
    await flush();
    const grouped = getPictureStats.mock.calls.length;
    tagGroups.refilterGroupTerms();
    await flush();
    expect(getPictureStats.mock.calls.length).toBeGreaterThan(grouped);
    wrapper.unmount();
  });
});
