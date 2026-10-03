// The Workflow tab (v1.12 F3): the three things about it that can silently
// invert.
//
// * A default's provenance and its reset. Reset is a WHOLE-SET write, so the
//   request has to carry every other edited value; a reset that sends only
//   the row it was fired on resets all of them and reads as working.
// * Models and Defaults read the detail's `default_recipe` (#1623), never the
//   base card's own slots once the recipe has been read.
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
const getLoraSummary = vi.fn();
const getLoraChain = vi.fn();
const readModelSwap = vi.fn();
const setWorkflowModelFix = vi.fn();
const setWorkflowDefaultLora = vi.fn();

vi.mock("../../api/workflows", () => ({
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  patchWorkflowCard: (...args) => patchWorkflowCard(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  setWorkflowDefaults: (...args) => setWorkflowDefaults(...args),
  setWorkflowPins: (...args) => setWorkflowPins(...args),
  getLoraSummary: (...args) => getLoraSummary(...args),
  // `WorkflowCard` renders its covers through this, so a mock without it
  // throws in the render and every assertion in the file goes with it.
  workflowCoverUrl: (cover) => cover?.url ?? "",
  getLoraChain: (...args) => getLoraChain(...args),
  readModelSwap: (...args) => readModelSwap(...args),
  setWorkflowModelFix: (...args) => setWorkflowModelFix(...args),
  setWorkflowDefaultLora: (...args) => setWorkflowDefaultLora(...args),
}));

const getPixlstashNode = vi.fn();
vi.mock("../../api/comfyui", () => ({
  getPixlstashNode: (...args) => getPixlstashNode(...args),
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
  const shown = {
    id: KEY,
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
        slot_label: "l1",
      },
    ],
    picture_count: 184,
    rating: 4.8,
    covers: [],
    saved_recipe_count: 0,
    defaults: [],
    base_topology: "d".repeat(64),
    topologies: ["d".repeat(64)],
    variant_count: 1,
    last_used: null,
    rank: 4.5,
    ...overrides,
  };
  // Each named slot records the file its recipe model names, as the server
  // serves it: that is what a recipe model is matched to its slot by.
  shown.models = (shown.models ?? []).map((model) =>
    model.name && model.filename === undefined
      ? { ...model, filename: `${model.name}.safetensors` }
      : model,
  );
  return shown;
}

const ADA = `asset:${"1".repeat(64)}`;
const BO = `asset:${"2".repeat(64)}`;

/** `GET /workflows/{id}/lora-summary`: two LoRAs that change, Ada on the cover. */
function loraSummary(overrides = {}) {
  return {
    pictures: 5,
    shared: [],
    varying: [
      {
        asset: BO,
        filename: "bo.safetensors",
        name: "Bo",
        on_shelf: true,
        // The shelf file's CONTENT digest: not the hex of the asset (a name hash).
        sha256: "b".repeat(64),
        pictures: 3,
        picture_ids: [31, 32, 33],
      },
      {
        asset: ADA,
        filename: "ada.safetensors",
        name: "Ada",
        on_shelf: true,
        sha256: "a".repeat(64),
        pictures: 2,
        picture_ids: [21, 22],
      },
    ],
    without: null,
    cover_asset: ADA,
    ...overrides,
  };
}

/** `GET /workflows/{id}/lora-chain`, as the route serves it (#1478). */
function loraChain(overrides = {}) {
  return {
    workflow_id: KEY,
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

/** A card slot word -> the `default_recipe.models[].kind` the server sends. */
const RECIPE_KIND = { unet: "checkpoint", clip: "text_encoder" };

/**
 * `GET /workflows/{id}`. The card's `default_recipe` is filled here, as the
 * detail read fills it: its `values` are the `defaults` a test names.
 */
function detail({ card: cardOverrides = {}, ...overrides } = {}) {
  const shown = card(cardOverrides);
  return {
    card: {
      default_recipe: {
        sampled: 3,
        // The base card's named slots at their addresses, as the server
        // samples them for a card with one variant.
        models: (shown.models ?? [])
          .filter((model) => model.name)
          .map((model) => ({
            address: `core:${model.slot_label}/ckpt_name`,
            // The model-fix kind the server sends, not the slot word.
            kind: RECIPE_KIND[model.kind] ?? model.kind,
            filename: `${model.name}.safetensors`,
            provenance: "best",
          })),
        loras: [],
        values: shown.defaults,
        stages: { upscale: false, face_detailer: false },
      },
      ...shown,
    },
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

/** A default row's lock: pressed when the parameter is fixed. */
function lockOf(wrapper, name) {
  return rowNamed(wrapper, name).find("[data-testid='wfdef-lock']");
}

/** Whether the row called `name` is drawn under the Fixed group's label. */
function inFixedGroup(wrapper, name) {
  const group = wrapper.find("[data-testid='wftab-fixed-group']");
  if (!group.exists()) return false;
  const row = rowNamed(wrapper, name).element;
  return (
    group.element.contains(row) ||
    Boolean(
      group.element.compareDocumentPosition(row) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    )
  );
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

/**
 * `readModelSwap` answering `?replacing=` per slot kind, as the server does:
 * `replacements` already filtered to what goes with the checkpoint and what
 * the loader lists (#1596).
 */
function replacementsByKind(byKind, reasons = {}) {
  return (_key, { slotKind } = {}) =>
    Promise.resolve({
      replacements: byKind[slotKind] ?? [],
      replacements_reason: reasons[slotKind] ?? null,
    });
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  route.query = {};
  useSidebarStore().workflowInspectorOpen = true;
  getWorkflowCard.mockReset().mockResolvedValue(detail());
  listWorkflowCards.mockReset().mockResolvedValue({
    cards: [card()],
    one_offs: 0,
    hidden: 0,
  });
  patchWorkflowCard.mockReset().mockResolvedValue(detail());
  setWorkflowDefaults.mockReset().mockResolvedValue(detail());
  setWorkflowPins.mockReset().mockResolvedValue({ pins: [] });
  setWorkflowDefaultLora.mockReset().mockResolvedValue(detail());
  getLoraSummary.mockReset().mockResolvedValue(loraSummary());
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

  it("names a shelf loader's checkpoint by the file its id names", async () => {
    // A `checkpoint_id` recipe model's `filename` is a shelf row id (#1721),
    // which the slot recorded too; neither reads as the number.
    const named = card({
      models: [
        {
          name: "flux 2 klein 9b",
          title: "FLUX.2 Klein 9B",
          kind: "checkpoint",
          slot_label: "m1",
          filename: "75",
        },
      ],
    });
    const shown = detail({ card: named });
    shown.card.default_recipe.models = [
      {
        address: "core:m1/checkpoint_id",
        kind: "checkpoint",
        filename: "75",
        shelf_filename: "flux-2-klein-9b-fp8.safetensors",
        provenance: "best",
      },
    ];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY], [named]);
    const text = textOf(wrapper);
    expect(text).toContain("FLUX.2 Klein 9B");
    expect(text).not.toMatch(/\b75\b/);
  });

  it("never falls back to the id when the shelf row names no file", async () => {
    // `shelf_filename: null` on a shelf loader: the row is on the shelf but
    // names no single file. The slot's title still names it; without one the
    // row says so in words.
    for (const title of ["FLUX.2 Klein 9B", null]) {
      const named = card({
        models: [
          {
            name: null,
            title,
            kind: "checkpoint",
            slot_label: "m1",
            filename: "75",
          },
        ],
      });
      const shown = detail({ card: named });
      shown.card.default_recipe.models = [
        {
          address: "core:m1/checkpoint_id",
          kind: "checkpoint",
          filename: "75",
          shelf_filename: null,
          provenance: "best",
        },
      ];
      getWorkflowCard.mockResolvedValue(shown);
      const { wrapper } = await mountWith([KEY], [named]);
      const text = textOf(wrapper);
      expect(text).not.toMatch(/\b75\b/);
      expect(text).toContain(title ?? "Unnamed shelf model");
      wrapper.unmount();
    }
  });

  /** The panel for one card and a default recipe of one checkpoint *file*. */
  async function checkpointRowFor(models, file) {
    const named = card({ models });
    const shown = detail({ card: named });
    shown.card.default_recipe.models = [
      {
        address: "core:m1/ckpt_name",
        kind: "checkpoint",
        filename: file,
        provenance: "best",
      },
    ];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY], [named]);
    return textOf(wrapper);
  }

  it("does not lend one quant build's slot to another", async () => {
    // Both derive to `flux 2 klein 9b`; only the recorded file tells them apart.
    const text = await checkpointRowFor(
      [
        {
          name: "flux 2 klein 9b",
          title: "FLUX.2 Klein 9B",
          kind: "checkpoint",
          quant: "fp8_e4m3",
          slot_label: "m1",
          filename: "flux-2-klein-9b-fp8.safetensors",
        },
      ],
      "flux-2-klein-9b-bf16.safetensors",
    );
    expect(text).toContain("flux-2-klein-9b-bf16.safetensors");
    expect(text).not.toContain("FLUX.2 Klein 9B");
  });

  it("does not match a slot whose name only prefixes the file", async () => {
    const text = await checkpointRowFor(
      [
        {
          name: "flux",
          title: "Flux Prefix",
          kind: "checkpoint",
          slot_label: "m0",
          filename: "flux.safetensors",
        },
        {
          name: "flux 2 klein 9b",
          title: "FLUX.2 Klein 9B",
          kind: "checkpoint",
          slot_label: "m1",
          filename: "flux-2-klein-9b.safetensors",
        },
      ],
      "flux-2-klein-9b.safetensors",
    );
    expect(text).toContain("FLUX.2 Klein 9B");
    expect(text).not.toContain("Flux Prefix");
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
          quant: "q4_k_m",
          slot_label: "l1",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: quantised }));
    const { wrapper } = await mountWith([KEY], [quantised]);
    const values = wrapper.findAll(".wftab-value").map((el) => el.text());
    expect(values.slice(0, 2)).toEqual(["t5xxl · FP8 E4M3", "ae"]);
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

  it("offers the shelf's checkpoints and replaces the missing file with one", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5_fp8.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind({
        checkpoint: [
          { id: 7, filename: "realvisXL_v5_bf16.safetensors", display_name: "RealVis 5" },
        ],
      }),
    );
    const fixed = detail({
      card: named,
      model_fixes: [
        {
          slot_label: "n1/ckpt_name",
          was: "SDXL/realvisXL_v5_fp8.safetensors",
          now: "realvisXL_v5_bf16.safetensors",
          slot_kind: "checkpoint",
        },
      ],
    });
    setWorkflowModelFix.mockReset().mockResolvedValue(fixed);
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);

    const picker = wrapper.find('[data-testid="wftab-replace-model"] select');
    expect(picker.text()).toContain("RealVis 5");
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    await picker.setValue("realvisXL_v5_bf16.safetensors");
    await settle(wrapper);

    // The graph's own spelling of the missing file, folders and all: that is
    // what the server matches the stored graphs on.
    expect(setWorkflowModelFix).toHaveBeenCalledWith(KEY, {
      was: "SDXL/realvisXL_v5_fp8.safetensors",
      now: "realvisXL_v5_bf16.safetensors",
      slot_kind: "checkpoint",
    });
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
    const row = wrapper.find('[data-testid="wftab-fixed-model"]');
    expect(row.text()).toContain("realvisXL_v5_bf16.safetensors");
    // A checkpoint's fix is the Checkpoint row's, never the VAE row's.
    expect(wrapper.find('[data-testid="wftab-fixed-vae"]').exists()).toBe(false);
    // The original, a hover away: the provenance the flag is there for.
    expect(row.find("tooltip-stub").attributes("text")).toBe(
      "Replaced. This workflow originally used realvisXL_v5_fp8.safetensors",
    );

    setWorkflowModelFix.mockResolvedValue(detail({ card: named }));
    await row.find('[data-testid="wftab-undo-fix"]').trigger("click");
    await flush(wrapper);
    expect(setWorkflowModelFix).toHaveBeenLastCalledWith(KEY, {
      was: "SDXL/realvisXL_v5_fp8.safetensors",
      now: null,
      slot_kind: "checkpoint",
    });
  });

  it.each([
    ["none_same_base_model", "is known to have this checkpoint's base model"],
    ["none_go_with_it", "is known to work with this checkpoint"],
    ["none_loadable", "is one this loader can load"],
    ["unread", "Could not read what could replace it"],
    [null, "Nothing on your shelf can replace it."],
  ])("says why no checkpoint is offered (%s)", async (reason, text) => {
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5_fp8.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    readModelSwap
      .mockReset()
      .mockImplementation(replacementsByKind({}, { checkpoint: reason }));
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(wrapper.find('[data-testid="wftab-replace-model"]').exists()).toBe(
      false,
    );
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-checkpoint"]').text(),
    ).toContain(text);
  });

  it("says the replacement is missing only when it is the file missing", async () => {
    const fix = {
      slot_label: "n1/ckpt_name",
      was: "realvisXL_v5_fp8.safetensors",
      now: "realvisXL_v5_bf16.safetensors",
      slot_kind: "checkpoint",
    };
    getWorkflowCard.mockResolvedValue(
      detail({ card: named, model_fixes: [fix] }),
    );
    readModelSwap.mockReset().mockResolvedValue({ checkpoints: [] });

    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/refiner.safetensors"),
    );
    let { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    expect(wrapper.find('[data-testid="wftab-fix-missing"]').exists()).toBe(
      false,
    );
    wrapper.unmount();
    mounted.pop();

    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5_bf16.safetensors"),
    );
    ({ wrapper } = await mountWith([KEY], [named]));
    await settle(wrapper);
    expect(wrapper.find('[data-testid="wftab-fix-missing"]').text()).toContain(
      "Replaced by realvisXL_v5_bf16.safetensors, which is missing too.",
    );
  });

  it("offers no replacement for a file nobody can name", async () => {
    preflightWorkflowRun.mockResolvedValue(missingFile("(forgotten model)"));
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: [] }),
    );
    readModelSwap.mockReset().mockResolvedValue({
      checkpoints: [{ id: 7, filename: "x.safetensors" }],
    });
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(readModelSwap).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="wftab-replace-model"]').exists()).toBe(
      false,
    );
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

  it("says Checkpoints for two files named only by the graph", async () => {
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    getWorkflowCard.mockResolvedValue(
      detail({
        card: unnamed,
        graph_base_models: ["wan2.2_high.safetensors", "wan2.2_low.safetensors"],
      }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    const row = wrapper.find("[data-testid='wftab-row-checkpoint']");
    expect(row.find(".wftab-label").text()).toBe("Checkpoints");
    expect(textOf(wrapper)).toContain(
      "wan2.2_high.safetensors + wan2.2_low.safetensors",
    );
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
      detail({ card: { id: key } }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card(), card({ id: OTHER, name: "Card B" })],
    );
    store.selectedKeys = [OTHER];
    await settle(wrapper);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(preflightWorkflowRun).toHaveBeenCalledWith({
      workflow_id: OTHER,
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
      body.workflow_id === KEY
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
      detail({ card: { id: key, name: key === KEY ? "Card A" : "Card B" } }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ id: OTHER, name: "Card B" })],
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

describe("a VAE or text encoder that will not load (#1596)", () => {
  const withSupport = card({
    models: [
      { name: "realvisXL_v5", kind: "checkpoint", slot_label: "n1/ckpt_name" },
      { name: "sdxl_vae", kind: "vae", slot_label: "n2/vae_name" },
      { name: "t5xxl", kind: "clip", slot_label: "n3/clip_name" },
    ],
  });

  function missing(...models) {
    return {
      groups: [{ reasons: [{ code: "missing_models", models }] }],
    };
  }

  function row(wrapper, kind) {
    return wrapper.find(`[data-testid="wftab-row-${kind}"]`);
  }

  it("names the default recipe's text encoder on the CLIP row", async () => {
    // The recipe says `text_encoder` where the card's slot says `clip`: the
    // row and its shelf name have to join the two.
    preflightWorkflowRun.mockResolvedValue({ groups: [{ reasons: [] }] });
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    const { wrapper } = await mountWith([KEY], [withSupport]);
    expect(row(wrapper, "text_encoder").text()).toContain("t5xxl");
  });

  it("offers the shelf's VAEs and replaces the missing one, and only that row", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missing({ file: "SDXL/sdxl_vae_fp8.safetensors", folder: "vae" }),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind({
        checkpoint: [{ id: 1, filename: "other-ckpt.safetensors" }],
        vae: [
          { id: 7, filename: "sdxl_vae_bf16.safetensors", display_name: "SDXL VAE", via: "grouped" },
          { id: 9, filename: "sdxl_vae_alt.safetensors", via: "declared" },
          { id: 10, filename: "sdxl_vae_shelf.safetensors", loader: "PixlStashVAELoader" },
        ],
        text_encoder: [{ id: 8, filename: "t5xxl_bf16.safetensors" }],
      }),
    );
    const fix = {
      slot_label: "n2/vae_name",
      was: "SDXL/sdxl_vae_fp8.safetensors",
      now: "sdxl_vae_bf16.safetensors",
      slot_kind: "vae",
    };
    setWorkflowModelFix
      .mockReset()
      .mockResolvedValue(detail({ card: withSupport, model_fixes: [fix] }));
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);

    expect(textOf(wrapper)).toContain("VAE missing");
    // Not the checkpoint's picker, and not the text encoder row.
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
    expect(wrapper.find('[data-testid="wftab-replace-model"]').exists()).toBe(false);
    expect(row(wrapper, "text_encoder").text()).toContain("t5xxl");
    // Asked for THIS file in a VAE slot: the server filters by what goes with
    // the checkpoint and what the loader lists.
    expect(readModelSwap).toHaveBeenCalledWith(KEY, {
      replacing: "SDXL/sdxl_vae_fp8.safetensors",
      slotKind: "vae",
    });
    expect(readModelSwap).toHaveBeenCalledTimes(1);
    const picker = row(wrapper, "vae").find('[data-testid="wftab-replace-vae"] select');
    expect(picker.text()).toContain("SDXL VAE");
    // Only the file layout fits: said, not hidden.
    expect(picker.text()).toContain("sdxl_vae_alt.safetensors (untested)");
    // The loader cannot load it; a run swaps in ours, and the option says so.
    expect(picker.text()).toContain(
      "sdxl_vae_shelf.safetensors (through a PixlStash loader)",
    );
    expect(picker.text()).not.toContain("other-ckpt");
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    await picker.setValue("sdxl_vae_bf16.safetensors");
    await settle(wrapper);

    expect(setWorkflowModelFix).toHaveBeenCalledWith(KEY, {
      was: "SDXL/sdxl_vae_fp8.safetensors",
      now: "sdxl_vae_bf16.safetensors",
      slot_kind: "vae",
    });
    const fixed = row(wrapper, "vae").find('[data-testid="wftab-fixed-vae"]');
    expect(fixed.text()).toContain("sdxl_vae_bf16.safetensors");
    expect(fixed.find("tooltip-stub").attributes("text")).toBe(
      "Replaced. This workflow originally used sdxl_vae_fp8.safetensors",
    );
    // The Checkpoint row is untouched by a VAE fix.
    expect(wrapper.find('[data-testid="wftab-fixed-model"]').exists()).toBe(false);

    setWorkflowModelFix.mockResolvedValue(detail({ card: withSupport }));
    await fixed.find('[data-testid="wftab-undo-vae"]').trigger("click");
    await flush(wrapper);
    expect(setWorkflowModelFix).toHaveBeenLastCalledWith(KEY, {
      was: "SDXL/sdxl_vae_fp8.safetensors",
      now: null,
      slot_kind: "vae",
    });
  });

  it("shows a missing text encoder on its own row, and never a vision one", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missing(
        { file: "t5xxl_fp8.safetensors", folder: "text_encoders" },
        { file: "clip_vision_h.safetensors", folder: "clip_vision" },
      ),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind(
        { text_encoder: [{ id: 8, filename: "t5xxl_bf16.safetensors" }] },
      ),
    );
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);

    const encoders = row(wrapper, "text_encoder");
    expect(encoders.text()).toContain("Text encoder missing");
    expect(encoders.text()).toContain("t5xxl_fp8.safetensors");
    expect(textOf(wrapper)).not.toContain("clip_vision_h");
    expect(
      encoders.find('[data-testid="wftab-replace-text_encoder"] select').text(),
    ).toContain("t5xxl_bf16.safetensors");
    expect(row(wrapper, "vae").text()).toContain("sdxl_vae");
  });
});

