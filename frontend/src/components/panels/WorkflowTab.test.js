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
  it("says where each value came from", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    const text = textOf(wrapper);
    expect(text).toContain("from your best pictures");
    expect(text).toContain("edited by you");
  });

  it("offers the reset only on a value the owner edited", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // Two rows, one reset: the one whose provenance is `edited`.
    expect(wrapper.findAll(".wfdef")).toHaveLength(2);
    expect(wrapper.findAll(".wfdef-reset")).toHaveLength(1);
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
      detail({ card: { defaults: [STEPS, { ...CFG, label: "sampler_name", input_name: "sampler_name" }] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // `steps` is pinned and shows above the disclosure; `sampler_name` is not.
    expect(wrapper.find("details.wftab-disclose summary").text()).toContain(
      "All 2 parameters",
    );
    expect(
      wrapper.findAll('.wfdef-pin[aria-pressed="true"]'),
    ).toHaveLength(1);
  });

  it("keeps an empty pin list apart from no choice at all", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS] }, pins: [] }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findAll('.wfdef-pin[aria-pressed="true"]')).toHaveLength(0);
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
    const workflow = wrapper
      .findAll("button")
      .find((button) => button.text() === "Workflow");
    await workflow.trigger("click");
    await flush(wrapper);
    expect(setWorkflowSlots).not.toHaveBeenCalled();
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
