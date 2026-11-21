// A training set's build screen ("Create a training set…"): the slider takes
// a prefix of one ranking, the coverage column sorts gaps into kinds, Create
// fills the set and finishes it, and the build is saved on the set.

import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { useTasksStore } from "../../stores/useTasksStore";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  fetchCharacterSetSuggestion: vi.fn(),
  patchPictureSet: vi.fn(async () => ({ status: "success" })),
  bulkAddPicturesToSet: vi.fn(),
  deletePictureSet: vi.fn(),
}));

vi.mock("../../api/training", () => ({
  fetchCharacterSetSuggestion: api.fetchCharacterSetSuggestion,
}));
vi.mock("../../api/pictureSets", () => ({
  patchPictureSet: api.patchPictureSet,
  bulkAddPicturesToSet: api.bulkAddPicturesToSet,
  deletePictureSet: api.deletePictureSet,
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));

// The Sheets step (CharacterSheetStep): what it reads and what it runs.
const gen = vi.hoisted(() => ({
  buildSheetPrompt: vi.fn(),
  rankSheets: vi.fn(),
  useSheets: vi.fn(),
  getCharacter: vi.fn(),
  getReferencePictures: vi.fn(),
  patchCharacter: vi.fn(),
  getFaceClusters: vi.fn(async () => ({
    faces: 0,
    reference_cluster: null,
    clusters: [],
    unclustered: [],
  })),
  setReferenceCluster: vi.fn(),
  listAdapters: vi.fn(),
  getRunOutcome: vi.fn(),
  stopRun: vi.fn(async () => ({ stopped: 1 })),
  getWorkflowCard: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
  sheetCopyWorkflow: vi.fn(),
  cardForWorkflow: vi.fn(async () => ({})),
}));
vi.mock("../../api/characterSheets", () => ({
  buildSheetPrompt: gen.buildSheetPrompt,
  rankSheets: gen.rankSheets,
  useSheets: gen.useSheets,
}));
vi.mock("../../api/characters", () => ({
  getCharacter: gen.getCharacter,
  getReferencePictures: gen.getReferencePictures,
  patchCharacter: gen.patchCharacter,
  getFaceClusters: gen.getFaceClusters,
  setReferenceCluster: gen.setReferenceCluster,
}));
vi.mock("../../api/modelShelf", () => ({ listAdapters: gen.listAdapters }));
vi.mock("../../api/comfyui", () => ({ cardForWorkflow: gen.cardForWorkflow }));
vi.mock("../../api/workflows", () => ({
  getRunOutcome: gen.getRunOutcome,
  stopRun: gen.stopRun,
  getWorkflowCard: gen.getWorkflowCard,
  listWorkflowCards: gen.listWorkflowCards,
  preflightWorkflowRun: gen.preflightWorkflowRun,
  runWorkflowCard: gen.runWorkflowCard,
  sheetCopyWorkflow: gen.sheetCopyWorkflow,
}));

import TrainingSetBuild from "./TrainingSetBuild.vue";
import CharacterSheetStep from "../io/CharacterSheetStep.vue";
import PicturePicker from "../widgets/PicturePicker.vue";
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

/** The screen of set 55, "Anna training set", built from *building*. */
function mountDialog(building = { v: 1, stage: "pick" }) {
  return mount(TrainingSetBuild, {
    props: {
      set: { id: 55, name: "Anna training set", building },
      character: { id: 7, name: "Anna" },
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
  // The sheet step remembers its last choices here.
  localStorage.clear();
});

describe("TrainingSetBuild", () => {
  it("asks for a reference cluster when the faces look like several people", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const cluster = (size) => ({ size, members: [] });
    gen.getFaceClusters.mockResolvedValueOnce({
      faces: 12,
      reference_cluster: null,
      clusters: [cluster(6), cluster(4), cluster(2)],
      unclustered: [],
    });
    let wrapper = mountDialog();
    await flushPromises();
    // Two clusters of three or more; the pair does not count as a person.
    expect(wrapper.find('[data-testid="cts-faces-notice"]').text()).toContain(
      "Anna's faces look like 2 people.",
    );
    wrapper.unmount();

    gen.getFaceClusters.mockResolvedValueOnce({
      faces: 12,
      reference_cluster: [1, 2, 3, 4, 5],
      clusters: [cluster(6), cluster(4)],
      unclustered: [],
    });
    wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cts-faces-notice"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="cts-faces-steered"]').text()).toContain(
      "Steered by Anna's reference cluster (5 pictures).",
    );
    wrapper.unmount();
  });

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
    expect(api.bulkAddPicturesToSet).not.toHaveBeenCalled();
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

  it("keeps the set and its build when filling it fails", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.bulkAddPicturesToSet.mockRejectedValue(new Error("boom"));
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    expect(api.deletePictureSet).not.toHaveBeenCalled();
    expect(api.patchPictureSet).not.toHaveBeenCalledWith(55, { building: null });
    expect(wrapper.find('[role="alert"]').text()).toContain(
      "Couldn't create the set.",
    );
    expect(wrapper.emitted("finished")).toBeUndefined();
    wrapper.unmount();
  });

  it("does not finish the set when none of the picks could be added", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 0 });
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    expect(api.patchPictureSet).not.toHaveBeenCalledWith(55, { building: null });
    expect(wrapper.emitted("finished")).toBeUndefined();
    wrapper.unmount();
  });

  it("fills the set with what was submitted, and locks the slider meanwhile", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    let release;
    api.bulkAddPicturesToSet.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ added: 15 });
      }),
    );
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

  it("fills the set with the picks and finishes it", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 15 });
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Create set of").trigger("click");
    await flushPromises();
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(
      55,
      Array.from({ length: 15 }, (_, i) => 100 + i),
    );
    expect(api.patchPictureSet).toHaveBeenLastCalledWith(55, { building: null });
    expect(api.deletePictureSet).not.toHaveBeenCalled();
    expect(wrapper.emitted("finished")).toEqual([
      [{ id: 55, name: "Anna training set", members: 15 }],
    ]);
    wrapper.unmount();
  });
});

const MOSS_CARD = {
  id: "manual:moss",
  name: "MOSS sheet",
  type: "txt2img",
  last_used: "2026-11-09T20:00:00Z",
  models: [{ kind: "unet", base_model_family: "moss_image" }],
};
const KREA_CARD = {
  id: "manual:krea",
  name: "Krea sheet",
  type: "txt2img",
  last_used: "2026-11-10T20:00:00Z",
  models: [{ kind: "checkpoint", base_model_family: "krea2" }],
};

/** Mock the Sheets step's reads: one MOSS workflow, LoRAs newest first. */
function sheetsWorld({ description = "", loras = [] } = {}) {
  gen.getCharacter.mockResolvedValue({ id: 7, name: "Anna", description });
  gen.getReferencePictures.mockResolvedValue({ reference_picture_ids: [] });
  gen.patchCharacter.mockResolvedValue({ status: "success" });
  gen.listAdapters.mockResolvedValue(loras);
  gen.listWorkflowCards.mockResolvedValue({
    cards: [
      MOSS_CARD,
      { id: "auto:sdxl", name: "SDXL", type: "txt2img", models: [] },
    ],
  });
  gen.getWorkflowCard.mockResolvedValue({
    card: {
      name: "MOSS sheet",
      defaults: [
        { slot_label: "ks", input_name: "steps", value: 25 },
        { slot_label: "lat", input_name: "width", value: 1024 },
        { slot_label: "lat", input_name: "height", value: 1024 },
      ],
    },
  });
  gen.preflightWorkflowRun.mockResolvedValue({
    ok: true,
    groups: [{ reasons: [] }],
  });
  gen.buildSheetPrompt.mockResolvedValue({
    prompt: "{layout}",
    negative: "nsfw",
    width: 2560,
    height: 1536,
  });
  gen.runWorkflowCard.mockResolvedValue({
    status: "success",
    prompts: [
      { run_id: "r1", status: "queued" },
      { run_id: "r2", status: "queued" },
    ],
  });
}

function sheet(id, agreement, extra = {}) {
  return {
    picture_id: id,
    faces_read: true,
    faces: 6,
    agreement,
    agreement_words: agreement >= 0.55 ? "faces agree strongly" : "faces agree somewhat",
    closeness: null,
    close: null,
    broken: false,
    ...extra,
  };
}

