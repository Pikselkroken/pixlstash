// Save as recipe (v1.12 F6): the two things that can silently invert.
//
// * The seed row is OFF. A recipe that keeps a seed makes the same picture
//   every run, and a row that starts ticked because every other row does is a
//   one-character change nobody notices until every run comes back identical.
// * An unticked row must not be sent. "What the recipe keeps" is a promise
//   about the file; a body that posts the prompt anyway makes the list a
//   decoration.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const createSavedRecipe = vi.fn();
const listSavedRecipes = vi.fn();
const editSavedRecipe = vi.fn();
vi.mock("../../api/recipes", () => ({
  createSavedRecipe: (...args) => createSavedRecipe(...args),
  listSavedRecipes: (...args) => listSavedRecipes(...args),
  editSavedRecipe: (...args) => editSavedRecipe(...args),
}));
const confirmed = vi.hoisted(() => ({ value: true, asked: null }));
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({
    confirm: async (options) => {
      confirmed.asked = options;
      return confirmed.value;
    },
  }),
}));
const getWorkflowCard = vi.fn();
vi.mock("../../api/workflows", () => ({
  getWorkflowCard: (...args) => getWorkflowCard(...args),
}));
const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import SaveRecipeDialog from "./SaveRecipeDialog.vue";

const KEY = "a".repeat(64);

function open(props = {}) {
  return mount(SaveRecipeDialog, {
    props: {
      open: true,
      workflowKey: KEY,
      suggestedName: "Cinematic portrait",
      prompt: "cinematic portrait of a rainy tram platform",
      loras: [{ filename: "mira_v2.safetensors", sha256: "d0", strength: 0.85 }],
      overrides: [
        { address: "KSampler/steps", label: "Steps", value: 12, base: 8 },
      ],
      seed: "418220931",
      sourcePictureId: 7,
      ...props,
    },
    global: { stubs: { teleport: true } },
  });
}

