// The Workflow tab (v1.12 F3): the three things about it that can silently
// invert.
//
// * A default's provenance and its reset. Reset is a WHOLE-SET write, so the
//   request has to carry every other edited value; a reset that sends only
//   the row it was fired on resets all of them and reads as working.
// * The LoRA slot's Workflow/Recipe switch. It re-keys the card, so the rail
//   has to follow the key the answer names — staying put leaves it reading a
//   card the hub no longer has.
// * Run… with several workflows selected. It must stay on screen and refuse,
//   which is `aria-disabled` plus the reason; a button that disappears
//   teaches nobody why.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getWorkflowCard = vi.fn();
const listWorkflowCards = vi.fn();
const patchWorkflowCard = vi.fn();
const setWorkflowDefaults = vi.fn();
const setWorkflowPins = vi.fn();
const setWorkflowSlots = vi.fn();
const stackWorkflows = vi.fn();

vi.mock("../../api/workflows", () => ({
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  patchWorkflowCard: (...args) => patchWorkflowCard(...args),
  setWorkflowDefaults: (...args) => setWorkflowDefaults(...args),
  setWorkflowPins: (...args) => setWorkflowPins(...args),
  setWorkflowSlots: (...args) => setWorkflowSlots(...args),
  // `WorkflowCard` renders its covers through this, so a mock without it
  // throws in the render and every assertion in the file goes with it.
  workflowCoverUrl: (cover) => cover?.url ?? "",
  stackWorkflows: (...args) => stackWorkflows(...args),
}));

// *Show all N pictures* (F7) leaves this screen for the library.
const push = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ name: "workflows", query: {} }),
}));

import WorkflowTab from "./WorkflowTab.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";

const KEY = "a".repeat(64);
const OTHER = "b".repeat(64);
const MOVED = "c".repeat(64);

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": {
        template: "<div><slot name='activator' :props='{}'/><slot/></div>",
      },
      Tooltip: true,
    },
  },
};

function card(overrides = {}) {
  return {
    key: KEY,
    name: "Cinematic portrait",
    type: "txt2img",
    imported: false,
    models: [
      {
        name: "realvisXL_v5.safetensors",
        kind: "checkpoint",
        slot_label: "m1",
      },
    ],
    loras: [
      {
        name: "lightning-8step",
        kind: "lora",
        mark: "structural",
        slot_label: "l1",
      },
    ],
    differs_by: [],
    picture_count: 184,
    rating: 4.8,
    covers: [],
    stack_size: 1,
    saved_recipe_count: 0,
    defaults: [],
    topology_hash: "d".repeat(64),
    variant_count: 1,
    member_keys: [],
    last_used: null,
    rank: 4.5,
    ...overrides,
  };
}

function detail(overrides = {}) {
  return {
    card: card(overrides.card ?? {}),
    notes: null,
    hidden: false,
    variants: [],
    pins: null,
    ...overrides,
  };
}

const STEPS = {
  label: "steps",
  slot_label: "slot-a",
  input_name: "steps",
  value: "8",
  provenance: "best",
};
const CFG = {
  label: "cfg",
  slot_label: "slot-a",
  input_name: "cfg",
  value: "7.5",
  provenance: "edited",
};
const WIDTH = {
  label: "width",
  slot_label: "slot-a",
  input_name: "width",
  value: "832",
  provenance: "edited",
};

const SAMPLER = {
  label: "sampler_name",
  slot_label: "slot-a",
  input_name: "sampler_name",
  value: "euler",
  provenance: "best",
};

/** The default row whose label is `name`, wherever the fold has put it. */
function rowNamed(wrapper, name) {
  const row = wrapper
    .findAll(".wfdef")
    .find((entry) => entry.find(".wfdef-name").text().startsWith(name));
  if (!row) throw new Error(`no defaults row called ${name}`);
  return row;
}

async function flush(wrapper) {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
}

