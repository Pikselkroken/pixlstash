// The lightbox sidebar's Recipe tab appears only for a picture that HAS a
// recipe.
//
// It used to be permanent, on the reasoning that a disabled tab is a dead
// control and the panel could say "nothing here was made in ComfyUI" in a
// sentence instead. That is still true of a *disabled* tab; it is not an
// argument for a tab whose only content is its own denial, which is what every
// holiday photo in the library got.
//
// The two things that make hiding it safe rather than merely tidy, and which
// are what this file actually guards:
//
//   1. It does not flicker. The read takes a file read, and the tab is not
//      taken away for the length of it - so walking the filmstrip does not
//      remove and replace a tab under the reader's cursor.
//   2. It does not lose the reader's choice. Stepping over a photo in a run of
//      ComfyUI pictures must not silently move them to Info for the rest of
//      the walk.

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";

// As the sibling overlay suites: the tab band renders a Vuetify `VIcon` and
// this file mounts without the Vuetify plugin.
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import ImageOverlay from "./ImageOverlay.vue";
import { isReadOnly } from "../../utils/apiClient";

enableAutoUnmount(afterEach);

// Which picture ids the recipe read answers for. Everything else answers the
// honest "this picture was not generated", which is what the route returns as
// `no_prompt_chunk` and the overlay maps to no recipe at all.
let picturesWithARecipe = new Set([7]);
// Resolved by hand, so a test can hold the read open and look at the tab band
// while the answer is still in flight.
let pendingRecipe = null;

// Set by a test that wants the "this is a ComfyUI picture PixlStash cannot
// hand back to ComfyUI" answer instead of the runnable one.
let recipeRefusal = null;

const recipeBody = (id) =>
  recipeRefusal ?? {
    available: true,
    reason: null,
    source: "comfyui",
    summary: `Editor Workflow · 4 nodes`,
    positive_prompt: `prompt for ${id}`,
    conversion_problems: [],
  };

// Set by the test that drives the read's failure path.
let recipeThrows = false;

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/recipe")) {
    if (recipeThrows) {
      const error = new Error("gone");
      error.response = { status: 404 };
      throw error;
    }
    const id = Number(url.match(/pictures\/(\d+)\/recipe/)[1]);
    const answer = picturesWithARecipe.has(id)
      ? recipeBody(id)
      : { available: false, reason: "no_prompt_chunk" };
    if (pendingRecipe) {
      return new Promise((resolve) => {
        pendingRecipe.release = () => resolve({ data: answer });
      });
    }
    return { data: answer };
  }
  if (typeof url === "string" && url.includes("/metadata")) {
    return { data: { id: 7, tags: [] } };
  }
  if (typeof url === "string" && url.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  return { data: [] };
});

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: { get: (...a) => getMock(...a), post: vi.fn(), delete: vi.fn() },
  appendShareToken: (u) => u,
  isReadOnly: { value: false },
}));

const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  // Real-but-thin, so the payload the overlay hands the panel can be read off
  // its prop. A `true` stub swallows it, and the field the whole refusal path
  // exists to deliver could then be deleted with the suite still green.
  OverlayRecipePanel: {
    name: "OverlayRecipePanel",
    props: ["recipe", "pictureId", "canGenerateVariants", "comfyuiConfigured"],
    template: "<div class='recipe-stub'></div>",
  },
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  ComfyUiRunner: true,
  ProgressOverlay: true,
  VTooltip: true,
  OverlayEditPanel: {
    name: "OverlayEditPanel",
    props: ["pictureId", "active"],
    template: "<div class='edit-stub'></div>",
  },
};

const flush = () => new Promise((r) => setTimeout(r, 0));

// The icon stub renders its name as text, so the label is what follows it.
const labelOf = (el) => el.text().replace(/^mdi-[\w-]+\s*/, "").trim();

/** The labels on the sidebar's tab band, in order. */
const tabLabels = (wrapper) =>
  wrapper.findAll(".inspector-tab").map(labelOf);

const activeTab = (wrapper) => labelOf(wrapper.find(".inspector-tab--active"));

