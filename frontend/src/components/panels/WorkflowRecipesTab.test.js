// The Recipes tab (v1.12 F6): the three things that can silently invert.
//
// * The order. A move writes the WHOLE list back, in the order the rail now
//   shows. A body that sends the old order, or only the moved id, leaves the
//   rail and the server disagreeing until the next read.
// * A refused write. The rail is moved first so it does not stall, so it has
//   to be put back when the server says no - otherwise the tab shows an order
//   that does not exist.
// * The header. A recipe runs on its stack, so a selected member lists the
//   STACK's recipes; the key it lists by is the selected card's, because that
//   is what `GET /recipes?workflow_key=` resolves from.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listSavedRecipes = vi.fn();
const getPictureRecipe = vi.fn();
const reorderSavedRecipes = vi.fn();
const editSavedRecipe = vi.fn();
const deleteSavedRecipe = vi.fn();
const listUsedLooks = vi.fn();
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
  exportSavedRecipe: vi.fn(),
}));
vi.mock("../../api/workflows", () => ({ exportWorkflow: vi.fn() }));
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

const KEY = "a".repeat(64);

function recipe(id, name, extra = {}) {
  return {
    id,
    name,
    position: id,
    workflow_key: KEY,
    prompt: `${name} prompt`,
    loras: [{ filename: "mira_v2.safetensors", strength: 0.85 }],
    overrides: { "KSampler/steps": 12 },
    pictures: 31,
    source_picture_id: 7,
    ...extra,
  };
}