describe("a missing replacement shared by two slots (#1596)", () => {
  it("undoes every original it replaced, by the replacement's name", async () => {
    const withVaes = card({
      models: [{ name: "sdxl_vae", kind: "vae", slot_label: "n2/vae_name" }],
    });
    const fixes = ["a", "b"].map((slot) => ({
      slot_label: `${slot}/vae_name`,
      was: `vae_${slot}_fp8.safetensors`,
      now: "vae_bf16.safetensors",
      slot_kind: "vae",
    }));
    preflightWorkflowRun.mockResolvedValue({
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file: "SDXL/vae_bf16.safetensors", folder: "vae" }],
            },
          ],
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(
      detail({ card: withVaes, model_fixes: fixes }),
    );
    readModelSwap.mockReset().mockImplementation(replacementsByKind({}));
    setWorkflowModelFix
      .mockReset()
      .mockResolvedValue(detail({ card: withVaes }));
    const { wrapper } = await mountWith([KEY], [withVaes]);
    await settle(wrapper);
    const undo = wrapper.find('[data-testid="wftab-undo-missing-vae"]');
    expect(undo.attributes("aria-label")).toBe(
      "Undo: load vae_a_fp8.safetensors and vae_b_fp8.safetensors again",
    );
    await undo.trigger("click");
    await flush(wrapper);
    expect(setWorkflowModelFix).toHaveBeenCalledWith(KEY, {
      was: "SDXL/vae_bf16.safetensors",
      now: null,
      slot_kind: "vae",
    });
  });
});

