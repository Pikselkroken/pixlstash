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
  stackWorkflows: (...args) => stackWorkflows(...args),
}));

import WorkflowTab from "./WorkflowTab.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";

const KEY = "a".repeat(64);
const OTHER = "b".repeat(64);
const MOVED = "c".repeat(64);

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": { template: "<div><slot name='activator' :props='{}'/><slot/></div>" },
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
      { name: "realvisXL_v5.safetensors", kind: "checkpoint", slot_label: "m1" },
    ],
    loras: [
      { name: "lightning-8step", kind: "lora", mark: "structural", slot_label: "l1" },
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
    expect(rowNamed(wrapper, "steps").element.closest("details")).not.toBeNull();
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
    const { wrapper } = await mountWith([KEY], [
      card({
        loras: [{ name: "mystery", kind: "lora", mark: "structural" }],
      }),
    ]);
    expect(wrapper.findComponent({ name: "Segmented" }).props("disabled")).toBe(
      true,
    );
    expect(textOf(wrapper)).toContain("no recorded address");
  });
});

describe("with several workflows selected", () => {
  it("keeps Run… on screen, refused, with the reason attached", async () => {
    const { wrapper } = await mountWith([KEY, OTHER], [card(), card({ key: OTHER })]);
    const run = wrapper.findAll("button").find((b) => b.text().includes("Run…"));
    expect(run).toBeTruthy();
    expect(run.attributes("aria-disabled")).toBe("true");
    const described = run.attributes("aria-describedby");
    expect(wrapper.find(`#${described}`).text()).toBe(
      "Run one workflow at a time",
    );
  });

  it("does not start a run when Run… is pressed", async () => {
    const { wrapper } = await mountWith([KEY, OTHER], [card(), card({ key: OTHER })]);
    const run = wrapper.findAll("button").find((b) => b.text().includes("Run…"));
    await run.trigger("click");
    await flush(wrapper);
    const { useWorkflowRunStore } = await import(
      "../../stores/useWorkflowRunStore"
    );
    expect(useWorkflowRunStore().open).toBe(false);
  });

  it("offers Stack together and Hide, and says how many are selected", async () => {
    const { wrapper } = await mountWith([KEY, OTHER], [card(), card({ key: OTHER })]);
    const text = textOf(wrapper);
    expect(text).toContain("2 workflows selected");
    expect(text).toContain("Stack together");
    const stack = wrapper.findAll("button").find((b) => b.text() === "Stack together");
    await stack.trigger("click");
    await flush(wrapper);
    expect(stackWorkflows).toHaveBeenCalledWith([KEY, OTHER]);
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
    // A hidden card is not in `GET /workflows/cards`, which is exactly the
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

  it("says how many of a multi-select hide actually went", async () => {
    patchWorkflowCard
      .mockResolvedValueOnce(detail())
      .mockRejectedValueOnce(new Error("nope"));
    const { wrapper } = await mountWith([KEY, OTHER], [card(), card({ key: OTHER })]);
    const hide = wrapper.findAll("button").find((b) => b.text() === "Hide");
    await hide.trigger("click");
    await flush(wrapper);
    // Both were attempted, the grid was re-read anyway, and the message is
    // not "none of it worked".
    expect(patchWorkflowCard).toHaveBeenCalledTimes(2);
    expect(listWorkflowCards).toHaveBeenCalled();
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    expect(useNoticeStore().notices.at(-1).text).toContain("1 of 2");
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