/** Mount the rail with `keys` selected and `cards` in the grid. */
async function mountWith(keys, cards = [card()]) {
  const store = useWorkflowsStore();
  store.cards = cards;
  store.selectedKeys = keys;
  const wrapper = mount(WorkflowTab, globalOpts);
  await flush(wrapper);
  return { wrapper, store };
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  useSidebarStore().statsOpen = true;
  getWorkflowCard.mockReset().mockResolvedValue(detail());
  listWorkflowCards.mockReset().mockResolvedValue({
    cards: [card()],
    one_offs: 0,
    hidden: 0,
  });
  patchWorkflowCard.mockReset().mockResolvedValue(detail());
  setWorkflowDefaults.mockReset().mockResolvedValue(detail());
  setWorkflowPins.mockReset().mockResolvedValue({ pins: [] });
  setWorkflowSlots.mockReset().mockResolvedValue({ key: MOVED, moved: {} });
  stackWorkflows.mockReset().mockResolvedValue({ stack_id: "s", keys: [] });
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("the models the panel names", () => {
  it("calls the checkpoint what the SHELF calls it, not what the file is", async () => {
    // The same preference the card's generated name row was built from
    // (#1454). This panel opens from that card, so a Checkpoint line reading
    // `realvisXL_v5.safetensors` under a title reading `Krea 2` is one model
    // named twice.
    const named = card({
      models: [
        {
          name: "realvisxl_v5.safetensors",
          title: "Krea 2",
          kind: "checkpoint",
          slot_label: "m1",
        },
        // The VAE line reads the shelf too: it is the same sentence one row
        // down, and half a panel agreeing is the drift, not the fix.
        {
          name: "ae.safetensors",
          title: "Flux Autoencoder",
          kind: "vae",
          slot_label: "m2",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    const { wrapper } = await mountWith([KEY], [named]);
    const text = textOf(wrapper);
    expect(text).toContain("Krea 2");
    expect(text).not.toContain("realvisxl_v5.safetensors");
    expect(text).toContain("Flux Autoencoder");
    expect(text).not.toContain("ae.safetensors");
  });

  it("falls back to the filename where the shelf has no name", async () => {
    // The ordinary case - the line must say the file, never go blank.
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
  });
});

describe("a default's provenance and reset", () => {
  it("says where each value came from, on the row it belongs to", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // Paired to the row, not counted across the panel: both sentences
    // appearing SOMEWHERE is also true when the two labels are swapped.
    expect(rowNamed(wrapper, "steps").text()).toContain(
      "from your best pictures",
    );
    expect(rowNamed(wrapper, "cfg").text()).toContain("edited by you");
  });

  it("offers the reset only on a value the owner edited", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // WHICH row, not how many: one reset among two rows is equally true of
    // the inverted condition.
    expect(rowNamed(wrapper, "cfg").find(".wfdef-reset").exists()).toBe(true);
    expect(rowNamed(wrapper, "steps").find(".wfdef-reset").exists()).toBe(
      false,
    );
  });

  it("resets one value by writing back every other edited one", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG, WIDTH] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // `cfg` is the first edited row; resetting it must leave `width` set.
    await wrapper.findAll(".wfdef-reset")[0].trigger("click");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "width", value: "832" },
    ]);
  });

  it("pins the parameters the design pins when the card has no choice", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // WHICH side of the fold each row is on. Counting pressed pins is
    // equally true when the two lists are swapped, and that swap is the
    // whole of what the pin does.
    expect(rowNamed(wrapper, "steps").element.closest("details")).toBeNull();
    expect(
      rowNamed(wrapper, "sampler_name").element.closest("details"),
    ).not.toBeNull();
    expect(wrapper.find("details.wftab-disclose summary").text()).toContain(
      "All 2 parameters",
    );
  });

  it("keeps an empty pin list apart from no choice at all", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS] }, pins: [] }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findAll('.wfdef [aria-pressed="true"]')).toHaveLength(0);
    // Nothing pinned means nothing above the fold, so every row is inside
    // it — the section must not simply be empty.
    expect(
      rowNamed(wrapper, "steps").element.closest("details"),
    ).not.toBeNull();
  });

  it("pins one parameter without unpinning the rest", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // The pin is a WHOLE-SET write, exactly like the reset above: pinning
    // `sampler_name` has to carry `steps` too, and the inverted predicate
    // ("pin everything except this one") is invisible to a test that only
    // counts pressed pins.
    await rowNamed(wrapper, "sampler_name").find("button").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps" },
      { slot_label: "slot-a", input_name: "sampler_name" },
    ]);
  });

  it("unpins one parameter without pinning the rest", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    await rowNamed(wrapper, "steps").find("button").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, []);
  });
});

