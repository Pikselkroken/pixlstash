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

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getWorkflowCard = vi.fn();
const listWorkflowCards = vi.fn();
const patchWorkflowCard = vi.fn();
const preflightWorkflowRun = vi.fn();
const setWorkflowDefaults = vi.fn();
const setWorkflowPins = vi.fn();
const setWorkflowSlots = vi.fn();
const stackWorkflows = vi.fn();
const getLoraChain = vi.fn();

vi.mock("../../api/workflows", () => ({
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  patchWorkflowCard: (...args) => patchWorkflowCard(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  setWorkflowDefaults: (...args) => setWorkflowDefaults(...args),
  setWorkflowPins: (...args) => setWorkflowPins(...args),
  setWorkflowSlots: (...args) => setWorkflowSlots(...args),
  // `WorkflowCard` renders its covers through this, so a mock without it
  // throws in the render and every assertion in the file goes with it.
  workflowCoverUrl: (cover) => cover?.url ?? "",
  stackWorkflows: (...args) => stackWorkflows(...args),
  getLoraChain: (...args) => getLoraChain(...args),
}));

// *Show all N pictures* (F7) leaves this screen for the library, and
// `?tab=recipes` (#1480) arrives on it from the lightbox's match banner.
const push = vi.fn();
const replace = vi.fn();
const route = vi.hoisted(() => ({ name: "workflows", query: {} }));
vi.mock("vue-router", () => ({
  useRouter: () => ({ push, replace }),
  useRoute: () => route,
}));

import WorkflowTab from "./WorkflowTab.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
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

/** `GET /workflows/{key}/lora-chain`, as the route serves it (#1478). */
function loraChain(overrides = {}) {
  return {
    workflow_key: KEY,
    editable: true,
    refusal: null,
    source: { node_id: "4", class_type: "CheckpointLoaderSimple", outputs: ["MODEL", "CLIP"] },
    sink: { summary: "KSampler #7 reads model", consumers: [] },
    loaders: [
      { node_id: "14", name: "lightning-8step", filename: "lightning-8step.safetensors", strength: 1, on_shelf: true, sha256: "s1" },
      { node_id: "22", name: "neon-rain-v2", filename: "neon-rain-v2.safetensors", strength: 0.85, on_shelf: true, sha256: "s2" },
      { node_id: "31", name: "film-grain-35mm", filename: "film-grain-35mm.safetensors", strength: 0.4, on_shelf: true, sha256: "s3" },
      { node_id: "33", name: "hairstyle-v3", filename: "hairstyle-v3.safetensors", strength: 0.6, on_shelf: false, sha256: null },
    ],
    added_loader_class: "LoraLoader",
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

// Unmounted after each test: a pre-flight still settling would otherwise
// land in the next test's call count.
const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

/** Mount the rail with `keys` selected and `cards` in the grid. */
async function mountWith(keys, cards = [card()]) {
  const store = useWorkflowsStore();
  store.cards = cards;
  store.selectedKeys = keys;
  const wrapper = mount(WorkflowTab, globalOpts);
  mounted.push(wrapper);
  await flush(wrapper);
  return { wrapper, store };
}

/** Past the pre-flight's settle delay (250 ms), and its answer rendered. */
async function settle(wrapper) {
  await new Promise((resolve) => setTimeout(resolve, 300));
  await flush(wrapper);
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  route.query = {};
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
  getLoraChain.mockReset().mockResolvedValue(loraChain());
  replace.mockReset();
  preflightWorkflowRun.mockReset().mockResolvedValue({ groups: [] });
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

  it("says the precision the server took out of the name", async () => {
    // The panel shows `name`, which no longer carries the postfix - so without
    // the badge two quant builds of one model read identically here, which is
    // the exact failure stripping the name is allowed only because the badge
    // prevents. Asserted per line, because a badge on the wrong row would
    // satisfy a search of the whole panel.
    const quantised = card({
      models: [
        {
          name: "t5xxl",
          title: null,
          kind: "checkpoint",
          quant: "fp8_e4m3",
          slot_label: "m1",
        },
        // Nothing recorded: no badge, and NOT an empty separator.
        { name: "ae", title: null, kind: "vae", quant: null, slot_label: "m2" },
      ],
      loras: [
        {
          name: "detail",
          mark: "structural",
          quant: "q4_k_m",
          slot_label: "l1",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: quantised }));
    const { wrapper } = await mountWith([KEY], [quantised]);
    const values = wrapper.findAll(".wftab-value").map((el) => el.text());
    expect(values).toEqual(["t5xxl · FP8 E4M3", "ae"]);
    expect(wrapper.find(".wftab-chip").text()).toContain("detail · Q4_K_M");
  });

  it("falls back to the filename where the shelf has no name", async () => {
    // The ordinary case - the line must say the file, never go blank.
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
  });
});

describe("a checkpoint that will not load", () => {
  // Never recorded, forgotten with the shelf's copy, or recorded and simply
  // not installed in ComfyUI. Whatever the case, the row says WHICH file: a
  // warning that cannot say what is missing gives the reader nothing to do.
  const unnamed = card({
    models: [{ name: null, kind: "checkpoint", slot_label: "n1/ckpt_name" }],
  });
  const named = card({
    models: [
      { name: "realvisXL_v5", kind: "checkpoint", slot_label: "n1/ckpt_name" },
    ],
  });

  function missingFile(file) {
    return {
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file, folder: "checkpoints" }],
            },
          ],
        },
      ],
    };
  }

  /** The row's file name and the tooltip carrying the whole value. */
  function missingLine(wrapper) {
    const line = wrapper.find('[data-testid="wftab-missing-file"]');
    return {
      text: line.text().replace(/\s+/g, " "),
      tooltip: line.find("tooltip-stub").attributes("text"),
    };
  }

  it("names the file ComfyUI does not have, without its folders", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    const line = missingLine(wrapper);
    expect(line.text).toBe(
      "realvisXL_v5.safetensors is not installed in ComfyUI.",
    );
    expect(line.tooltip).toBe("SDXL/realvisXL_v5.safetensors");
  });

  it("names an unnamed checkpoint from the graph a run would submit", async () => {
    // The hub forgot the name; the pre-flight says "(forgotten model)", which
    // names nothing. The graph still says which file it loads.
    preflightWorkflowRun.mockResolvedValue(missingFile("(forgotten model)"));
    getWorkflowCard.mockResolvedValue(
      detail({
        card: unnamed,
        graph_base_models: ["Flux/klein-9b-fp8.safetensors"],
      }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    const line = missingLine(wrapper);
    expect(line.text).toBe(
      "klein-9b-fp8.safetensors is not installed in ComfyUI.",
    );
    expect(line.tooltip).toBe("Flux/klein-9b-fp8.safetensors");
    expect(textOf(wrapper)).not.toContain("Not recorded");
  });

  it("says so plainly when no file name was kept anywhere", async () => {
    preflightWorkflowRun.mockResolvedValue(missingFile("(forgotten model)"));
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: [] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    expect(textOf(wrapper)).toContain("No file name was kept for it anywhere.");
    expect(textOf(wrapper)).not.toContain("(forgotten model)");
  });

  it("does not call a checkpoint missing that ComfyUI has", async () => {
    // Unnamed on the card, but the pre-flight found nothing missing: it is
    // installed, and the row shows the file the graph loads.
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: ["SDXL/pony.safetensors"] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
    expect(textOf(wrapper)).toContain("pony.safetensors");
    expect(textOf(wrapper)).not.toContain("SDXL/");
  });

  it("warns from the card alone while ComfyUI cannot be asked", async () => {
    preflightWorkflowRun.mockRejectedValue(new Error("ComfyUI is down"));
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: ["SDXL/pony.safetensors"] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    // Named, but not claimed uninstalled: nobody could ask.
    expect(missingLine(wrapper).text).toBe("pony.safetensors");
  });

  it("draws no warning before the card's detail has arrived", async () => {
    getWorkflowCard.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountWith([KEY], [unnamed]);
    expect(wrapper.find(".wftab-missing").exists()).toBe(false);
  });

  it("says None in this workflow for a graph that loads no base model", async () => {
    // An upscaler: "Not recorded" read as a gap in the records, when the
    // graph was read and simply loads none.
    const upscale = card({
      models: [
        {
          name: "4x-ultrasharp",
          kind: "upscale_model",
          slot_label: "u/model_name",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(
      detail({ card: upscale, graph_base_models: [] }),
    );
    const { wrapper } = await mountWith([KEY], [upscale]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("None in this workflow");
    expect(textOf(wrapper)).not.toContain("Not recorded");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });

  it("does not blame the checkpoint for another missing model", async () => {
    preflightWorkflowRun.mockResolvedValue({
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file: "other.vae.safetensors", folder: "vae" }],
            },
          ],
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith([KEY]);
    await settle(wrapper);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });

  it("asks ComfyUI once for a selection passed straight through", async () => {
    // Each ask is a fresh object_info read, so arrowing across the grid
    // must not ask once per card it crosses.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { key } }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card(), card({ key: OTHER, name: "Card B" })],
    );
    store.selectedKeys = [OTHER];
    await settle(wrapper);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(preflightWorkflowRun).toHaveBeenCalledWith({
      workflow_key: OTHER,
      values: [],
    });
  });

  it("asks ComfyUI nothing once the rail has closed", async () => {
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith([KEY]);
    wrapper.unmount();
    mounted.length = 0;
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(preflightWorkflowRun).not.toHaveBeenCalled();
  });

  it("does not put card A's pre-flight answer on card B", async () => {
    let answerA;
    preflightWorkflowRun.mockImplementation((body) =>
      body.workflow_key === KEY
        ? new Promise((resolve) => {
            answerA = () =>
              resolve({
                groups: [
                  {
                    reasons: [
                      {
                        code: "missing_models",
                        models: [
                          { file: "a.safetensors", folder: "checkpoints" },
                        ],
                      },
                    ],
                  },
                ],
              });
          })
        : Promise.resolve({ groups: [] }),
    );
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { key, name: key === KEY ? "Card A" : "Card B" } }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ key: OTHER, name: "Card B" })],
    );
    await settle(wrapper);
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    // A answers while B's own ask is still settling: nothing else would
    // cover the wrong answer up.
    answerA();
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Card B");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });

  it("names the checkpoint plainly when the pre-flight cannot be asked", async () => {
    preflightWorkflowRun.mockRejectedValue(new Error("ComfyUI is down"));
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith([KEY]);
    await settle(wrapper);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });
  it("does not call a Flux graph's unet a missing checkpoint", async () => {
    const flux = card({
      models: [{ name: "flux1-dev", kind: "unet", slot_label: "n1/unet_name" }],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: flux }));
    const { wrapper } = await mountWith([KEY], [flux]);
    expect(textOf(wrapper)).toContain("flux1-dev");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
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
  // The other half of the lightbox banner's link (#1480): `?topology=` selects
  // the card in `WorkflowsView`, and this opens the rail on its recipes.
  it("lands on the recipes, with the rail open, from ?tab=recipes", async () => {
    route.query = { topology: "f00d", tab: "recipes" };
    const sidebar = useSidebarStore();
    // Shut, which is the state that made the link a dead end of its own: the
    // card would be selected behind a rail nobody opened.
    sidebar.statsOpen = false;
    const { wrapper } = await mountWith([KEY]);

    expect(sidebar.statsOpen).toBe(true);
    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      true,
    );
  });

  it("leaves the rail alone without that query", async () => {
    // The control for the check above: `tab` defaults to Workflow, so a
    // watcher that fired unconditionally would only show up in the RAIL being
    // forced open on a screen the reader had shut it on.
    const sidebar = useSidebarStore();
    sidebar.statsOpen = false;
    const { wrapper } = await mountWith([KEY]);
    expect(sidebar.statsOpen).toBe(false);
    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      false,
    );
  });

  it("shows the stack's recipes, and the footer belongs to Workflow", async () => {
    const { wrapper } = await mountWith([KEY]);
    const tab = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes");
    expect(tab).toBeTruthy();
    await tab.trigger("click");
    await flush(wrapper);

    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      true,
    );
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
      "Run one workflow, or one whole stack, at a time",
    );
  });

  it("opens the Run popup on THIS card when Run… is pressed", async () => {
    // The only entry point to a card-sourced run now that the toolbar's
    // Generate button is gone, and it was unguarded: `function run() { return; }`
    // kept the whole suite green.
    const { wrapper } = await mountWith([KEY], [card()]);
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));

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
    // Seeded first: `source` is null on a fresh store, so asserting null
    // against an untouched default would pass with `run()` deleted entirely.
    const runDialog = useRunDialogStore();
    expect(runDialog.source).toBe(null);
    runDialog.openRun({ kind: "picture", pictureIds: [99] });
    await run.trigger("click");
    await flush(wrapper);
    expect(runDialog.source.pictureIds).toEqual([99]);
  });

  it("shows a stack selected whole as its cover, with a member picker", async () => {
    // A click on a stack card selects the cover and its members: several keys,
    // one card on screen. The rail reads it as one workflow, the cover first,
    // and the picker at the top switches to another member without expanding
    // the stack.
    const cover = card({
      stack_size: 2,
      member_keys: [OTHER],
      members: [
        { key: KEY, name: "Cinematic portrait", sets_apart: [], differs_by: [] },
        { key: OTHER, name: "Cinematic portrait", sets_apart: ["Flux"], differs_by: [] },
      ],
    });
    getWorkflowCard.mockImplementation(async (key) =>
      key === OTHER
        ? detail({ card: { key: OTHER, name: "Flux portrait", stack_size: 2 } })
        : detail({ card: { stack_size: 2 } }),
    );
    const { wrapper } = await mountWith([KEY, OTHER], [cover]);

    const text = textOf(wrapper);
    expect(text).not.toContain("workflows selected");
    expect(text).toContain("A stack of 2 workflows");
    const pick = wrapper.find("[data-testid='wftab-stack-pick'] select");
    expect(pick.element.value).toBe(KEY);
    expect(pick.findAll("option").map((o) => o.text())).toEqual([
      "Cinematic portrait",
      "Cinematic portrait — Flux",
    ]);
    expect(getWorkflowCard).toHaveBeenLastCalledWith(KEY);

    const run = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Run…"));
    expect(run().attributes("aria-disabled")).toBeUndefined();
    await run().trigger("click");
    await flush(wrapper);
    expect(useRunDialogStore().source).toMatchObject({
      kind: "card",
      workflowKey: KEY,
    });

    // Picking the member reads it and makes it what Run… runs.
    await pick.setValue(OTHER);
    await flush(wrapper);
    expect(getWorkflowCard).toHaveBeenLastCalledWith(OTHER);
    expect(getLoraChain).toHaveBeenLastCalledWith(OTHER);
    await run().trigger("click");
    await flush(wrapper);
    expect(useRunDialogStore().source).toMatchObject({ workflowKey: OTHER });
  });

  /** A two-card stack whose cover is KEY and member OTHER. */
  function twoStack() {
    return card({
      stack_size: 2,
      member_keys: [OTHER],
      members: [
        { key: KEY, name: "Cinematic portrait" },
        { key: OTHER, name: "Flux portrait" },
      ],
    });
  }

  it("keeps the picker on screen while a member reads, and says a failed read", async () => {
    let fail;
    getWorkflowCard.mockImplementation((key) =>
      key === OTHER
        ? new Promise((_, reject) => {
            fail = reject;
          })
        : Promise.resolve(detail({ card: { stack_size: 2 } })),
    );
    const { wrapper } = await mountWith([KEY, OTHER], [twoStack()]);
    const pick = () => wrapper.find("[data-testid='wftab-stack-pick'] select");
    await pick().setValue(OTHER);
    await flush(wrapper);
    expect(pick().exists()).toBe(true);
    expect(pick().element.value).toBe(OTHER);
    expect(textOf(wrapper)).toContain("Reading this workflow…");
    // Run… stays on screen, refusing only for as long as the read.
    const run = wrapper.findAll("button").find((b) => b.text().includes("Run…"));
    expect(run.attributes("aria-disabled")).toBe("true");

    fail(new Error("offline"));
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Could not read this workflow just now.");
    await pick().setValue(KEY);
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Cinematic portrait");
    expect(textOf(wrapper)).not.toContain("Could not read");
  });

  it("follows a picked member that a LoRA flip takes out of its stack", async () => {
    getWorkflowCard.mockImplementation(async (key) =>
      detail({
        card: card({ key, name: key === OTHER ? "Flux portrait" : "Cover" }),
      }),
    );
    const { wrapper, store } = await mountWith([KEY, OTHER], [twoStack()]);
    await wrapper.find("[data-testid='wftab-stack-pick'] select").setValue(OTHER);
    await flush(wrapper);
    // The flip re-keys the member and splits the stack: the grid comes back
    // without the old stack, so the rail can no longer derive the pick.
    listWorkflowCards.mockResolvedValue({
      cards: [card({ key: KEY }), card({ key: MOVED })],
      one_offs: 0,
      hidden: 0,
    });
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Recipe")
      .trigger("click");
    await flush(wrapper);
    expect(setWorkflowSlots).toHaveBeenCalledWith(OTHER, { l1: "recipe" });
    expect(store.selectedKeys).toEqual([MOVED]);
  });

  it("leaves a reader who picked another member during a flip where they are", async () => {
    const stack = card({
      stack_size: 3,
      member_keys: [OTHER, MOVED],
      members: [
        { key: KEY, name: "Cover" },
        { key: OTHER, name: "Flux portrait" },
        { key: MOVED, name: "Third" },
      ],
    });
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: card({ key, name: key }) }),
    );
    let answer;
    setWorkflowSlots.mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    // The re-read after the flip hands the grid the same stack back, minus
    // the flipped member, which the answer re-keyed.
    listWorkflowCards.mockResolvedValue({
      cards: [{ ...stack, member_keys: [MOVED], stack_size: 2 }],
      one_offs: 0,
      hidden: 0,
    });
    const { wrapper, store } = await mountWith([KEY, OTHER, MOVED], [stack]);
    const pick = () => wrapper.find("[data-testid='wftab-stack-pick'] select");
    await pick().setValue(OTHER);
    await flush(wrapper);
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Recipe")
      .trigger("click");
    await flush(wrapper);
    // Mid-write, the reader moves on to another member of the same stack.
    await pick().setValue(MOVED);
    await flush(wrapper);
    answer({ key: "f".repeat(64), moved: {} });
    await flush(wrapper);
    await flush(wrapper);
    // Not yanked onto the flipped card: the selection and the pick stand.
    expect(store.selectedKeys).toEqual([KEY, OTHER, MOVED]);
    expect(store.stackPick.key).toBe(MOVED);
  });

  it("starts every newly selected stack on its cover", async () => {
    const stack = (key, member) =>
      card({
        key,
        stack_size: 2,
        member_keys: [member],
        members: [
          { key, name: `Cover ${key[0]}` },
          { key: member, name: `Member ${member[0]}` },
        ],
      });
    const { wrapper, store } = await mountWith(
      [KEY, OTHER],
      [stack(KEY, OTHER), stack(MOVED, "e".repeat(64))],
    );
    await wrapper.find("[data-testid='wftab-stack-pick'] select").setValue(OTHER);
    await flush(wrapper);
    store.selectedKeys = [MOVED, "e".repeat(64)];
    await flush(wrapper);
    expect(
      wrapper.find("[data-testid='wftab-stack-pick'] select").element.value,
    ).toBe(MOVED);
    expect(getWorkflowCard).toHaveBeenLastCalledWith(MOVED);
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

describe("Open in ComfyUI", () => {
  const openButton = (wrapper) => {
    const found = wrapper.find('[data-testid="wftab-open-comfyui"]');
    return found.exists() ? found : undefined;
  };

  let open;
  beforeEach(() => {
    open = vi.spyOn(window, "open").mockImplementation(() => null);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.pixlstashDesktop;
  });

  function configure(url = "http://127.0.0.1:8188/") {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    filterStore.comfyuiUrl = url;
  }

  it("opens the configured ComfyUI on THIS card, in a new tab", async () => {
    configure();
    const { wrapper } = await mountWith([KEY], [card()]);

    await openButton(wrapper).trigger("click");

    expect(open).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${KEY}`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("sends a listen-everywhere address to the machine the page came from", async () => {
    configure("http://0.0.0.0:8188/");
    const { wrapper } = await mountWith([KEY], [card()]);

    await openButton(wrapper).trigger("click");

    expect(open.mock.calls[0][0]).toBe(
      `http://${window.location.hostname}:8188/?pixlstash_workflow=${KEY}`,
    );
  });

  it("is not offered without a ComfyUI address", async () => {
    const { wrapper } = await mountWith([KEY], [card()]);
    expect(openButton(wrapper)).toBeUndefined();

    // The flag alone is not enough: the button is gated on the address it
    // opens, so a flag set without one never draws a button that does nothing.
    useFilterStore().comfyuiConfigured = true;
    await flush(wrapper);
    expect(openButton(wrapper)).toBeUndefined();

    useFilterStore().comfyuiUrl = "http://127.0.0.1:8188/";
    await flush(wrapper);
    expect(openButton(wrapper)).toBeTruthy();
  });

  it("is named for a screen reader, since it is only the ComfyUI mark", async () => {
    configure();
    const { wrapper } = await mountWith([KEY], [card()]);
    expect(openButton(wrapper).attributes("aria-label")).toBe("Open in ComfyUI");
  });

  it("goes through the desktop shell's bridge, which window.open cannot", async () => {
    configure();
    const openComfyui = vi.fn().mockResolvedValue(true);
    window.pixlstashDesktop = { openComfyui };
    const { wrapper } = await mountWith([KEY], [card()]);

    await openButton(wrapper).trigger("click");

    expect(openComfyui).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${KEY}`,
    );
    expect(open).not.toHaveBeenCalled();
  });

  it("says so when the desktop shell refuses the link", async () => {
    configure();
    window.pixlstashDesktop = { openComfyui: vi.fn().mockResolvedValue(false) };
    const { wrapper } = await mountWith([KEY], [card()]);

    await openButton(wrapper).trigger("click");
    await flush(wrapper);

    expect(JSON.stringify(useNoticeStore().$state)).toContain(
      "Could not open ComfyUI",
    );
  });

  it("is not offered on an older desktop shell with no bridge", async () => {
    configure();
    window.pixlstashDesktop = {};
    const { wrapper } = await mountWith([KEY], [card()]);
    expect(openButton(wrapper)).toBeUndefined();
  });

  it("stays on screen and refuses, with the reason, when several are selected", async () => {
    configure();
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ key: OTHER })],
    );
    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    const described = button.attributes("aria-describedby");
    expect(wrapper.find(`#${described}`).text()).toBe(
      "Open one workflow, or one whole stack, at a time",
    );
    await button.trigger("click");
    expect(open).not.toHaveBeenCalled();
  });

  it("opens a stack selected whole on its cover, as Run… runs it", async () => {
    configure();
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card({ stack_size: 2, member_keys: [OTHER] })],
    );
    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBeUndefined();

    await button.trigger("click");

    expect(open.mock.calls[0][0]).toBe(
      `http://127.0.0.1:8188/?pixlstash_workflow=${KEY}`,
    );
  });

  it("refuses an address that is not a web address", async () => {
    configure("javascript:alert(1)");
    const { wrapper } = await mountWith([KEY], [card()]);

    await openButton(wrapper).trigger("click");

    expect(open).not.toHaveBeenCalled();
    expect(JSON.stringify(useNoticeStore().$state)).toContain(
      "not a web address",
    );
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

  it("drops a whole-set write queued behind another when the selection moves", async () => {
    // The pins PUT is whole-set, built from `defaults` when it RUNS, which is
    // after the notes save it waited behind. By then those are card B's, and
    // sending them to A would replace A's own choice with B's.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { key, defaults: key === KEY ? [CFG] : [WIDTH] } }),
    );
    let settle;
    patchWorkflowCard.mockReturnValue(
      new Promise((resolve) => {
        settle = () => resolve(detail({ card: { defaults: [CFG] } }));
      }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ key: OTHER, name: "Card B" })],
    );
    await wrapper.find("textarea").setValue("a note");
    await wrapper.find("textarea").trigger("blur");
    await rowNamed(wrapper, "cfg").find("button").trigger("click");
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    settle();
    await flush(wrapper);
    expect(patchWorkflowCard).toHaveBeenCalledWith(KEY, { notes: "a note" });
    expect(setWorkflowPins).not.toHaveBeenCalled();
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
        .findAll("button.inspector-tab")
        .at(-1)
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
        .findAll("button.inspector-tab")
        .at(-1)
        .find(".inspector-tab-pulse")
        .exists(),
    ).toBe(true);
  });
});

