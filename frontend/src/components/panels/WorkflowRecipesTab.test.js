// The Recipes tab (v1.12 F6): the three things that can silently invert.
//
// * The order. A move writes the WHOLE list back, in the order the rail now
//   shows. A body that sends the old order, or only the moved id, leaves the
//   rail and the server disagreeing until the next read.
// * A refused write. The rail is moved first so it does not stall, so it has
//   to be put back when the server says no - otherwise the tab shows an order
//   that does not exist.
// * The read. The tab lists the selected workflows' recipes by workflow id
//   (`GET /recipes?workflow_id=`), #1623.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listSavedRecipes = vi.fn();
const getPictureRecipe = vi.fn();
const reorderSavedRecipes = vi.fn();
const editSavedRecipe = vi.fn();
const deleteSavedRecipe = vi.fn();
const listUsedLooks = vi.fn();
const getWorkflowCard = vi.fn();
const setWorkflowDefaults = vi.fn();
const extractRecipeWorkflow = vi.fn();
const listUnfiledRecipes = vi.fn();
const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
vi.mock("../../api/comfyui", () => ({
  getPictureRecipe: (...args) => getPictureRecipe(...args),
}));
vi.mock("../../api/modelShelf", () => ({ listAdapters: async () => [] }));
vi.mock("../../api/recipes", () => ({
  listUsedLooks: (...args) => listUsedLooks(...args),
  listSavedRecipes: (...args) => listSavedRecipes(...args),
  reorderSavedRecipes: (...args) => reorderSavedRecipes(...args),
  editSavedRecipe: (...args) => editSavedRecipe(...args),
  deleteSavedRecipe: (...args) => deleteSavedRecipe(...args),
  extractRecipeWorkflow: (...args) => extractRecipeWorkflow(...args),
  listUnfiledRecipes: (...args) => listUnfiledRecipes(...args),
  exportSavedRecipe: vi.fn(),
}));
vi.mock("../../api/workflows", () => ({
  exportWorkflow: vi.fn(),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  setWorkflowDefaults: (...args) => setWorkflowDefaults(...args),
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/api/v1/pictures/thumbnails/${id}.webp`,
}));
const confirmed = vi.hoisted(() => ({ value: true }));
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: async () => confirmed.value }),
}));
const openRun = vi.fn();
vi.mock("../../stores/useRunDialogStore", () => ({
  useRunDialogStore: () => ({ openRun }),
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { useNoticeStore } from "../../stores/useNoticeStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import WorkflowRecipesTab from "./WorkflowRecipesTab.vue";

const KEY = `auto:${"a".repeat(64)}`;

function recipe(id, name, extra = {}) {
  return {
    id,
    name,
    position: id,
    workflow_id: KEY,
    prompt: `${name} prompt`,
    loras: [{ filename: "mira_v2.safetensors", strength: 0.85 }],
    overrides: { "KSampler/steps": 12 },
    pictures: 31,
    source_picture_id: 7,
    ...extra,
  };
}

/** A workflow detail whose default recipe `recipe()` changes nothing in. */
function detail(id = KEY, name = "Cinematic portrait", extra = {}) {
  return {
    card: {
      id,
      name,
      default_recipe: {
        models: [
          { address: "core:Loader/ckpt_name", kind: "checkpoint", filename: "juggernaut.safetensors" },
        ],
        loras: [{ filename: "mira_v2.safetensors", sha256: null, strength: 0.85 }],
        values: [
          { label: "Steps", slot_label: "core:KSampler", input_name: "steps", value: 12, provenance: "best" },
          { label: "CFG", slot_label: "core:KSampler", input_name: "cfg", value: 7, provenance: "edited" },
        ],
        ...extra,
      },
    },
  };
}

/** The diff lines, top to bottom. */
function diffsOf(wrapper) {
  return wrapper.findAll(".wfrt-diff").map((line) => line.text());
}

function render(props = {}) {
  return mount(WorkflowRecipesTab, {
    props: {
      workflowIds: [KEY],
      workflowName: "Cinematic portrait",
      ...props,
    },
    global: { stubs: { teleport: true } },
  });
}

/** The recipe names the rail is showing, top to bottom. */
function namesOf(wrapper) {
  return wrapper.findAll(".wfrt-name").map((row) => row.text());
}

describe("WorkflowRecipesTab", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    confirmed.value = true;
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform"),
      recipe(2, "Red coat, backlit"),
      recipe(3, "Harbour fog"),
    ]);
    reorderSavedRecipes.mockImplementation(async (ids) => ids);
    listUsedLooks.mockResolvedValue([]);
    getWorkflowCard.mockImplementation(async (id) => detail(id));
    getPictureRecipe.mockResolvedValue({
      workflow_id: KEY,
      positive_prompt: "a look nobody kept",
      loras: ["mira_v2.safetensors"],
      model_slots: [
        { name: "mira_v2.safetensors", widget: "lora_name", strength: 0.7 },
      ],
      seed_text: "42",
    });
  });

  it("lists the workflow's recipes by its id", async () => {
    const wrapper = render();
    await flushPromises();
    expect(listSavedRecipes).toHaveBeenCalledWith([KEY]);
    expect(wrapper.find(".wfrt-title").text()).toBe("Cinematic portrait");
    expect(namesOf(wrapper)).toEqual([
      "Rainy tram platform",
      "Red coat, backlit",
      "Harbour fog",
    ]);
    expect(wrapper.find(".wfrt-sub").text()).toBe(
      "3 saved",
    );
    // Nothing but the prompt differs from the default, and the card says so
    // in place of the LoRA chips and the facts line (#1653).
    expect(diffsOf(wrapper)[0]).toBe("Only the prompt");
    expect(wrapper.find(".wfrt-chip").exists()).toBe(false);
    expect(wrapper.find(".wfrt-facts").exists()).toBe(false);
  });

  it("writes the whole new order when a recipe is moved by keyboard", async () => {
    const wrapper = render();
    await flushPromises();
    // Alt+↓ on the first handle: it goes below the second.
    await wrapper.findAll(".wfrt-handle")[0].trigger("keydown", {
      key: "ArrowDown",
      altKey: true,
    });
    await flushPromises();

    expect(reorderSavedRecipes).toHaveBeenCalledWith([2, 1, 3]);
    expect(namesOf(wrapper)).toEqual([
      "Red coat, backlit",
      "Rainy tram platform",
      "Harbour fog",
    ]);
  });

  it("does not move the first recipe up, or the last one down", async () => {
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll(".wfrt-handle")[0]
      .trigger("keydown", { key: "ArrowUp", altKey: true });
    await wrapper
      .findAll(".wfrt-handle")[2]
      .trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();

    expect(reorderSavedRecipes).not.toHaveBeenCalled();
  });

  it("puts the order back when the server refuses the write", async () => {
    reorderSavedRecipes.mockRejectedValue(new Error("no"));
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll(".wfrt-handle")[0]
      .trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();

    expect(namesOf(wrapper)).toEqual([
      "Rainy tram platform",
      "Red coat, backlit",
      "Harbour fog",
    ]);
  });

  it("deletes a recipe once it is confirmed, and not before", async () => {
    deleteSavedRecipe.mockResolvedValue({ deleted: 2 });
    confirmed.value = false;
    const wrapper = render();
    await flushPromises();
    // Four ⋯ entries per card, so the second card's Delete is the eighth.
    const deleteSecond = () => wrapper.findAll(".ctx-item")[7].trigger("click");

    await deleteSecond();
    await flushPromises();
    expect(deleteSavedRecipe).not.toHaveBeenCalled();
    expect(namesOf(wrapper)).toHaveLength(3);

    confirmed.value = true;
    await deleteSecond();
    await flushPromises();
    expect(deleteSavedRecipe).toHaveBeenCalledWith(2);
    expect(namesOf(wrapper)).toEqual(["Rainy tram platform", "Harbour fog"]);
  });

  it("renames a recipe in place", async () => {
    editSavedRecipe.mockResolvedValue({ id: 1, name: "Tram, rewritten" });
    const wrapper = render();
    await flushPromises();
    await wrapper.findAll(".ctx-item")[0].trigger("click"); // Rename
    await flushPromises();
    await wrapper.find(".wfrt-card input").setValue("Tram, rewritten");
    await wrapper.find(".wfrt-card input").trigger("keydown", { key: "Enter" });
    await flushPromises();

    expect(editSavedRecipe).toHaveBeenCalledWith(1, { name: "Tram, rewritten" });
    expect(namesOf(wrapper)[0]).toBe("Tram, rewritten");
  });

  // ── The looks the pictures already carry ────────────────────────────────
  //
  // The tab shipped listing only what somebody had pressed Save on, so it was
  // empty on every workflow of a full library. These are the looks that were
  // actually run.

  const LOOK = {
    prompt: "a look nobody kept",
    loras: [{ filename: "mira_v2.safetensors" }],
    pictures: 12,
    cover_picture_id: 88,
  };

  it("offers no help text, saved recipes or not", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();
    expect(wrapper.find(".wfrt-hint").exists()).toBe(false);
    expect(wrapper.find(".wfrt-sub").text()).toBe(
      "1 recipe from your pictures",
    );
  });

  it("lists the looks the pictures carry when nothing is saved", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();

    expect(listUsedLooks).toHaveBeenCalledWith([KEY]);
    // Not the empty sentence: a library with pictures has looks to show.
    expect(wrapper.text()).not.toContain("No recipes here yet");
    expect(wrapper.text()).toContain("a look nobody kept");
    expect(wrapper.text()).toContain("12 pictures");
    expect(wrapper.text()).toContain("From your pictures");
  });

  it("says so only when there is neither a recipe nor a look", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([]);
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain("No recipes here yet");
    // Never "nothing has been made with this workflow": a picture only shows
    // up once its ComfyUI metadata has been read, so an unread workflow is not
    // an empty one.
    expect(wrapper.text()).not.toContain("Nothing has been made");
    // An answered read of nothing is a count, and says 0.
    expect(wrapper.find(".wfrt-sub").text()).toBe(
      "0 recipes from your pictures",
    );
  });

  it("counts nothing before a read has answered, or when it failed", async () => {
    let answer;
    listSavedRecipes.mockImplementation(
      () => new Promise((resolve) => (answer = resolve)),
    );
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain("Reading your recipes");
    expect(wrapper.find(".wfrt-sub").text()).toBe("");
    answer([]);
    await flushPromises();
    expect(wrapper.find(".wfrt-sub").text()).toBe("0 recipes from your pictures");

    listSavedRecipes.mockRejectedValue(new Error("boom"));
    useWorkflowsStore().notedRecipesChanged();
    await flushPromises();
    expect(wrapper.find(".wfrt-sub").text()).toBe("");
  });

  it("drops a superseded read's loading note when the selection empties", async () => {
    listSavedRecipes.mockImplementation(() => new Promise(() => {}));
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain("Reading your recipes");
    await wrapper.setProps({ workflowIds: [] });
    await flushPromises();
    expect(wrapper.text()).not.toContain("Reading your recipes");
  });

  it("keeps an unsaved look from its cover picture's own recipe", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Clone…"))
      .trigger("click");
    await flushPromises();

    // The picture is read for the two things a picture ROW does not store:
    // the LoRA strengths and the seed.
    expect(getPictureRecipe).toHaveBeenCalledWith(88, { preflight: false });
    const dialog = wrapper.findComponent({ name: "SaveRecipeDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("seed")).toBe("42");
    expect(dialog.props("sourcePictureId")).toBe(88);
    expect(dialog.props("loras")).toEqual([
      { filename: "mira_v2.safetensors", sha256: "", strength: 0.7 },
    ]);
  });

  it("runs an unsaved look from the picture that made it", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"))
      .trigger("click");

    // No `saved_recipe_id` exists for a look, so the run's source is the
    // picture: "run what made this" is exactly what the look is.
    // And the row's prompt as the box's editable starting text: the picture's
    // re-read can come back without one.
    expect(openRun).toHaveBeenCalledWith({
      kind: "picture",
      pictureIds: [88],
      prompt: "a look nobody kept",
    });
  });

  it("opens a recipe's picture from its thumbnail, in both lists", async () => {
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();
    const store = useWorkflowsStore();
    const thumbs = wrapper.findAll(".wfrt-thumb-btn");
    // Three saved (source picture 7) and one used (cover 88).
    expect(thumbs).toHaveLength(4);
    await thumbs[0].trigger("click");
    expect(store.pictureToOpen).toBe(7);
    await thumbs[3].trigger("click");
    expect(store.pictureToOpen).toBe(88);
  });

  it("asks about every selected workflow at once", async () => {
    const OTHER = "b".repeat(64);
    render({ workflowIds: [KEY, OTHER] });
    await flushPromises();
    expect(listSavedRecipes).toHaveBeenCalledWith([KEY, OTHER]);
    expect(listUsedLooks).toHaveBeenCalledWith([KEY, OTHER]);
  });

  it("opens the Run popup ON THE RECIPE, not on the card", async () => {
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"))
      .trigger("click");

    expect(openRun).toHaveBeenCalledTimes(1);
    const source = openRun.mock.calls[0][0];
    // `savedRecipe` is the whole point of the button: without it the popup
    // opens on the bare card and the recipe's look is not what runs.
    expect(source.savedRecipe).toMatchObject({ id: 1 });
    expect(source.workflowId).toBe(KEY);
    expect(source).not.toHaveProperty("workflowKey");
  });

  it("re-reads when a recipe is saved on another surface", async () => {
    const store = useWorkflowsStore();
    const wrapper = render();
    await flushPromises();
    expect(listSavedRecipes).toHaveBeenCalledTimes(1);

    // The Run popup, opened from this very tab, saving onto the card the tab
    // is already showing: the selection never moves, so nothing else would
    // tell this list it is out of date.
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform"),
      recipe(2, "Red coat, backlit"),
      recipe(3, "Harbour fog"),
      recipe(4, "Saved from the Run popup"),
    ]);
    store.notedRecipesChanged();
    await flushPromises();

    expect(listSavedRecipes).toHaveBeenCalledTimes(2);
    expect(namesOf(wrapper)).toContain("Saved from the Run popup");
  });

  it("keeps a refused reorder off another card's list", async () => {
    // Switching cards mid-write and then being refused used to render the
    // previous card's recipes under the new card's header.
    let refuse;
    reorderSavedRecipes.mockImplementation(
      () => new Promise((_, reject) => (refuse = reject)),
    );
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll(".wfrt-handle")[0]
      .trigger("keydown", { key: "ArrowDown", altKey: true });

    listSavedRecipes.mockResolvedValue([recipe(9, "Another card's recipe")]);
    await wrapper.setProps({ workflowIds: ["b".repeat(64)] });
    await flushPromises();
    refuse(new Error("no"));
    await flushPromises();

    expect(namesOf(wrapper)).toEqual(["Another card's recipe"]);
  });

  it("says a move and a refusal to a reader who is not watching", async () => {
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll(".wfrt-handle")[0]
      .trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();
    expect(wrapper.find(".wfrt-live").text()).toBe(
      "Rainy tram platform moved to 2 of 3.",
    );

    reorderSavedRecipes.mockRejectedValue(new Error("nope"));
    await wrapper
      .findAll(".wfrt-handle")[0]
      .trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();
    expect(wrapper.find(".wfrt-live").text()).toContain("Order not saved");
  });

  it("backs a rename out on Escape, and commits it on blur", async () => {
    editSavedRecipe.mockResolvedValue({ id: 1, name: "Committed on blur" });
    const wrapper = render();
    await flushPromises();

    await wrapper.findAll(".ctx-item")[0].trigger("click");
    await flushPromises();
    await wrapper.find(".wfrt-card input").setValue("Thrown away");
    await wrapper.find(".wfrt-card input").trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(editSavedRecipe).not.toHaveBeenCalled();
    expect(namesOf(wrapper)[0]).toBe("Rainy tram platform");

    await wrapper.findAll(".ctx-item")[0].trigger("click");
    await flushPromises();
    await wrapper.find(".wfrt-card input").setValue("Committed on blur");
    await wrapper.find(".wfrt-card input").trigger("blur");
    await flushPromises();
    expect(editSavedRecipe).toHaveBeenCalledWith(1, {
      name: "Committed on blur",
    });
  });

  it("says so when the order cannot be written", async () => {
    reorderSavedRecipes.mockRejectedValue(new Error("no"));
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll(".wfrt-handle")[0]
      .trigger("keydown", { key: "ArrowDown", altKey: true });
    await flushPromises();
    expect(useNoticeStore().notices.map((n) => n.level)).toContain("error");
  });

  it("refuses a selection bigger than one read, in words", async () => {
    // One shift-click down a long grid is a single gesture. The server would
    // 422, and the catch would wipe both halves behind "Could not read your
    // saved recipes", which is neither true nor useful.
    const many = Array.from({ length: 101 }, (_, i) => `key-${i}`);
    const wrapper = render({ workflowIds: many });
    await flushPromises();

    expect(listSavedRecipes).not.toHaveBeenCalled();
    expect(listUsedLooks).not.toHaveBeenCalled();
    expect(wrapper.find("[role=alert]").text()).toContain(
      "Too many workflows selected",
    );
  });

  it("saves an unsaved look under the look's own key, not the re-read's", async () => {
    // The look was keyed on the stored columns; the live extraction can
    // differ. Saving the re-read's version makes a recipe whose key is not
    // this look's, so the look stays unmarked and the new recipe is
    // credited 0 — the one thing both halves promise cannot happen.
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    getPictureRecipe.mockResolvedValue({
      workflow_id: KEY,
      // What the graph says today, which is not what the column holds.
      positive_prompt: "a prompt the column never got",
      loras: ["some/Other.safetensors"],
      model_slots: [
        { name: "mira_v2.safetensors", widget: "lora_name", strength: 0.7 },
      ],
      seed_text: "42",
    });
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Clone…"))
      .trigger("click");
    await flushPromises();

    const dialog = wrapper.findComponent({ name: "SaveRecipeDialog" });
    expect(dialog.props("prompt")).toBe(LOOK.prompt);
    expect(dialog.props("loras").map((row) => row.filename)).toEqual([
      "mira_v2.safetensors",
    ]);
    // Still the two things the columns do not hold.
    expect(dialog.props("seed")).toBe("42");
    expect(dialog.props("loras")[0].strength).toBe(0.7);
  });

  it("refuses to file a look on a workflow the picture does not name", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    getPictureRecipe.mockResolvedValue({ workflow_id: null });
    const wrapper = render({ workflowIds: [KEY, "b".repeat(64)] });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Clone…"))
      .trigger("click");
    await flushPromises();

    // Never the first of the selection: that files it on a card that never
    // made it, and it would be credited 0 for good.
    expect(wrapper.findComponent({ name: "SaveRecipeDialog" }).exists()).toBe(
      false,
    );
    expect(useNoticeStore().notices.map((n) => n.level)).toContain("error");
  });

  it("lists saved recipes above the used ones, and marks a cloned one in place", async () => {
    const kept = recipe(1, "Rainy tram platform", {
      prompt: "a kept look",
      loras: [{ filename: "mira_v2.safetensors", strength: 0.85 }],
    });
    listSavedRecipes.mockResolvedValue([kept]);
    listUsedLooks.mockResolvedValue([
      { ...LOOK, prompt: "a kept look", saved: true },
      LOOK,
    ]);
    const wrapper = render();
    await flushPromises();

    const labels = wrapper.findAll(".section-label").map((l) => l.text());
    expect(labels).toEqual(["Saved", "From your pictures"]);
    const lists = wrapper.findAll(".wfrt-list");
    expect(lists).toHaveLength(2);
    expect(lists[0].text()).toContain("Rainy tram platform");
    expect(lists[0].text()).not.toContain("a look nobody kept");

    // The cloned one is still in the list, marked Saved, with no Clone…;
    // the other one offers it.
    const [markedRow, plainRow] = lists[1].findAll(".wfrt-card");
    expect(markedRow.text()).toContain("a kept look");
    expect(markedRow.find(".wfrt-marked").exists()).toBe(true);
    expect(markedRow.text()).not.toContain("Clone…");
    expect(plainRow.find(".wfrt-marked").exists()).toBe(false);
    expect(plainRow.text()).toContain("Clone…");
    expect(wrapper.find(".wfrt-sub").text()).toContain(
      "1 saved · 2 recipes from your pictures",
    );
  });

  it("re-reads the looks after a removal and shows the server's mark", async () => {
    deleteSavedRecipe.mockResolvedValue({ deleted: 1 });
    const kept = recipe(1, "Rainy tram platform", { prompt: "a kept look" });
    listSavedRecipes.mockResolvedValue([kept]);
    listUsedLooks
      .mockResolvedValueOnce([{ ...LOOK, prompt: "a kept look", saved: true }])
      .mockResolvedValueOnce([{ ...LOOK, prompt: "a kept look", saved: false }]);
    const wrapper = render();
    await flushPromises();
    expect(wrapper.find(".wfrt-marked").exists()).toBe(true);

    // Rename, Export…, Extract workflow, Delete.
    await wrapper.findAll(".ctx-item")[3].trigger("click");
    await flushPromises();

    expect(deleteSavedRecipe).toHaveBeenCalledWith(1);
    expect(listUsedLooks).toHaveBeenCalledTimes(2);
    // Only the looks were read again, not the whole tab.
    expect(listSavedRecipes).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("a kept look");
    expect(wrapper.find(".wfrt-marked").exists()).toBe(false);
    expect(wrapper.text()).toContain("Clone…");
  });

  it("keeps a removal that lands late off another card's list", async () => {
    let finish;
    deleteSavedRecipe.mockImplementation(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const wrapper = render();
    await flushPromises();
    await wrapper.findAll(".ctx-item")[3].trigger("click"); // first card's Remove
    await flushPromises();

    const other = [recipe(1, "Another card's recipe")];
    listSavedRecipes.mockResolvedValue(other);
    await wrapper.setProps({ workflowIds: ["b".repeat(64)] });
    await flushPromises();
    const reads = listUsedLooks.mock.calls.length;

    finish({ deleted: 1 });
    await flushPromises();
    expect(namesOf(wrapper)).toEqual(["Another card's recipe"]);
    expect(listUsedLooks).toHaveBeenCalledTimes(reads);
  });

  // ── The diff line and "Make defaults…" (#1653) ─────────────────

  it("states only what a recipe changes, in the fixed order", async () => {
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform", {
        models: [{ address: "core:Loader/ckpt_name", filename: "xl/realvisXL.safetensors" }],
        loras: [
          { filename: "mira_v2.safetensors", strength: 0.5 },
          { filename: "chars/ada.safetensors", strength: 0.8 },
        ],
        overrides: { "KSampler/steps": 40 },
        keep_seed: true,
        seed: 1234,
      }),
    ]);
    const wrapper = render();
    await flushPromises();
    const text = "realvisXL · + ada 0.8 · mira_v2 0.85 → 0.5 · Steps 40 · seed 1234";
    expect(diffsOf(wrapper)).toEqual([text]);
    // The separators and the arrow are the quiet ink; the names are not.
    const quiet = wrapper.findAll(".wfrt-diff .wfrt-quiet").map((el) => el.text());
    expect(quiet).toEqual(["·", "·", "→", "·", "·"]);
    // A screen reader hears the difference with the name.
    expect(wrapper.find(".wfrt-card").attributes("aria-label")).toBe(
      `Rainy tram platform, ${text}`,
    );
  });

  it("says why a card is not compared, and never 'Only the prompt' early", async () => {
    let answer;
    getWorkflowCard.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Filed"),
      recipe(2, "Loose", { workflow_id: null }),
    ]);
    const wrapper = render();
    await flushPromises();
    expect(diffsOf(wrapper)).toEqual([
      "Comparing with the default…",
      "Not compared: not filed on a workflow",
    ]);
    answer(detail());
    await flushPromises();
    expect(diffsOf(wrapper)[0]).toBe("Only the prompt");

    getWorkflowCard.mockRejectedValue(new Error("boom"));
    useWorkflowsStore().recipesEpoch += 1;
    await flushPromises();
    expect(diffsOf(wrapper)[0]).toBe("Could not compare with the default.");
  });

  it("diffs each card against its own workflow on a multi-selection", async () => {
    const OTHER = "b".repeat(32);
    getWorkflowCard.mockImplementation(async (id) =>
      id === OTHER ? detail(id, "Neon", { values: [] }) : detail(id),
    );
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Here"),
      recipe(2, "There", { workflow_id: OTHER }),
    ]);
    listUsedLooks.mockResolvedValue([{ ...LOOK, loras: [{ filename: "ada.safetensors" }] }]);
    // A third selected workflow with no saved recipe has nothing to diff.
    const wrapper = render({ workflowIds: [KEY, OTHER, "c".repeat(32)] });
    await flushPromises();
    expect(getWorkflowCard.mock.calls.map(([id]) => id).sort()).toEqual([KEY, OTHER].sort());
    expect(diffsOf(wrapper)).toEqual([
      "Only the prompt · on Cinematic portrait",
      "steps 12 · on Neon",
    ]);
    // A look carries no workflow, so it keeps its LoRA list.
    expect(wrapper.find(".wfrt-chip").text()).toContain("ada.safetensors");
    // The defaults verb belongs to one workflow.
    expect(wrapper.find("[data-testid='wfrt-make-defaults']").exists()).toBe(false);
  });

  it("gives a look from pictures its LoRA part and its count", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([
      { ...LOOK, loras: [{ filename: "x/ada.safetensors" }] },
      { ...LOOK, prompt: "same LoRAs", loras: [{ filename: "mira_v2.safetensors" }] },
    ]);
    const wrapper = render();
    await flushPromises();
    expect(diffsOf(wrapper)).toEqual([
      "+ ada · without mira_v2 · 12 pictures",
      "Default LoRAs · 12 pictures",
    ]);
  });

  it("clamps the diff to two lines behind +N more", async () => {
    // jsdom lays nothing out: a line two segments tall, each segment one high.
    const proto = HTMLElement.prototype;
    const saved = ["clientHeight", "offsetTop", "offsetHeight"].map((key) => [
      key,
      Object.getOwnPropertyDescriptor(proto, key),
    ]);
    const segIndex = (el) => [...el.parentElement.children].indexOf(el);
    Object.defineProperty(proto, "clientHeight", {
      configurable: true,
      get() { return this.classList.contains("wfrt-diff") ? 32 : 0; },
    });
    Object.defineProperty(proto, "offsetTop", {
      configurable: true,
      get() { return this.classList.contains("wfrt-seg") ? segIndex(this) * 16 : 0; },
    });
    Object.defineProperty(proto, "offsetHeight", {
      configurable: true,
      get() { return this.classList.contains("wfrt-seg") ? 16 : 0; },
    });
    try {
      listSavedRecipes.mockResolvedValue([
        recipe(1, "Busy", { overrides: { "x/a": 1, "x/b": 2, "x/c": 3, "x/d": 4 } }),
      ]);
      const wrapper = render();
      await flushPromises();
      const more = wrapper.find(".wfrt-more");
      expect(more.text()).toBe("+2 more");
      expect(more.attributes("aria-expanded")).toBe("false");
      await more.trigger("click");
      expect(wrapper.find(".wfrt-more").text()).toBe("Fewer");
      expect(wrapper.find(".wfrt-more").attributes("aria-expanded")).toBe("true");
      expect(wrapper.find(".wfrt-diff--open").exists()).toBe(true);
    } finally {
      for (const [key, descriptor] of saved) {
        if (descriptor) Object.defineProperty(proto, key, descriptor);
        else delete proto[key];
      }
    }
  });

  it("re-counts +N more when a line's box resizes, as the rail opening does", async () => {
    const proto = HTMLElement.prototype;
    const saved = ["clientHeight", "offsetTop", "offsetHeight"].map((key) => [
      key,
      Object.getOwnPropertyDescriptor(proto, key),
    ]);
    const Observer = globalThis.ResizeObserver;
    let resized = null;
    const observed = new Set();
    globalThis.ResizeObserver = class {
      constructor(callback) { resized = callback; }
      observe(el) { observed.add(el); }
      unobserve(el) { observed.delete(el); }
      disconnect() { observed.clear(); }
    };
    // Mid-animation the line is one segment tall; open, it is two.
    let lineHeight = 16;
    const segIndex = (el) => [...el.parentElement.children].indexOf(el);
    Object.defineProperty(proto, "clientHeight", {
      configurable: true,
      get() { return this.classList.contains("wfrt-diff") ? lineHeight : 0; },
    });
    Object.defineProperty(proto, "offsetTop", {
      configurable: true,
      get() { return this.classList.contains("wfrt-seg") ? segIndex(this) * 16 : 0; },
    });
    Object.defineProperty(proto, "offsetHeight", {
      configurable: true,
      get() { return this.classList.contains("wfrt-seg") ? 16 : 0; },
    });
    try {
      listSavedRecipes.mockResolvedValue([
        recipe(1, "Busy", { overrides: { "x/a": 1, "x/b": 2, "x/c": 3, "x/d": 4 } }),
      ]);
      const wrapper = render();
      await flushPromises();
      expect(wrapper.find(".wfrt-more").text()).toBe("+3 more");
      expect([...observed].some((el) => el.classList.contains("wfrt-diff"))).toBe(true);
      lineHeight = 32;
      resized();
      await flushPromises();
      expect(wrapper.find(".wfrt-more").text()).toBe("+2 more");
    } finally {
      globalThis.ResizeObserver = Observer;
      for (const [key, descriptor] of saved) {
        if (descriptor) Object.defineProperty(proto, key, descriptor);
        else delete proto[key];
      }
    }
  });

  it("makes a recipe's parameters the defaults, keeping existing edits, with Undo", async () => {
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform", { overrides: { "KSampler/steps": 40, "KSampler/cfg": 5 } }),
    ]);
    setWorkflowDefaults.mockImplementation(async () => detail());
    const wrapper = render();
    await flushPromises();
    // On the card itself, not in its ⋯ menu.
    expect(wrapper.findAll(".ctx-item").some((el) => el.text().includes("defaults"))).toBe(false);
    await wrapper.find("[data-testid='wfrt-make-defaults']").trigger("click");
    await flushPromises();

    const boxes = wrapper.findAll(".mkd-box");
    expect(boxes.map((box) => box.element.checked)).toEqual([true, true]);
    expect(wrapper.findAll(".mkd-check label").map((el) => el.text())).toEqual([
      "Steps 12 → 40",
      "CFG 7 → 5",
    ]);
    // Untick CFG: only Steps is taken, and the edited CFG 7 goes back unchanged.
    await boxes[1].setValue(false);
    const primary = wrapper.findAll("button").find((el) => el.text().startsWith("Make 1 default"));
    await primary.trigger("click");
    await flushPromises();
    expect(setWorkflowDefaults).toHaveBeenCalledWith(KEY, [
      { slot_label: "core:KSampler", input_name: "cfg", value: 7 },
      { slot_label: "core:KSampler", input_name: "steps", value: 40 },
    ]);
    expect(wrapper.find(".mkd-box").exists()).toBe(false);

    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("success");
    expect(notice.timeout).toBe(8000);
    // The server now holds what the write left.
    getWorkflowCard.mockImplementation(async () =>
      detail(KEY, "Cinematic portrait", {
        values: [
          { label: "Steps", slot_label: "core:KSampler", input_name: "steps", value: 40, provenance: "edited" },
          { label: "CFG", slot_label: "core:KSampler", input_name: "cfg", value: 7, provenance: "edited" },
        ],
      }),
    );
    await notice.action.handler();
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "core:KSampler", input_name: "cfg", value: 7 },
    ]);
  });

  it("builds each defaults PUT from the set as it is now, and Undo reverts only its own rows", async () => {
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform", { overrides: { "KSampler/steps": 40 } }),
    ]);
    setWorkflowDefaults.mockImplementation(async () => detail());
    const wrapper = render();
    await flushPromises();
    // Since the tab read its detail, the Workflow tab edited Denoise.
    const denoise = { label: "Denoise", slot_label: "core:KSampler", input_name: "denoise", value: 0.5, provenance: "edited" };
    const withDenoise = () =>
      detail(KEY, "Cinematic portrait", {
        values: [...detail().card.default_recipe.values, denoise],
      });
    getWorkflowCard.mockImplementation(async () => withDenoise());
    await wrapper.find("[data-testid='wfrt-make-defaults']").trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((el) => el.text().startsWith("Make 1 default")).trigger("click");
    await flushPromises();
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "core:KSampler", input_name: "cfg", value: 7 },
      { slot_label: "core:KSampler", input_name: "denoise", value: 0.5 },
      { slot_label: "core:KSampler", input_name: "steps", value: 40 },
    ]);
    // The Workflow tab is told, or its next whole-set PUT drops Steps.
    expect(wrapper.emitted("defaults-changed")?.at(-1)?.[0]).toBe(KEY);

    // Before Undo, CFG was edited again elsewhere: Undo keeps that and only
    // takes Steps back to computed.
    getWorkflowCard.mockImplementation(async () =>
      detail(KEY, "Cinematic portrait", {
        values: [
          { label: "Steps", slot_label: "core:KSampler", input_name: "steps", value: 40, provenance: "edited" },
          { label: "CFG", slot_label: "core:KSampler", input_name: "cfg", value: 9, provenance: "edited" },
          denoise,
        ],
      }),
    );
    await useNoticeStore().notices.at(-1).action.handler();
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "core:KSampler", input_name: "cfg", value: 9 },
      { slot_label: "core:KSampler", input_name: "denoise", value: 0.5 },
    ]);
  });

  it("reads the set once per write, and sends nothing for a value already so", async () => {
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform", { overrides: { "KSampler/steps": 40, "KSampler/cfg": 5 } }),
    ]);
    const wrapper = render();
    await flushPromises();
    await wrapper.find("[data-testid='wfrt-make-defaults']").trigger("click");
    await flushPromises();
    // Since the dialog opened, both became edits: Steps at 40 already.
    getWorkflowCard.mockReset().mockImplementation(async () =>
      detail(KEY, "Cinematic portrait", {
        values: [
          { label: "Steps", slot_label: "core:KSampler", input_name: "steps", value: 40, provenance: "edited" },
          { label: "CFG", slot_label: "core:KSampler", input_name: "cfg", value: 5, provenance: "edited" },
        ],
      }),
    );
    await wrapper.findAll("button").find((el) => el.text().startsWith("Make 2 defaults")).trigger("click");
    await flushPromises();
    // One read: the Undo snapshot and the PUT come from the same one.
    expect(getWorkflowCard).toHaveBeenCalledTimes(1);
    expect(setWorkflowDefaults).not.toHaveBeenCalled();
  });

  it("does not undo a taken value that was edited again since", async () => {
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform", { overrides: { "KSampler/steps": 40 } }),
    ]);
    setWorkflowDefaults.mockImplementation(async () => detail());
    const wrapper = render();
    await flushPromises();
    await wrapper.find("[data-testid='wfrt-make-defaults']").trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((el) => el.text().startsWith("Make 1 default")).trigger("click");
    await flushPromises();
    const writes = setWorkflowDefaults.mock.calls.length;
    // Steps was set to 50 on the Workflow tab after the change.
    getWorkflowCard.mockImplementation(async () =>
      detail(KEY, "Cinematic portrait", {
        values: [
          { label: "Steps", slot_label: "core:KSampler", input_name: "steps", value: 50, provenance: "edited" },
          { label: "CFG", slot_label: "core:KSampler", input_name: "cfg", value: 7, provenance: "edited" },
        ],
      }),
    );
    await useNoticeStore().notices.at(-1).action.handler();
    expect(setWorkflowDefaults.mock.calls.length).toBe(writes);
    expect(useNoticeStore().notices.at(-1).text).toContain("Nothing to undo");
  });

  it("will not make nothing the default", async () => {
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Rainy tram platform", { overrides: { "KSampler/steps": 40 } }),
    ]);
    const wrapper = render();
    await flushPromises();
    await wrapper.find("[data-testid='wfrt-make-defaults']").trigger("click");
    await flushPromises();
    await wrapper.find(".mkd-box").setValue(false);
    const primary = wrapper.findAll("button").find((el) => el.text().startsWith("Make 0"));
    expect(primary.attributes("aria-disabled")).toBe("true");
    expect(wrapper.text()).toContain("Tick at least one");
    await primary.trigger("click");
    expect(setWorkflowDefaults).not.toHaveBeenCalled();
  });

  it("offers no defaults verb when no parameter differs", async () => {
    const wrapper = render();
    await flushPromises();
    expect(wrapper.find("[data-testid='wfrt-make-defaults']").exists()).toBe(false);
  });

  // ── Extract workflow, and the recipes whose workflow is gone ─────────────

  /** One ⋯ row of the first card, by what it says. */
  const menuRow = (wrapper, label) =>
    wrapper.findAll(".wfrt-card")[0].findAll(".ctx-item").find((el) => el.text().endsWith(label));

  it("extracts a workflow, re-reads the grid, then opens the new one", async () => {
    extractRecipeWorkflow.mockResolvedValue({ workflow_id: "manual:abc", name: "Rainy tram platform" });
    const store = useWorkflowsStore();
    const order = [];
    vi.spyOn(store, "refetch").mockImplementation(async () => order.push("refetch"));
    push.mockImplementation(() => order.push("push"));
    const wrapper = render();
    await flushPromises();

    await menuRow(wrapper, "Extract workflow").trigger("click");
    await flushPromises();

    expect(extractRecipeWorkflow).toHaveBeenCalledWith(1);
    // Re-read FIRST, or the link lands on a grid that does not hold the card.
    expect(order).toEqual(["refetch", "push"]);
    expect(push).toHaveBeenCalledWith({ name: "workflows", query: { workflow: "manual:abc" } });
    expect(useNoticeStore().notices.at(-1)).toMatchObject({ level: "success" });
    expect(useNoticeStore().notices.at(-1).text).toContain("Rainy tram platform");
  });

  it("says why an extract was refused, and opens nothing", async () => {
    extractRecipeWorkflow.mockRejectedValue(
      Object.assign(new Error("409"), { response: { status: 409, data: { detail: "No graph to build on." } } }),
    );
    const wrapper = render();
    await flushPromises();
    await menuRow(wrapper, "Extract workflow").trigger("click");
    await flushPromises();
    expect(push).not.toHaveBeenCalled();
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });

  it("lists the unfiled recipes, with Extract and Delete and no Run or order", async () => {
    listUnfiledRecipes.mockResolvedValue([recipe(9, "Orphan", { workflow_id: null })]);
    const wrapper = render({ unfiled: true, workflowIds: [], workflowName: "Unfiled recipes" });
    await flushPromises();

    expect(listUnfiledRecipes).toHaveBeenCalled();
    expect(listSavedRecipes).not.toHaveBeenCalled();
    expect(listUsedLooks).not.toHaveBeenCalled();
    expect(wrapper.find(".wfrt-title").text()).toBe("Unfiled recipes");
    expect(namesOf(wrapper)).toEqual(["Orphan"]);
    expect(wrapper.find(".wfrt-handle").exists()).toBe(false);
    expect(wrapper.find(".wfrt-card").text()).not.toContain("Run…");
    expect(menuRow(wrapper, "Extract workflow")).toBeDefined();
    expect(menuRow(wrapper, "Delete")).toBeDefined();
  });

  it("draws nothing at all while no recipe is unfiled", async () => {
    listUnfiledRecipes.mockResolvedValue([]);
    const wrapper = render({ unfiled: true, workflowIds: [], workflowName: "Unfiled recipes" });
    await flushPromises();
    expect(wrapper.find(".wfrt-head").exists()).toBe(false);
    expect(wrapper.find(".wfrt-note").exists()).toBe(false);
    expect(wrapper.find(".wfrt-card").exists()).toBe(false);
  });

  it("re-reads the unfiled list when recipes change elsewhere", async () => {
    listUnfiledRecipes.mockResolvedValue([]);
    render({ unfiled: true, workflowIds: [] });
    await flushPromises();
    listUnfiledRecipes.mockResolvedValue([recipe(9, "Orphan", { workflow_id: null })]);
    useWorkflowsStore().notedRecipesChanged();
    await flushPromises();
    expect(listUnfiledRecipes).toHaveBeenCalledTimes(2);
  });
});