describe("SaveRecipeDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    createSavedRecipe.mockResolvedValue({ id: 3, name: "Cinematic portrait" });
    editSavedRecipe.mockResolvedValue({ id: 9, name: "Cinematic Portrait" });
    listSavedRecipes.mockResolvedValue([{ id: 3, pictures: 31 }]);
    getWorkflowCard.mockResolvedValue({ card: { name: "Cinematic portrait" } });
    confirmed.value = true;
    confirmed.asked = null;
  });

  it("lists what the recipe keeps, and leaves the seed off", async () => {
    const wrapper = open();
    await flushPromises();
    const boxes = wrapper.findAll("input[type=checkbox]");
    // The LoRAs are rows of their own now (#1478), not a checkbox line.
    expect(boxes).toHaveLength(3);
    // Prompt and the one override are kept; the seed is not.
    expect(boxes.map((box) => box.element.checked)).toEqual([
      true,
      true,
      false,
    ]);
    const text = wrapper.text();
    expect(text).toContain("Steps 12");
    expect(text).toContain("instead of the workflow's 8");
    expect(text).toContain("off: every run varies");
  });

  it("names the stack it is saved to", async () => {
    const wrapper = open();
    await flushPromises();
    expect(getWorkflowCard).toHaveBeenCalledWith(KEY);
    expect(wrapper.text()).toContain("It runs on any workflow in that stack");
  });

  it("saves exactly the rows that are ticked", async () => {
    const wrapper = open();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();

    expect(createSavedRecipe).toHaveBeenCalledTimes(1);
    const body = createSavedRecipe.mock.calls[0][0];
    expect(body.workflow_key).toBe(KEY);
    expect(body.name).toBe("Cinematic portrait");
    expect(body.prompt).toBe("cinematic portrait of a rainy tram platform");
    expect(body.loras).toEqual([
      { filename: "mira_v2.safetensors", sha256: "d0", strength: 0.85 },
    ]);
    expect(body.overrides).toEqual({ "KSampler/steps": 12 });
    expect(body.source_picture_id).toBe(7);
    // The row nobody ticked. `keep_seed` is what makes a stored seed live, so
    // an unticked row must clear BOTH: digits with the flag off would be a
    // seed nothing ever reads, which is the checkbox being decoration.
    expect(body.seed).toBeNull();
    expect(body.keep_seed).toBe(false);
  });

  it("leaves out a row the owner unticked", async () => {
    const wrapper = open();
    await flushPromises();
    const boxes = wrapper.findAll("input[type=checkbox]");
    await boxes[0].setValue(false); // the prompt
    await boxes[1].setValue(false); // the override
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();

    const body = createSavedRecipe.mock.calls[0][0];
    expect(body.prompt).toBe("");
    expect(body.overrides).toEqual({});
    // And the ones still ticked are still there.
    expect(body.loras).toHaveLength(1);
  });

  it("keeps the seed, and flags it, when the owner ticks it", async () => {
    const wrapper = open();
    await flushPromises();
    await wrapper.findAll("input[type=checkbox]")[2].setValue(true);
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();

    const body = createSavedRecipe.mock.calls[0][0];
    expect(body.seed).toBe("418220931");
    // `POST /workflows/run` applies a saved seed only when `keep_seed` is
    // true. The digits alone are a recipe that still draws a new seed every
    // run, which is the opposite of what the ticked row promises.
    expect(body.keep_seed).toBe(true);
  });

  it("says how many pictures the new recipe already accounts for", async () => {
    const wrapper = open();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();

    // Read back rather than taken from the POST, which answers 0 by design.
    expect(listSavedRecipes).toHaveBeenCalledWith(KEY);
    expect(wrapper.emitted("saved")?.[0]?.[0]).toEqual({
      id: 3,
      name: "Cinematic portrait",
    });
  });

  it("says so when the save is refused, and stays open", async () => {
    createSavedRecipe.mockRejectedValue(new Error("nope"));
    const wrapper = open();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();

    expect(wrapper.find("[role=alert]").exists()).toBe(true);
    // Not closed, and not reported as saved: the form still holds the work.
    expect(wrapper.emitted("close")).toBeFalsy();
    expect(wrapper.emitted("saved")).toBeFalsy();
  });

  it("names a LoRA a run will ignore, and keeps it in the recipe", async () => {
    // Kept because the recipe's key has to be the picture's key, said out
    // loud because `POST /workflows/run` applies only digested LoRAs.
    const wrapper = open({
      loras: [
        { filename: "mira_v2.safetensors", sha256: "d0", strength: 0.85 },
        { filename: "mystery.safetensors", sha256: "", strength: 0.5 },
      ],
    });
    await flushPromises();
    const said = wrapper.text().replace(/\s+/g, " ");
    expect(said).toContain(
      "Not on your model shelf. A run ignores this one, so a recipe that lists it promises a LoRA it will not apply.",
    );
    expect(said).toContain("mystery.safetensors");

    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();
    expect(createSavedRecipe.mock.calls[0][0].loras).toHaveLength(2);
  });

  it("falls back to the card's name when the caller suggests none", async () => {
    // The lightbox has no name to offer, and an empty box over a disabled
    // primary is what the owner got one press after "Save as recipe".
    const wrapper = open({ suggestedName: "" });
    await flushPromises();
    expect(wrapper.find("input[type=text]").element.value).toBe(
      "Cinematic portrait",
    );
  });

  it("says a settings choice it cannot carry, when given one", async () => {
    const wrapper = open({ overrides: [], settingsAside: "Not kept: the seed choice." });
    await flushPromises();
    expect(wrapper.text()).toContain("Not kept: the seed choice.");
  });

  // ── A name already on this stack (#1480) ─────────────────────────
  //
  // Nothing makes a recipe name unique - not the column, not `POST /recipes`,
  // not this box - so without the collision the same two presses make two rows
  // reading exactly the same thing, with no undo.

  /**
   * Put a recipe of this name on the card the dialog is about to read.
   *
   * Read by the dialog itself, never handed to it: only `GET /recipes` knows
   * the STACK a card belongs to, and a replace is a `PATCH` on one id.
   *
   * Deliberately mixed-case and spaced, because every other fixture in this
   * suite is lowercase and a case-folding bug in the match would pass all of
   * them.
   */
  function alreadySaved(name = " Cinematic Portrait ") {
    listSavedRecipes.mockResolvedValue([
      { id: 9, name, prompt: "something else", pictures: 4 },
    ]);
  }

  /** Open with the taken name in the box, as an owner who typed it gets it. */
  async function openAndType(typed = "Cinematic portrait") {
    alreadySaved();
    const wrapper = open({ suggestedName: "" });
    await flushPromises();
    await wrapper.find("input[type=text]").setValue(typed);
    await flushPromises();
    return wrapper;
  }

  it("withdraws a name it suggested itself once that name is taken", async () => {
    // The box is prefilled with the card's name, so a card whose first recipe
    // took that name would hand every later save a destructive primary by
    // default, one Enter away, over a dialog titled "Save as recipe".
    alreadySaved("Cinematic portrait");
    const wrapper = open();
    await flushPromises();

    expect(wrapper.find("input[type=text]").element.value).toBe("");
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Replace")),
    ).toBe(false);
    // And the primary is the one this dialog already has for a nameless box.
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Save recipe")),
    ).toBe(true);
  });

  it("keeps a free name it suggested", async () => {
    listSavedRecipes.mockResolvedValue([{ id: 9, name: "Something else" }]);
    const wrapper = open();
    await flushPromises();
    expect(wrapper.find("input[type=text]").element.value).toBe(
      "Cinematic portrait",
    );
  });

  it("waits for the collision lookup before accepting a save", async () => {
    let settle;
    listSavedRecipes.mockImplementation(
      () => new Promise((resolve) => {
        settle = resolve;
      }),
    );
    const wrapper = open();
    await flushPromises();

    const primary = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"));
    expect(primary.attributes("disabled")).toBeDefined();
    await primary.trigger("click");
    await flushPromises();
    expect(createSavedRecipe).not.toHaveBeenCalled();

    settle([]);
    await flushPromises();
    expect(primary.attributes("disabled")).toBeUndefined();
  });

  it("offers Replace, naming the row, when the name is already taken", async () => {
    const wrapper = await openAndType();
    const said = wrapper.text().replace(/\s+/g, " ");
    // The ROW's own spelling, not what was typed: it is what is about to go.
    expect(said).toContain("“ Cinematic Portrait ” is already saved here");
    expect(said).toContain("Saving replaces it");
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Replace")),
    ).toBe(true);
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Save recipe")),
    ).toBe(false);
  });

  it("replaces that row rather than adding a second one", async () => {
    const wrapper = await openAndType();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Replace"))
      .trigger("click");
    await flushPromises();

    expect(createSavedRecipe).not.toHaveBeenCalled();
    expect(editSavedRecipe).toHaveBeenCalledTimes(1);
    const [id, body] = editSavedRecipe.mock.calls[0];
    expect(id).toBe(9);
    // The whole look, not only the name: a replace re-keeps what is on screen.
    expect(body.name).toBe("Cinematic portrait");
    expect(body.prompt).toBe("cinematic portrait of a rainy tram platform");
    expect(body.overrides).toEqual({ "KSampler/steps": 12 });
    // Not settable on a PATCH, and the recipe does not move between cards.
    expect(body).not.toHaveProperty("workflow_key");
    // A caller that HAS a picture still names it, so the strip above is
    // about a caller with none rather than about replaces in general.
    expect(body.source_picture_id).toBe(7);
    expect(wrapper.emitted("saved")?.[0]?.[0]).toEqual({
      id: 9,
      name: "Cinematic Portrait",
    });
  });

  it("writes nothing when the replace is not confirmed", async () => {
    confirmed.value = false;
    const wrapper = await openAndType();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Replace"))
      .trigger("click");
    await flushPromises();

    expect(editSavedRecipe).not.toHaveBeenCalled();
    expect(createSavedRecipe).not.toHaveBeenCalled();
    // And the form still holds the work.
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("says so when the replace also renames the row", async () => {
    // The button prints the ROW's spelling and the write sends the TYPED one,
    // so a replace that folds "Cinematic Portrait" onto "cinematic portrait"
    // renames it, and nothing else on the screen says so.
    const wrapper = await openAndType("cinematic portrait");
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Replace"))
      .trigger("click");
    await flushPromises();
    expect(confirmed.asked.message).toContain(
      "renamed to \u201ccinematic portrait\u201d",
    );
    expect(confirmed.asked.danger).toBe(true);
  });

  it("does not claim a rename when the spelling is unchanged", async () => {
    alreadySaved("Typed exactly");
    const wrapper = open({ suggestedName: "" });
    await flushPromises();
    await wrapper.find("input[type=text]").setValue("Typed exactly");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Replace"))
      .trigger("click");
    await flushPromises();
    expect(confirmed.asked.message).not.toContain("renamed");
  });

  it("leaves the row's own picture alone when it has none to offer", async () => {
    // `PATCH /recipes/{id}` writes every field the request CARRIES, so a null
    // here is an instruction to forget the picture the recipe is drawn from -
    // and nothing on screen would have mentioned it.
    alreadySaved("Taken");
    const wrapper = open({ suggestedName: "", sourcePictureId: null });
    await flushPromises();
    await wrapper.find("input[type=text]").setValue("Taken");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Replace"))
      .trigger("click");
    await flushPromises();

    expect(editSavedRecipe.mock.calls[0][1]).not.toHaveProperty(
      "source_picture_id",
    );
  });

  it("goes back to Save the moment the name is changed", async () => {
    const wrapper = await openAndType();
    await wrapper.find("input[type=text]").setValue("Another name");
    await flushPromises();
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Replace")),
    ).toBe(false);
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();
    expect(createSavedRecipe).toHaveBeenCalledTimes(1);
    expect(editSavedRecipe).not.toHaveBeenCalled();
  });

  it("tells every surface showing this card that a recipe was saved", async () => {
    const store = useWorkflowsStore();
    const before = store.recipesEpoch;
    const wrapper = open();
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text().includes("Save recipe"))
      .trigger("click");
    await flushPromises();
    expect(store.recipesEpoch).toBe(before + 1);
  });
});

