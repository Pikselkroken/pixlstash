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
import { useNoticeStore } from "../../stores/useNoticeStore";

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

// What a read of pictures by id (`/pictures?id=…&fields=grid`) answers.
let gridRows = [];

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
  if (typeof url === "string" && url.startsWith("/pictures?id=")) {
    return { data: gridRows };
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
  ProgressOverlay: true,
  VTooltip: true,
  OverlayEditPanel: {
    name: "OverlayEditPanel",
    props: [
      "pictureId",
      "active",
      "comfyuiProgress",
      "comfyuiProgressPercent",
      "paint",
      "applyToIds",
    ],
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
  gridRows = [];
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
    // Hidden, not unmounted: the typed instruction and a run in flight live
    // in the panel.
    expect(wrapper.find(".edit-stub").exists()).toBe(true);

    await wrapper.setProps({ initialImageId: 7 });
    await flush();
    await flush();
    expect(activeTab(wrapper)).toBe("Edit");
  });

  const editPanel = (wrapper) =>
    wrapper.findComponent({ name: "OverlayEditPanel" });
  const lightboxBar = (wrapper) =>
    wrapper.find(".overlay-progress--comfyui").exists();
  const running = { visible: true, status: "running", message: "ComfyUI" };

  it("drops the lightbox ComfyUI bar only while the Edit tab shows the run", async () => {
    const wrapper = await openOn(7, undefined, configured);
    await wrapper.setProps({ comfyuiProgress: running });
    // A run from the grid's menus: the Edit tab has nothing of its own.
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    expect(lightboxBar(wrapper)).toBe(true);
    editPanel(wrapper).vm.$emit("running", true);
    await flush();
    expect(lightboxBar(wrapper)).toBe(false);
    // On Info the tab's bar is out of sight, so the lightbox's comes back.
    await wrapper.findAll(".inspector-tab")[0].trigger("click");
    expect(lightboxBar(wrapper)).toBe(true);
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    editPanel(wrapper).vm.$emit("running", false);
    await flush();
    expect(lightboxBar(wrapper)).toBe(true);
  });

  it("shows an edit result the grid has not inserted yet", async () => {
    gridRows = [{ id: 8, stack_id: "s1", tags: [] }];
    const wrapper = await openOn(
      7,
      [
        { id: 7, stack_id: "s1", tags: [] },
        { id: 5, tags: [] },
      ],
      configured,
    );
    editPanel(wrapper).vm.$emit("show-picture", 8, 7);
    await flush();
    await flush();
    expect(editPanel(wrapper).props("pictureId")).toBe(8);
    expect(wrapper.emitted("show-picture")).toEqual([[8]]);
    expect(getMock).toHaveBeenCalledWith("/pictures?id=8&fields=grid");
  });

  it("stays put and says so when the result cannot be read", async () => {
    const wrapper = await openOn(7, undefined, configured);
    const error = vi.spyOn(useNoticeStore(), "error");
    editPanel(wrapper).vm.$emit("show-picture", 8, 7);
    await flush();
    await flush();
    expect(editPanel(wrapper).props("pictureId")).toBe(7);
    expect(wrapper.emitted("show-picture")).toBeUndefined();
    expect(error).toHaveBeenCalledOnce();
  });

  it("lays the original over a result it stepped to, for the compare slider", async () => {
    gridRows = [{ id: 8, stack_id: "s1", tags: [], format: "png" }];
    const wrapper = await openOn(
      7,
      [
        { id: 7, stack_id: "s1", tags: [], format: "png" },
        { id: 5, tags: [] },
      ],
      configured,
    );
    editPanel(wrapper).vm.$emit("show-picture", 8, 7);
    await flush();
    await flush();
    expect(wrapper.vm.compare).toMatchObject({ resultId: "8" });
    expect(wrapper.vm.compare.beforeSrc).toContain("/7");
  });

  it("drags the compare cut anywhere on the picture, whatever tool is in hand", async () => {
    gridRows = [{ id: 8, stack_id: "s1", tags: [], format: "png" }];
    const wrapper = await openOn(
      7,
      [{ id: 7, stack_id: "s1", tags: [], format: "png" }],
      configured,
    );
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    wrapper.vm.paint.setTool("rect");
    editPanel(wrapper).vm.$emit("show-picture", 8, 7);
    await flush();
    await flush();
    // A measured picture, as the lightbox has once the result has loaded.
    const img = wrapper.find(".overlay-img");
    for (const [property, value] of [
      ["complete", true],
      ["naturalWidth", 800],
      ["naturalHeight", 600],
      ["clientWidth", 400],
      ["clientHeight", 300],
    ]) {
      Object.defineProperty(img.element, property, { configurable: true, value });
    }
    await img.trigger("load");
    await flush();
    // The rectangle tool's input layer would sit over the slider.
    expect(wrapper.find(".paint-input-layer").exists()).toBe(false);
    const range = wrapper.find(".paint-compare-range");
    range.element.getBoundingClientRect = () => ({ left: 100, width: 400 });
    range.element.setPointerCapture = () => {};
    range.element.hasPointerCapture = () => true;
    // jsdom has no PointerEvent; a MouseEvent of the same name carries clientX.
    const pointer = (type, clientX) =>
      range.element.dispatchEvent(
        new MouseEvent(type, { clientX, bubbles: true, cancelable: true }),
      );
    pointer("pointerdown", 200);
    expect(wrapper.vm.comparePosition).toBe(25);
    pointer("pointermove", 460);
    expect(wrapper.vm.comparePosition).toBe(90);
    // Out again without leaving the lightbox: Escape on the slider...
    await range.trigger("keydown", { key: "Escape" });
    expect(wrapper.find(".paint-compare").exists()).toBe(false);
    expect(wrapper.emitted("close")).toBeUndefined();
    // ...Escape anywhere else, or the Done button.
    const reopen = async () => {
      wrapper.vm.compare = { resultId: "8", beforeSrc: "http://test/pictures/7.png" };
      await flush();
    };
    await reopen();
    expect(wrapper.find(".paint-compare").exists()).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flush();
    expect(wrapper.find(".paint-compare").exists()).toBe(false);
    expect(wrapper.emitted("close")).toBeUndefined();
    await reopen();
    await wrapper.find(".compare-exit-main").trigger("click");
    expect(wrapper.find(".paint-compare").exists()).toBe(false);
  });

  it("sets no compare when the reader was not on the original", async () => {
    gridRows = [{ id: 8, tags: [] }];
    const wrapper = await openOn(5, [{ id: 5, tags: [] }], configured);
    editPanel(wrapper).vm.$emit("show-picture", 8, 7);
    await flush();
    await flush();
    expect(wrapper.vm.compare).toBeNull();
  });

  it("opens on the Edit tab for the grid's Edit…, offering every picture", async () => {
    const wrapper = await openOn(7, undefined, configured);
    expect(activeTab(wrapper)).toBe("Info");
    await wrapper.setProps({ editRequest: { pictureIds: [7, 5, 3] } });
    await flush();
    expect(activeTab(wrapper)).toBe("Edit");
    expect(editPanel(wrapper).props("applyToIds")).toEqual([7, 5, 3]);
    // One picture is no batch: nothing extra to offer.
    await wrapper.setProps({ editRequest: { pictureIds: [7] } });
    await flush();
    expect(editPanel(wrapper).props("applyToIds")).toEqual([]);
  });

  it("takes the Paint keys only while the Edit tab is open", async () => {
    const wrapper = await openOn(7, undefined, configured);
    const press = (key) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    press("b");
    expect(wrapper.vm.paint.tool.value).toBe("hand");
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    press("b");
    expect(wrapper.vm.paint.tool.value).toBe("brush");
    press("e");
    expect(wrapper.vm.paint.tool.value).toBe("eraser");
  });

  it("clears the selection on the first Escape and closes on the next", async () => {
    const wrapper = await openOn(7, undefined, configured);
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    wrapper.vm.paint.setTool("wand");
    wrapper.vm.paint.begin({ x: 0.5, y: 0.5 });
    wrapper.vm.paint.end();
    // From a Refine slider just let go of, as from anywhere else.
    const slider = document.createElement("input");
    slider.type = "range";
    document.body.appendChild(slider);
    slider.focus();
    slider.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    slider.remove();
    expect(wrapper.vm.paint.hasSelection.value).toBe(false);
    expect(wrapper.emitted("close")).toBeUndefined();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("compares two pictures Ctrl-clicked in the filmstrip, ComfyUI or not", async () => {
    const wrapper = await openOn(7, [
      { id: 7, tags: [], format: "png" },
      { id: 5, tags: [], format: "png" },
    ]);
    const compareButton = () =>
      wrapper
        .findAll(".overlay-icon-btn")
        .find((b) => b.find("v-icon").text() === "mdi-compare-horizontal");
    // Shown, and greyed with how to pick, before there are two.
    expect(compareButton().attributes("aria-disabled")).toBe("true");
    expect(compareButton().attributes("aria-label")).toContain("Ctrl-click");
    await compareButton().trigger("click");
    expect(wrapper.vm.compare).toBeNull();
    // One Ctrl-click on another thumb picks it and the picture on screen.
    wrapper
      .findComponent({ name: "OverlayFilmstrip" })
      .vm.$emit("pick", { id: 5, index: 1 });
    await flush();
    expect(wrapper.vm.filmstripPicks).toEqual(["7", "5"]);
    expect(compareButton().attributes("aria-disabled")).toBeUndefined();
    await compareButton().trigger("click");
    await flush();
    expect(wrapper.vm.compare).toMatchObject({
      resultId: "5",
      beforeLabel: "A",
      afterLabel: "B",
    });
    expect(wrapper.vm.compare.beforeSrc).toContain("/7");
    expect(wrapper.vm.image.id).toBe(5);
    expect(wrapper.vm.filmstripPicks).toEqual([]);
  });

  it("leaves the comparison onto either side, from the pill or the tags", async () => {
    const wrapper = await openOn(5, [
      { id: 7, tags: [], format: "png" },
      { id: 5, tags: [], format: "png" },
    ]);
    const start = async () => {
      await wrapper.setProps({ compareRequest: { beforeId: 7, afterId: 5 } });
      await flush();
    };
    // Measured, so the comparison draws over the picture.
    const img = wrapper.find(".overlay-img");
    for (const [property, value] of [
      ["complete", true],
      ["naturalWidth", 800],
      ["naturalHeight", 600],
      ["clientWidth", 400],
      ["clientHeight", 300],
    ]) {
      Object.defineProperty(img.element, property, { configurable: true, value });
    }
    await img.trigger("load");
    await start();
    // The pill's After side stays on the picture under the cut.
    await wrapper.find(".compare-exit-side--after").trigger("click");
    expect(wrapper.vm.compare).toBeNull();
    expect(wrapper.vm.image.id).toBe(5);
    // The tag's Before side goes to the picture laid over it.
    await start();
    expect(wrapper.find(".paint-compare-tag--before").text()).toContain("A");
    await wrapper.find(".paint-compare-tag--before").trigger("click");
    await flush();
    expect(wrapper.vm.compare).toBeNull();
    expect(wrapper.vm.image.id).toBe(7);
  });

  it("leaves the Paint keys alone under a comparison", async () => {
    const wrapper = await openOn(
      7,
      [{ id: 7, tags: [], format: "png" }],
      configured,
    );
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    const img = wrapper.find(".overlay-img");
    for (const [property, value] of [
      ["complete", true],
      ["naturalWidth", 800],
      ["naturalHeight", 600],
      ["clientWidth", 400],
      ["clientHeight", 300],
    ]) {
      Object.defineProperty(img.element, property, { configurable: true, value });
    }
    await img.trigger("load");
    wrapper.vm.compare = { resultId: "7", beforeSrc: "http://test/pictures/5.png" };
    await flush();
    const press = (key) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    press("b");
    press("i");
    expect(wrapper.vm.paint.tool.value).toBe("hand");
    expect(wrapper.vm.paint.steps.value).toEqual([]);
  });

  it("counts a pick whose row is not loaded, refusing only a known video", async () => {
    const wrapper = await openOn(7, [
      { id: 7, tags: [], format: "png" },
      { id: 4, tags: [], format: "mp4" },
    ]);
    const filmstrip = wrapper.findComponent({ name: "OverlayFilmstrip" });
    filmstrip.vm.$emit("pick", { id: 99, index: 5 });
    await flush();
    expect(wrapper.vm.filmstripCompareReason).toBeNull();
    filmstrip.vm.$emit("pick", { id: 99, index: 5 });
    filmstrip.vm.$emit("pick", { id: 4, index: 1 });
    await flush();
    expect(wrapper.vm.filmstripCompareReason).toBe(
      "Compare works on pictures, not videos",
    );
  });

  it("refuses to compare a video it could only guess was a picture", async () => {
    gridRows = [{ id: 99, tags: [], format: "mp4" }];
    const wrapper = await openOn(7, [{ id: 7, tags: [], format: "png" }]);
    const filmstrip = wrapper.findComponent({ name: "OverlayFilmstrip" });
    filmstrip.vm.$emit("pick", { id: 99, index: 5 });
    await flush();
    expect(wrapper.vm.filmstripCompareReason).toBeNull();
    wrapper.vm.compareFilmstripPicks();
    await flush();
    await flush();
    expect(wrapper.vm.compare).toBeNull();
    // Still picked, so the video can be swapped out rather than both re-picked.
    expect(wrapper.vm.filmstripPicks).toEqual(["7", "99"]);
  });

  it("offers Compare on a filmstrip right-click, greyed until there are two", async () => {
    const wrapper = await openOn(7, [
      { id: 7, tags: [], format: "png" },
      { id: 5, tags: [], format: "png" },
    ]);
    const filmstrip = wrapper.findComponent({ name: "OverlayFilmstrip" });
    const thumb = document.body.appendChild(document.createElement("button"));
    const rightClick = async (item) => {
      filmstrip.vm.$emit("context-menu", item, {
        clientX: 40,
        clientY: 80,
        currentTarget: thumb,
      });
      await flush();
    };
    const menu = () => document.body.querySelector(".filmstrip-ctx-menu");
    const compareItem = () => menu().querySelector(".ctx-item");
    await rightClick({ id: 7, index: 0 });
    expect(compareItem().disabled).toBe(true);
    expect(compareItem().textContent).toContain("Ctrl-click another");
    // Escape closes the menu, not the lightbox, and hands focus back.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flush();
    expect(menu()).toBeNull();
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(document.activeElement).toBe(thumb);
    // A press anywhere else closes it too.
    await rightClick({ id: 7, index: 0 });
    window.dispatchEvent(new MouseEvent("pointerdown"));
    await flush();
    expect(menu()).toBeNull();
    // A right-click on another picture pairs it with the one on screen.
    await rightClick({ id: 5, index: 1 });
    expect(wrapper.vm.filmstripPicks).toEqual(["7", "5"]);
    expect(compareItem().disabled).toBe(false);
    compareItem().click();
    await flush();
    await flush();
    expect(wrapper.vm.compare).toMatchObject({ resultId: "5", beforeLabel: "A" });
    expect(menu()).toBeNull();
    thumb.remove();
  });

  it("brings a selection step back with Ctrl+Y before any library redo", async () => {
    const wrapper = await openOn(7, undefined, configured);
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    wrapper.vm.paint.setTool("wand");
    wrapper.vm.paint.begin({ x: 0.5, y: 0.5 });
    wrapper.vm.paint.end();
    const key = (init) =>
      window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, ...init }));
    key({ key: "z", ctrlKey: true });
    expect(wrapper.vm.paint.hasSelection.value).toBe(false);
    key({ key: "y", ctrlKey: true });
    expect(wrapper.vm.paint.hasSelection.value).toBe(true);
    // With a selection and nothing of it to redo, still never a library redo.
    const redo = vi.spyOn(wrapper.vm.operationStore, "redo");
    key({ key: "y", ctrlKey: true });
    expect(redo).not.toHaveBeenCalled();
  });

  it("keeps two filmstrip picks, a third dropping the oldest", async () => {
    const wrapper = await openOn(7, [{ id: 7, tags: [], format: "png" }]);
    const filmstrip = wrapper.findComponent({ name: "OverlayFilmstrip" });
    filmstrip.vm.$emit("pick", { id: 5, index: 1 });
    filmstrip.vm.$emit("pick", { id: 3, index: 2 });
    await flush();
    expect(wrapper.vm.filmstripPicks).toEqual(["5", "3"]);
  });

  it("lays the grid's first picked picture over the second", async () => {
    const wrapper = await openOn(5, [
      { id: 7, tags: [], format: "png" },
      { id: 5, tags: [], format: "png" },
    ]);
    await wrapper.setProps({ compareRequest: { beforeId: 7, afterId: 5 } });
    await flush();
    expect(wrapper.vm.compare).toMatchObject({ resultId: "5", beforeLabel: "A" });
    expect(wrapper.vm.compare.beforeSrc).toContain("/7");
  });

  it("keeps the chrome up when a control's own click re-renders the part clicked", async () => {
    // Run edit's play icon goes as the run starts, before the lightbox sees
    // the click: detached, it has no button left to find above it.
    const wrapper = await openOn(7, undefined, configured);
    const later = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 1000);
    const button = document.createElement("button");
    const icon = document.createElement("i");
    button.appendChild(icon);
    wrapper.find(".overlay-canvas").element.appendChild(button);
    icon.addEventListener("click", () => icon.remove());
    icon.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await flush();
    expect(wrapper.find(".overlay-shell").classes()).not.toContain(
      "chrome-hidden",
    );
    later.mockRestore();
  });

  it("keeps the chrome up when the toolbox or options strip is clicked between its buttons", async () => {
    const wrapper = await openOn(7, undefined, configured);
    await wrapper.findAll(".inspector-tab")[2].trigger("click");
    const later = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 1000);
    for (const selector of [".paint-toolbox", ".paint-options"]) {
      const strip = wrapper.find(selector);
      expect(strip.exists()).toBe(true);
      await strip.trigger("click");
      expect(wrapper.find(".overlay-shell").classes()).not.toContain(
        "chrome-hidden",
      );
    }
    later.mockRestore();
  });

  it("does not move a reader who stepped on while the result was read", async () => {
    gridRows = [{ id: 8, tags: [] }];
    const wrapper = await openOn(
      7,
      [
        { id: 7, tags: [] },
        { id: 5, tags: [] },
      ],
      configured,
    );
    editPanel(wrapper).vm.$emit("show-picture", 8, 7);
    await wrapper.setProps({ initialImageId: 5 });
    await flush();
    await flush();
    expect(editPanel(wrapper).props("pictureId")).toBe(5);
    expect(wrapper.emitted("show-picture")).toBeUndefined();
  });
});
