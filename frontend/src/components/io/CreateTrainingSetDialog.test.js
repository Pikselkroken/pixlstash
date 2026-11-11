// "Create a training set…": the slider takes a prefix of one ranking, the
// coverage column sorts gaps into kinds, and Create makes then fills a set.

import { afterEach, describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { useTasksStore } from "../../stores/useTasksStore";

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

// The Sheets step (CharacterSheetStep): what it reads and what it runs.
const gen = vi.hoisted(() => ({
  buildSheetPrompt: vi.fn(),
  rankSheets: vi.fn(),
  useSheets: vi.fn(),
  discardSheets: vi.fn(),
  getCharacter: vi.fn(),
  getReferencePictures: vi.fn(),
  patchCharacter: vi.fn(),
  listAdapters: vi.fn(),
  getRunOutcome: vi.fn(),
  getWorkflowCard: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
}));
vi.mock("../../api/characterSheets", () => ({
  buildSheetPrompt: gen.buildSheetPrompt,
  rankSheets: gen.rankSheets,
  useSheets: gen.useSheets,
  discardSheets: gen.discardSheets,
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
}));

import CreateTrainingSetDialog from "./CreateTrainingSetDialog.vue";

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
    const notice = wrapper.find('[data-testid="cts-capped-stills"]').text();
    expect(notice).toContain("Only 9 pictures pass.");
    expect(notice).toContain("Keeping near-duplicates would let 14 more");
    expect(notice).toContain("no minimum score 6 more");
    wrapper.unmount();
  });

  it("tells a gap the slider can close from one the library cannot", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    const closable = wrapper.findAll('[data-testid="cts-gap-closable"]');
    expect(closable.map((g) => g.text())).toEqual([
      expect.stringContaining("Closed eyes 1 short · at 18"),
    ]);
    const missing = wrapper.findAll('[data-testid="cts-gap-missing"]');
    expect(missing.map((g) => g.find(".cts-g__t").text())).toEqual([
      "No full-body pictures of Anna",
    ]);
    expect(wrapper.find('[data-testid="cts-bar-full body"]').text()).toContain(
      "none",
    );

    await button(wrapper, "Show 18").trigger("click");
    expect(shownIds(wrapper)).toHaveLength(18);
    expect(wrapper.findAll('[data-testid="cts-gap-closable"]')).toHaveLength(0);
    wrapper.unmount();
  });

  it("offers a slider position only where the target there is met", async () => {
    // Smiles at ranks 16 and 30 only: 2 at 30, but a set of 30 wants 4.
    const group = stills(50);
    group.picks.forEach((p, i) => {
      p.buckets.expression = [i === 15 || i === 29 ? "smile" : "neutral"];
    });
    api.fetchCharacterSetSuggestion.mockResolvedValue(
      suggestion({ stillsGroup: group }),
    );
    const wrapper = mountDialog();
    await flushPromises();
    const texts = (id) =>
      wrapper.findAll(`[data-testid="${id}"]`).map((g) => g.text());
    expect(texts("cts-gap-closable").join()).not.toContain("Smile");
    expect(texts("cts-gap-outside")).toContainEqual(
      expect.stringContaining("Smile: 9 in the pool, only 2 in the top 50"),
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
    expect(floor.text()).toContain("23 set aside");
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

  it("stops a floor slider at the best value among the pictures", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue({
      ...suggestion(),
      limits: { min_likeness: 80, min_score: 4.5 },
    });
    const wrapper = mountDialog();
    await flushPromises();
    const slider = wrapper.find('[data-testid="cts-slider-min_likeness"]');
    // The track keeps its whole scale; the part past 80 is hatched.
    expect(slider.attributes("max")).toBe("100");
    const floor = wrapper.find('[data-testid="cts-floor-min_likeness"]');
    expect(floor.find(".cts-range__cap").attributes("style")).toContain(
      "width: 20%",
    );
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
    expect(notice).toContain("Only 49 of the ranked pictures are left");
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

const MOSS_CARD = {
  id: "manual:moss",
  name: "MOSS sheet",
  type: "txt2img",
  last_used: "2026-11-09T20:00:00Z",
  models: [{ base_model_family: "moss_image" }],
};

/** Mock the Sheets step's reads: one MOSS workflow, LoRAs newest first. */
function sheetsWorld({ description = "", loras = [] } = {}) {
  gen.getCharacter.mockResolvedValue({ id: 7, name: "Anna", description });
  gen.discardSheets.mockResolvedValue({ picture_ids: [] });
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

describe("CreateTrainingSetDialog: generating the pictures", () => {
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
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await flushPromises();

    // No description on the character: the typed one becomes hers.
    expect(gen.patchCharacter).toHaveBeenCalledWith(7, {
      description: "Adult woman, copper bob",
    });
    expect(gen.buildSheetPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ trigger: "anna", framing: "full_body", size: "large" }),
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
      "Keep sheet 2",
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
    // The sheets, kept or not, were never the library's: all of them go.
    expect(gen.discardSheets).toHaveBeenCalledWith(7, [501, 502]);
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

    // The same prompt again: more of the same, side by side.
    startsRun("r2");
    await button(wrapper, "Draw 4 more").trigger("click");
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
    // The dropped sheets are discarded, never left in the library.
    expect(gen.discardSheets).toHaveBeenCalledWith(7, [501, 502]);
    wrapper.unmount();
  });

  it("zooms a sheet to its own file, and Escape leaves the zoom, not the dialog", async () => {
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
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3000);

    await wrapper.findAll('[data-testid="cs-zoom-open"]')[1].trigger("click");
    await flushPromises();
    const zoom = wrapper.find('[data-testid="cs-zoom"]');
    expect(zoom.find("img").attributes("src")).toMatch(/\/pictures\/502\.png$/);
    expect(wrapper.find('[data-testid="cs-sheet"]').exists()).toBe(false);

    await zoom.trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(wrapper.find('[data-testid="cs-zoom"]').exists()).toBe(false);

    // Keep from the zoom adds that sheet to the best one, ticked already.
    await wrapper.findAll('[data-testid="cs-zoom-open"]')[1].trigger("click");
    await button(wrapper, "Keep").trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="cts-generate-primary"]').text()).toContain(
      "Keep 2 sheets",
    );
    // And closing the dialog discards every sheet drawn.
    await wrapper.find('[aria-label="Close"]').trigger("click");
    expect(gen.discardSheets).toHaveBeenCalledWith(7, [501, 502]);
    wrapper.unmount();
  });

  it("keeps each sheet in its run's place and shows the drawing one's progress", async () => {
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
        .map((li) => (li.find("img").exists() ? li.find("img").attributes("src") : li.text()));
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.find('[data-testid="cs-description"] textarea').setValue("x");
    await wrapper.find('[data-testid="cts-generate-primary"]').trigger("click");

    // The second run finishes first: its sheet takes the second place.
    done.r2 = 502;
    await vi.advanceTimersByTimeAsync(3000);
    expect(places()).toEqual(["drawing", "/thumb/502.webp", "queued", "queued"]);
    const bar = wrapper.find('[role="progressbar"]');
    expect(bar.attributes("aria-valuenow")).toBe("40");

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
});