describe("no replacement to offer (#1596)", () => {
  it("says why instead of drawing an empty picker", async () => {
    const withVae = card({
      models: [
        { name: "realvisXL_v5", kind: "checkpoint", slot_label: "n1/ckpt_name" },
        { name: "sdxl_vae", kind: "vae", slot_label: "n2/vae_name" },
      ],
    });
    preflightWorkflowRun.mockResolvedValue({
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file: "sdxl_vae_fp8.safetensors", folder: "vae" }],
            },
          ],
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: withVae }));
    readModelSwap
      .mockReset()
      .mockImplementation(replacementsByKind({}, { vae: "none_loadable" }));
    const { wrapper } = await mountWith([KEY], [withVae]);
    await settle(wrapper);
    expect(wrapper.find('[data-testid="wftab-replace-vae"]').exists()).toBe(false);
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-vae"]').text(),
    ).toContain("not something this loader can load");
  });

  it("says a failed read rather than showing nothing", async () => {
    const withVae = card({
      models: [{ name: "sdxl_vae", kind: "vae", slot_label: "n2/vae_name" }],
    });
    preflightWorkflowRun.mockResolvedValue({
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file: "sdxl_vae_fp8.safetensors", folder: "vae" }],
            },
          ],
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: withVae }));
    readModelSwap.mockReset().mockRejectedValue(new Error("offline"));
    const { wrapper } = await mountWith([KEY], [withVae]);
    await settle(wrapper);
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-vae"]').text(),
    ).toContain("Could not read what could replace it just now.");
  });
});