describe("TrainingSetBuild: generating the pictures", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens a character with no pictures on the Sheets step", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cts-steps"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="cs-main"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-testid="cts-picks"]').isVisible()).toBe(false);
    // Nothing to describe yet: drawing waits for a description.
    const primary = wrapper.find('[data-testid="cts-generate-primary"]');
    expect(primary.text()).toContain("Draw 4 sheets");
    expect(primary.attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });

  it("refuses a workflow with no picture size, or one that can't run, before drawing", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.getWorkflowCard.mockResolvedValue({
      card: { name: "MOSS sheet", defaults: [{ slot_label: "ks", input_name: "steps", value: 25 }] },
    });
    let wrapper = mountDialog();
    await flushPromises();
    const primary = () => wrapper.find('[data-testid="cts-generate-primary"]');
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "has no picture size",
    );
    expect(primary().attributes("disabled")).toBeDefined();
    wrapper.unmount();

    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [{ reasons: [{ code: "missing_models", models: [] }] }],
    });
    wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "can't run as it is",
    );
    expect(primary().attributes("disabled")).toBeDefined();
    wrapper.unmount();

    // The positive control: a sized workflow that runs can draw.
    sheetsWorld({ description: "Adult woman, copper bob" });
    wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').exists()).toBe(false);
    expect(primary().attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("says ComfyUI is down rather than blaming the workflows, and checks again", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    // Unchecked, so no size is known either: the size must not be blamed.
    gen.getWorkflowCard.mockResolvedValue({ card: { name: "MOSS sheet", defaults: [] } });
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [{ reasons: [{ code: "comfyui_unreachable" }] }],
    });
    let wrapper = mountDialog();
    await flushPromises();
    const problem = () => wrapper.find('[data-testid="cs-workflow-problem"]');
    expect(problem().text()).toContain("ComfyUI isn't answering");
    expect(problem().text()).not.toContain("picture size");
    expect(problem().text()).not.toContain("None of");

    // Its address is taken away meanwhile: Settings is named, and the step
    // stops asking.
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [{ reasons: [{ code: "comfyui_not_configured" }] }],
    });
    await vi.advanceTimersByTimeAsync(15_000);
    await flushPromises();
    expect(problem().text()).toContain("Add one in Settings");
    const asked = gen.preflightWorkflowRun.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(gen.preflightWorkflowRun.mock.calls.length).toBe(asked);

    // Back to unreachable, as a fresh open would find it, then ComfyUI answers.
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [{ reasons: [{ code: "comfyui_unreachable" }] }],
    });
    wrapper.unmount();
    wrapper = mountDialog();
    await flushPromises();
    expect(problem().text()).toContain("ComfyUI isn't answering");

    // ComfyUI comes back: the step finds out by itself.
    sheetsWorld({ description: "Adult woman, copper bob" });
    await vi.advanceTimersByTimeAsync(15_000);
    await flushPromises();
    expect(problem().exists()).toBe(false);
    expect(
      wrapper.find('[data-testid="cts-generate-primary"]').attributes("disabled"),
    ).toBeUndefined();
    wrapper.unmount();
  });

  it("draws with the workflow chosen when Draw was pressed, not one picked meanwhile", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    let built;
    gen.buildSheetPrompt.mockReturnValueOnce(new Promise((r) => (built = r)));
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    // While the prompt is built, another workflow, with no size to set.
    gen.getWorkflowCard.mockResolvedValue({ card: { name: "SDXL", defaults: [] } });
    wrapper
      .findAllComponents({ name: "AppSelect" })
      .find((c) => c.props("label") === "Sheet workflow")
      .vm.$emit("update:modelValue", "auto:sdxl");
    await flushPromises();
    built({ prompt: "{layout}", negative: "nsfw", width: 2560, height: 1536 });
    await flushPromises();
    expect(gen.runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        workflow_id: "manual:moss",
        values: [
          { slot_label: "lat", input_name: "width", value: 2560 },
          { slot_label: "lat", input_name: "height", value: 1536 },
        ],
      }),
    );
    wrapper.unmount();
  });

  it("draws with a Krea 2 workflow in prose, with a Krea 2 LoRA", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({
      description: "Adult woman, copper bob",
      loras: [
        { sha256: "krea1", display_name: "Anna Krea", base_model_family: "krea2", added_at: "2026-11-09" },
        { sha256: "moss1", display_name: "Anna v1", base_model_family: "moss_image", added_at: "2026-11-08" },
      ],
    });
    gen.listWorkflowCards.mockResolvedValue({
      cards: [
        MOSS_CARD,
        {
          id: "manual:krea",
          name: "Krea sheet",
          type: "txt2img",
          last_used: "2026-11-10T20:00:00Z",
          models: [{ kind: "checkpoint", base_model_family: "krea2" }],
        },
      ],
    });
    const wrapper = mountDialog();
    await flushPromises();
    const select = (label) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === label);
    // Both models are offered; the most recently used, Krea 2, is chosen and
    // its LoRA follows it.
    expect(select("Sheet workflow").props("options").map((o) => o.value)).toEqual([
      "manual:krea",
      "manual:moss",
    ]);
    expect(select("Character LoRA · optional").props("modelValue")).toBe("krea1");
    // The LoRA follows a workflow of the other model, there and back.
    const pick = async (id) => {
      select("Sheet workflow").vm.$emit("update:modelValue", id);
      await flushPromises();
      return select("Character LoRA · optional").props("modelValue");
    };
    expect(await pick("manual:moss")).toBe("moss1");
    expect(await pick("manual:krea")).toBe("krea1");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.buildSheetPrompt).toHaveBeenLastCalledWith(
      expect.objectContaining({ layout: "prose" }),
    );
    expect(gen.buildSheetPrompt.mock.lastCall[0]).not.toHaveProperty("reference");
    expect(gen.runWorkflowCard).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workflow_id: "manual:krea",
        add_loras: [expect.objectContaining({ sha256: "krea1" })],
      }),
    );
    wrapper.unmount();
  });

  it("offers the photo look only to a prose prompt, on unless turned off", async () => {
    const open = async () => {
      api.fetchCharacterSetSuggestion.mockResolvedValue(
        suggestion({ stillsGroup: emptyGroup() }),
      );
      sheetsWorld({ description: "Adult woman, copper bob" });
      gen.listWorkflowCards.mockResolvedValue({ cards: [MOSS_CARD, KREA_CARD] });
      const wrapper = mountDialog();
      await flushPromises();
      return wrapper;
    };
    const look = (wrapper) => wrapper.find('[data-testid="cs-photo-look"]');
    const sent = async (wrapper) => {
      await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
      await flushPromises();
      wrapper.unmount();
      return gen.buildSheetPrompt.mock.lastCall[0];
    };
    let wrapper = await open();
    expect(look(wrapper).find('[aria-checked="true"]').text()).toBe("Natural");
    // MOSS-Image draws from its layout, which has no photo look to turn off.
    const pick = async (id) => {
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === "Sheet workflow")
        .vm.$emit("update:modelValue", id);
      await flushPromises();
      return look(wrapper).exists();
    };
    expect(await pick("manual:moss")).toBe(false);
    expect(await pick("manual:krea")).toBe(true);
    expect(await sent(wrapper)).toEqual(
      expect.objectContaining({ layout: "prose", photo_look: true }),
    );
    wrapper = await open();
    await look(wrapper)
      .findAll("button")
      .find((b) => b.text() === "Off")
      .trigger("click");
    expect((await sent(wrapper)).photo_look).toBe(false);
  });

  it("draws text sheets with a MiniMax H3 text-to-image workflow, in prose", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({
      description: "Adult woman, copper bob",
      loras: [
        { sha256: "qwen", display_name: "Anna Qwen", base_model_family: "qwen", added_at: "2026-11-09" },
        { sha256: "h3", display_name: "Anna H3", base_model_family: "minimax_h3", added_at: "2026-11-08" },
      ],
    });
    gen.listWorkflowCards.mockResolvedValue({
      cards: [
        MOSS_CARD,
        {
          id: "manual:h3-t2i",
          name: "MiniMax H3: Text to Image",
          type: "txt2img",
          last_used: "2026-11-12T20:00:00Z",
          models: [
            { kind: "clip", base_model_family: "minimax_h3" },
            { kind: "unet", base_model_family: "minimax_h3" },
          ],
        },
        // A video workflow of the same model draws no sheet.
        {
          id: "manual:h3-t2v",
          name: "MiniMax H3: Text to Video",
          type: "video",
          last_used: "2026-11-13T20:00:00Z",
          models: [{ kind: "unet", base_model_family: "minimax_h3" }],
        },
      ],
    });
    const wrapper = mountDialog();
    await flushPromises();
    const select = (label) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === label);
    expect(select("Sheet workflow").props("options").map((o) => o.value)).toEqual([
      "manual:h3-t2i",
      "manual:moss",
    ]);
    expect(select("Character LoRA · optional").props("modelValue")).toBe("h3");
    expect(
      select("Character LoRA · optional")
        .props("options")
        .filter((o) => o.value && !o.disabled)
        .map((o) => o.value),
    ).toEqual(["h3"]);
    // H3 wants a large canvas: Small and Medium are greyed out, 4K is chosen.
    const sizes = () =>
      wrapper
        .findAll('[data-testid="cs-size"] button')
        .map((b) => [b.text(), b.attributes("disabled") !== undefined, b.attributes("aria-checked")]);
    expect(sizes()).toEqual([
      ["Small", true, "false"],
      ["Medium", true, "false"],
      ["Large", false, "false"],
      ["4K", false, "true"],
    ]);
    // Another model greys 4K out and goes back to Large.
    select("Sheet workflow").vm.$emit("update:modelValue", "manual:moss");
    await flushPromises();
    expect(sizes()).toEqual([
      ["Small", false, "false"],
      ["Medium", false, "false"],
      ["Large", false, "true"],
      ["4K", true, "false"],
    ]);
    select("Sheet workflow").vm.$emit("update:modelValue", "manual:h3-t2i");
    await flushPromises();
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.buildSheetPrompt).toHaveBeenLastCalledWith(
      expect.objectContaining({ layout: "prose", size: "4k" }),
    );
    expect(gen.runWorkflowCard).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workflow_id: "manual:h3-t2i",
        add_loras: [expect.objectContaining({ sha256: "h3" })],
      }),
    );
    wrapper.unmount();
  });

  it("draws text sheets with MiniMax H3 Image on its own LoRAs, never H3 video's", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({
      description: "Adult woman, copper bob",
      loras: [
        { sha256: "h3", display_name: "Anna H3", base_model_family: "minimax_h3", added_at: "2026-11-09" },
        { sha256: "h3i", display_name: "Anna H3 Image", base_model_family: "minimax_h3_image", added_at: "2026-11-08" },
        { sha256: "qwen", display_name: "Anna Qwen", base_model_family: "qwen", added_at: "2026-11-07" },
      ],
    });
    gen.listWorkflowCards.mockResolvedValue({
      cards: [
        MOSS_CARD,
        {
          id: "manual:h3i",
          name: "Sheet-MiniMax-H3-Image",
          type: "txt2img",
          last_used: "2026-11-12T20:00:00Z",
          models: [
            { kind: "clip", base_model_family: "minimax_h3" },
            { kind: "unet", base_model_family: "minimax_h3_image" },
          ],
        },
      ],
    });
    const wrapper = mountDialog();
    await flushPromises();
    const select = (label) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === label);
    expect(select("Sheet workflow").props("modelValue")).toBe("manual:h3i");
    expect(
      select("Character LoRA · optional")
        .props("options")
        .filter((o) => o.value && !o.disabled)
        .map((o) => o.value),
    ).toEqual(["h3i"]);
    const sizes = () =>
      wrapper
        .findAll('[data-testid="cs-size"] button')
        .map((b) => [b.text(), b.attributes("disabled") !== undefined, b.attributes("aria-checked")]);
    expect(sizes()).toEqual([
      ["Small", true, "false"],
      ["Medium", true, "false"],
      ["Large", false, "false"],
      ["4K", false, "true"],
    ]);
    expect(wrapper.text()).toContain("MiniMax H3 Image needs Large or 4K.");
    wrapper.unmount();
  });

  it("draws text sheets with Qwen-Image and FLUX.2 Klein, read off the diffusion model (#2700)", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({
      description: "Adult woman, copper bob",
      loras: [
        { sha256: "dev", display_name: "Anna dev", base_model_family: "flux2", base_model_canonical: "FLUX.2", added_at: "2026-11-09" },
        { sha256: "klein", display_name: "Anna Klein", base_model_family: "flux2", base_model_canonical: "FLUX.2 Klein", added_at: "2026-11-08" },
        { sha256: "qwen", display_name: "Anna Qwen", base_model_family: "qwen", added_at: "2026-11-07" },
      ],
    });
    gen.listWorkflowCards.mockResolvedValue({
      cards: [
        MOSS_CARD,
        {
          id: "manual:klein-t2i",
          name: "Klein T2I",
          type: "txt2img",
          last_used: "2026-11-12T20:00:00Z",
          models: [
            { kind: "clip", base_model_family: "qwen" },
            { kind: "unet", base_model_family: "flux2", filename: "flux-2-klein-9b.safetensors" },
          ],
        },
        {
          id: "manual:qwen-t2i",
          name: "Qwen T2I",
          type: "txt2img",
          last_used: "2026-11-11T20:00:00Z",
          models: [{ kind: "unet", base_model_family: "qwen" }],
        },
        // A Qwen text encoder does not make a Qwen workflow.
        {
          id: "auto:zimage",
          name: "Z-Image",
          type: "txt2img",
          last_used: "2026-11-13T20:00:00Z",
          models: [
            { kind: "unet", base_model_family: "zimage" },
            { kind: "clip", base_model_family: "qwen" },
          ],
        },
      ],
    });
    const wrapper = mountDialog();
    await flushPromises();
    const select = (label) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === label);
    expect(select("Sheet workflow").props("options").map((o) => o.value)).toEqual([
      "manual:klein-t2i",
      "manual:qwen-t2i",
      "manual:moss",
    ]);
    // Klein takes its own LoRAs, not FLUX.2 dev's.
    expect(select("Character LoRA · optional").props("modelValue")).toBe("klein");
    expect(
      select("Character LoRA · optional")
        .props("options")
        .filter((o) => o.value && !o.disabled)
        .map((o) => o.value),
    ).toEqual(["klein"]);
    // Qwen takes Qwen's, and back.
    const pick = async (id) => {
      select("Sheet workflow").vm.$emit("update:modelValue", id);
      await flushPromises();
      return select("Character LoRA · optional").props("modelValue");
    };
    expect(await pick("manual:qwen-t2i")).toBe("qwen");
    expect(await pick("manual:klein-t2i")).toBe("klein");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    // No layout JSON for Klein: the sheet in one sentence, and no reference.
    expect(gen.buildSheetPrompt).toHaveBeenLastCalledWith(
      expect.objectContaining({ layout: "prose" }),
    );
    expect(gen.buildSheetPrompt.mock.lastCall[0]).not.toHaveProperty("reference");
    expect(gen.runWorkflowCard).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workflow_id: "manual:klein-t2i",
        add_loras: [expect.objectContaining({ sha256: "klein" })],
      }),
    );
    wrapper.unmount();
  });

  it("makes a sheet workflow from another text-to-image one and draws regions with it", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({
      description: "Adult woman, copper bob",
      loras: [
        { sha256: "sdxl1", display_name: "Anna SDXL", base_model_family: "sdxl", added_at: "2026-11-09" },
        { sha256: "moss1", display_name: "Anna v1", base_model_family: "moss_image", added_at: "2026-11-08" },
      ],
    });
    const sdxl = {
      id: "auto:sdxl",
      name: "SDXL",
      type: "txt2img",
      models: [{ base_model_family: "sdxl", base_model: "SDXL" }],
    };
    const flux = {
      id: "auto:flux",
      name: "Flux",
      type: "txt2img",
      last_used: "2026-11-01T20:00:00Z",
      models: [{ base_model_family: "flux" }],
    };
    const copy = {
      id: "manual:sdxl-sheet",
      name: "SDXL (sheet)",
      type: "txt2img",
      sheet_layout: true,
      // The text encoder comes first and is of another family: the LoRAs
      // still follow the checkpoint.
      models: [
        { kind: "clip", base_model_family: "qwen", base_model: "Qwen" },
        { kind: "checkpoint", base_model_family: "sdxl", base_model: "SDXL" },
      ],
    };
    gen.listWorkflowCards
      .mockResolvedValueOnce({ cards: [MOSS_CARD, sdxl, flux] })
      .mockResolvedValue({ cards: [MOSS_CARD, sdxl, flux, copy] });
    gen.sheetCopyWorkflow.mockResolvedValue({ workflow_id: copy.id });
    const wrapper = mountDialog();
    await flushPromises();
    const select = (label) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === label);
    // MOSS reads the layout itself; every other text-to-image card can be copied.
    expect(select("Copy").props("options").map((o) => o.value)).toEqual([
      "auto:flux",
      "auto:sdxl",
    ]);
    select("Copy").vm.$emit("update:modelValue", "auto:sdxl");
    await flushPromises();
    const offer = wrapper.find('[data-testid="cs-convert-text"]');
    expect(offer.text()).toContain("Make a sheet workflow from SDXL");
    await offer.find("button").trigger("click");
    await flushPromises();
    expect(gen.sheetCopyWorkflow).toHaveBeenCalledWith("auto:sdxl");
    // The copy is chosen, and the LoRA follows its model.
    expect(select("Sheet workflow").props("modelValue")).toBe(copy.id);
    expect(select("Character LoRA · optional").props("modelValue")).toBe("sdxl1");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.buildSheetPrompt).toHaveBeenLastCalledWith(
      expect.objectContaining({ layout: "regions" }),
    );
    expect(gen.runWorkflowCard).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workflow_id: copy.id,
        add_loras: [expect.objectContaining({ sha256: "sdxl1" })],
      }),
    );
    wrapper.unmount();
  });

  it("draws, ranks, and files the chosen sheet as new pictures in the ranking", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({
      loras: [
        // Newest, but for another model: listed, never preselected.
        { sha256: "sdxl", display_name: "Anna SDXL", base_model_family: "sdxl", added_at: "2026-11-09" },
        { sha256: "moss1", display_name: "Anna v1", base_model_family: "moss_image", trigger_words: ["anna"], added_at: "2026-11-08" },
      ],
    });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper
      .find('[data-testid="cs-description"] textarea')
      .setValue("Adult woman, copper bob");
    await wrapper
      .find('[data-testid="cs-background"] input')
      .setValue("a sunlit park");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();

    // No description on the character: the typed one becomes hers.
    expect(gen.patchCharacter).toHaveBeenCalledWith(7, {
      description: "Adult woman, copper bob",
    });
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({
        trigger: "anna",
        framing: "full_body",
        size: "large",
        background: "a sunlit park",
      }),
    );
    expect(gen.runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({
        workflow_id: "manual:moss",
        prompt: "{layout}",
        count: 4,
        character_ids: [],
        hold: true,
        values: [
          { slot_label: "lat", input_name: "width", value: 2560 },
          { slot_label: "lat", input_name: "height", value: 1536 },
        ],
        add_loras: [{ sha256: "moss1", strength_model: 0.8, strength_clip: 0.8 }],
      }),
    );
    expect(wrapper.find('[data-testid="cts-steps"]').text()).toContain(
      "with Anna v1",
    );

    gen.getRunOutcome.mockImplementation(async (key) => ({
      status: "completed",
      picture_ids: key === "r1" ? [501] : [502],
    }));
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(501, 0.5), sheet(502, 0.66)],
      references: 0,
    });
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    const sheets = wrapper.findAll('[data-testid="cs-sheet"]');
    // Each in its run's place, never reordered; the best is preselected there.
    expect(sheets.map((b) => b.attributes("aria-label"))).toEqual([
      "Sheet 1: faces agree somewhat",
      "Sheet 2: faces agree strongly",
    ]);
    expect(sheets[1].attributes("aria-pressed")).toBe("true");
    expect(wrapper.find('[data-testid="cts-generate-primary"]').text()).toContain(
      "Use sheet 2",
    );

    gen.useSheets.mockResolvedValue({ picture_ids: [601, 602] });
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(601, null), sheet(602, null)],
      references: 0,
    });
    const after = stills(3);
    after.picks[0].picture_id = 601;
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: after }),
    );
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.useSheets).toHaveBeenCalledWith(7, [502]);
    expect(wrapper.find('[data-testid="cts-picks"]').isVisible()).toBe(true);
    expect(wrapper.findAll('[data-testid="cts-new"]')).toHaveLength(1);

    // "New only" keeps just what was made here.
    const newOnly = wrapper
      .find('[data-testid="cts-new-only"]')
      .findAll("button")
      .find((b) => b.text() === "New only");
    await newOnly.trigger("click");
    expect(shownIds(wrapper)).toEqual(["/thumb/601.webp"]);

    // Back to Sheets through the step row: the sheets are gone, so a new draw
    // starts, and the step is live, not left filing.
    await wrapper.find("button.cts-stp__s").trigger("click");
    expect(wrapper.find('[data-testid="cs-main"]').isVisible()).toBe(true);
    expect(wrapper.findAll('[data-testid="cs-sheet"]')).toHaveLength(0);
    expect(
      wrapper.find('[data-testid="cts-generate-primary"]').attributes("disabled"),
    ).toBeUndefined();
    wrapper.unmount();
  });

  it("stops waiting for faces that never come and lets the sheet be used", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501] });
    gen.rankSheets.mockResolvedValue({
      sheets: [{ ...sheet(501, null), faces_read: false, faces: 0 }],
      references: 0,
    });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10_000);
    const tile = () => wrapper.find('[data-testid="cs-sheet"]');
    expect(tile().attributes("aria-label")).toBe("Sheet 1: reading faces…");
    expect(tile().attributes("disabled")).toBeDefined();

    await vi.advanceTimersByTimeAsync(125_000);
    expect(tile().attributes("aria-label")).toBe("Sheet 1: faces not read");
    expect(tile().attributes("disabled")).toBeUndefined();
    // Polling has stopped.
    const calls = gen.rankSheets.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(gen.rankSheets.mock.calls.length).toBe(calls);
    await tile().trigger("click");
    expect(
      wrapper.find('[data-testid="cts-generate-primary"]').attributes("disabled"),
    ).toBeUndefined();
    wrapper.unmount();
  });

  it("adds Draw more to the sheets on show, but starts over on a new prompt", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    const runOf = { r1: [501], r2: [502], r3: [503] };
    gen.getRunOutcome.mockImplementation(async (key) => ({
      status: "completed",
      picture_ids: runOf[key],
    }));
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6)),
      references: 0,
    }));
    const drawn = () =>
      wrapper.findAll('[data-testid="cs-sheet"] img').map((i) => i.attributes("src"));
    const startsRun = (key) =>
      gen.runWorkflowCard.mockResolvedValueOnce({
        status: "success",
        prompts: [{ run_id: key, status: "queued" }],
      });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    startsRun("r1");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(drawn()).toEqual(["/thumb/501.webp"]);

    // The same prompt again: more of the same, side by side, and the new
    // place is scrolled into view below the sheets on show.
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = function () {
      scrolled(this.dataset.slot);
    };
    startsRun("r2");
    await button(wrapper, "Draw 4 more").trigger("click");
    await flushPromises();
    expect(scrolled).toHaveBeenLastCalledWith("r2");
    delete Element.prototype.scrollIntoView;
    await vi.advanceTimersByTimeAsync(3000);
    expect(drawn().sort()).toEqual(["/thumb/501.webp", "/thumb/502.webp"]);

    // Edited and drawn again: the earlier sheets are of another prompt.
    await button(wrapper, "Edit").trigger("click");
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("y");
    gen.buildSheetPrompt.mockResolvedValueOnce({
      prompt: "{another layout}",
      negative: "nsfw",
      width: 2560,
      height: 1536,
    });
    startsRun("r3");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    expect(drawn()).toEqual(["/thumb/503.webp"]);
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [503]);
    wrapper.unmount();
  });

  it("zooms a sheet in a pop-out over the screen, and Escape leaves only the pop-out", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501, 502] });
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(501, 0.7, { format: "png" }), sheet(502, 0.5, { format: "png" })],
      references: 0,
    });
    const popout = () => document.querySelector('[data-testid="zoom-popout"]');
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);

    // Clicking the sheet ticks it; only its magnifier zooms.
    expect(popout()).toBeNull();
    await wrapper.findAll('[data-testid="cs-zoom-open"]')[1].trigger("click");
    await flushPromises();
    expect(popout().querySelector("img").getAttribute("src")).toMatch(/\/pictures\/502\.png$/);
    // The pick stays as it was behind it: the dialog does not change.
    expect(wrapper.findAll('[data-testid="cs-sheet"]')).toHaveLength(2);

    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("cancelled")).toBeUndefined();
    expect(popout()).toBeNull();

    // Keep in the pop-out adds that sheet to the best one, ticked already, and
    // the kept sheets sit in the rail's grid.
    await wrapper.findAll('[data-testid="cs-zoom-open"]')[1].trigger("click");
    await flushPromises();
    const choice = () =>
      Object.fromEntries(
        [...popout().querySelectorAll('[role="radio"]')].map((b) => [
          b.querySelector(".seg__label").textContent,
          b.getAttribute("aria-checked"),
        ]),
      );
    expect(choice()).toEqual({ Keep: "false", Drop: "true" });
    [...popout().querySelectorAll('[role="radio"]')]
      .find((b) => b.textContent.includes("Keep"))
      .click();
    await flushPromises();
    expect(choice()).toEqual({ Keep: "true", Drop: "false" });
    expect(wrapper.find('[data-testid="cts-generate-primary"]').text()).toContain(
      "Use 2 sheets",
    );
    expect(wrapper.findAll('[data-testid="cs-kept"] li')).toHaveLength(2);

    // Left steps to the sheet before, in its place.
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    await flushPromises();
    expect(popout().querySelector("img").getAttribute("src")).toMatch(/\/pictures\/501\.png$/);
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();

    // A plain wheel scrolls the sheets either way; Ctrl+wheel up opens one.
    await wrapper.findAll('[data-testid="cs-sheet"]')[0].trigger("wheel", { deltaY: 100 });
    expect(popout()).toBeNull();
    await wrapper.findAll('[data-testid="cs-sheet"]')[0].trigger("wheel", { deltaY: -100 });
    await flushPromises();
    expect(popout()).toBeNull();
    wrapper
      .findAll('[data-testid="cs-sheet"]')[0]
      .element.dispatchEvent(
        new window.WheelEvent("wheel", { deltaY: -100, ctrlKey: true, cancelable: true }),
      );
    await flushPromises();
    expect(popout().querySelector("img").getAttribute("src")).toMatch(/\/pictures\/501\.png$/);
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(popout()).toBeNull();
    wrapper.unmount();
  });

  it("says a sheet may not cut cleanly, keeps a clean one first, and draws its cut", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501, 502] });
    const cells = [
      [0, 0, 0.25, 0.5],
      [0.25, 0, 0.5, 0.5],
      [0, 0.5, 0.25, 1],
      [0.25, 0.5, 0.5, 1],
      [0.5, 0, 0.75, 1],
      [0.75, 0, 1, 1],
    ];
    gen.rankSheets.mockResolvedValue({
      sheets: [
        // The better faces, but no clean line between two of its photos.
        sheet(501, 0.7, { format: "png", cuts_cleanly: false, cells }),
        sheet(502, 0.5, { format: "png", cuts_cleanly: true, cells }),
      ],
      references: 0,
    });
    const popout = () => document.querySelector('[data-testid="zoom-popout"]');
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);

    const tiles = wrapper.findAll('[data-testid="cs-sheet"]');
    expect(tiles.map((t) => t.attributes("aria-label"))).toEqual([
      "Sheet 1: faces agree strongly · may not cut cleanly",
      "Sheet 2: faces agree somewhat",
    ]);
    // The sheet kept for the user is the clean one, though the other's faces
    // agree better.
    expect(tiles.map((t) => t.attributes("aria-pressed"))).toEqual(["false", "true"]);
    // Still a sheet to keep: a warning, not a refusal.
    await tiles[0].trigger("click");
    expect(wrapper.find('[data-testid="cts-generate-primary"]').text()).toContain(
      "Use 2 sheets",
    );

    // Zoomed, the six photos it cuts are drawn on it, in the warning colour.
    await wrapper.findAll('[data-testid="cs-zoom-open"]')[0].trigger("click");
    await flushPromises();
    const marks = [...popout().querySelectorAll('[data-testid="cs-cell"]')];
    expect(marks).toHaveLength(6);
    expect(marks[4].style.left).toBe("50%");
    expect(marks[4].style.width).toBe("25%");
    expect(marks.every((m) => m.classList.contains("cs-cell--unclean"))).toBe(true);
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    await flushPromises();
    const clean = [...popout().querySelectorAll('[data-testid="cs-cell"]')];
    expect(clean).toHaveLength(6);
    expect(clean.some((m) => m.classList.contains("cs-cell--unclean"))).toBe(false);
    popout().dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    wrapper.unmount();
  });

  it("warns, without refusing, when the sheets kept look like different people", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    const keepBoth = async (ranked) => {
      sheetsWorld();
      gen.runWorkflowCard.mockResolvedValue({
        status: "success",
        prompts: [{ run_id: "r1", status: "queued" }],
      });
      gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501, 502] });
      gen.rankSheets.mockResolvedValue({ sheets: ranked, references: 0 });
      const wrapper = mountDialog();
      await flushPromises();
      await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
      await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
      await vi.advanceTimersByTimeAsync(3000);
      return wrapper;
    };
    const lead = (w) => w.find('[data-testid="cts-generate-lead"]');

    let wrapper = await keepBoth([
      sheet(501, 0.5, { different_from: [502] }),
      sheet(502, 0.7, { different_from: [501] }),
    ]);
    // One kept: nothing to mix.
    expect(lead(wrapper).classes()).not.toContain("cts-lead--warn");
    await wrapper.findAll('[data-testid="cs-sheet"]')[0].trigger("click");
    expect(lead(wrapper).text()).toContain(
      "Sheets 1 and 2 look like different people",
    );
    expect(lead(wrapper).classes()).toContain("cts-lead--warn");
    const primary = wrapper.find('[data-testid="cts-generate-primary"]');
    expect(primary.text()).toContain("Use 2 sheets");
    expect(primary.attributes("disabled")).toBeUndefined();
    wrapper.unmount();

    // The positive control: two sheets of one person, kept together, pass.
    wrapper = await keepBoth([sheet(501, 0.5), sheet(502, 0.7)]);
    await wrapper.findAll('[data-testid="cs-sheet"]')[0].trigger("click");
    expect(lead(wrapper).text()).toContain("become Anna's pictures");
    expect(lead(wrapper).classes()).not.toContain("cts-lead--warn");
    wrapper.unmount();
  });

  it("keeps each sheet in its run's place: generating, displaying, queued", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: ["r1", "r2", "r3", "r4"].map((run_id) => ({ run_id, status: "queued" })),
    });
    const done = {};
    gen.getRunOutcome.mockImplementation(async (key) =>
      done[key] ? { status: "completed", picture_ids: [done[key]] } : { status: "running" },
    );
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6)),
      references: 0,
    }));
    const tasks = useTasksStore();
    tasks.comfyuiRuns = [
      { run_id: "r1", status: "running", percent: 40 },
      { run_id: "r2", status: "queued", percent: 0 },
    ];
    const places = () =>
      wrapper
        .findAll(".cs-sheets > li")
        .map((li) =>
          li.find("img").exists()
            ? li.find("img").attributes("src")
            : li.find('[data-testid="cs-slot"] > span').text(),
        );
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");

    // The second run finishes first: its sheet takes the second place.
    done.r2 = 502;
    await vi.advanceTimersByTimeAsync(3000);
    expect(places()).toEqual(["generating", "/thumb/502.webp", "queued", "queued"]);
    const bar = wrapper.find('[role="progressbar"]');
    expect(bar.attributes("aria-valuenow")).toBe("40");

    // ComfyUI is done with r1 and PixlStash imports it: never "queued" again.
    tasks.comfyuiRuns = [
      { run_id: "r1", status: "running", percent: 100, generated: true },
      { run_id: "r3", status: "queued", percent: 0 },
    ];
    await flushPromises();
    expect(places()).toEqual(["displaying", "/thumb/502.webp", "queued", "queued"]);
    expect(wrapper.find('[role="progressbar"]').exists()).toBe(false);
    // Imported, gone from the runs in flight, its outcome not read yet.
    tasks.comfyuiRuns = [{ run_id: "r3", status: "queued", percent: 0 }];
    await flushPromises();
    expect(places()).toEqual(["displaying", "/thumb/502.webp", "queued", "queued"]);

    done.r1 = 501;
    done.r4 = 504;
    await vi.advanceTimersByTimeAsync(3000);
    expect(places()).toEqual([
      "/thumb/501.webp",
      "/thumb/502.webp",
      "queued",
      "/thumb/504.webp",
    ]);
    expect(wrapper.find('[aria-label="Zoom in on sheet 4"]').exists()).toBe(true);
    wrapper.unmount();
  });

  it("stops one sheet's run from its place, and the others keep drawing", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: ["r1", "r2", "r3"].map((run_id) => ({ run_id, status: "queued" })),
    });
    const stopped = new Set();
    gen.getRunOutcome.mockImplementation(async (key) =>
      stopped.has(key)
        ? { status: "failed", stopped: true, message: "Stopped." }
        : { status: "running" },
    );
    gen.rankSheets.mockResolvedValue({ sheets: [], references: 0 });
    const tasks = useTasksStore();
    tasks.comfyuiRuns = [
      { run_id: "r1", status: "running", percent: 100, generated: true },
      { run_id: "r2", status: "queued", percent: 0 },
      { run_id: "r3", status: "queued", percent: 0 },
    ];
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    const labels = () =>
      wrapper.findAll('[data-testid="cs-slot"] > span').map((label) => label.text());
    const stops = () =>
      wrapper.findAll('[data-testid="cs-stop-run"]').map((b) => b.attributes("aria-label"));
    // Done drawing and being imported, the first has nothing left to stop.
    expect(labels()).toEqual(["displaying", "queued", "queued"]);
    expect(stops()).toEqual(["Stop generation of sheet 2", "Stop generation of sheet 3"]);

    // A stop that stopped nothing leaves the place as it was, stop and all.
    gen.stopRun.mockResolvedValueOnce({ stopped: 0 });
    await wrapper.find('[aria-label="Stop generation of sheet 3"]').trigger("click");
    await flushPromises();
    expect(labels()).toEqual(["displaying", "queued", "queued"]);
    gen.stopRun.mockClear();

    await wrapper.find('[aria-label="Stop generation of sheet 2"]').trigger("click");
    // That run alone, never ComfyUI's whole queue.
    expect(gen.stopRun.mock.calls).toEqual([["r2"]]);
    await flushPromises();
    expect(labels()).toEqual(["displaying", "stopping", "queued"]);
    stopped.add("r2");
    await vi.advanceTimersByTimeAsync(3000);
    expect(labels()).toEqual(["displaying", "stopped", "queued"]);
    expect(stops()).toEqual(["Stop generation of sheet 3"]);
    // Stopped on purpose is not a failure to warn about.
    expect(wrapper.find('[data-testid="cs-failed"]').exists()).toBe(false);

    // Drawing on ComfyUI, a run can still be stopped from its place (the
    // trailing "" is its progress bar).
    tasks.comfyuiRuns = [
      { run_id: "r1", status: "running", percent: 100, generated: true },
      { run_id: "r3", status: "running", percent: 40 },
    ];
    await flushPromises();
    expect(labels()).toEqual(["displaying", "stopped", "generating", ""]);
    expect(stops()).toEqual(["Stop generation of sheet 3"]);

    // The dialog's Cancel: every run not ended, and not the stopped one again.
    gen.stopRun.mockClear();
    wrapper.findComponent(CharacterSheetStep).vm.stopAll();
    await flushPromises();
    expect(gen.stopRun.mock.calls.flat().sort()).toEqual(["r1", "r3"]);
    wrapper.unmount();
  });

  it("hands over a cut sheet even when reading its faces fails", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501] });
    gen.rankSheets.mockResolvedValue({ sheets: [sheet(501, 0.7)], references: 0 });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);

    gen.useSheets.mockResolvedValue({ picture_ids: [601] });
    gen.rankSheets.mockRejectedValue(new Error("network down"));
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(95_000);
    expect(gen.useSheets).toHaveBeenCalledTimes(1);
    // The picker took over: the cut is not offered again.
    expect(wrapper.find('[data-testid="cts-picks"]').isVisible()).toBe(true);
    expect(wrapper.find('[data-testid="cs-main"]').isVisible()).toBe(false);
    wrapper.unmount();
  });

  it("keeps a character's own description and starts a picker from Generate more", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    sheetsWorld({ description: "Woman in her thirties, dark hair" });
    const wrapper = mountDialog();
    await flushPromises();
    // Pictures exist: the picker first, generation one click away.
    expect(wrapper.find('[data-testid="cts-steps"]').exists()).toBe(false);
    await wrapper.find('[data-testid="cts-generate"]').trigger("click");
    await flushPromises();
    expect(
      wrapper.find('[data-testid="cs-description"] textarea').element.value,
    ).toBe("Woman in her thirties, dark hair");
    expect(wrapper.find('[data-testid="cs-likeness"]').text()).toContain(
      "drawn from words, not from Anna's 300 pictures",
    );
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.patchCharacter).not.toHaveBeenCalled();
    expect(gen.runWorkflowCard).toHaveBeenCalledWith(
      expect.objectContaining({ add_loras: [] }),
    );
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ framing: "mid_thigh", trigger: "" }),
    );
    await wrapper.find('[data-testid="cts-generate-cancel"]').trigger("click");
    expect(wrapper.find('[data-testid="cts-picks"]').isVisible()).toBe(true);
    wrapper.unmount();
  });

  // ── Drawn from a reference picture (#2679) ──

  const QWEN_SHEET = {
    id: "manual:qwen-sheet",
    name: "Qwen edit (sheet)",
    type: "img2img",
    reference_inputs: 1,
    models: [{ kind: "unet", base_model_family: "qwen" }],
  };
  const QWEN_EDIT = {
    id: "auto:qwen-edit",
    name: "Qwen edit",
    type: "img2img",
    reference_inputs: 0,
    models: [{ kind: "unet", base_model_family: "qwen" }],
  };
  // Both load a Qwen text encoder; only the diffusion model says what draws.
  const KLEIN_SHEET = {
    id: "manual:klein-sheet",
    name: "Klein edit (sheet)",
    type: "img2img",
    reference_inputs: 1,
    last_used: "2026-11-10T20:00:00Z",
    models: [
      { kind: "clip", base_model_family: "qwen" },
      { kind: "unet", base_model_family: "flux2", base_model: "FLUX.2 Klein 9B" },
    ],
  };
  const KREA_REFERENCE = {
    id: "manual:krea-style",
    name: "Krea 2: style reference",
    type: "txt2img",
    reference_inputs: 1,
    last_used: "2026-11-11T20:00:00Z",
    models: [
      { kind: "clip", base_model_family: "qwen" },
      { kind: "unet", base_model_family: "krea2" },
      { kind: "vae", base_model_family: "qwen" },
    ],
  };

  /** sheetsWorld plus reference faces and what the library's cards are. */
  function referenceWorld({ cards = [MOSS_CARD, QWEN_SHEET], faces = [31, 32] } = {}) {
    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.getReferencePictures.mockResolvedValue({ reference_picture_ids: faces });
    gen.listWorkflowCards.mockResolvedValue({ cards });
    // Its picture input is open until the draw fills it.
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [
        {
          reasons: [{ code: "picture_input_unfilled" }],
          picture_inputs: [
            { slot_label: "load", input_name: "image", mode: "picker" },
            { slot_label: "pinned", input_name: "image", mode: "fixed" },
          ],
          prompt: { positive_settable: true },
        },
      ],
    });
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({
      status: "completed",
      picture_ids: [501, 502],
    });
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
  }

  const mode = (wrapper) =>
    wrapper.find('[data-testid="cs-mode"] [aria-checked="true"]');
  const draw = (wrapper) =>
    wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");

  it("offers the reference mode only where a reference workflow exists", async () => {
    // No such card: no control, and words alone draw.
    referenceWorld({ cards: [MOSS_CARD] });
    let wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-mode"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="cs-reference"]').exists()).toBe(false);
    wrapper.unmount();

    // A card, but a character with no pictures: offered, and text is chosen.
    referenceWorld({ faces: [] });
    wrapper = mountDialog();
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Text");
    expect(wrapper.find('[data-testid="cs-reference-warning"]').exists()).toBe(false);
    wrapper.unmount();

    // A card and a face: drawn from the first reference face, with the warning.
    referenceWorld();
    wrapper = mountDialog();
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Reference and text");
    expect(wrapper.find('[data-testid="cs-reference"]').attributes("src")).toBe(
      "/thumb/31.webp",
    );
    expect(wrapper.find('[data-testid="cs-reference-warning"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="cs-likeness"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("offers the mode's workflows without waiting for the other mode's checks", async () => {
    referenceWorld();
    const answer = await gen.preflightWorkflowRun();
    gen.preflightWorkflowRun.mockClear();
    // The text card's check never comes back.
    gen.preflightWorkflowRun.mockImplementation(async (body) =>
      body.workflow_id === MOSS_CARD.id ? new Promise(() => {}) : answer,
    );
    const wrapper = mountDialog();
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Reference and text");
    // The mode on show is asked first.
    expect(gen.preflightWorkflowRun.mock.calls[0][0].workflow_id).toBe(QWEN_SHEET.id);
    const select = wrapper
      .findAllComponents({ name: "AppSelect" })
      .find((c) => c.props("label") === "Sheet workflow");
    expect(select.props("options").map((o) => o.value)).toEqual([QWEN_SHEET.id]);
    expect(select.props("modelValue")).toBe(QWEN_SHEET.id);
    wrapper.unmount();
  });

  it("feeds the reference through the run's inputs and ranks against it", async () => {
    referenceWorld();
    gen.rankSheets.mockResolvedValue({
      sheets: [
        // Both close; the first agrees with itself far better.
        sheet(501, 0.95, { closeness: 0.5, close: true }),
        sheet(502, 0.5, { closeness: 0.74, close: true }),
      ],
      references: 1,
    });
    const wrapper = mountDialog();
    await flushPromises();
    await draw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ reference: true, layout: "prose" }),
    );
    const body = gen.runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe("manual:qwen-sheet");
    // The open input takes the picture; the pinned one keeps its pin.
    expect(body.inputs).toEqual([
      { slot_label: "load", input_name: "image", picture_id: 31 },
    ]);
    expect(body.hold).toBe(true);
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [501, 502], [31]);
    // Closeness decides: the closer sheet is kept, though its faces agree less.
    const sheets = wrapper.findAll('[data-testid="cs-sheet"]');
    expect(sheets.map((b) => b.attributes("aria-pressed"))).toEqual(["false", "true"]);
    expect(sheets[1].attributes("aria-label")).toBe(
      "Sheet 2: 0.74 close to the reference · faces agree somewhat",
    );
    expect(wrapper.findAll('[data-testid="cs-closeness"]')[0].text()).toContain(
      "0.50close to the reference",
    );
    // The picture stays in the rail beside the sheets.
    expect(wrapper.find('[data-testid="cs-drawn-from"]').attributes("src")).toBe(
      "/thumb/31.webp",
    );
    expect(wrapper.find('[data-testid="cs-none-close"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("compares the sheets with the reference cluster when switched", async () => {
    referenceWorld();
    gen.rankSheets.mockImplementation(async (_id, ids, refs) => ({
      // Against the picture neither sheet is close; against the cluster both.
      sheets: ids.map((id) =>
        sheet(id, 0.7, { closeness: refs ? 0.3 : 0.6, close: !refs }),
      ),
      references: refs ? 1 : 2,
    }));
    const wrapper = mountDialog();
    await flushPromises();
    await draw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [501, 502], [31]);
    const compare = () =>
      wrapper.find('[data-testid="cs-compare-to"] [aria-checked="true"]');
    expect(compare().text()).toBe("This picture");
    expect(wrapper.find('[data-testid="cs-none-close"]').text()).toContain(
      "came close to the reference",
    );

    const cluster = wrapper
      .findAll('[data-testid="cs-compare-to"] [role="radio"]')
      .find((b) => b.text() === "Reference cluster");
    await cluster.trigger("click");
    await flushPromises();
    expect(compare().text()).toBe("Reference cluster");
    // Ranked again without the picture: the server takes the cluster.
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [501, 502]);
    expect(wrapper.findAll('[data-testid="cs-sheet"]')[0].attributes("aria-label")).toBe(
      "Sheet 1: 0.60 close to Anna · faces agree strongly",
    );
    expect(wrapper.find('[data-testid="cs-none-close"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("scores each photo of a sheet on its own, under its face", async () => {
    const photo = (panel, closeness, agreement) => ({
      panel,
      box: [0.25, 0.2, 0.75, 0.4],
      agreement,
      agrees: agreement == null ? null : agreement >= 0.45,
      closeness,
      close: closeness == null ? null : closeness >= 0.45,
    });
    referenceWorld();
    gen.rankSheets.mockResolvedValue({
      sheets: [
        sheet(501, 0.6, {
          closeness: 0.5,
          close: true,
          panels: [photo(0, 0.62, 0.3), photo(3, 0.31, 0.7), photo(4, null, 0.6)],
        }),
      ],
      references: 1,
    });
    const wrapper = mountDialog();
    await flushPromises();
    await draw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    // Against the reference: each photo's closeness, the one that drifts
    // marked; a photo with no closeness shows nothing.
    const scores = wrapper.findAll('[data-testid="cs-photo-score"]');
    expect(scores.map((s) => s.text())).toEqual(["0.62", "0.31"]);
    expect(scores.map((s) => s.classes().includes("cs-pscore--off"))).toEqual([
      false,
      true,
    ]);
    // Under the face: its centre, its bottom edge.
    expect(scores[0].attributes("style")).toContain("left: 50%");
    expect(wrapper.find('[data-testid="cs-sheet"]').attributes("aria-label")).toBe(
      "Sheet 1: 0.50 close to the reference · faces agree strongly · photo 4 drifts",
    );
    wrapper.unmount();
  });

  it("scores each photo against the sheet's others with nothing to compare it to", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld();
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ run_id: "r1", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501] });
    gen.rankSheets.mockResolvedValue({
      sheets: [
        sheet(501, 0.6, {
          panels: [
            { panel: 1, box: [0, 0, 1, 1], agreement: 0.7, agrees: true, closeness: null, close: null },
            { panel: 5, box: [0, 0, 1, 1], agreement: 0.2, agrees: false, closeness: null, close: null },
          ],
        }),
      ],
      references: 0,
    });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);
    const scores = wrapper.findAll('[data-testid="cs-photo-score"]');
    expect(scores.map((s) => s.text())).toEqual(["0.70", "0.20"]);
    expect(scores[1].classes()).toContain("cs-pscore--off");
    expect(wrapper.find('[data-testid="cs-sheet"]').attributes("aria-label")).toBe(
      "Sheet 1: faces agree strongly · photo 6 drifts",
    );
    wrapper.unmount();
  });

  it("keeps no sheet for the user when none comes close to the reference", async () => {
    referenceWorld();
    gen.rankSheets.mockResolvedValue({
      sheets: [
        sheet(501, 0.7, { closeness: 0.31, close: false }),
        sheet(502, 0.6, { closeness: 0.4, close: false }),
      ],
      references: 1,
    });
    const wrapper = mountDialog();
    await flushPromises();
    await draw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    expect(
      wrapper.findAll('[data-testid="cs-sheet"]').map((b) => b.attributes("aria-pressed")),
    ).toEqual(["false", "false"]);
    expect(wrapper.find('[data-testid="cs-none-close"]').text()).toContain(
      "None of the 2 sheets came close to the reference",
    );
    expect(wrapper.findAll('[data-testid="cs-closeness"]')[0].text()).toContain(
      "drifts from the reference",
    );
    wrapper.unmount();
  });

  it("starts a fresh step from the choices last made; a saved build keeps its own", async () => {
    const KREA = {
      id: "manual:krea",
      name: "Krea sheet",
      type: "txt2img",
      last_used: "2026-11-10T20:00:00Z",
      models: [{ kind: "checkpoint", base_model_family: "krea2" }],
    };
    const open = async (building, faces) => {
      referenceWorld({ cards: [MOSS_CARD, KREA, QWEN_SHEET], faces });
      // The text cards run as they are; only the sheet card waits for its picture.
      const reference = gen.preflightWorkflowRun.getMockImplementation();
      gen.preflightWorkflowRun.mockImplementation(async (body, ...rest) =>
        body.workflow_id === QWEN_SHEET.id
          ? reference(body, ...rest)
          : { ok: true, groups: [{ reasons: [] }] },
      );
      gen.listAdapters.mockResolvedValue([
        { sha256: "moss1", display_name: "Anna v1", base_model_family: "moss_image", added_at: "2026-11-08" },
      ]);
      const wrapper = mountDialog(building);
      await flushPromises();
      return wrapper;
    };
    const select = (wrapper, label) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === label);
    const on = (wrapper, group) =>
      wrapper.find(`[aria-label="${group}"] [aria-checked="true"]`).text();
    const click = (wrapper, group, text) =>
      wrapper
        .findAll(`[aria-label="${group}"] button`)
        .find((b) => b.text() === text)
        .trigger("click");

    // The defaults: a face draws from the reference, with the newest card.
    let wrapper = await open();
    expect(mode(wrapper).text()).toBe("Reference and text");
    wrapper.unmount();
    // A default is not a choice: a character with no face still draws from words.
    wrapper = await open(undefined, []);
    expect(mode(wrapper).text()).toBe("Text");
    wrapper.unmount();

    wrapper = await open();
    await click(wrapper, "Draw from", "Text");
    await flushPromises();
    expect(select(wrapper, "Sheet workflow").props("modelValue")).toBe("manual:krea");
    select(wrapper, "Sheet workflow").vm.$emit("update:modelValue", "manual:moss");
    await flushPromises();
    expect(select(wrapper, "Character LoRA · optional").props("modelValue")).toBe("moss1");
    select(wrapper, "Character LoRA · optional").vm.$emit("update:modelValue", "");
    await click(wrapper, "Sheet size", "Small");
    await click(wrapper, "Sheets to draw", "2");
    await flushPromises();
    wrapper.unmount();

    // A new set: the same mode, workflow, LoRA, size and count.
    wrapper = await open();
    expect(mode(wrapper).text()).toBe("Text");
    expect(select(wrapper, "Sheet workflow").props("modelValue")).toBe("manual:moss");
    expect(select(wrapper, "Character LoRA · optional").props("modelValue")).toBe("");
    expect(on(wrapper, "Sheet size")).toBe("Small");
    expect(on(wrapper, "Sheets to draw")).toBe("2");
    await click(wrapper, "Draw from", "Reference and text");
    await flushPromises();
    wrapper.unmount();

    // A remembered reference mode needs a face to draw from.
    wrapper = await open(undefined, []);
    expect(mode(wrapper).text()).toBe("Text");
    wrapper.unmount();
    wrapper = await open();
    expect(mode(wrapper).text()).toBe("Reference and text");
    wrapper.unmount();

    // A set with a build of its own opens as it was left, not as last chosen.
    wrapper = await open({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: {
        phase: "describe",
        form: { description: "Adult woman", size: "large", count: 4 },
        mode: "reference",
        workflow_id: QWEN_SHEET.id,
      },
    });
    expect(mode(wrapper).text()).toBe("Reference and text");
    expect(select(wrapper, "Sheet workflow").props("modelValue")).toBe(QWEN_SHEET.id);
    expect(on(wrapper, "Sheet size")).toBe("Large");
    expect(on(wrapper, "Sheets to draw")).toBe("4");
    wrapper.unmount();

    // Opening a saved build is not choosing: it leaves the choices as they were.
    wrapper = await open({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: {
        phase: "describe",
        form: { description: "Adult woman" },
        mode: "text",
        workflow_id: KREA.id,
        lora_sha: "moss1",
        strength: 1.2,
      },
    });
    expect(select(wrapper, "Sheet workflow").props("modelValue")).toBe(KREA.id);
    wrapper.unmount();
    wrapper = await open();
    expect(select(wrapper, "Character LoRA · optional").props("modelValue")).toBe("");
    await click(wrapper, "Draw from", "Text");
    await flushPromises();
    expect(select(wrapper, "Sheet workflow").props("modelValue")).toBe("manual:moss");
    wrapper.unmount();
  });

  it("starts the clothes and background from the last draw and offers the earlier ones", async () => {
    localStorage.setItem(
      "pixlstash:characterSheetChoices",
      JSON.stringify({
        recent: {
          top: ["a red coat", "a grey hoodie"],
          trousers: ["", "dark jeans"],
          background: ["a sunlit park", "a beach"],
        },
      }),
    );
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ loras: [] });
    const wrapper = mountDialog();
    await flushPromises();
    const input = (testid) => wrapper.find(`[data-testid="${testid}"] input`);
    expect(input("cs-top").element.value).toBe("a red coat");
    // The last draw left Bottoms empty, and an empty choice is kept as one.
    expect(wrapper.find('input[placeholder^="dark straight jeans"]').element.value).toBe("");
    expect(input("cs-background").element.value).toBe("a sunlit park");

    // Typing offers the earlier values that match, never the empty one.
    await input("cs-top").setValue("gr");
    await flushPromises();
    const items = () =>
      [...document.querySelectorAll(".bmi-item")].map((el) =>
        el.firstChild.textContent.trim(),
      );
    expect(items()).toEqual(["a grey hoodie"]);
    await input("cs-top").setValue("A Red Coat");

    await wrapper
      .find('[data-testid="cs-description"] textarea')
      .setValue("Adult woman, copper bob");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ top: "A Red Coat", background: "a sunlit park" }),
    );
    // What was drawn with goes first, once (case aside); the rest keep their order.
    const { recent } = JSON.parse(
      localStorage.getItem("pixlstash:characterSheetChoices"),
    );
    expect(recent.top).toEqual(["A Red Coat", "a grey hoodie"]);
    expect(recent.background).toEqual(["a sunlit park", "a beach"]);
    expect(recent.trousers).toEqual(["", "dark jeans"]);
    wrapper.unmount();
  });

  it("starts a new pick when the reference picture is changed", async () => {
    referenceWorld();
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      sheets: ids.map((id) => sheet(id, 0.6, { closeness: 0.7, close: true })),
      references: 1,
    }));
    const wrapper = mountDialog();
    await flushPromises();
    await draw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    expect(wrapper.findAll('[data-testid="cs-sheet"]')).toHaveLength(2);

    await button(wrapper, "Draw from another").trigger("click");
    await wrapper.findAll('[data-testid="cs-reference-choice"]')[1].trigger("click");
    gen.runWorkflowCard.mockResolvedValueOnce({
      status: "success",
      prompts: [{ run_id: "r2", status: "queued" }],
    });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [503] });
    await draw(wrapper);
    await vi.advanceTimersByTimeAsync(3000);
    expect(gen.runWorkflowCard.mock.calls[1][0].inputs[0].picture_id).toBe(32);
    expect(
      wrapper.findAll('[data-testid="cs-sheet"] img').map((i) => i.attributes("src")),
    ).toEqual(["/thumb/503.webp"]);
    expect(gen.rankSheets).toHaveBeenLastCalledWith(7, [503], [32]);
    wrapper.unmount();
  });

  it("hands focus back to the pick button when the picture picker closes", async () => {
    referenceWorld();
    // The picker itself reads the library when it opens; only its events matter.
    const wrapper = mount(TrainingSetBuild, {
      props: {
        set: { id: 55, name: "Anna training set", building: { v: 1, stage: "pick" } },
        character: { id: 7, name: "Anna" },
      },
      global: { stubs: { teleport: true, PicturePicker: true } },
      attachTo: document.body,
    });
    await flushPromises();
    const pick = () => wrapper.find('[data-testid="cs-reference-pick"]');
    for (const [event, payload] of [["close"], ["pick", { id: 77 }]]) {
      await pick().trigger("click");
      await flushPromises();
      // Focus is inside the picker while it is open, not on the button.
      pick().element.blur();
      wrapper.findComponent(PicturePicker).vm.$emit(event, payload);
      await flushPromises();
      expect(wrapper.findComponent(PicturePicker).exists()).toBe(false);
      expect(document.activeElement).toBe(pick().element);
    }
    expect(wrapper.find('[data-testid="cs-reference"]').attributes("src")).toBe(
      "/thumb/77.webp",
    );
    wrapper.unmount();
  });

  it("refuses a reference workflow with nowhere to put the picture or the prompt", async () => {
    referenceWorld();
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: true,
      groups: [{ reasons: [], picture_inputs: [], prompt: { positive_settable: true } }],
    });
    let wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "no picture input",
    );
    wrapper.unmount();

    referenceWorld();
    gen.preflightWorkflowRun.mockResolvedValue({
      ok: true,
      groups: [
        {
          reasons: [],
          picture_inputs: [{ slot_label: "load", input_name: "image", mode: "picker" }],
          prompt: { positive_settable: false },
        },
      ],
    });
    wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "no prompt",
    );
    expect(
      wrapper.find('[data-testid="cts-generate-primary"]').attributes("disabled"),
    ).toBeDefined();
    wrapper.unmount();
  });

  it("lists Qwen and Klein sheet workflows, never Krea 2's style reference (#2700)", async () => {
    referenceWorld({ cards: [MOSS_CARD, QWEN_SHEET, KREA_REFERENCE, KLEIN_SHEET] });
    gen.listAdapters.mockResolvedValue([
      { sha256: "dev", display_name: "Anna FLUX.2 dev", base_model_family: "flux2", base_model_canonical: "FLUX.2", added_at: "2026-11-09" },
      { sha256: "klein", display_name: "Anna Klein", base_model_family: "flux2", base_model_canonical: "FLUX.2 Klein", added_at: "2026-11-08" },
      { sha256: "qwen", display_name: "Anna Qwen", base_model_family: "qwen", added_at: "2026-11-07" },
    ]);
    const wrapper = mountDialog();
    await flushPromises();
    // The shipped sheet workflows are cards from start-up: the step files none.
    expect(gen.cardForWorkflow).not.toHaveBeenCalled();
    const select = (label) =>
      wrapper.findAllComponents({ name: "AppSelect" }).find((c) => c.props("label") === label);
    const workflow = select("Sheet workflow");
    expect(workflow.props("options").map((o) => o.value)).toEqual([
      KLEIN_SHEET.id,
      QWEN_SHEET.id,
    ]);
    // Klein takes Klein LoRAs only: not FLUX.2 dev's, not Qwen's.
    const usable = () =>
      select("Character LoRA · optional")
        .props("options")
        .filter((o) => o.value && !o.disabled)
        .map((o) => o.value);
    expect(workflow.props("modelValue")).toBe(KLEIN_SHEET.id);
    expect(usable()).toEqual(["klein"]);
    expect(select("Character LoRA · optional").props("modelValue")).toBe("klein");
    // Another engine's workflow swaps the LoRA for one that loads into it.
    workflow.vm.$emit("update:modelValue", QWEN_SHEET.id);
    await flushPromises();
    expect(usable()).toEqual(["qwen"]);
    expect(select("Character LoRA · optional").props("modelValue")).toBe("qwen");
    wrapper.unmount();
  });

  it("lists a MiniMax H3 reference sheet workflow, with H3 LoRAs only", async () => {
    const H3_SHEET = {
      id: "manual:h3-sheet",
      name: "Sheet MiniMax H3 Reference",
      type: "txt2img",
      reference_inputs: 1,
      last_used: "2026-11-12T20:00:00Z",
      models: [
        { kind: "clip", base_model_family: "minimax_h3" },
        { kind: "unet", base_model_family: "minimax_h3" },
      ],
    };
    referenceWorld({ cards: [MOSS_CARD, QWEN_SHEET, H3_SHEET] });
    gen.listAdapters.mockResolvedValue([
      { sha256: "qwen", display_name: "Anna Qwen", base_model_family: "qwen", added_at: "2026-11-09" },
      { sha256: "h3", display_name: "Anna H3", base_model_family: "minimax_h3", added_at: "2026-11-08" },
    ]);
    const wrapper = mountDialog();
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Reference and text");
    const select = (label) =>
      wrapper.findAllComponents({ name: "AppSelect" }).find((c) => c.props("label") === label);
    const workflow = select("Sheet workflow");
    expect(workflow.props("options").map((o) => o.value)).toEqual([
      H3_SHEET.id,
      QWEN_SHEET.id,
    ]);
    expect(workflow.props("modelValue")).toBe(H3_SHEET.id);
    expect(
      select("Character LoRA · optional")
        .props("options")
        .filter((o) => o.value && !o.disabled)
        .map((o) => o.value),
    ).toEqual(["h3"]);
    expect(select("Character LoRA · optional").props("modelValue")).toBe("h3");
    await draw(wrapper);
    await flushPromises();
    expect(gen.buildSheetPrompt).toHaveBeenLastCalledWith(
      expect.objectContaining({ layout: "prose", reference: true }),
    );
    expect(gen.runWorkflowCard).toHaveBeenLastCalledWith(
      expect.objectContaining({ workflow_id: H3_SHEET.id }),
    );
    wrapper.unmount();
  });

  it("offers a sheet copy of a reference workflow that draws at its picture's size", async () => {
    const copy = { ...KLEIN_SHEET, id: "manual:klein-copy", name: "Klein edit (sheet) (sheet)" };
    referenceWorld({ cards: [MOSS_CARD, KLEIN_SHEET] });
    // Its canvas is sized from the picture: no width or height to set.
    gen.getWorkflowCard.mockImplementation(async (id) => ({
      card: {
        name: id === copy.id ? copy.name : KLEIN_SHEET.name,
        defaults:
          id === copy.id
            ? [
                { slot_label: "lat", input_name: "width", value: 2560 },
                { slot_label: "lat", input_name: "height", value: 1536 },
              ]
            : [],
      },
    }));
    gen.sheetCopyWorkflow.mockResolvedValue({ workflow_id: copy.id });
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "no picture size to set",
    );
    gen.listWorkflowCards.mockResolvedValue({ cards: [MOSS_CARD, KLEIN_SHEET, copy] });
    await wrapper.find('[data-testid="cs-convert-chosen"]').trigger("click");
    await flushPromises();
    expect(gen.sheetCopyWorkflow).toHaveBeenCalledWith(KLEIN_SHEET.id);
    // The copy is chosen in its place, and it can draw.
    expect(gen.getWorkflowCard).toHaveBeenCalledWith(copy.id);
    expect(
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === "Sheet workflow")
        .props("modelValue"),
    ).toBe(copy.id);
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("makes a sheet workflow from the library's Qwen edit workflow", async () => {
    referenceWorld({ cards: [MOSS_CARD, QWEN_EDIT] });
    gen.sheetCopyWorkflow.mockResolvedValue({ workflow_id: QWEN_SHEET.id });
    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-mode"]').exists()).toBe(false);
    gen.listWorkflowCards.mockResolvedValue({
      cards: [MOSS_CARD, QWEN_EDIT, QWEN_SHEET],
    });
    await button(wrapper, "Make a sheet workflow from Qwen edit").trigger("click");
    await flushPromises();
    expect(gen.sheetCopyWorkflow).toHaveBeenCalledWith("auto:qwen-edit");
    // The mode is now offered; the one chosen does not change under the user.
    expect(mode(wrapper).text()).toBe("Text");
    expect(wrapper.find('[data-testid="cs-convert"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("goes to the reference mode when the sheet workflow is the only one", async () => {
    referenceWorld({ cards: [QWEN_EDIT] });
    gen.sheetCopyWorkflow.mockResolvedValue({ workflow_id: QWEN_SHEET.id });
    const wrapper = mountDialog();
    await flushPromises();
    gen.listWorkflowCards.mockResolvedValue({ cards: [QWEN_EDIT, QWEN_SHEET] });
    await button(wrapper, "Make a sheet workflow from Qwen edit").trigger("click");
    await flushPromises();
    expect(mode(wrapper).text()).toBe("Reference and text");
    expect(gen.getWorkflowCard).toHaveBeenCalledWith(QWEN_SHEET.id);
    wrapper.unmount();
  });
});