describe("marking a LoRA slot", () => {
  it("calls the slots route with the slot's own label", async () => {
    const { wrapper } = await mountWith([KEY]);
    const recipe = wrapper
      .findAll("button")
      .find((button) => button.text() === "Recipe");
    await recipe.trigger("click");
    await flush(wrapper);
    expect(setWorkflowSlots).toHaveBeenCalledWith(KEY, { l1: "recipe" });
  });

  it("follows the key the flip moved the card to", async () => {
    const { wrapper, store } = await mountWith([KEY]);
    const recipe = wrapper
      .findAll("button")
      .find((button) => button.text() === "Recipe");
    await recipe.trigger("click");
    await flush(wrapper);
    // The card was re-keyed, so the rail has to be reading the new key —
    // otherwise it sits on a card the hub no longer has.
    expect(store.selectedKeys).toEqual([MOVED]);
    expect(getWorkflowCard).toHaveBeenLastCalledWith(MOVED);
  });

  it("writes nothing when the mark it was given is the mark it has", async () => {
    const { wrapper } = await mountWith([KEY]);
    // Through the component's own handler, not through `Segmented`: that
    // widget already refuses to emit for the active option, so a click on
    // "Workflow" is silent whether or not this guard exists.
    await wrapper.vm.flipMark(
      { label: "l1", name: "lightning-8step", mark: "structural" },
      "structural",
    );
    await flush(wrapper);
    expect(setWorkflowSlots).not.toHaveBeenCalled();
  });

  it("refuses a slot the payload gave no address for", async () => {
    // `slot_label` is nullable, and the mark is written BY label, so an
    // enabled switch on a slot without one is a control that answers a
    // click with nothing at all.
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          loras: [{ name: "mystery", kind: "lora", mark: "structural" }],
        }),
      ],
    );
    expect(wrapper.findComponent({ name: "Segmented" }).props("disabled")).toBe(
      true,
    );
    expect(textOf(wrapper)).toContain("no recorded address");
  });
});

describe("the Recipes tab (v1.12 F6)", () => {
  it("shows the stack's recipes, and the footer belongs to Workflow", async () => {
    const { wrapper } = await mountWith([KEY]);
    const tab = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes");
    expect(tab).toBeTruthy();
    await tab.trigger("click");
    await flush(wrapper);

    expect(
      wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists(),
    ).toBe(true);
    // Run… is the Workflow tab's; the Recipes tab runs a recipe from its row.
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Run…")),
    ).toBe(false);
  });

  it("answers for a selection of several, as the union of their stacks", async () => {
    const { wrapper, store } = await mountWith([KEY]);
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes")
      .trigger("click");
    await flush(wrapper);

    store.selectedKeys = [KEY, OTHER];
    await flush(wrapper);

    // Still the Recipes body, now asking about both cards: selecting several
    // is the owner asking what they have between them.
    const tab = wrapper.findComponent({ name: "WorkflowRecipesTab" });
    expect(tab.exists()).toBe(true);
    expect(tab.props("workflowKeys")).toEqual([KEY, OTHER]);
  });

  it("puts the refused Run… back when the Workflow tab is chosen", async () => {
    // The footer belongs to the Workflow body, and Run… has to stay on screen
    // and refuse rather than vanish. Leaving the Recipes tab is what brings
    // that screen — and so that control — back.
    const { wrapper, store } = await mountWith([KEY]);
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes")
      .trigger("click");
    await flush(wrapper);
    store.selectedKeys = [KEY, OTHER];
    await flush(wrapper);
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Run…")),
    ).toBe(false);

    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Workflow")
      .trigger("click");
    await flush(wrapper);

    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));
    expect(run).toBeTruthy();
    expect(run.attributes("aria-disabled")).toBe("true");
  });
});