describe("the LoRA chain (#1478)", () => {
  const chainOpts = {
    global: {
      stubs: {
        ...globalOpts.global.stubs,
        EditLorasDialog: {
          name: "EditLorasDialog",
          props: ["open", "workflowKey", "cardName", "pictureCount", "dropLora"],
          template: "<div class='eld-stub' />",
        },
      },
    },
  };

  async function mountChain(keys = [KEY]) {
    const store = useWorkflowsStore();
    store.cards = [card()];
    store.selectedKeys = keys;
    const wrapper = mount(WorkflowTab, chainOpts);
    await flush(wrapper);
    return { wrapper, store };
  }

  function editButton(wrapper) {
    return wrapper.find("[data-testid='wftab-edit-loras']");
  }

  it("lists the loaders in apply order, with strengths and the shelf count", async () => {
    const { wrapper } = await mountChain();
    expect(getLoraChain).toHaveBeenCalledWith(KEY);
    const rows = wrapper.findAll(".wftab-chain-row");
    expect(
      rows.map((row) => [
        row.find(".wftab-chain-name").text(),
        row.find(".wftab-chain-strength").text(),
      ]),
    ).toEqual([
      ["lightning-8step", "1.00"],
      ["neon-rain-v2", "0.85"],
      ["film-grain-35mm", "0.40"],
      // Not on the shelf, so shown as the file it is.
      ["hairstyle-v3.safetensors", "0.60"],
    ]);
    expect(
      wrapper.find("[data-testid='wftab-shelf-line']").text().replace(/\s+/g, " "),
    ).toBe("In the order the chain applies them. 3 of 4 are on your model shelf.");
    // B1's workflow/look mark stays beside it.
    expect(wrapper.findComponent({ name: "Segmented" }).exists()).toBe(true);
  });

  it("offers Edit LoRAs… on a workflow with no loader at all", async () => {
    getLoraChain.mockResolvedValue(loraChain({ loaders: [] }));
    const { wrapper } = await mountChain();
    expect(textOf(wrapper)).toContain("No LoRA loader. Editing adds the first one.");
    const edit = editButton(wrapper);
    expect(edit.exists()).toBe(true);
    expect(edit.attributes("disabled")).toBeUndefined();
    expect(wrapper.find("[data-testid='wftab-shelf-line']").exists()).toBe(false);

    await edit.trigger("click");
    await flush(wrapper);
    const dialog = wrapper.findComponent({ name: "EditLorasDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("workflowKey")).toBe(KEY);
    expect(dialog.props("cardName")).toBe("Cinematic portrait");
    expect(dialog.props("pictureCount")).toBe(184);
    expect(dialog.props("dropLora")).toBe("");
  });

  it("says so, and refuses Edit, for a card with no graph", async () => {
    getLoraChain.mockRejectedValue({ response: { status: 409 } });
    const { wrapper } = await mountChain();
    expect(textOf(wrapper)).toContain("PixlStash has no graph for this workflow");
    expect(editButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("opens Edit LoRAs… from Save-as-recipe's link, with the entry deleted", async () => {
    route.query = { card: OTHER, edit: "loras", drop_lora: "hairstyle-v3.safetensors" };
    const sidebar = useSidebarStore();
    sidebar.statsOpen = false;
    const { wrapper, store } = await mountChain([]);

    expect(store.selectedKeys).toEqual([OTHER]);
    expect(sidebar.statsOpen).toBe(true);
    const dialog = wrapper.findComponent({ name: "EditLorasDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("workflowKey")).toBe(OTHER);
    expect(dialog.props("dropLora")).toBe("hairstyle-v3.safetensors");
    // One-shot: taken off the URL so a reload does not reopen it.
    expect(replace).toHaveBeenCalledWith({ query: { card: OTHER } });
  });
});