async function openOn(
  id,
  allImages,
  { attach = false, comfyuiConfigured = false } = {},
) {
  const wrapper = mount(ImageOverlay, {
    // Focus only moves for real in an attached tree, so the test that is
    // about focus asks for one; the rest do not pay for it.
    ...(attach ? { attachTo: document.body } : {}),
    props: {
      open: false,
      initialImageId: id,
      allImages: allImages ?? [{ id, tags: [] }],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
      comfyuiConfigured,
    },
    global: { stubs: STUBS },
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  picturesWithARecipe = new Set([7]);
  pendingRecipe = null;
  recipeRefusal = null;
  recipeThrows = false;
  getMock.mockClear();
  isReadOnly.value = false;
});

const recipePanel = (wrapper) =>
  wrapper.findComponent({ name: "OverlayRecipePanel" });

describe("the lightbox Recipe tab follows the picture", () => {
  it("is offered for a picture that has a recipe", async () => {
    const wrapper = await openOn(7);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
  });

  it("takes the whole tab band away for a picture that has none", async () => {
    picturesWithARecipe = new Set();
    const wrapper = await openOn(7);
    // The regression this replaces: a tab whose whole content was "nothing
    // here was made in ComfyUI". And NOT a lone Info button either - a tab
    // band with one tab is a control with nothing to switch to.
    expect(tabLabels(wrapper)).toEqual([]);
    expect(wrapper.find(".inspector-tabs").exists()).toBe(false);
  });

  it("goes away when the reader steps onto a picture without one", async () => {
    const wrapper = await openOn(7, [
      { id: 7, tags: [] },
      { id: 8, tags: [] },
    ]);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
    await wrapper.findAll(".inspector-tab")[1].trigger("click");
    expect(activeTab(wrapper)).toBe("Recipe");

    await wrapper.setProps({ initialImageId: 8 });
    await flush();
    await flush();
    expect(tabLabels(wrapper)).toEqual([]);
    // And the reader is looking at the Info panel rather than at a pane whose
    // tab has gone.
    expect(wrapper.find(".overlay-sidebar-group").isVisible()).toBe(true);
  });

  it("comes back, still selected, on the next picture that has one", async () => {
    picturesWithARecipe = new Set([7, 9]);
    const wrapper = await openOn(7, [
      { id: 7, tags: [] },
      { id: 8, tags: [] },
      { id: 9, tags: [] },
    ]);
    await wrapper.findAll(".inspector-tab")[1].trigger("click");

    await wrapper.setProps({ initialImageId: 8 });
    await flush();
    await flush();
    expect(tabLabels(wrapper)).toEqual([]);

    await wrapper.setProps({ initialImageId: 9 });
    await flush();
    await flush();
    // Not "Info": somebody reading recipes wants the next picture's recipe,
    // and having to re-pick the tab after every photo would make the walk
    // useless.
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
    expect(activeTab(wrapper)).toBe("Recipe");
  });

  it("keeps the tab for a recipe that could not be rebuilt", async () => {
    // The case the change exists for. `available: false` is not `no recipe`:
    // the picture was made in ComfyUI, its prompt and models are readable, and
    // only the offer to run it again is withheld.
    recipeRefusal = {
      available: false,
      reason: "editor_graph",
      source: "comfyui",
      summary: "Editor Workflow · 4 nodes · 3 links",
      positive_prompt: "a cat in a hat",
      conversion_problems: ["this ComfyUI has no node class 'SomePackNode'"],
    };
    const wrapper = await openOn(7);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
  });

  it("hands the panel the reasons the rebuild failed", async () => {
    recipeRefusal = {
      available: false,
      reason: "editor_graph",
      source: "comfyui",
      summary: "Editor Workflow · 4 nodes · 3 links",
      conversion_problems: [
        "this ComfyUI has no node class 'SomePackNode'",
        "KSampler (node 3) carries 5 widget values, and its inputs account for 4",
      ],
    };
    const wrapper = await openOn(7);
    await wrapper.findAll(".inspector-tab")[1].trigger("click");
    const recipe = recipePanel(wrapper).props("recipe");
    expect(recipe.reason).toBe("editor_graph");
    // Read straight off the response's snake_case field: the panel prints
    // these under its refusal, and a dropped mapping would leave it saying
    // "could not rebuild it" with nothing to act on.
    expect(recipe.conversionProblems).toEqual(
      recipeRefusal.conversion_problems,
    );
  });

  it("gives an ordinary recipe an empty problem list, not undefined", async () => {
    const wrapper = await openOn(7);
    await wrapper.findAll(".inspector-tab")[1].trigger("click");
    expect(recipePanel(wrapper).props("recipe").conversionProblems).toEqual([]);
  });

  it("moves focus off the tab band before it unmounts", async () => {
    // Arrow keys are a window listener, so a reader can walk the filmstrip
    // with focus on a tab button. Losing it to `document.body` is silent:
    // the shortcuts keep working and only the focus ring goes.
    const wrapper = await openOn(
      7,
      [
        { id: 7, tags: [] },
        { id: 8, tags: [] },
      ],
      { attach: true },
    );
    const recipeTab = wrapper.findAll(".inspector-tab")[1];
    recipeTab.element.focus();
    expect(document.activeElement).toBe(recipeTab.element);

    await wrapper.setProps({ initialImageId: 8 });
    await flush();
    await flush();
    expect(tabLabels(wrapper)).toEqual([]);
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toBe(
      wrapper.find(".overlay-canvas").element,
    );
  });

  it("takes the tab away when the recipe read fails outright", async () => {
    // The `catch` path. A 404 means the picture or its file is gone, so the
    // tab must not stay up over a recipe that will never arrive.
    const wrapper = await openOn(7, [
      { id: 7, tags: [] },
      { id: 8, tags: [] },
    ]);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
    recipeThrows = true;
    await wrapper.setProps({ initialImageId: 8 });
    await flush();
    await flush();
    expect(tabLabels(wrapper)).toEqual([]);
  });

  it("does not blink out while the next picture's recipe is being read", async () => {
    const wrapper = await openOn(7, [
      { id: 7, tags: [] },
      { id: 8, tags: [] },
    ]);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);

    // Hold the next read open: this is the window in which the overlay knows
    // nothing about picture 8's recipe.
    pendingRecipe = {};
    await wrapper.setProps({ initialImageId: 8 });
    await flush();
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);

    pendingRecipe.release();
    pendingRecipe = null;
    await flush();
    await flush();
    expect(tabLabels(wrapper)).toEqual([]);
  });
});