describe("the default recipe (#1623)", () => {
  it("names the default recipe's checkpoint, not the base card's", async () => {
    const shown = detail();
    shown.card.default_recipe.models = [
      {
        address: "core:ckpt/ckpt_name",
        kind: "checkpoint",
        filename: "SDXL/juggernautXL_v9.safetensors",
        provenance: "best",
      },
    ];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    const text = textOf(wrapper);
    expect(text).toContain("juggernautXL_v9.safetensors");
    expect(text).not.toContain("realvisXL_v5");
  });

  it("names both checkpoints of a two-loader workflow, once each", async () => {
    // A Wan 2.2 high + low pair, the low file on two loaders: never one of
    // them, and never which loader is high.
    const shown = detail();
    shown.card.default_recipe.models = [
      ["core:a/unet_name", "wan2.2_high.safetensors"],
      ["core:b/unet_name", "wan2.2_low.safetensors"],
      ["core:c/unet_name", "wan2.2_low.safetensors"],
    ].map(([address, filename]) => ({ address, kind: "checkpoint", filename }));
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    const row = wrapper.find("[data-testid='wftab-row-checkpoint']");
    expect(row.find(".wftab-label").text()).toBe("Checkpoints");
    expect(row.find(".wftab-value").text()).toBe(
      "wan2.2_high.safetensors + wan2.2_low.safetensors",
    );
  });

  it("lists the default recipe's values as the defaults, not the card's", async () => {
    const shown = detail({ card: { defaults: [SAMPLER] } });
    shown.card.default_recipe.values = [STEPS];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    expect(rowNamed(wrapper, "steps").exists()).toBe(true);
    expect(wrapper.findAll(".wfdef").length).toBe(1);
  });
});

