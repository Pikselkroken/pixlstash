// "Create a training set…": the slider takes a prefix of one ranking, the
// coverage column sorts gaps into kinds, and Create makes then fills a set.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

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
    expect(notice).toContain("Turning off Near-duplicates would let 14 more");
    expect(notice).toContain("Below the quality floor 6 more");
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

  it("sets every reason aside by default; switching one off asks again", async () => {
    api.fetchCharacterSetSuggestion.mockResolvedValue(suggestion());
    const wrapper = mountDialog();
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(7, {
      other_people: false,
      near_duplicates: false,
      low_quality: false,
      low_likeness: false,
    });
    const switches = wrapper.findAllComponents({ name: "VSwitch" });
    // The stub declares no props, so the bound value lands as an attribute.
    expect(switches.map((s) => s.attributes("model-value"))).toEqual([
      "true",
      "true",
      "true",
      "true",
    ]);
    switches[1].vm.$emit("update:modelValue", false);
    await flushPromises();
    expect(api.fetchCharacterSetSuggestion).toHaveBeenLastCalledWith(7, {
      other_people: false,
      near_duplicates: true,
      low_quality: false,
      low_likeness: false,
    });
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