// The Edit tab (#1381) hangs off the machine and the session, never the
// picture's recipe: a holiday photo gets it too. Absent, not disabled, where it
// cannot be used - the Recipe tab's rule.
describe("the lightbox Edit tab", () => {
  const configured = { comfyuiConfigured: true };

  it("sits after Recipe when ComfyUI is configured", async () => {
    const wrapper = await openOn(7, undefined, configured);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe", "Edit"]);
  });

  it("is offered on a picture with no recipe", async () => {
    picturesWithARecipe = new Set();
    const wrapper = await openOn(7, undefined, configured);
    expect(tabLabels(wrapper)).toEqual(["Info", "Edit"]);
  });

  it("is absent without ComfyUI", async () => {
    const wrapper = await openOn(7);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
    expect(wrapper.find(".edit-stub").exists()).toBe(false);
  });

  it("is absent in a read-only session", async () => {
    isReadOnly.value = true;
    const wrapper = await openOn(7, undefined, configured);
    expect(tabLabels(wrapper)).not.toContain("Edit");
    expect(wrapper.find(".edit-stub").exists()).toBe(false);
  });

  it("is absent on a video, and the choice comes back after it", async () => {
    const wrapper = await openOn(
      7,
      [
        { id: 7, tags: [] },
        { id: 9, tags: [], format: "mp4" },
      ],
      configured,
    );
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    expect(activeTab(wrapper)).toBe("Edit");
    expect(
      wrapper.findComponent({ name: "OverlayEditPanel" }).props("active"),
    ).toBe(true);

    await wrapper.setProps({ initialImageId: 9 });
    await flush();
    await flush();
    expect(tabLabels(wrapper)).not.toContain("Edit");
    expect(wrapper.find(".overlay-sidebar-group").isVisible()).toBe(true);

    await wrapper.setProps({ initialImageId: 7 });
    await flush();
    await flush();
    expect(activeTab(wrapper)).toBe("Edit");
  });
});
