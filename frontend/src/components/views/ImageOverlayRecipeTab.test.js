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

enableAutoUnmount(afterEach);

// Which picture ids the recipe read answers for. Everything else answers the
// honest "this picture was not generated", which is what the route returns as
// `no_prompt_chunk` and the overlay maps to no recipe at all.
let picturesWithARecipe = new Set([7]);
// Resolved by hand, so a test can hold the read open and look at the tab band
// while the answer is still in flight.
let pendingRecipe = null;

const recipeBody = (id) => ({
  available: true,
  reason: null,
  source: "comfyui",
  summary: `Editor Workflow · 4 nodes`,
  positive_prompt: `prompt for ${id}`,
  conversion_problems: [],
});

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/recipe")) {
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
  OverlayRecipePanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  ComfyUiRunner: true,
  ProgressOverlay: true,
  VTooltip: true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

// The icon stub renders its name as text, so the label is what follows it.
const labelOf = (el) => el.text().replace(/^mdi-[\w-]+\s*/, "").trim();

/** The labels on the sidebar's tab band, in order. */
const tabLabels = (wrapper) =>
  wrapper.findAll(".inspector-tab").map(labelOf);

const activeTab = (wrapper) => labelOf(wrapper.find(".inspector-tab--active"));

async function openOn(id, allImages) {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: id,
      allImages: allImages ?? [{ id, tags: [] }],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
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
  getMock.mockClear();
});

describe("the lightbox Recipe tab follows the picture", () => {
  it("is offered for a picture that has a recipe", async () => {
    const wrapper = await openOn(7);
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
  });

  it("is absent for a picture that has none", async () => {
    picturesWithARecipe = new Set();
    const wrapper = await openOn(7);
    // The regression this replaces: a tab whose whole content was "nothing
    // here was made in ComfyUI".
    expect(tabLabels(wrapper)).toEqual(["Info"]);
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
    expect(tabLabels(wrapper)).toEqual(["Info"]);
    // And the reader is looking at Info rather than at a pane with no tab.
    expect(activeTab(wrapper)).toBe("Info");
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
    expect(tabLabels(wrapper)).toEqual(["Info"]);

    await wrapper.setProps({ initialImageId: 9 });
    await flush();
    await flush();
    // Not "Info": somebody reading recipes wants the next picture's recipe,
    // and having to re-pick the tab after every photo would make the walk
    // useless.
    expect(tabLabels(wrapper)).toEqual(["Info", "Recipe"]);
    expect(activeTab(wrapper)).toBe("Recipe");
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
    expect(tabLabels(wrapper)).toEqual(["Info"]);
  });
});