describe("the DEFAULT RECIPE section (#1653)", () => {
  // Shelf CONTENT digests: deliberately not the hex of the `asset:` refs
  // (BO / ADA hash the NAME), which is the join a real library needs.
  const BO_SHA = "b".repeat(64);
  const ADA_SHA = "a".repeat(64);

  /** The detail with `loras` in its default recipe and `recipe_values` on the card. */
  function withRecipe({ loras = [], stages, recipeValues, provenance } = {}) {
    const shown = detail({
      card: recipeValues ? { recipe_values: recipeValues } : {},
    });
    shown.card.default_recipe.loras = loras;
    if (stages) shown.card.default_recipe.stages = stages;
    if (provenance) {
      shown.card.default_recipe.models.forEach((model) => {
        model.provenance = provenance;
      });
    }
    return shown;
  }

  function loraRow(wrapper, name) {
    const row = wrapper
      .findAll("[data-testid='wftab-default-lora']")
      .find((entry) => entry.find(".wftab-chain-name").text() === name);
    if (!row) throw new Error(`no default LoRA row called ${name}`);
    return row;
  }

  it("draws Models, LoRAs and Parameters as panels, each verb in its own head", async () => {
    const { wrapper } = await mountWith([KEY]);
    const heads = wrapper.findAll(".wftab-panel > .wftab-sec-head");
    expect(heads.map((head) => head.find("[role='heading']").text())).toEqual([
      "Models",
      "LoRAs",
      "Parameters",
    ]);
    expect(heads[0].find("[data-testid='wftab-clone-onto-set']").exists()).toBe(true);
    expect(heads[1].find("[data-testid='wftab-edit-loras']").exists()).toBe(true);
    expect(textOf(wrapper)).not.toContain("In every picture");
  });

  it("draws its star ratings in the head, the 4★+ bars the defaults come from at full ink", async () => {
    const counts = [0, 2, 5, 8, 4];
    const { wrapper } = await mountWith([KEY], [card({ rating_counts: counts })]);
    const chart = wrapper.find(".wftab-head [data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).toBe(
      "Ratings: 1 star 0, 2 stars 2, 3 stars 5, 4 stars 8, 5 stars 4. " +
        "The defaults come from its pictures rated 4 stars or more.",
    );
    const bars = chart.findAll("rect");
    expect(bars.map((bar) => bar.classes()[0])).toEqual([
      "wfstars-lo",
      "wfstars-lo",
      "wfstars-lo",
      "wfstars-hi",
      "wfstars-hi",
    ]);
    // Scaled to the tallest; an empty rating keeps a 1px stub.
    expect(bars.map((bar) => bar.attributes("height"))).toEqual([
      "1",
      "4",
      "10",
      "16",
      "8",
    ]);
  });

  it("claims no source for a default recipe nobody sampled", async () => {
    const own = detail();
    own.card.default_recipe.sampled = 0;
    getWorkflowCard.mockResolvedValue(own);
    const { wrapper } = await mountWith([KEY], [card({ rating_counts: [0, 0, 1, 2, 0] })]);
    const chart = wrapper.find("[data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).toBe(
      "Ratings: 1 star 0, 2 stars 0, 3 stars 1, 4 stars 2, 5 stars 0.",
    );
    expect(chart.findAll("rect.wfstars-lo")).toHaveLength(0);
  });

  it("inks every bar when none is rated 4★ yet, and draws no chart when nothing is rated", async () => {
    const { wrapper } = await mountWith([KEY], [card({ rating_counts: [1, 3, 0, 0, 0] })]);
    const chart = wrapper.find("[data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).toContain(
      "None is rated 4 stars yet, so the defaults come from all its pictures.",
    );
    expect(chart.findAll("rect.wfstars-lo")).toHaveLength(0);

    const unrated = await mountWith([KEY], [card({ rating_counts: [0, 0, 0, 0, 0] })]);
    expect(unrated.wrapper.find("[data-testid='wftab-stars']").exists()).toBe(false);
    expect(textOf(unrated.wrapper)).not.toContain("Star ratings");
  });

  it("reads its default recipe before naming any model, never the card's", async () => {
    getWorkflowCard.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("Reading its default recipe…");
    expect(wrapper.find("[data-testid='wftab-row-checkpoint']").exists()).toBe(false);
  });

  it("offers Retry when the default recipe could not be read", async () => {
    getWorkflowCard.mockRejectedValueOnce(new Error("down"));
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("Could not read its default recipe just now.");
    await wrapper.find("[data-testid='wftab-recipe-retry']").trigger("click");
    await flush(wrapper);
    expect(getWorkflowCard).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[data-testid='wftab-row-checkpoint']").exists()).toBe(true);
  });

  it("counts a default LoRA not in every picture, and takes it out of the pile", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        // Named differently from the summary's file: the join is the digest.
        loras: [{ asset: BO.toUpperCase(), filename: "loras/bo_v2.safetensors", sha256: BO_SHA.toUpperCase(), strength: 0.8, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    const row = loraRow(wrapper, "Bo");
    expect(row.find(".wftab-coverage").text()).toBe("in 3 of 5");
    expect(row.find(".wftab-chain-strength").text()).toBe("0.80");
    // Once: a default row or the pile, never both.
    expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
    const pile = wrapper.find("[data-testid='wftab-pile']");
    expect(pile.text()).toContain("Ada");
    expect(pile.text()).not.toContain("+1");
    // The pile sits in the LoRAs panel, under the default rows.
    const pileRow = wrapper.find("[data-testid='wftab-loras'] [data-testid='wftab-changes']");
    expect(pileRow.find(".wftab-label").text()).toBe("Also used");
  });

  it("never joins by filename: a same-named LoRA with another digest stays in the pile", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ asset: `asset:${"f".repeat(64)}`, filename: "bo.safetensors", sha256: "f".repeat(64), strength: 1, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(true);
    expect(wrapper.find(".wftab-coverage").exists()).toBe(false);
  });

  it("drops the pile's No LoRA row once a default LoRA leaves the pile", async () => {
    const none = { asset: "", filename: null, name: null, on_shelf: true, pictures: 1, picture_ids: [9] };
    getLoraSummary.mockResolvedValue(loraSummary({ without: none }));
    getWorkflowCard.mockResolvedValue(
      withRecipe({ loras: [{ asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" }] }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findComponent({ name: "WorkflowLoraPile" }).props("summary").without).toBe(null);
  });

  it("keeps the No LoRA row while the pile is the server's whole varying", async () => {
    const none = { asset: "", filename: null, name: null, on_shelf: true, pictures: 1, picture_ids: [9] };
    getLoraSummary.mockResolvedValue(loraSummary({ without: none }));
    getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findComponent({ name: "WorkflowLoraPile" }).props("summary").without).toEqual(none);
  });

  it("matches a default LoRA with no digest to the pile by its file", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ filename: "loras/BO.safetensors", sha256: null, strength: 1, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
    expect(wrapper.find("[data-testid='wftab-pile']").text()).toContain("Ada");
  });

  describe("Add to default on a pile row (#1653)", () => {
    it("takes a default LoRA out of the pile by shelf file when its asset differs", async () => {
      // One file loaded under another name: the default names it by one
      // asset, the pile by another, and they are still the one LoRA.
      getWorkflowCard.mockResolvedValue(
        withRecipe({
          loras: [{ asset: `asset:${"9".repeat(64)}`, filename: "bo_old.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" }],
        }),
      );
      const { wrapper } = await mountWith([KEY]);
      expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
      expect(loraRow(wrapper, "Bo").find(".wftab-coverage").text()).toBe("in 3 of 5");
    });


    const boAdded = (strength = 0.8) =>
      withRecipe({
        loras: [{ asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength, provenance: "edited" }],
      });
    const addButton = (wrapper, asset) =>
      wrapper.find(`[data-testid='wftab-fan-row-${asset}'] [data-testid='wftab-add-default']`);

    it("moves the LoRA from the pile into the default recipe, and Undo takes it out", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      setWorkflowDefaultLora.mockResolvedValueOnce(boAdded());
      const { wrapper } = await mountWith([KEY]);
      await addButton(wrapper, BO).trigger("click");
      await flush(wrapper);
      expect(setWorkflowDefaultLora).toHaveBeenCalledWith(KEY, { asset: BO, include: true });
      expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
      expect(loraRow(wrapper, "Bo").text()).toContain("Yours");

      const notice = useNoticeStore().notices.at(-1);
      expect(notice.level).toBe("success");
      expect(notice.text).toContain("Your pictures stay where they are");
      getWorkflowCard.mockResolvedValue(boAdded());
      setWorkflowDefaultLora.mockResolvedValueOnce(withRecipe({ loras: [] }));
      await notice.action.handler();
      await flush(wrapper);
      expect(setWorkflowDefaultLora).toHaveBeenLastCalledWith(KEY, { sha256: BO_SHA, include: null });
      expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(true);
    });

    it("still confirms, with Undo, when the selection moved while it was out", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      let answer;
      setWorkflowDefaultLora.mockImplementationOnce(
        () => new Promise((resolve) => (answer = resolve)),
      );
      const { wrapper, store } = await mountWith([KEY]);
      await addButton(wrapper, BO).trigger("click");
      await flush(wrapper);
      store.selectedKeys = [];
      answer(boAdded());
      await flush(wrapper);
      const notice = useNoticeStore().notices.at(-1);
      expect(notice.level).toBe("success");
      expect(notice.action.label).toBe("Undo");
    });

    it("does not undo a LoRA whose strength was changed since", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      setWorkflowDefaultLora.mockResolvedValueOnce(boAdded());
      const { wrapper } = await mountWith([KEY]);
      await addButton(wrapper, BO).trigger("click");
      await flush(wrapper);
      getWorkflowCard.mockResolvedValue(boAdded(0.5));
      await useNoticeStore().notices.at(-1).action.handler();
      expect(setWorkflowDefaultLora).toHaveBeenCalledTimes(1);
      expect(useNoticeStore().notices.at(-1).text).toContain("Nothing to undo");
    });

    it("offers it only on a LoRA the shelf names as one file", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      const summary = loraSummary();
      // On the shelf, but under two files: the route could not choose.
      summary.varying[1].sha256 = null;
      getLoraSummary.mockResolvedValue(summary);
      const { wrapper } = await mountWith([KEY]);
      expect(addButton(wrapper, summary.varying[0].asset).exists()).toBe(true);
      expect(addButton(wrapper, summary.varying[1].asset).exists()).toBe(false);
    });
  });

  it("has no ALSO USED section when every LoRA that changes is a default", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [
          { asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" },
          { asset: ADA, filename: "ada.safetensors", sha256: ADA_SHA, strength: 1, provenance: "best" },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find("[data-testid='wftab-changes']").exists()).toBe(false);
  });

  it("marks an edited LoRA Yours, with no reset and no Make default", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [
          { asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "edited" },
          { asset: ADA, filename: "ada.safetensors", sha256: ADA_SHA, strength: 1, provenance: "best" },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(loraRow(wrapper, "Bo").find(".wftab-yours").text()).toBe("Yours");
    expect(loraRow(wrapper, "Ada").find(".wftab-yours").exists()).toBe(false);
    // LoRAs have no write yet (F-1): nothing on screen may pretend otherwise.
    expect(loraRow(wrapper, "Bo").find(".wfdef-reset").exists()).toBe(false);
    expect(textOf(wrapper)).not.toContain("Make default");
    expect(textOf(wrapper)).not.toContain("Take out of default");
  });

  it("says a stage is off, and why, and draws no Stages row without stages", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({ stages: { upscale: true, face_detailer: false } }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(
      wrapper.find("[data-testid='wftab-stages'] .wftab-value").text().replace(/\s+/g, " "),
    ).toBe("Upscale · Face detailer off");
    expect(textOf(wrapper)).toContain("Off: most of its pictures ran without it.");

    getWorkflowCard.mockResolvedValue(withRecipe({ stages: {} }));
    const { wrapper: none } = await mountWith([OTHER], [card({ id: OTHER })]);
    expect(none.find("[data-testid='wftab-stages']").exists()).toBe(false);
    expect(textOf(none)).not.toContain("Off: most of its pictures");
  });

  it("offers Show N on the checkpoint only below the workflow's count", async () => {
    const values = (pictures) => ({
      checkpoints: [
        { name: "SDXL/realvisXL_v5.safetensors.safetensors", pictures },
        { name: "SDXL/juggernautXL_v9.safetensors", pictures: 6 },
      ],
      loras: [],
    });
    getWorkflowCard.mockResolvedValue(withRecipe());
    const { wrapper } = await mountWith([KEY], [card({ recipe_values: values(178) })]);
    const show = wrapper.find("[data-testid='wftab-show-checkpoint']");
    expect(show.text()).toBe("Show 178");
    expect(show.attributes("aria-label")).toBe(
      "Show the 178 pictures in Cinematic portrait made with realvisXL_v5.safetensors",
    );
    await show.trigger("click");
    const filters = useFilterStore();
    expect(filters.workflowFilter).toEqual({
      id: KEY,
      name: "Cinematic portrait",
      opened: {
        kind: "model",
        value: "SDXL/realvisXL_v5.safetensors.safetensors",
        label: "realvisXL_v5.safetensors",
      },
    });
    expect(filters.comfyuiModelFilter).toEqual(["SDXL/realvisXL_v5.safetensors.safetensors"]);
    expect(push).toHaveBeenCalledWith("/");

    // In every picture: it would only repeat the head's link.
    const { wrapper: every } = await mountWith([KEY], [card({ recipe_values: values(184) })]);
    expect(every.find("[data-testid='wftab-show-checkpoint']").exists()).toBe(false);
  });

  it("lists no other checkpoint that is in every picture", async () => {
    getWorkflowCard.mockResolvedValue(withRecipe());
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          recipe_values: {
            // The default's file spelled so it matches no entry here.
            checkpoints: [{ name: "SDXL/juggernautXL_v9.safetensors", pictures: 184 }],
            loras: [],
          },
        }),
      ],
    );
    expect(wrapper.find("[data-testid='wftab-other-checkpoints']").exists()).toBe(false);
  });

  it("discloses the other checkpoints, each with its own Show N", async () => {
    getWorkflowCard.mockResolvedValue(withRecipe());
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          recipe_values: {
            checkpoints: [
              { name: "realvisXL_v5.safetensors.safetensors", pictures: 178 },
              { name: "SDXL/juggernautXL_v9.safetensors", pictures: 6 },
            ],
            loras: [],
          },
        }),
      ],
    );
    const toggle = wrapper.find("[data-testid='wftab-other-checkpoints']");
    expect(toggle.text()).toContain("+1 other");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find("#wftab-other-checkpoints").exists()).toBe(false);
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    const item = wrapper.find("#wftab-other-checkpoints li");
    expect(item.text().replace(/\s+/g, " ")).toBe("juggernautXL_v9 Show 6");
    await item.find("button").trigger("click");
    expect(useFilterStore().comfyuiModelFilter).toEqual(["SDXL/juggernautXL_v9.safetensors"]);
  });

  it("marks the Checkpoints row Yours when only the second file was set", async () => {
    const shown = withRecipe();
    shown.card.default_recipe.models = [
      ["core:a/unet_name", "wan2.2_high.safetensors", "best"],
      ["core:b/unet_name", "wan2.2_low.safetensors", "edited"],
    ].map(([address, filename, provenance]) => ({
      address,
      kind: "checkpoint",
      filename,
      provenance,
    }));
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    const row = wrapper.find("[data-testid='wftab-row-checkpoint']");
    expect(row.find(".wftab-yours").exists()).toBe(true);
  });

  it("lists neither of a two-loader pair as another checkpoint, and shows each", async () => {
    // A Wan 2.2 high + low pair: the low file is the workflow's own, not an
    // alternative its pictures used.
    const shown = withRecipe();
    shown.card.default_recipe.models = [
      ["core:a/unet_name", "wan2.2_high.safetensors"],
      ["core:b/unet_name", "wan2.2_low.safetensors"],
    ].map(([address, filename]) => ({ address, kind: "checkpoint", filename }));
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          recipe_values: {
            checkpoints: [
              { name: "wan/wan2.2_high.safetensors", pictures: 170 },
              { name: "wan/wan2.2_low.safetensors", pictures: 160 },
              { name: "wan/wan2.1.safetensors", pictures: 6 },
            ],
            loras: [],
          },
        }),
      ],
    );
    const toggle = wrapper.find("[data-testid='wftab-other-checkpoints']");
    expect(toggle.text()).toContain("+1 other");
    await toggle.trigger("click");
    expect(
      wrapper.findAll("#wftab-other-checkpoints li").map((li) => li.text().replace(/\s+/g, " ")),
    ).toEqual(["wan2.1 Show 6"]);
    const shows = wrapper.findAll("[data-testid='wftab-show-checkpoint']");
    expect(shows.map((button) => button.text())).toEqual(["Show 170", "Show 160"]);
    await shows[1].trigger("click");
    expect(useFilterStore().comfyuiModelFilter).toEqual(["wan/wan2.2_low.safetensors"]);
  });

  it("offers Show N on a default LoRA from recipe_values, matched by file", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ asset: BO, filename: "loras/bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith(
      [KEY],
      [card({ recipe_values: { checkpoints: [], loras: [{ name: "Loras\\BO.safetensors", pictures: 3 }] } })],
    );
    const show = loraRow(wrapper, "Bo").findAll("button").find((b) => b.text() === "Show 3");
    await show.trigger("click");
    const filters = useFilterStore();
    expect(filters.comfyuiLoraFilter).toEqual(["Loras\\BO.safetensors"]);
    expect(filters.workflowFilter.opened).toEqual({
      kind: "lora",
      value: "Loras\\BO.safetensors",
      label: "Bo",
    });
  });
});