describe("with several workflows selected", () => {
  it("keeps Run… on screen, refused, with the reason attached", async () => {
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ key: OTHER })],
    );
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));
    expect(run).toBeTruthy();
    expect(run.attributes("aria-disabled")).toBe("true");
    const described = run.attributes("aria-describedby");
    expect(wrapper.find(`#${described}`).text()).toBe(
      "Run one workflow at a time",
    );
  });

  it("opens the Run popup on THIS card when Run… is pressed", async () => {
    // The only entry point to a card-sourced run now that the toolbar's
    // Generate button is gone, and it was unguarded: `function run() { return; }`
    // kept the whole suite green.
    const { wrapper } = await mountWith([KEY], [card()]);
    const { useRunDialogStore } = await import("../../stores/useRunDialogStore");
    const run = wrapper.findAll("button").find((b) => b.text().includes("Run…"));

    await run.trigger("click");
    await flush(wrapper);

    expect(useRunDialogStore().source).toMatchObject({
      kind: "card",
      workflowKey: KEY,
      // No picture behind it, so the popup shows the card's cover and an empty
      // prompt rather than prefilling from a recipe it does not have.
      emptyPrompt: true,
    });
  });

  it("does not start a run when Run… is pressed", async () => {
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ key: OTHER })],
    );
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));
    await run.trigger("click");
    await flush(wrapper);
    const { useRunDialogStore } = await import(
      "../../stores/useRunDialogStore"
    );
    // Seeded first: `source` is null on a fresh store, so asserting null
    // against an untouched default would pass with `run()` deleted entirely.
    const runDialog = useRunDialogStore();
    expect(runDialog.source).toBe(null);
    runDialog.openRun({ kind: "picture", pictureIds: [99] });
    await run.trigger("click");
    await flush(wrapper);
    expect(runDialog.source.pictureIds).toEqual([99]);
  });

  it("says how many are selected and offers no verbs of its own", async () => {
    // **The rail counts; the grid's selection pill acts** (#1455). These two
    // buttons were here first, and once the pill offered the same two they
    // disagreed within a week: this Hide was hardcoded where the pill's is a
    // toggle that reads Unhide on an all-hidden selection, and this Stack
    // said "Stack together" where the pill says "Fuse into one stack" for a
    // selection that already holds one. Two live controls on one screen, one
    // of them wrong about what it was about to do.
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ key: OTHER })],
    );
    const text = textOf(wrapper);
    expect(text).toContain("2 workflows selected");
    // The reader is told where the verbs went rather than left to find them.
    expect(text).toContain("bar at the bottom of the grid");
    const labels = wrapper.findAll("button").map((b) => b.text());
    expect(labels).not.toContain("Stack together");
    expect(labels).not.toContain("Hide");
  });
});

describe("a write that comes back after the selection moved", () => {
  it("does not write card A's notes onto card B", async () => {
    // Blur is exactly when the selection moves: clicking another card blurs
    // the textarea. A's PATCH must not land in a rail showing B.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({
        card: { key, defaults: [key === KEY ? STEPS : SAMPLER] },
        notes: null,
      }),
    );
    let settle;
    patchWorkflowCard.mockReturnValue(
      new Promise((resolve) => {
        settle = () =>
          resolve(detail({ card: { key: KEY, defaults: [STEPS] } }));
      }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ key: OTHER, name: "Card B" })],
    );
    await wrapper.find("textarea").setValue("a note for A");
    await wrapper.find("textarea").trigger("blur");
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    settle();
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Card B");
    expect(textOf(wrapper)).not.toContain("Card A");
    // The detail carries the DEFAULTS as well as the notes, so A's answer
    // landing here puts A's parameters under B's name. The header comes from
    // the grid and would keep saying B either way.
    expect(() => rowNamed(wrapper, "steps")).toThrow();
    expect(rowNamed(wrapper, "sampler_name").exists()).toBe(true);
    expect(wrapper.find("textarea").element.value).toBe("");
  });

  it("does not offer the notes box before this card's notes have arrived", async () => {
    let settle;
    getWorkflowCard.mockReturnValue(
      new Promise((resolve) => {
        settle = () => resolve(detail({ notes: "the real notes" }));
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    // No box, so there is no stale draft to blur onto this card.
    expect(wrapper.find("textarea").exists()).toBe(false);
    settle();
    await flush(wrapper);
    expect(wrapper.find("textarea").element.value).toBe("the real notes");
  });

  it("does not let a notes save put the pre-toggle pins back", async () => {
    // The sequence is one gesture: clicking a pin is what blurs the notes
    // box, so the PATCH and the PUT are started by the same click. The PATCH
    // answers with the WHOLE detail, pins included, so landing second it
    // puts the pin back while the server holds the new value.
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    // A hub that answers with the pins it holds AT THAT MOMENT. The PATCH is
    // the slow one, which is the reviewer's case: blur queues it first, so
    // run beside the pin write it answers last, carrying `pins: null`.
    let held = null;
    let settleNotes;
    setWorkflowPins.mockImplementation(async (_key, pins) => {
      held = pins;
      return { pins };
    });
    patchWorkflowCard.mockImplementation(
      () =>
        new Promise((resolve) => {
          const answer = { pins: held };
          settleNotes = () =>
            resolve(
              detail({
                card: { defaults: [STEPS, SAMPLER] },
                notes: "a note",
                ...answer,
              }),
            );
        }),
    );
    const { wrapper } = await mountWith([KEY]);
    await wrapper.find("textarea").setValue("a note");
    await wrapper.find("textarea").trigger("blur");
    await rowNamed(wrapper, "sampler_name").find("button").trigger("click");
    await flush(wrapper);

    // Blur runs before the click, so the notes PATCH is the one in flight
    // and the pin write waits behind it rather than racing it.
    expect(patchWorkflowCard).toHaveBeenCalledWith(KEY, { notes: "a note" });
    expect(setWorkflowPins).not.toHaveBeenCalled();
    settleNotes();
    await flush(wrapper);

    // Both ran, and the pin stuck. Fired together, the PATCH's answer lands
    // last carrying the pins from before the toggle, the default set applies
    // again and `sampler_name` drops back inside the fold while the hub
    // holds it pinned.
    expect(setWorkflowPins).toHaveBeenCalledTimes(1);
    expect(
      rowNamed(wrapper, "sampler_name").element.closest("details"),
    ).toBeNull();
  });

  it("runs one write at a time, so two cannot discard each other", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    setWorkflowPins.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountWith([KEY]);
    await rowNamed(wrapper, "steps").find("button").trigger("click");
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    await flush(wrapper);
    // `detail` carries the defaults, the pins and the card together, so a
    // second write in flight means the slower answer overwrites the faster
    // one's change.
    expect(setWorkflowDefaults).not.toHaveBeenCalled();
  });
});

