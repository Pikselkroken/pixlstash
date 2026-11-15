// "Create a training set…": the slider takes a prefix of one ranking, the
// coverage column sorts gaps into kinds, and Create makes then fills a set.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  fetchCharacterSetSuggestion: vi.fn(),
  createPictureSet: vi.fn(),
  bulkAddPicturesToSet: vi.fn(),
  deletePictureSet: vi.fn(),
}));

vi.mock("../../api/training", () => ({
  fetchCharacterSetSuggestion: api.fetchCharacterSetSuggestion,
}));
vi.mock("../../api/pictureSets", () => ({
  createPictureSet: api.createPictureSet,
  bulkAddPicturesToSet: api.bulkAddPicturesToSet,
  deletePictureSet: api.deletePictureSet,
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));

const sheetDraws = vi.hoisted(() => ({ value: { draws: [] } }));
// Sheet draws: the dialog reads the newest cut draw for its "new" marks.
const gen = vi.hoisted(() => ({
  buildSheetPrompt: vi.fn(),
  rankSheets: vi.fn(),
  useSheets: vi.fn(),
  getCharacter: vi.fn(),
  getReferencePictures: vi.fn(),
  patchCharacter: vi.fn(),
  listAdapters: vi.fn(),
  getRunOutcome: vi.fn(),
  getWorkflowCard: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
  sheetCopyWorkflow: vi.fn(),
}));
vi.mock("../../api/characterSheets", () => ({
  listSheetDraws: vi.fn(async () => sheetDraws.value),
  buildSheetPrompt: gen.buildSheetPrompt,
  rankSheets: gen.rankSheets,
  useSheets: gen.useSheets,
}));
vi.mock("../../api/characters", () => ({
  getCharacter: gen.getCharacter,
  getReferencePictures: gen.getReferencePictures,
  patchCharacter: gen.patchCharacter,
}));
vi.mock("../../api/modelShelf", () => ({ listAdapters: gen.listAdapters }));
vi.mock("../../api/workflows", () => ({
  getRunOutcome: gen.getRunOutcome,
  getWorkflowCard: gen.getWorkflowCard,
  listWorkflowCards: gen.listWorkflowCards,
  preflightWorkflowRun: gen.preflightWorkflowRun,
  runWorkflowCard: gen.runWorkflowCard,
  sheetCopyWorkflow: gen.sheetCopyWorkflow,
}));

import CreateTrainingSetDialog from "./CreateTrainingSetDialog.vue";
import Tooltip from "../widgets/Tooltip.vue";

const TARGETS = {
  framing: {
    "close-up": 1 / 5,
    portrait: 1 / 5,
    "upper body": 1 / 5,
    "full body": 2 / 15,
  },
  expression: {
    smile: 2 / 15,
    neutral: 2 / 15,
    "open mouth": 1 / 15,
    "closed eyes": 1 / 15,
  },
};

const FRAMINGS = ["close-up", "portrait", "upper body"];
const FACES = ["smile", "neutral", "open mouth"];

/** n stills, ranked; the first closed eyes is at rank 18, no full body. */
function stills(n, { passing = n } = {}) {
  const picks = Array.from({ length: n }, (_, i) => ({
    picture_id: 100 + i,
    rank: i + 1,
    likeness: 80,
    buckets: {
      framing: [FRAMINGS[i % 3]],
      expression: [i === 17 ? "closed eyes" : FACES[i % 3]],
    },
  }));
  return {
    candidates: 300,
    passing,
    picks,
    excluded: {
      other_people: 41,
      near_duplicates: 68,
      low_quality: 23,
      low_likeness: 12,
    },
    would_add: {
      other_people: 0,
      near_duplicates: 14,
      low_quality: 6,
      low_likeness: 0,
    },
    available: {
      framing: { "close-up": 9, portrait: 9, "upper body": 9, "full body": 0 },
      expression: { smile: 9, neutral: 9, "open mouth": 9, "closed eyes": 1 },
    },
  };
}

function emptyGroup() {
  return {
    candidates: 0,
    passing: 0,
    picks: [],
    excluded: {},
    would_add: {},
    available: { framing: {}, expression: {} },
  };
}

function suggestion({ stillsGroup = stills(50), clips = null } = {}) {
  return {
    character_id: 7,
    character_name: "Anna",
    set_name: "Anna training set",
    has_clips: !!clips,
    stills: stillsGroup,
    clips: clips || emptyGroup(),
    targets: TARGETS,
  };
}

function mountDialog() {
  return mount(CreateTrainingSetDialog, {
    props: {
      open: true,
      character: { id: 7, name: "Anna", project_ids: [3] },
    },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
}

function shownIds(wrapper, group = "stills") {
  return wrapper
    .find(`[data-testid="cts-group-${group}"]`)
    .findAll("img")
    .map((img) => img.attributes("src"));
}

function button(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().startsWith(text));
}

beforeEach(() => {
  vi.clearAllMocks();
  setActivePinia(createPinia());
});

describe("CreateTrainingSetDialog", () => {
  it("shows the clips group only when the character has clips", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    let wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cts-group-stills"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="cts-group-clips"]').exists()).toBe(false);
    wrapper.unmount();

    const clips = stills(7);
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion({ clips }));
    wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cts-group-clips"]').exists()).toBe(true);
    expect(shownIds(wrapper, "clips")).toHaveLength(5);
    expect(button(wrapper, "Create set of").text()).toContain("20");
    wrapper.unmount();
  });

  it("takes a prefix of the ranking: a bigger set keeps the first picks", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    const first = shownIds(wrapper);
    expect(first).toHaveLength(15);
    await wrapper.find('[data-testid="cts-slider-stills"]').setValue("25");
    const bigger = shownIds(wrapper);
    expect(bigger).toHaveLength(25);
    expect(bigger.slice(0, 15)).toEqual(first);
    wrapper.unmount();
  });

  it("stops the slider at what passes and names the switches that widen it", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: stills(9) }),
    );
    const wrapper = mountDialog();
    await flushPromises();
    expect(shownIds(wrapper)).toHaveLength(9);
    const slider = wrapper.find('[data-testid="cts-slider-stills"]');
    await slider.setValue("30");
    expect(shownIds(wrapper)).toHaveLength(9);
    expect(slider.element.value).toBe("9");
    const capped = wrapper.find('[data-testid="cts-capped-stills"]');
    // Short in the heading; the switches that widen it are its tooltip.
    expect(capped.text()).toBe("Only 9 pictures pass");
    const notice = capped.findComponent(Tooltip).props("text");
    expect(notice).toContain("Only 9 pictures pass.");
    expect(notice).toContain("Keeping near-duplicates would let 14 more");
    expect(notice).toContain("no minimum score 6 more");
    wrapper.unmount();
  });

  it("says a label is short on its bar, with no line under the bars", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cts-bar-closed eyes"]').text()).toContain(
      "1 short",
    );
    const none = wrapper.find('[data-testid="cts-bar-full body"]');
    expect(none.text()).toContain("not in the library");
    expect(none.text()).toContain("none");
    expect(wrapper.findAll(".cts-bar__none")).toHaveLength(1);
    expect(wrapper.find('[data-testid="cts-coverage"]').text()).not.toContain(
      "short ·",
    );
    wrapper.unmount();
  });

  it("does not create on Enter in a slider", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper
      .find('[data-testid="cts-slider-stills"]')
      .trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(api.createPictureSet).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("sets both reasons aside by default; switching one off asks again", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(7, {
      include_other_people: false,
      include_near_duplicates: false,
      min_likeness: 30,
      min_score: 3,
      framing_variety: "important",
      expression_variety: "important",
    });
    const switches = wrapper.findAllComponents({ name: "VSwitch" });
    // The stub declares no props, so the bound value lands as an attribute.
    expect(switches.map((s) => s.attributes("model-value"))).toEqual([
      "true",
      "true",
    ]);
    switches[1].vm.$emit("update:modelValue", false);
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(7, {
      include_other_people: false,
      include_near_duplicates: true,
      min_likeness: 30,
      min_score: 3,
      framing_variety: "important",
      expression_variety: "important",
    });
    wrapper.unmount();
  });

  it("asks again with a floor when its slider is let go, not while it moves", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    const floor = wrapper.find('[data-testid="cts-floor-min_score"]');
    // 23 stills are under the score floor; the count sits under its slider.
    expect(floor.text()).toContain("★ 3.0");
    expect(floor.text()).toContain("23 excluded");
    const slider = wrapper.find('[data-testid="cts-slider-min_score"]');
    slider.element.value = "4.5";
    await slider.trigger("input");
    expect(floor.text()).toContain("★ 4.5");
    expect(api.fetchCharacterSetSuggestion).toHaveBeenCalledTimes(1);
    await slider.trigger("change");
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(
      7,
      expect.objectContaining({ min_score: 4.5, min_likeness: 30 }),
    );

    const likeness = wrapper.find('[data-testid="cts-slider-min_likeness"]');
    likeness.element.value = "55";
    await likeness.trigger("input");
    await likeness.trigger("change");
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(
      7,
      expect.objectContaining({ min_score: 4.5, min_likeness: 55 }),
    );
    wrapper.unmount();
  });

  it("asks again with how much a kind of variety matters", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    const framing = wrapper.find('[data-testid="cts-variety-framing_variety"]');
    expect(framing.text()).toContain("a missing framing always wins");
    expect(
      framing.find('[role="radio"][aria-checked="true"]').text(),
    ).toBe("Important");
    await framing
      .findAll('[role="radio"]')
      .find((b) => b.text() === "Somewhat")
      .trigger("click");
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(
      7,
      expect.objectContaining({
        framing_variety: "somewhat",
        expression_variety: "important",
      }),
    );
    expect(framing.text()).toContain("a gap beats a repeat up to 60% better");
    wrapper.unmount();
  });

  it("gives a bar and the chips of its pictures the same colour", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    const hue = (el) => el.classes().find((c) => c.startsWith("cts-hue--"));
    const fill = (label) =>
      hue(wrapper.find(`[data-testid="cts-bar-${label}"] .cts-bar__fill`));
    // Pick 1 is a close-up smile, pick 2 a portrait neutral.
    const chips = wrapper
      .findAll('[data-testid="cts-pick"]')
      .slice(0, 2)
      .map((pick) => pick.findAll(".cts-chip"));
    expect(chips[0].map((c) => c.text())).toEqual(["close-up", "smile"]);
    expect(chips[0].map(hue)).toEqual([fill("close-up"), fill("smile")]);
    expect(chips[1].map(hue)).toEqual([fill("portrait"), fill("neutral")]);
    // Eight bars, eight colours.
    const all = wrapper.findAll(".cts-bar__fill").map(hue);
    expect(new Set(all).size).toBe(8);
    wrapper.unmount();
  });

  it("shades a floor's track by what each position would leave", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue({
      ...suggestion(),
      kept: { min_likeness: [], min_score: [8, 8, 8, 8, 6, 4, 2, 0, 0] },
    });
    const wrapper = mountDialog();
    await flushPromises();
    const track = (key) =>
      wrapper
        .find(`[data-testid="cts-floor-${key}"] .cts-range`)
        .attributes("style");
    // All eight remain at the low end, half at ★ 3.5, none from ★ 4.5.
    expect(track("min_score")).toContain("var(--cts-kept-all) 100%, var(--cts-kept-none)) 0%");
    expect(track("min_score")).toContain("var(--cts-kept-all) 50%, var(--cts-kept-none)) 63%");
    expect(track("min_score")).toContain("var(--cts-kept-all) 0%, var(--cts-kept-none)) 100%");
    // The fill ends at the thumb: ★ 3.0 is halfway along 1-5.
    expect(track("min_score")).toContain("--cts-at: 0.5");
    // No counts, no shading.
    expect(track("min_likeness")).not.toContain("--cts-kept:");
    wrapper.unmount();
  });

  it("stops a floor slider at the best value among the pictures", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue({
      ...suggestion(),
      limits: { min_likeness: 80, min_score: 4.5 },
    });
    const wrapper = mountDialog();
    await flushPromises();
    const slider = wrapper.find('[data-testid="cts-slider-min_likeness"]');
    // The track keeps its whole scale; the thumb stops at 80.
    expect(slider.attributes("max")).toBe("100");
    slider.element.value = "95";
    await slider.trigger("input");
    expect(slider.element.value).toBe("80");
    await slider.trigger("change");
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(
      7,
      expect.objectContaining({ min_likeness: 80 }),
    );
    slider.element.value = "60";
    await slider.trigger("input");
    expect(slider.element.value).toBe("60");
    wrapper.unmount();
  });

  it("greys a pick left out by hand and takes the next one in its place", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 58 } });
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 15 });
    const wrapper = mountDialog();
    await flushPromises();
    const tiles = () => wrapper.findAll('[data-testid="cts-pick"]');
    const toggles = () => wrapper.findAll('[data-testid="cts-pick-toggle"]');
    expect(tiles()).toHaveLength(15);
    await toggles()[1].trigger("click");
    // Still shown, greyed, in its place; pick 16 joins at the end.
    expect(tiles()).toHaveLength(16);
    expect(tiles()[1].classes()).toContain("cts-pick--off");
    expect(toggles()[1].attributes("aria-pressed")).toBe("true");
    expect(shownIds(wrapper).at(-1)).toBe("/thumb/115.webp");
    expect(button(wrapper, "Create set of").text()).toContain("15");
    expect(wrapper.find('[data-testid="cts-group-stills"]').text()).toContain(
      "of 49",
    );

    // It stays out when the ranking is asked for again.
    wrapper.findAllComponents({ name: "VSwitch" })[0].vm.$emit(
      "update:modelValue",
      false,
    );
    await flushPromises();
    expect(tiles()[1].classes()).toContain("cts-pick--off");

    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    const ids = api.bulkAddPicturesToSet.mock.calls[0][1];
    expect(ids).toHaveLength(15);
    expect(ids).not.toContain(101);
    expect(ids).toContain(115);
    wrapper.unmount();
  });

  it("does not blame the filters when leaving picks out is what caps the set", async () => {
    // 135 pass, the ranking holds 50: one left out leaves 49 to choose from.
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: stills(50, { passing: 135 }) }),
    );
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll('[data-testid="cts-pick-toggle"]')[0].trigger("click");
    const slider = wrapper.find('[data-testid="cts-slider-stills"]');
    await slider.setValue("50");
    expect(slider.element.value).toBe("49");
    const notice = wrapper.find('[data-testid="cts-capped-stills"]').text();
    expect(notice).toBe("Only 49 left: put one back for more");
    expect(notice).not.toContain("pass.");
    wrapper.unmount();
  });

  it("puts a left-out pick back, and opens a tile's preview", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    const toggle = () => wrapper.findAll('[data-testid="cts-pick-toggle"]')[0];
    await toggle().trigger("click");
    await toggle().trigger("click");
    expect(wrapper.findAll('[data-testid="cts-pick"]')).toHaveLength(15);
    expect(wrapper.find(".cts-pick--off").exists()).toBe(false);

    const preview = wrapper.findComponent({ name: "TrainingPickPreview" });
    expect(preview.props("pick")).toBeNull();
    await wrapper.findAll(".cts-tile")[2].trigger("click");
    expect(preview.props("pick")).toMatchObject({
      picture_id: 102,
      rank: 3,
      off: false,
    });
    // Leaving it out from the preview is the same de-select.
    preview.vm.$emit("toggle", 102);
    await flushPromises();
    expect(preview.props("pick").off).toBe(true);
    expect(wrapper.findAll(".cts-pick--off")).toHaveLength(1);
    preview.vm.$emit("close");
    await flushPromises();
    expect(preview.props("pick")).toBeNull();
    wrapper.unmount();
  });

  it("removes the set it made when filling it fails", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 55 } });
    api.bulkAddPicturesToSet.mockRejectedValue(new Error("boom"));
    api.deletePictureSet.mockResolvedValue({});
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    expect(api.createPictureSet).toHaveBeenCalledWith({
      name: "Anna training set",
      parent_character_id: 7,
      project_ids: [3],
    });
    expect(api.deletePictureSet).toHaveBeenCalledWith(55);
    expect(wrapper.find('[role="alert"]').text()).toContain(
      "Couldn't create the set.",
    );
    expect(wrapper.emitted("created")).toBeUndefined();
    wrapper.unmount();
  });

  it("removes the set when none of the picks could be added", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 56 } });
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 0 });
    api.deletePictureSet.mockResolvedValue({});
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    expect(api.deletePictureSet).toHaveBeenCalledWith(56);
    expect(wrapper.emitted("created")).toBeUndefined();
    wrapper.unmount();
  });

  it("fills the set with what was submitted, and locks the slider meanwhile", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    let release;
    api.createPictureSet.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ picture_set: { id: 57 } });
      }),
    );
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 15 });
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    const slider = wrapper.find('[data-testid="cts-slider-stills"]');
    expect(slider.attributes("disabled")).toBeDefined();
    await slider.setValue("25");
    release();
    await flushPromises();
    expect(api.bulkAddPicturesToSet.mock.calls[0][1]).toHaveLength(15);
    wrapper.unmount();
  });

  it("keeps Create off while the last reload failed", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValueOnce(suggestion());
    api.fetchCharacterSetSuggestion.mockRejectedValueOnce(new Error("down"));
    const wrapper = mountDialog();
    await flushPromises();
    wrapper.findAllComponents({ name: "VSwitch" })[0].vm.$emit(
      "update:modelValue",
      false,
    );
    await flushPromises();
    expect(button(wrapper, "Create set of").attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });

  it("creates the set from the picks and hands it to the Train dialog", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 55 } });
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 15 });
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(
      55,
      Array.from({ length: 15 }, (_, i) => 100 + i),
    );
    expect(api.deletePictureSet).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="cts-created"]').text()).toContain(
      "Anna training set · 15 members",
    );
    await wrapper.find('[data-testid="cts-train"]').trigger("click");
    expect(wrapper.emitted("train")).toEqual([
      [{ type: "set", id: 55, name: "Anna training set" }],
    ]);
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });
});