describe("a default's provenance and reset", () => {
  it("marks only the edited value, as Yours, and no computed one (#1653)", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // Paired to the row, not counted across the panel: one "Yours"
    // SOMEWHERE is also true when the condition is inverted.
    expect(rowNamed(wrapper, "cfg").find(".wfdef-prov").text()).toBe("Yours");
    expect(rowNamed(wrapper, "steps").find(".wfdef-prov").exists()).toBe(false);
    expect(textOf(wrapper)).not.toContain("from your best pictures");
  });

  it("takes the defaults the Recipes tab wrote, so its own next PUT keeps them", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: { defaults: [STEPS, CFG] } }));
    const { wrapper } = await mountWith([KEY]);
    const tabButton = (name) =>
      wrapper.findAll("button").find((b) => b.text().trim() === name);
    await tabButton("Recipes").trigger("click");
    await flush(wrapper);
    // "Make these the defaults" made steps an edit.
    const written = detail({
      card: { defaults: [{ ...STEPS, value: "30", provenance: "edited" }, CFG] },
    });
    wrapper.findComponent({ name: "WorkflowRecipesTab" }).vm.$emit("defaults-changed", KEY, written);
    await tabButton("Workflow").trigger("click");
    await flush(wrapper);
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps", value: "30" },
    ]);
  });

  it("keeps the Recipes tab's answer over a detail read that lands after it", async () => {
    const { wrapper } = await mountWith([KEY]);
    // A re-read (Retry, a reselect) goes out and is slow ...
    let late;
    getWorkflowCard.mockImplementationOnce(() => new Promise((resolve) => (late = resolve)));
    const tabButton = (name) =>
      wrapper.findAll("button").find((b) => b.text().trim() === name);
    useWorkflowsStore().selectedKeys = [];
    await flush(wrapper);
    useWorkflowsStore().selectedKeys = [KEY];
    await flush(wrapper);
    await tabButton("Recipes").trigger("click");
    await flush(wrapper);
    // ... the Recipes tab writes, and its answer arrives first ...
    const written = detail({
      card: { defaults: [{ ...STEPS, value: "30", provenance: "edited" }, CFG] },
    });
    wrapper.findComponent({ name: "WorkflowRecipesTab" }).vm.$emit("defaults-changed", KEY, written);
    // ... then the stale read lands.
    late(detail({ card: { defaults: [STEPS, CFG] } }));
    await flush(wrapper);
    await tabButton("Workflow").trigger("click");
    await flush(wrapper);
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps", value: "30" },
    ]);
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

  it("sets the design's numbers each run and fixes the rest when the card has no choice", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // WHICH group each row is in. Counting pressed locks is equally true
    // when the two groups are swapped, and that swap is the whole of what
    // the lock does.
    expect(inFixedGroup(wrapper, "steps")).toBe(false);
    expect(inFixedGroup(wrapper, "sampler_name")).toBe(true);
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("true");
    expect(lockOf(wrapper, "steps").attributes("aria-pressed")).toBe("false");
    expect(textOf(wrapper)).toContain("Set each run");
    expect(textOf(wrapper)).toContain("Fixed · 1");
    expect(textOf(wrapper)).toContain("Fixed values are used by every run.");
    expect(textOf(wrapper)).not.toContain("parameters");
  });

  it("keeps an empty pin list apart from no choice at all", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS] }, pins: [] }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findAll('.wfdef [aria-pressed="false"]')).toHaveLength(0);
    // Nothing set each run means every row is fixed, and the panel must not
    // simply be empty.
    expect(inFixedGroup(wrapper, "steps")).toBe(true);
    expect(textOf(wrapper)).not.toContain("Set each run");
  });

  it("says nothing about fixed values when nothing is fixed", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: { defaults: [STEPS] } }));
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find("[data-testid='wftab-fixed-group']").exists()).toBe(false);
    expect(textOf(wrapper)).not.toContain("Fixed values are used by every run.");
  });

  it("folds more than three fixed rows behind their count", async () => {
    const fixed = ["sampler_name", "scheduler", "denoise", "seed"].map((name) => ({
      ...SAMPLER,
      label: name,
      input_name: name,
    }));
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, ...fixed] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    const group = wrapper.find("details[data-testid='wftab-fixed-group']");
    expect(group.find("summary").text().replace(/\s+/g, " ")).toBe("Fixed · 4");
    expect(rowNamed(wrapper, "seed").element.closest("details")).toBe(group.element);
    expect(rowNamed(wrapper, "steps").element.closest("details")).toBeNull();
  });

  it("keeps a stored pin whose parameter is not among today's defaults", async () => {
    const gone = { slot_label: "slot-b", input_name: "denoise" };
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] }, pins: [gone] }),
    );
    const { wrapper } = await mountWith([KEY]);
    await lockOf(wrapper, "sampler_name").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [
      gone,
      { slot_label: "slot-a", input_name: "sampler_name" },
    ]);
  });

  it("keeps a toggled row where it is until the workflow is opened again", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    setWorkflowPins.mockImplementation(async (_key, pins) => ({ pins }));
    const { wrapper } = await mountWith([KEY]);
    await lockOf(wrapper, "sampler_name").trigger("click");
    await flush(wrapper);
    // The lock says it is set each run now, but the row did not jump out
    // from under the pointer.
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
    expect(inFixedGroup(wrapper, "sampler_name")).toBe(true);
  });

  it("takes the pins the Run popup wrote, so the next lock does not undo them", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    const freed = [
      { slot_label: "slot-a", input_name: "steps" },
      { slot_label: "slot-a", input_name: "sampler_name" },
    ];
    useRunDialogStore().pinsWritten = { workflowId: KEY, pins: freed };
    await flush(wrapper);
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
    await lockOf(wrapper, "steps").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [freed[1]]);
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