describe("the more menu and hiding", () => {
  it("hides the card and can still unhide it once it has left the grid", async () => {
    getWorkflowCard.mockResolvedValue(detail());
    patchWorkflowCard.mockResolvedValue(detail({ hidden: true }));
    // A hidden card is not in `GET /workflows`, which is exactly the
    // case that used to take the footer and its menu off screen.
    listWorkflowCards.mockResolvedValue({ cards: [], one_offs: 0, hidden: 1 });
    const { wrapper } = await mountWith([KEY]);
    const hide = wrapper.findAll("button").find((b) => b.text() === "Hide");
    await hide.trigger("click");
    await flush(wrapper);
    expect(patchWorkflowCard).toHaveBeenCalledWith(KEY, { hidden: true });
    expect(
      wrapper.findAll("button").find((b) => b.text() === "Unhide"),
    ).toBeTruthy();
  });

  });

describe("which card the rail shows", () => {
  it("keeps showing a card the flip moved out of the grid", async () => {
    // A flip can merge this card into somebody else's stack, and the grid
    // lists one card per stack — so the successor key is not in `cards` and
    // the detail read is the only thing that still has it.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { key, name: "Merged into a stack" } }),
    );
    listWorkflowCards.mockResolvedValue({
      cards: [card({ key: "e".repeat(64) })],
      one_offs: 0,
      hidden: 0,
    });
    const { wrapper } = await mountWith([KEY]);
    const recipe = wrapper
      .findAll("button")
      .find((button) => button.text() === "Recipe");
    await recipe.trigger("click");
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Merged into a stack");
    expect(textOf(wrapper)).not.toContain("Pick a workflow");
  });

  it("forgets the cached stack members a flip may have re-keyed", async () => {
    const { wrapper, store } = await mountWith([KEY]);
    store.members = { [KEY]: [card()] };
    const recipe = wrapper
      .findAll("button")
      .find((button) => button.text() === "Recipe");
    await recipe.trigger("click");
    await flush(wrapper);
    expect(store.members).toEqual({});
  });

  // Half of the chip's round trip: this screen is the only one that knows a
  // card's key, and `useGridFetch.test.js` holds the half that spends it.
  it("opens the library on the card's own pictures, as a removable chip", async () => {
    const { wrapper } = await mountWith([KEY]);
    const link = wrapper.find('[data-testid="wftab-show-pictures"]');
    expect(link.text()).toBe("184 pictures");
    expect(link.attributes("aria-label")).toBe("Show all 184 pictures");

    await link.trigger("click");

    expect(useFilterStore().workflowFilter).toEqual({
      key: KEY,
      name: "Cinematic portrait",
    });
    expect(push).toHaveBeenCalledWith("/");
  });

  // The link promises a number it was handed by the card. A reader with a tag
  // filter on, or a character selected, would be shown fewer than that with
  // nothing on screen saying why — so the view it lands in gives way.
  it("clears the filters and the sidebar scope, because it says 'all'", async () => {
    const { wrapper } = await mountWith([KEY]);
    const filterStore = useFilterStore();
    const selectionStore = useSelectionStore();
    filterStore.tagFilter = ["hat"];
    filterStore.minScoreFilter = 4;
    selectionStore.selectedCharacter = 9;
    selectionStore.selectedSet = 3;
    useSearchStore().searchQuery = "cats";

    await wrapper.find('[data-testid="wftab-show-pictures"]').trigger("click");

    expect(filterStore.tagFilter).toEqual([]);
    expect(filterStore.minScoreFilter).toBeNull();
    expect(selectionStore.selectedCharacter).toBe("ALL");
    expect(selectionStore.selectedSet).toBeNull();
    expect(useSearchStore().searchQuery).toBe("");
    // And the one filter it is FOR survives `resetFilters`, which clears it
    // too — the order of those two lines is the whole behaviour.
    expect(filterStore.workflowFilter).toEqual({
      key: KEY,
      name: "Cinematic portrait",
    });
  });

  // Nothing to show, so nothing to press: a link that filtered the library
  // down to nothing would read as a broken grid rather than as an empty card.
  it("leaves the figure as plain text on a card with no pictures", async () => {
    const { wrapper } = await mountWith([KEY], [card({ picture_count: 0 })]);
    expect(wrapper.find('[data-testid="wftab-show-pictures"]').exists()).toBe(
      false,
    );
    expect(textOf(wrapper)).toContain("0 pictures");
  });

  it("shows a selected stack member, which the grid never lists", async () => {
    const member = card({ key: OTHER, name: "SDXL portrait (lightning)" });
    const cover = card({ stack_size: 2, member_keys: [OTHER] });
    const store = useWorkflowsStore();
    store.cards = [cover];
    store.members = { [KEY]: [cover, member] };
    store.selectedKeys = [OTHER];
    const wrapper = mount(WorkflowTab, globalOpts);
    await flush(wrapper);
    const text = textOf(wrapper);
    expect(text).toContain("SDXL portrait (lightning)");
    expect(text).toContain("In the Cinematic portrait stack");
  });
});