describe("CreateTrainingSetDialog: sheets", () => {
  beforeEach(() => {
    sheetDraws.value = { draws: [] };
  });

  it("sends a character with no pictures to its Sheets screen", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.emitted("draw-sheets")).toEqual([[{ id: 7, name: "Anna" }]]);
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.unmount();
  });

  it("offers Draw sheets… beside the picks, which goes to the Sheets screen", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    // Pictures exist: the picker stays.
    expect(wrapper.emitted("draw-sheets")).toBeUndefined();
    await wrapper.find('[data-testid="cts-generate"]').trigger("click");
    expect(wrapper.emitted("draw-sheets")).toEqual([[{ id: 7, name: "Anna" }]]);
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.unmount();
  });

  it("marks the pictures the newest cut draw made as new", async () => {
    const picks = stills(3);
    picks.picks[0].picture_id = 601;
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: picks }),
    );
    sheetDraws.value = {
      draws: [
        { id: 3, used: [] },
        { id: 2, used: [601, 602] },
        { id: 1, used: [101] },
      ],
    };
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.findAll('[data-testid="cts-new"]')).toHaveLength(1);
    // "New only" keeps just those.
    const newOnly = wrapper
      .find('[data-testid="cts-new-only"]')
      .findAll("button")
      .find((b) => b.text() === "New only");
    await newOnly.trigger("click");
    expect(shownIds(wrapper)).toEqual(["/thumb/601.webp"]);
    wrapper.unmount();
  });
});