function render(props = {}) {
  return mount(WorkflowRecipesTab, {
    props: {
      workflowKeys: [KEY],
      stackName: "Cinematic portrait",
      stackSize: 6,
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
    getPictureRecipe.mockResolvedValue({
      workflow_key: KEY,
      positive_prompt: "a look nobody kept",
      loras: ["mira_v2.safetensors"],
      model_slots: [
        { name: "mira_v2.safetensors", widget: "lora_name", strength: 0.7 },
      ],
      seed_text: "42",
    });
  });

  it("lists the stack's recipes and says so in the header", async () => {
    const wrapper = render();
    await flushPromises();
    expect(listSavedRecipes).toHaveBeenCalledWith([KEY]);
    expect(namesOf(wrapper)).toEqual([
      "Rainy tram platform",
      "Red coat, backlit",
      "Harbour fog",
    ]);
    expect(wrapper.find(".wfrt-sub").text()).toBe(
      "3 saved recipes · runs on any of its 6 workflows",
    );
    // What the card credits and what it changed, on one line.
    expect(wrapper.findAll(".wfrt-facts")[0].text()).toBe("31 pictures · steps 12");
    // LoRA name and strength, which is the look.
    expect(wrapper.find(".wfrt-chip").text()).toContain("mira_v2.safetensors");
    expect(wrapper.find(".wfrt-strength").text()).toBe("0.85");
  });

  it("leaves the stack clause out when the card is not a stack", async () => {
    const wrapper = render({ stackSize: 1 });
    await flushPromises();
    expect(wrapper.find(".wfrt-sub").text()).toBe("3 saved recipes");
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
    // Three ⋯ entries per card, so the second card's Delete is the sixth.
    const deleteSecond = () => wrapper.findAll(".ctx-item")[5].trigger("click");

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

  it("points at the gesture that fills it", async () => {
    listSavedRecipes.mockResolvedValue([]);
    const wrapper = render();
    await flushPromises();
    expect(wrapper.find(".wfrt-hint").text()).toContain("Save as recipe");
    expect(wrapper.find(".wfrt-sub").text()).toContain("0 saved recipes");
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

  it("lists the looks the pictures carry when nothing is saved", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();

    expect(listUsedLooks).toHaveBeenCalledWith([KEY]);
    // Not the empty sentence: a library with pictures has looks to show.
    expect(wrapper.text()).not.toContain("No looks here yet");
    expect(wrapper.text()).toContain("a look nobody kept");
    expect(wrapper.text()).toContain("12 pictures");
    expect(wrapper.text()).toContain("Used in your pictures");
  });

  it("says so only when there is neither a recipe nor a look", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([]);
    const wrapper = render();
    await flushPromises();
    expect(wrapper.text()).toContain("No looks here yet");
    // Never "nothing has been made with this workflow": a picture only shows
    // up once its ComfyUI metadata has been read, so an unread stack is not
    // an empty one.
    expect(wrapper.text()).not.toContain("Nothing has been made");
  });

  it("keeps an unsaved look from its cover picture's own recipe", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Save…"))
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
    expect(openRun).toHaveBeenCalledWith({
      kind: "picture",
      pictureIds: [88],
    });
  });

  it("asks about every selected workflow at once", async () => {
    const OTHER = "b".repeat(64);
    render({ workflowKeys: [KEY, OTHER] });
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
    expect(source.workflowKey).toBe(KEY);
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
    await wrapper.setProps({ workflowKeys: ["b".repeat(64)] });
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
    const wrapper = render({ workflowKeys: many });
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
    // this look's, so the look stays in the used half and the new recipe is
    // credited 0 — the one thing both halves promise cannot happen.
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    getPictureRecipe.mockResolvedValue({
      workflow_key: KEY,
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
      .find((b) => b.text().includes("Save…"))
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

  it("hands the Save dialog only the rows the save could collide with", async () => {
    // A replace is a `PATCH` on one id (#1480), and `recipes` here is the
    // union of every selected card's stack. Matching a typed name against
    // another stack's row would overwrite a recipe on a workflow the owner is
    // not saving to, so with several selected only this card's own rows go.
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Mine"),
      recipe(2, "Somebody else's", { workflow_key: "b".repeat(64) }),
    ]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render({ workflowKeys: [KEY, "b".repeat(64)] });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Save…"))
      .trigger("click");
    await flushPromises();

    const existing = wrapper
      .findComponent({ name: "SaveRecipeDialog" })
      .props("existing");
    expect(existing.map((row) => row.name)).toEqual(["Mine"]);
  });

  it("hands the whole stack's rows when one card is selected", async () => {
    // One card: the list already IS that stack's, resolved server-side, so
    // narrowing it by `workflow_key` would miss the stack's own siblings.
    listSavedRecipes.mockResolvedValue([
      recipe(1, "Mine"),
      recipe(2, "A sibling's", { workflow_key: "b".repeat(64) }),
    ]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Save…"))
      .trigger("click");
    await flushPromises();

    expect(
      wrapper
        .findComponent({ name: "SaveRecipeDialog" })
        .props("existing")
        .map((row) => row.name),
    ).toEqual(["Mine", "A sibling's"]);
  });

  it("refuses to file a look on a workflow the picture does not name", async () => {
    listSavedRecipes.mockResolvedValue([]);
    listUsedLooks.mockResolvedValue([LOOK]);
    getPictureRecipe.mockResolvedValue({ workflow_key: null });
    const wrapper = render({ workflowKeys: [KEY, "b".repeat(64)] });
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Save…"))
      .trigger("click");
    await flushPromises();

    // Never the first of the selection: that files it on a card that never
    // made it, and it would be credited 0 for good.
    expect(wrapper.findComponent({ name: "SaveRecipeDialog" }).exists()).toBe(
      false,
    );
    expect(useNoticeStore().notices.map((n) => n.level)).toContain("error");
  });

  it("renders a used look in the used list, not among the kept", async () => {
    listSavedRecipes.mockResolvedValue([recipe(1, "Rainy tram platform")]);
    listUsedLooks.mockResolvedValue([LOOK]);
    const wrapper = render();
    await flushPromises();

    const lists = wrapper.findAll(".wfrt-list");
    expect(lists).toHaveLength(2);
    expect(lists[0].text()).toContain("Rainy tram platform");
    expect(lists[0].text()).not.toContain("a look nobody kept");
    expect(lists[1].text()).toContain("a look nobody kept");
    expect(lists[1].text()).toContain("Not kept yet");
    // And the header counts both halves, not just the kept one.
    expect(wrapper.find(".wfrt-sub").text()).toContain("1 saved recipe");
    expect(wrapper.find(".wfrt-sub").text()).toContain("1 more used");
  });
});