// ── One row per LoRA, and the question (#1478) ─────────────────────────
//
// The saved recipe holds exactly the rows the list shows. A LoRA the shelf
// cannot name is flagged, and its trash asks which of two different acts is
// meant, every time.

describe("SaveRecipeDialog's LoRA rows", () => {
  const LORAS = [
    { filename: "lightning-8step.safetensors", sha256: "d1", strength: 1 },
    { filename: "neon-rain-v2.safetensors", sha256: "d2", strength: 0.85 },
    { filename: "loras/Hairstyle-V3.safetensors", sha256: "", strength: 0.6 },
  ];

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    document.body.innerHTML = "";
    createSavedRecipe.mockResolvedValue({ id: 3, name: "Rainy tram platform" });
    listSavedRecipes.mockResolvedValue([]);
    getWorkflowCard.mockResolvedValue({ card: { name: "SDXL fast" } });
  });

  function openRows() {
    return mount(SaveRecipeDialog, {
      props: {
        open: true,
        workflowKey: KEY,
        suggestedName: "Rainy tram platform",
        prompt: "a rainy tram platform",
        loras: LORAS,
      },
      global: { stubs: { teleport: true } },
      attachTo: document.body,
    });
  }

  function labelOf(entry) {
    const own = entry.find(".app-btn__label");
    return (own.exists() ? own.text() : entry.text()).trim();
  }

  function press(wrapper, label) {
    const found = wrapper.findAll("button").find((b) => labelOf(b) === label);
    if (!found) throw new Error(`no button reading ${label}`);
    return found.trigger("click");
  }

  function trashOf(wrapper, index) {
    return wrapper.findAll(".svr-lora")[index].find("[data-focus='delete']");
  }

  async function save(wrapper) {
    await press(wrapper, "Save recipe");
    await flushPromises();
    return createSavedRecipe.mock.calls[0][0];
  }

  it("lists one row per LoRA, the unknown one as its file, and flags it", async () => {
    const wrapper = openRows();
    await flushPromises();
    const rows = wrapper.findAll(".svr-lora");
    expect(rows.map((row) => row.find(".svr-lora-name").text())).toEqual([
      "lightning-8step",
      "neon-rain-v2",
      "Hairstyle-V3.safetensors",
    ]);
    expect(rows.map((row) => row.find(".svr-lora-strength").text())).toEqual([
      "1.00",
      "0.85",
      "0.60",
    ]);
    expect(rows[2].find(".svr-flag").text().replace(/\s+/g, " ")).toBe(
      "Not on your model shelf. A run ignores this one, so a recipe that lists it promises a LoRA it will not apply.",
    );
    // Only the unknown one is flagged.
    expect(rows[0].find(".svr-flag").exists()).toBe(false);
  });

  it("takes a shelf LoRA straight off, and saves exactly the rows left", async () => {
    const wrapper = openRows();
    await flushPromises();
    await trashOf(wrapper, 1).trigger("click");
    await flushPromises();

    const row = wrapper.findAll(".svr-lora")[1];
    expect(row.classes()).toContain("svr-lora--off");
    expect(document.activeElement?.textContent?.trim()).toBe("Restore");
    // No question for a LoRA the shelf names.
    expect(wrapper.find("[data-testid='svr-question']").exists()).toBe(false);

    const body = await save(wrapper);
    expect(body.loras).toEqual([
      { filename: "lightning-8step.safetensors", sha256: "d1", strength: 1 },
      { filename: "loras/Hairstyle-V3.safetensors", sha256: null, strength: 0.6 },
    ]);
  });


  it("saves a strength of 0 as 0, not as 1", async () => {
    const wrapper = mount(SaveRecipeDialog, {
      props: {
        open: true,
        workflowKey: KEY,
        suggestedName: "Rainy tram platform",
        prompt: "a rainy tram platform",
        loras: [{ filename: "zero.safetensors", sha256: "d0", strength: 0 }],
      },
      global: { stubs: { teleport: true } },
      attachTo: document.body,
    });
    await flushPromises();
    const body = await save(wrapper);
    expect(body.loras).toEqual([
      { filename: "zero.safetensors", sha256: "d0", strength: 0 },
    ]);
  });

  it("puts a row back with Restore", async () => {
    const wrapper = openRows();
    await flushPromises();
    await trashOf(wrapper, 0).trigger("click");
    await flushPromises();
    await press(wrapper, "Restore");
    await flushPromises();
    const body = await save(wrapper);
    expect(body.loras).toHaveLength(3);
  });

  it("asks 'Take X out of what?' for the unknown one", async () => {
    const wrapper = openRows();
    await flushPromises();
    await trashOf(wrapper, 2).trigger("click");
    await flushPromises();

    const said = wrapper.text().replace(/\s+/g, " ");
    expect(wrapper.find("[data-testid='svr-question']").text()).toBe(
      "Take Hairstyle-V3 out of what?",
    );
    expect(said).toContain(
      "The workflow has a loader for it, and a recipe cannot delete one. These are two different acts.",
    );
    expect(said).toContain("This recipe");
    expect(said).toContain("The workflow");
    expect(said).toContain(
      "Either way the saved recipe holds exactly the 2 LoRAs the list showed.",
    );
    // Nothing is chosen for the owner: no preference is stored.
    expect(
      wrapper.findAll("input[type=radio]").map((r) => r.element.checked),
    ).toEqual([false, false]);
    const takeItOut = wrapper
      .findAll("button")
      .find((b) => labelOf(b) === "Take it out");
    expect(takeItOut.attributes("disabled")).toBeDefined();
  });

  it("'This recipe' drops it from the list, and the save leaves it out", async () => {
    const wrapper = openRows();
    await flushPromises();
    await trashOf(wrapper, 2).trigger("click");
    await flushPromises();
    await wrapper.findAll("input[type=radio]")[0].setValue(true);
    await press(wrapper, "Take it out");
    await flushPromises();

    expect(wrapper.find("[data-testid='svr-question']").exists()).toBe(false);
    expect(wrapper.findAll(".svr-lora")[2].classes()).toContain("svr-lora--off");
    expect(push).not.toHaveBeenCalled();

    const body = await save(wrapper);
    expect(body.loras.map((lora) => lora.filename)).toEqual([
      "lightning-8step.safetensors",
      "neon-rain-v2.safetensors",
    ]);
  });

  it("'The workflow' closes and opens Edit LoRAs… with that entry deleted", async () => {
    const wrapper = openRows();
    await flushPromises();
    await trashOf(wrapper, 2).trigger("click");
    await flushPromises();
    await wrapper.findAll("input[type=radio]")[1].setValue(true);
    await press(wrapper, "Take it out");
    await flushPromises();

    expect(push).toHaveBeenCalledWith({
      name: "workflows",
      query: {
        card: KEY,
        edit: "loras",
        drop_lora: "loras/Hairstyle-V3.safetensors",
      },
    });
    expect(wrapper.emitted("close")).toBeTruthy();
    expect(wrapper.emitted("handoff")?.[0]?.[0]).toEqual({
      workflowKey: KEY,
      filename: "loras/Hairstyle-V3.safetensors",
    });
    // Nothing saved here: the edit is saved as a new workflow over there.
    expect(createSavedRecipe).not.toHaveBeenCalled();
  });

  it("goes back to the list, unchanged, from the question", async () => {
    const wrapper = openRows();
    await flushPromises();
    await trashOf(wrapper, 2).trigger("click");
    await flushPromises();
    await press(wrapper, "Back");
    await flushPromises();
    expect(wrapper.findAll(".svr-lora")[2].classes()).not.toContain(
      "svr-lora--off",
    );
    const body = await save(wrapper);
    expect(body.loras).toHaveLength(3);
  });
});