describe("the Tasks tab", () => {
  // The rail on /workflows is the only thing on that screen, so without this
  // tab a run started here cannot be watched from here.
  it("offers Tasks last, and shows the task manager instead of the workflow", async () => {
    const { wrapper } = await mountWith([KEY]);
    const band = wrapper.findAll("button.inspector-tab");
    // Recipes joined the band in F6 (#1408); Tasks stays last, which is the
    // property this test is about.
    expect(band.map((t) => t.text())).toEqual(["Recipes", "Workflow", "Tasks"]);

    await band[2].trigger("click");
    await flush(wrapper);
    const text = textOf(wrapper);
    // The task manager's own empty state, and none of the workflow.
    expect(text).toContain("No active tasks");
    expect(text).not.toContain("Cinematic portrait");
    // Run… acts on the workflow, so it goes with the workflow: a footer left
    // behind would run a card the reader can no longer see.
    expect(wrapper.find(".wftab-foot").exists()).toBe(false);

    await wrapper.findAll("button.inspector-tab")[1].trigger("click");
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Cinematic portrait");
    expect(wrapper.find(".wftab-foot").exists()).toBe(true);
  });

  it("follows the store's deep link onto the Tasks tab", async () => {
    // A run started from THIS rail pushes a toast whose *Show* action asks for
    // the Tasks tab. It used to call a method exposed by `StatsSidebar`, which
    // is not mounted on /workflows, so the one screen that starts runs was the
    // one screen where *Show* did nothing at all.
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).not.toContain("No active tasks");

    useSidebarStore().showTasksTab();
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("No active tasks");
  });

  it("pulses the tab while work is running, and only then", async () => {
    const { wrapper } = await mountWith([KEY]);
    const tasksStore = useTasksStore();
    expect(
      wrapper
        .findAll("button.inspector-tab").at(-1)
        .find(".inspector-tab-pulse")
        .exists(),
    ).toBe(false);

    tasksStore.setComfyuiRun("run-1", {
      status: "running",
      percent: 10,
      message: "sampling",
      label: "Cinematic portrait",
    });
    await flush(wrapper);
    expect(
      wrapper
        .findAll("button.inspector-tab").at(-1)
        .find(".inspector-tab-pulse")
        .exists(),
    ).toBe(true);
  });
});