describe("an editor file ComfyUI has not converted (#1530)", () => {
  const editorFile = { imported: true, variant_count: 0 };

  it("says its parameters are not available yet, and how to convert it", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: editorFile }));
    const { wrapper } = await mountWith([KEY], [card(editorFile)]);
    expect(wrapper.find('[data-testid="wftab-editor-only"]').exists()).toBe(true);
    expect(textOf(wrapper)).toContain("Convert for PixlStash");
  });

  it("says nothing of converting a card its pictures made", async () => {
    const pictureOnly = { imported: false, variant_count: 0 };
    getWorkflowCard.mockResolvedValue(detail({ card: pictureOnly }));
    const { wrapper } = await mountWith([KEY], [card(pictureOnly)]);
    expect(wrapper.find('[data-testid="wftab-editor-only"]').exists()).toBe(false);
  });

  it("says nothing of converting once it has a recipe", async () => {
    const converted = { imported: true, variant_count: 1 };
    getWorkflowCard.mockResolvedValue(detail({ card: converted }));
    const { wrapper } = await mountWith([KEY], [card(converted)]);
    expect(wrapper.find('[data-testid="wftab-editor-only"]').exists()).toBe(false);
  });
});

describe("the LoRA pile", () => {
  it("piles what changes, with the cover's LoRA on top", async () => {
    const { wrapper } = await mountWith([KEY]);
    // The cover's LoRA is on top, though Bo has more pictures.
    const pile = wrapper.find("[data-testid='wftab-pile']");
    expect(pile.text()).toContain("Ada");
    expect(pile.text()).toContain("+1");
    expect(textOf(wrapper)).toContain("On top: the cover picture's LoRA.");
    expect(getLoraSummary).toHaveBeenCalledWith(KEY, { cover: undefined });
  });

  it("shows no pile when nothing changes between pictures", async () => {
    getLoraSummary.mockResolvedValue(loraSummary({ varying: [] }));
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find("[data-testid='wftab-changes']").exists()).toBe(false);
  });

  it("offers no Promote: the pile only shows (#1623)", async () => {
    getLoraSummary.mockResolvedValue(
      loraSummary({
        without: { asset: "", pictures: 2, picture_ids: [41] },
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    const row = wrapper.find("[data-testid='wftab-fan-row-none']");
    expect(row.text()).toContain("No LoRA");
    const fan = wrapper.find("[data-testid='wftab-fan']");
    expect(fan.text()).not.toContain("Promote");
    expect(fan.text()).toContain("Show 3");
  });

  it("shows the workflow's pictures of one LoRA, as a removable chip", async () => {
    const { wrapper } = await mountWith([KEY]);
    await wrapper
      .find(`[data-testid='wftab-fan-row-${BO}']`)
      .findAll("button")
      .find((button) => button.text() === "Show 3")
      .trigger("click");
    // Two chips' worth (F-4): the workflow, and the LoRA narrowing it.
    expect(useFilterStore().workflowFilter).toEqual({
      id: KEY,
      lora: BO,
      name: "Cinematic portrait",
      loraName: "Bo",
    });
    expect(push).toHaveBeenCalledWith("/");
  });
});

describe("the Recipes tab (v1.12 F6)", () => {
  // The other half of the lightbox banner's link (#1480): `?workflow=`
  // selects the card, and `?tab=recipes` opens the rail on its recipes. The
  // `?workflow=` watcher runs after the `?tab=` one and used to put the tab
  // back on Workflow.
  it("lands on the recipes, with the rail open, from ?tab=recipes", async () => {
    route.query = { workflow: KEY, tab: "recipes" };
    const sidebar = useSidebarStore();
    // Shut, which is the state that made the link a dead end of its own: the
    // card would be selected behind a rail nobody opened.
    sidebar.workflowInspectorOpen = false;
    const { wrapper } = await mountWith([KEY]);

    expect(sidebar.workflowInspectorOpen).toBe(true);
    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      true,
    );
  });

  it("leaves the rail alone without that query", async () => {
    // The control for the check above: `tab` defaults to Workflow, so a
    // watcher that fired unconditionally would only show up in the RAIL being
    // forced open on a screen the reader had shut it on.
    const sidebar = useSidebarStore();
    sidebar.workflowInspectorOpen = false;
    const { wrapper } = await mountWith([KEY]);
    expect(sidebar.workflowInspectorOpen).toBe(false);
    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      false,
    );
  });

  it("shows the workflow's recipes, and the footer belongs to Workflow", async () => {
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

  it("answers for a selection of several, as the union of their recipes", async () => {
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
    expect(tab.props("workflowIds")).toEqual([KEY, OTHER]);
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
      [card(), card({ id: OTHER })],
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
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));

    await run.trigger("click");
    await flush(wrapper);

    expect(useRunDialogStore().source).toMatchObject({
      kind: "card",
      workflowId: KEY,
      // No picture behind it, so the popup shows the card's cover and an empty
      // prompt rather than prefilling from a recipe it does not have.
      emptyPrompt: true,
    });
  });

  it("does not start a run when Run… is pressed", async () => {
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
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

  it("says how many are selected and offers no verbs of its own", async () => {
    // **The rail counts; the grid's selection pill acts** (#1455). These two
    // buttons were here first, and once the pill offered the same two they
    // disagreed within a week: this Hide was hardcoded where the pill's is a
    // toggle that reads Unhide on an all-hidden selection. Two live controls
    // on one screen, one of them wrong about what it was about to do.
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
    );
    const text = textOf(wrapper);
    expect(text).toContain("2 workflows selected");
    // The reader is told where the verbs went rather than left to find them.
    expect(text).toContain("bar at the bottom of the grid");
    const labels = wrapper.findAll("button").map((b) => b.text());
    expect(labels).not.toContain("Merge");
    expect(labels).not.toContain("Hide");
  });
});

describe("Open in ComfyUI", () => {
  // A real workflow id, `auto:` + core hash, which the link percent-encodes:
  // the desktop shell's guard refuses the bare 64-hex key the ids replaced.
  const ID = `auto:${"a".repeat(64)}`;
  const ID_PARAM = `auto%3A${"a".repeat(64)}`;

  const openButton = (wrapper) => {
    const found = wrapper.find('[data-testid="wftab-open-comfyui"]');
    return found.exists() ? found : undefined;
  };

  let open;
  beforeEach(() => {
    open = vi.spyOn(window, "open").mockImplementation(() => null);
    getPixlstashNode.mockReset();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
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
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(open).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${ID_PARAM}`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("sends a listen-everywhere address to the machine the page came from", async () => {
    configure("http://0.0.0.0:8188/");
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(open.mock.calls[0][0]).toBe(
      `http://${window.location.hostname}:8188/?pixlstash_workflow=${ID_PARAM}`,
    );
  });

  it("is not offered without a ComfyUI address", async () => {
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
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
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    expect(openButton(wrapper).attributes("aria-label")).toBe(
      "Open a copy in ComfyUI",
    );
  });

  it("goes through the desktop shell's bridge, which window.open cannot", async () => {
    configure();
    const openComfyui = vi.fn().mockResolvedValue(true);
    window.pixlstashDesktop = { openComfyui };
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(openComfyui).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${ID_PARAM}`,
    );
    expect(open).not.toHaveBeenCalled();
  });

  it("says so when the desktop shell refuses the link", async () => {
    configure();
    window.pixlstashDesktop = { openComfyui: vi.fn().mockResolvedValue(false) };
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");
    await flush(wrapper);

    expect(JSON.stringify(useNoticeStore().$state)).toContain(
      "Could not open ComfyUI",
    );
  });

  it("is not offered on an older desktop shell with no bridge", async () => {
    configure();
    window.pixlstashDesktop = {};
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    expect(openButton(wrapper)).toBeUndefined();
  });

  it("stays on screen and refuses, with the reason, when several are selected", async () => {
    configure();
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
    );
    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    const described = button.attributes("aria-describedby");
    expect(wrapper.find(`#${described}`).text()).toBe(
      "Open one workflow at a time",
    );
    await button.trigger("click");
    expect(open).not.toHaveBeenCalled();
  });

  it("refuses, with the reason, when ComfyUI lacks the PixlStash node", async () => {
    configure();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    await flush(wrapper);

    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    const described = button.attributes("aria-describedby");
    const reason = wrapper.find(`#${described}`);
    expect(reason.text()).toContain("needs the ComfyUI-PixlStash node");
    const link = reason.find("a");
    expect(link.attributes("href")).toBe(
      "https://github.com/Pikselkroken/ComfyUI-PixlStash",
    );
    expect(link.attributes("target")).toBe("_blank");
    await button.trigger("click");
    expect(open).not.toHaveBeenCalled();
  });

  it("still opens when ComfyUI cannot be asked about the node", async () => {
    configure();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: null });
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    await flush(wrapper);

    await openButton(wrapper).trigger("click");

    expect(open).toHaveBeenCalled();
  });

  it("refuses an address that is not a web address", async () => {
    configure("javascript:alert(1)");
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

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
          resolve(detail({ card: { id: KEY, defaults: [STEPS] } }));
      }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ id: OTHER, name: "Card B" })],
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
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
  });

  it("drops a whole-set write queued behind another when the selection moves", async () => {
    // The pins PUT is whole-set, built from `defaults` when it RUNS, which is
    // after the notes save it waited behind. By then those are card B's, and
    // sending them to A would replace A's own choice with B's.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { id: key, defaults: key === KEY ? [CFG] : [WIDTH] } }),
    );
    let settle;
    patchWorkflowCard.mockReturnValue(
      new Promise((resolve) => {
        settle = () => resolve(detail({ card: { defaults: [CFG] } }));
      }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ id: OTHER, name: "Card B" })],
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
  // Half of the chip's round trip: this screen is the only one that knows a
  // card's key, and `useGridFetch.test.js` holds the half that spends it.
  it("opens the library on the card's own pictures, as a removable chip", async () => {
    const { wrapper } = await mountWith([KEY]);
    const link = wrapper.find('[data-testid="wftab-show-pictures"]');
    expect(link.text()).toBe("184 pictures");
    expect(link.attributes("aria-label")).toBe("Show all 184 pictures");

    await link.trigger("click");

    expect(useFilterStore().workflowFilter).toEqual({
      id: KEY,
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
      id: KEY,
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
          props: ["open", "workflowId", "cardName", "pictureCount", "dropLora"],
          template: "<div class='eld-stub' />",
        },
      },
    },
  };

  // No picture unless a test says so: nothing sampled, so the chain a run
  // loads is the whole list.
  beforeEach(() => {
    getLoraSummary.mockResolvedValue(loraSummary({ pictures: 0, varying: [] }));
    const unsampled = detail();
    unsampled.card.default_recipe.sampled = 0;
    getWorkflowCard.mockResolvedValue(unsampled);
  });

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

  it("lists a picture-less workflow's loaders in apply order, with the shelf count", async () => {
    // Nothing to count, so nothing is shared and nothing changes: the rows
    // are the workflow's own chain.
    const { wrapper } = await mountChain();
    expect(getLoraChain).toHaveBeenCalledWith(KEY);
    const rows = wrapper.findAll("[data-testid='wftab-default-lora']");
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
      wrapper.find("[data-testid='wftab-shared-note']").text().replace(/\s+/g, " "),
    ).toBe("In the order the chain applies them. 3 of 4 are on your model shelf.");
    // The Workflow | Recipe switches are gone.
    expect(wrapper.findComponent({ name: "Segmented" }).exists()).toBe(false);
  });

  it("puts Edit LoRAs… in the head of the panel holding the LoRAs, apart from the clone", async () => {
    // Both verbs in one head overflowed a narrow inspector sideways.
    const { wrapper } = await mountChain();
    const models = wrapper.find("[data-testid='wftab-default-recipe'] .wftab-sec-head");
    expect(models.find("[data-testid='wftab-clone-onto-set']").exists()).toBe(true);
    expect(models.find("[data-testid='wftab-edit-loras']").exists()).toBe(false);
    const loras = wrapper.find("[data-testid='wftab-loras']");
    expect(loras.find(".wftab-sec-head [data-testid='wftab-edit-loras']").exists()).toBe(true);
    const rows = wrapper.findAll("[data-testid='wftab-default-lora']");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row) => loras.element.contains(row.element))).toBe(true);
  });

  it("gives a chain row the file its name's tooltip carries", async () => {
    const { wrapper } = await mountChain();
    const names = wrapper.findAll(".wftab-chain-name");
    expect(names.length).toBeGreaterThan(0);
    expect(names[0].find("tooltip-stub").attributes("text")).toBe(
      "lightning-8step.safetensors",
    );
  });

  it("lists the default recipe's LoRAs in the chain's order, joined by digest", async () => {
    // Given out of order. The chain says the order, by shelf DIGEST; the
    // summary, joined by ASSET reference (another hash), says what to call each and whether it is on the shelf; the strength is
    // the default recipe's own.
    const sampled = detail();
    sampled.card.default_recipe.loras = [
      { asset: "asset:n9", filename: "hairstyle-v3.safetensors", sha256: "S9", strength: 0.6, provenance: "best" },
      { asset: "asset:n3", filename: "film-grain-35mm.safetensors", sha256: "s3", strength: 0.5, provenance: "best" },
      { asset: "asset:n1", filename: "renamed.safetensors", sha256: "s1", strength: 1, provenance: "best" },
    ];
    getWorkflowCard.mockResolvedValue(sampled);
    const use = (sha, name, onShelf = true) => ({
      asset: `asset:${sha}`,
      filename: `${name}.safetensors`,
      name,
      on_shelf: onShelf,
      pictures: 5,
    });
    getLoraSummary.mockResolvedValue(
      loraSummary({
        shared: [use("N9", "Hairstyle v3", false), use("n1", "Lightning"), use("n3", "Film grain")],
        varying: [],
      }),
    );
    const { wrapper } = await mountChain();
    const rows = wrapper.findAll("[data-testid='wftab-default-lora']");
    expect(
      rows.map((row) => [
        row.find(".wftab-chain-name").text(),
        row.find(".wftab-chain-strength").text(),
      ]),
    ).toEqual([
      ["Lightning", "1.00"],
      ["Film grain", "0.50"],
      ["Hairstyle v3", "0.60"],
    ]);
    // In every picture, so no count on any of them.
    expect(wrapper.find(".wftab-coverage").exists()).toBe(false);
    expect(
      wrapper.find("[data-testid='wftab-shared-note']").text().replace(/\s+/g, " "),
    ).toBe("Hairstyle v3 is not on your shelf.");
  });

  it("lists a forked chain's lane loaders after its trunk", async () => {
    const [first, second] = loraChain().loaders;
    getLoraChain.mockResolvedValue(
      loraChain({
        source: null,
        loaders: [],
        lanes: [
          { sampler: { node_id: "3" }, loaders: [first] },
          { sampler: { node_id: "15" }, loaders: [second] },
        ],
      }),
    );
    const { wrapper } = await mountChain();
    expect(
      wrapper.findAll(".wftab-chain-name").map((name) => name.text()),
    ).toEqual(["lightning-8step", "neon-rain-v2"]);
    expect(textOf(wrapper)).not.toContain("No LoRA loader");
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
    expect(dialog.props("workflowId")).toBe(KEY);
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
    route.query = { workflow: OTHER, edit: "loras", drop_lora: "hairstyle-v3.safetensors" };
    const sidebar = useSidebarStore();
    sidebar.workflowInspectorOpen = false;
    const { wrapper, store } = await mountChain([]);

    expect(store.selectedKeys).toEqual([OTHER]);
    expect(sidebar.workflowInspectorOpen).toBe(true);
    const dialog = wrapper.findComponent({ name: "EditLorasDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("workflowId")).toBe(OTHER);
    expect(dialog.props("dropLora")).toBe("hairstyle-v3.safetensors");
    // One-shot: taken off the URL so a reload does not reopen it.
    expect(replace).toHaveBeenCalledWith({ query: { workflow: OTHER } });
  });
});