describe("TrainingSetBuild: the build is kept on the set", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  const lastBuild = () =>
    api.patchPictureSet.mock.calls.filter(([id]) => id === 55).at(-1)?.[1]
      .building;

  it("saves a draw as it goes, and opens again as it was left", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.getRunOutcome.mockResolvedValue({ status: "running" });
    let wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(700);
    // Drawing: the runs a delete would stop are on the set.
    expect(lastBuild()).toMatchObject({
      stage: "generate",
      run_keys: ["r1", "r2"],
      sheets: { phase: "drawing", form: { description: "Adult woman, copper bob" } },
    });

    gen.getRunOutcome.mockImplementation(async (key) => ({
      status: "completed",
      picture_ids: key === "r1" ? [501] : [502],
    }));
    gen.rankSheets.mockResolvedValue({
      sheets: [sheet(501, 0.5), sheet(502, 0.66)],
      references: 0,
    });
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    await vi.advanceTimersByTimeAsync(700);
    const build = lastBuild();
    expect(build).toMatchObject({
      run_keys: [],
      sheets: { phase: "pick", order: [501, 502], kept: [502] },
    });
    wrapper.unmount();

    // Opened again from its row: the same sheets, the same one kept, and
    // nothing drawn twice.
    wrapper = mountDialog(build);
    await flushPromises();
    expect(wrapper.find('[data-testid="cs-main"]').isVisible()).toBe(true);
    const sheets = wrapper.findAll('[data-testid="cs-sheet"]');
    expect(sheets).toHaveLength(2);
    expect(sheets[1].attributes("aria-pressed")).toBe("true");
    expect(wrapper.find('[data-testid="cts-generate-primary"]').text()).toContain(
      "Use sheet 2",
    );
    expect(gen.runWorkflowCard).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("keeps runs a new draw started over from on the list a delete stops", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.getRunOutcome.mockResolvedValue({ status: "running" });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    // Another prompt: a new pick, while r1 and r2 still draw.
    await button(wrapper, "Edit").trigger("click");
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("Adult man");
    gen.buildSheetPrompt.mockResolvedValue({
      prompt: "another {layout}",
      negative: "nsfw",
      width: 2560,
      height: 1536,
    });
    gen.runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [
        { run_id: "r3", status: "queued" },
        { run_id: "r4", status: "queued" },
      ],
    });
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(700);
    const build = lastBuild();
    expect(build.sheets.runs.map((r) => r.key)).toEqual(["r3", "r4"]);
    expect(build.run_keys).toEqual(["r1", "r2", "r3", "r4"]);
    wrapper.unmount();
  });

  it("writes no build over a set Create is finishing", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    let land;
    api.bulkAddPicturesToSet.mockReturnValue(new Promise((r) => (land = r)));
    const wrapper = mountDialog();
    await flushPromises();
    // A change whose save is still waiting when Create is pressed.
    await wrapper.find('[data-testid="cts-slider-stills"]').setValue("20");
    await wrapper.find('[data-testid="cts-create"]').trigger("click");
    await vi.advanceTimersByTimeAsync(2000);
    land({ added: 20 });
    await flushPromises();
    await vi.advanceTimersByTimeAsync(2000);
    const writes = api.patchPictureSet.mock.calls.map(([, body]) => body.building);
    expect(writes).toEqual([null]);
    wrapper.unmount();
  });

  it("lets a save already on its way land before Create finishes the set", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 20 });
    const order = [];
    let landSave;
    api.patchPictureSet.mockImplementation((_id, body) => {
      if (body.building === null) {
        order.push("finish");
        return Promise.resolve({ status: "success" });
      }
      order.push("save sent");
      return new Promise((resolve) => {
        landSave = () => {
          order.push("save landed");
          resolve({ status: "success" });
        };
      });
    });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cts-slider-stills"]').setValue("20");
    await vi.advanceTimersByTimeAsync(700);
    expect(order).toEqual(["save sent"]);
    await wrapper.find('[data-testid="cts-create"]').trigger("click");
    await flushPromises();
    expect(order).toEqual(["save sent"]);
    landSave();
    await flushPromises();
    expect(order).toEqual(["save sent", "save landed", "finish"]);
    expect(wrapper.emitted("finished")).toHaveLength(1);
    wrapper.unmount();
  });

  it("draws a man when the description says so, until a toggle is picked", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult man, short beard" });
    const wrapper = mountDialog();
    await flushPromises();
    const picked = () =>
      wrapper.find('[data-testid="cs-subject"] [aria-checked="true"]').text();
    expect(picked()).toBe("Man");

    await wrapper.find('[data-testid="cs-description"] textarea').setValue("Adult woman");
    expect(picked()).toBe("Woman");
    const man = wrapper
      .find('[data-testid="cs-subject"]')
      .findAll("button")
      .find((b) => b.text() === "Man");
    await man.trigger("click");
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("Adult woman, red hair");
    expect(picked()).toBe("Man");
    wrapper.unmount();
  });

  it("draws a little person when the description says so, and asks for one", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult man with dwarfism, short beard" });
    const wrapper = mountDialog();
    await flushPromises();
    const picked = () =>
      wrapper.find('[data-testid="cs-proportions"] [aria-checked="true"]').text();
    expect(picked()).toBe("Little person");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    expect(gen.buildSheetPrompt).toHaveBeenLastCalledWith(
      expect.objectContaining({ proportions: "little_person" }),
    );
    wrapper.unmount();
  });

  it("saves on leaving after a save failed, though nothing was pending", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.patchPictureSet.mockRejectedValueOnce(new Error("offline"));
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cts-slider-stills"]').setValue("20");
    await vi.advanceTimersByTimeAsync(700);
    expect(api.patchPictureSet).toHaveBeenCalledTimes(1);
    wrapper.unmount();
    await flushPromises();
    expect(api.patchPictureSet).toHaveBeenCalledTimes(2);
    expect(lastBuild().picker.sizes.stills).toBe(20);
  });

  it("waits for every save on its way, not only the newest, before finishing", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.bulkAddPicturesToSet.mockResolvedValue({ added: 20 });
    const order = [];
    const lands = [];
    api.patchPictureSet.mockImplementation((_id, body) => {
      if (body.building === null) {
        order.push("finish");
        return Promise.resolve({ status: "success" });
      }
      const n = lands.length + 1;
      order.push(`save ${n} sent`);
      return new Promise((resolve) => {
        lands.push(() => {
          order.push(`save ${n} landed`);
          resolve({ status: "success" });
        });
      });
    });
    const wrapper = mountDialog();
    await flushPromises();
    // Two saves on the wire at once: the older one is still out.
    await wrapper.find('[data-testid="cts-slider-stills"]').setValue("20");
    await vi.advanceTimersByTimeAsync(700);
    await wrapper.find('[data-testid="cts-slider-stills"]').setValue("22");
    await vi.advanceTimersByTimeAsync(700);
    expect(order).toEqual(["save 1 sent", "save 2 sent"]);
    await wrapper.find('[data-testid="cts-create"]').trigger("click");
    lands[1]();
    await flushPromises();
    expect(order).not.toContain("finish");
    lands[0]();
    await flushPromises();
    expect(order.at(-1)).toBe("finish");
    wrapper.unmount();
  });

  it("shows the saved sheets at once, before anything is read again", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    // Nothing the step reads answers: the character, its LoRAs, the workflows,
    // the outcome of a run and a new ranking all hang.
    const never = () => new Promise(() => {});
    for (const fn of [
      gen.getCharacter,
      gen.listAdapters,
      gen.listWorkflowCards,
      gen.getReferencePictures,
      gen.getRunOutcome,
      gen.rankSheets,
    ]) {
      fn.mockImplementation(never);
    }
    const wrapper = mountDialog({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: {
        phase: "pick",
        form: { description: "Adult woman, copper bob" },
        runs: [
          { key: "r1", status: "completed", picture_ids: [501] },
          { key: "r2", status: "completed", picture_ids: [502] },
        ],
        order: [501, 502],
        ranking: { 501: sheet(501, 0.5), 502: sheet(502, 0.66) },
        kept: [502],
        kept_touched: true,
      },
    });
    await nextTick();
    const sheets = wrapper.findAll('[data-testid="cs-sheet"]');
    expect(sheets.map((b) => b.attributes("aria-label"))).toEqual([
      "Sheet 1: faces agree somewhat",
      "Sheet 2: faces agree strongly",
    ]);
    expect(sheets[1].attributes("aria-pressed")).toBe("true");
    expect(wrapper.find('[data-testid="cs-description"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("folds a build saved with a body into the description, and sends no body", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman" });
    const wrapper = mountDialog({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: { phase: "describe", form: { description: "Adult woman", body: "curvy" } },
    });
    await flushPromises();
    const text = wrapper.find('[data-testid="cs-description"] textarea');
    expect(text.element.value).toBe("Adult woman, curvy");
    // No Body field ("Body photos" is the framing, not a field).
    expect(wrapper.text()).not.toMatch(/Body(?! photos)/);
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    const sent = gen.buildSheetPrompt.mock.calls[0][0];
    expect(sent.description).toBe("Adult woman, curvy");
    expect(sent).not.toHaveProperty("body");
    await vi.advanceTimersByTimeAsync(700);
    expect(lastBuild().sheets.form).not.toHaveProperty("body");
    wrapper.unmount();
  });

  it("keeps the photo look as remembered and as saved; an older build reads as on", async () => {
    const open = async (building) => {
      api.fetchCharacterSetSuggestion.mockResolvedValue(
        suggestion({ stillsGroup: emptyGroup() }),
      );
      sheetsWorld({ description: "Adult woman" });
      gen.listWorkflowCards.mockResolvedValue({ cards: [KREA_CARD] });
      const wrapper = mountDialog(building);
      await flushPromises();
      return wrapper;
    };
    const chosen = (wrapper) =>
      wrapper.find('[data-testid="cs-photo-look"] [aria-checked="true"]').text();
    const saved = (form) => ({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: { phase: "describe", form: { description: "Adult woman", ...form } },
    });
    localStorage.setItem(
      "pixlstash:characterSheetChoices",
      JSON.stringify({ photo_look: false }),
    );
    let wrapper = await open(undefined);
    expect(chosen(wrapper)).toBe("Off");
    await wrapper
      .findAll('[data-testid="cs-photo-look"] button')
      .find((b) => b.text() === "Natural")
      .trigger("click");
    await vi.advanceTimersByTimeAsync(700);
    expect(
      JSON.parse(localStorage.getItem("pixlstash:characterSheetChoices")).photo_look,
    ).toBe(true);
    expect(lastBuild().sheets.form.photo_look).toBe(true);
    wrapper.unmount();
    // A saved build stands over what is remembered.
    localStorage.setItem(
      "pixlstash:characterSheetChoices",
      JSON.stringify({ photo_look: false }),
    );
    wrapper = await open(saved({}));
    expect(chosen(wrapper)).toBe("Natural");
    wrapper.unmount();
    wrapper = await open(saved({ photo_look: false }));
    expect(chosen(wrapper)).toBe("Off");
    wrapper.unmount();
  });

  it("keeps a size saved for an H3 workflow, unless H3 cannot draw it", async () => {
    const H3 = {
      id: "manual:h3-t2i",
      name: "MiniMax H3: Text to Image",
      type: "txt2img",
      models: [{ kind: "unet", base_model_family: "minimax_h3" }],
    };
    const open = async (size) => {
      api.fetchCharacterSetSuggestion.mockResolvedValue(
        suggestion({ stillsGroup: emptyGroup() }),
      );
      sheetsWorld({ description: "Adult woman" });
      gen.listWorkflowCards.mockResolvedValue({ cards: [MOSS_CARD, H3] });
      const wrapper = mountDialog({
        v: 1,
        stage: "generate",
        stepped: true,
        sheets: {
          phase: "describe",
          form: { description: "Adult woman", size },
          workflow_id: H3.id,
        },
      });
      await flushPromises();
      const chosen = wrapper
        .find('[data-testid="cs-size"] [aria-checked="true"]')
        .text();
      wrapper.unmount();
      return chosen;
    };
    expect(await open("large")).toBe("Large");
    expect(await open("small")).toBe("4K");
  });

  it("keeps following a run still drawing when the build is opened again", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    let finished = false;
    gen.getRunOutcome.mockImplementation(async () =>
      finished ? { status: "completed", picture_ids: [501] } : { status: "running" },
    );
    gen.rankSheets.mockResolvedValue({ sheets: [sheet(501, 0.6)], references: 0 });
    const wrapper = mountDialog({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: {
        phase: "drawing",
        form: { description: "Adult woman, copper bob" },
        runs: [{ key: "r1", status: "queued", picture_ids: [] }],
      },
    });
    await flushPromises();
    expect(wrapper.findAll('[data-testid="cs-sheet"]')).toHaveLength(0);
    // Polled again and again, not once: it lands three polls later.
    await vi.advanceTimersByTimeAsync(5000);
    finished = true;
    await vi.advanceTimersByTimeAsync(3000);
    expect(wrapper.findAll('[data-testid="cs-sheet"]')).toHaveLength(1);
    expect(gen.getRunOutcome.mock.calls.length).toBeGreaterThan(2);
    wrapper.unmount();
  });

  it("stores a cut sheet as used before its faces are read", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    sheetsWorld({ description: "Adult woman, copper bob" });
    gen.getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [501] });
    gen.rankSheets.mockImplementation(async (_id, ids) => ({
      // The sheet's faces are read; the new pictures' never are.
      sheets: ids.map((id) =>
        id === 501 ? sheet(501, 0.6) : { ...sheet(id, null), faces_read: false },
      ),
      references: 0,
    }));
    gen.useSheets.mockResolvedValue({ picture_ids: [601, 602] });
    const wrapper = mountDialog({
      v: 1,
      stage: "generate",
      stepped: true,
      sheets: {
        phase: "pick",
        form: { description: "Adult woman, copper bob" },
        runs: [{ key: "r1", status: "completed", picture_ids: [501] }],
        order: [501],
        kept: [501],
        kept_touched: true,
      },
    });
    await flushPromises();
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(700);
    expect(gen.useSheets).toHaveBeenCalledWith(7, [501]);
    // Still reading faces, but the set already holds the cut as done.
    expect(lastBuild()).toMatchObject({
      stage: "pick",
      picker: { generated: [601, 602] },
      sheets: { phase: "describe", runs: [], order: [], kept: [] },
    });
    wrapper.unmount();
  });

  it("Cancel set asks once, then deletes the set", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    api.deletePictureSet.mockResolvedValue({ status: "success" });
    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="tsb-cancel"]').trigger("click");
    await flushPromises();
    expect(ask).toHaveBeenCalledTimes(1);
    expect(api.deletePictureSet).not.toHaveBeenCalled();
    expect(wrapper.emitted("cancelled")).toBeUndefined();

    ask.mockReturnValue(true);
    await wrapper.find('[data-testid="tsb-cancel"]').trigger("click");
    await flushPromises();
    expect(ask.mock.calls[1][0]).toContain("Drawn sheets stay in the Scrapheap");
    expect(api.deletePictureSet).toHaveBeenCalledWith(55);
    expect(wrapper.emitted("cancelled")).toEqual([[{ id: 55, characterId: 7 }]]);
    wrapper.unmount();
  });

  it("offers only the sheet workflows that can draw a sheet", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: emptyGroup() }),
    );
    const BAD = { ...MOSS_CARD, id: "manual:bad", name: "Unsized MOSS" };
    const sized = {
      card: {
        name: "MOSS sheet",
        defaults: [
          { slot_label: "lat", input_name: "width", value: 1024 },
          { slot_label: "lat", input_name: "height", value: 1024 },
        ],
      },
    };
    const world = (good) => {
      sheetsWorld({ description: "Adult woman, copper bob" });
      gen.listWorkflowCards.mockResolvedValue({ cards: [BAD, MOSS_CARD] });
      gen.getWorkflowCard.mockImplementation(async (id) =>
        good && id === MOSS_CARD.id
          ? sized
          : { card: { name: id === BAD.id ? BAD.name : "MOSS sheet", defaults: [] } },
      );
    };
    const workflowSelect = (wrapper) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((c) => c.props("label") === "Sheet workflow");

    world(true);
    let wrapper = mountDialog();
    await flushPromises();
    expect(workflowSelect(wrapper).props("options")).toEqual([
      { value: "manual:moss", label: "MOSS sheet" },
    ]);
    expect(workflowSelect(wrapper).props("modelValue")).toBe("manual:moss");
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').exists()).toBe(false);
    wrapper.unmount();

    world(false);
    wrapper = mountDialog();
    await flushPromises();
    expect(workflowSelect(wrapper)).toBeUndefined();
    expect(wrapper.find('[data-testid="cs-workflow-problem"]').text()).toContain(
      "None of the 2 sheet workflows can draw a sheet. Unsized MOSS has no picture size",
    );
    expect(
      wrapper.find('[data-testid="cts-generate-primary"]').attributes("disabled"),
    ).toBeDefined();
    wrapper.unmount();
  });
});
