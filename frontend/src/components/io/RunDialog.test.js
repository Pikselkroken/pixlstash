// The Run popup (v1.12 F5): the three things about it that can silently
// invert, and one that can silently leak.
//
// * The ↺ chip. It carries the value the field STARTED at, lives in the label
//   row, and puts that value back. A chip that carries the current value, or
//   that sits beside the control, is the same pixels and the wrong thing:
//   the first resets to nothing, the second pushes the four-column grid out of
//   alignment the moment one field is edited.
// * Switching to another stack member. The edits are kept, and the ones the
//   new card has no address for are NAMED. Dropping them silently is what
//   makes a run quietly use 8 steps when the form said 16.
// * The body's source. `POST /workflows/run` takes exactly one of
//   picture_ids / saved_recipe_id / workflow_key, and a card chosen over
//   pictures is `target`, not a second source. Getting this wrong is a 400 on
//   every run from the grid.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getWorkflowCard = vi.fn();
const listWorkflowCards = vi.fn();
const preflightWorkflowRun = vi.fn();
const runWorkflowCard = vi.fn();
const getPictureRecipe = vi.fn();

vi.mock("../../api/workflows", () => ({
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  runWorkflowCard: (...args) => runWorkflowCard(...args),
  workflowCoverUrl: (cover) => `/api/v1${cover}`,
}));
vi.mock("../../api/comfyui", () => ({
  getPictureRecipe: (...args) => getPictureRecipe(...args),
}));
const listAdapters = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));
vi.mock("../../api/recipes", () => ({ createSavedRecipe: vi.fn() }));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import RunDialog from "./RunDialog.vue";

const KEY = "a".repeat(64);
const OTHER = "b".repeat(64);

/** A parameter as `GET /workflows/cards/{key}` serves it. */
function def(label, inputName, value, slotLabel = "KSampler") {
  return {
    label,
    slot_label: slotLabel,
    input_name: inputName,
    value,
    provenance: "best",
  };
}

function card(overrides = {}) {
  return {
    key: KEY,
    name: "Cinematic portrait",
    covers: [],
    models: [],
    loras: [],
    member_keys: [],
    defaults: [
      def("Steps", "steps", 8),
      def("CFG", "cfg", 2.0),
      def("Width", "width", 832, "EmptyLatentImage"),
      def("Height", "height", 1216, "EmptyLatentImage"),
      def("Checkpoint", "ckpt_name", "realvisXL_v5.safetensors", "CheckpointLoader"),
      def("Denoise", "denoise", 1.0),
    ],
    ...overrides,
  };
}

/** One addressed value out of a submitted body, by widget name. */
function sentValue(body, inputName) {
  return (body.values || []).find((v) => v.input_name === inputName);
}

const AppDialogStub = {
  name: "AppDialog",
  template: "<div><slot /><footer><slot name='footer' /></footer></div>",
};

const globalOpts = {
  global: {
    stubs: {
      AppDialog: AppDialogStub,
      AppTextarea: true,
      AppSelect: true,
      AppInput: true,
      AppButton: { template: "<button><slot /></button>" },
      AppBarButton: true,
      RunReasonNotice: true,
      "v-icon": true,
    },
  },
};

async function mountRun(source = { kind: "picture", pictureIds: [42] }) {
  const wrapper = mount(RunDialog, {
    props: { open: true, source, context: { client_id: "test-client" } },
    ...globalOpts,
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  getWorkflowCard.mockImplementation(async (key) =>
    key === OTHER
      ? {
          card: card({
            key: OTHER,
            name: "Cinematic portrait · detailer",
            // No `denoise`, so an edit made on the first card has no address
            // here and must be reported as having fallen back.
            defaults: [def("Steps", "steps", 20), def("CFG", "cfg", 3.5)],
          }),
        }
      : { card: card() },
  );
  listWorkflowCards.mockResolvedValue({ cards: [] });
  preflightWorkflowRun.mockResolvedValue({ ok: true, runs: 1, groups: [] });
  runWorkflowCard.mockResolvedValue({
    status: "success",
    prompts: [{ prompt_id: "p1" }],
  });
  listAdapters.mockResolvedValue([
    { sha256: "s".repeat(64), filename: "mira_v2.safetensors", display_name: "mira_v2" },
    { sha256: "t".repeat(64), filename: "loras/film-grain-35mm.safetensors" },
  ]);
  getPictureRecipe.mockResolvedValue({
    available: true,
    workflow_key: KEY,
    positive_prompt: "a rainy tram platform",
    negative_prompt: null,
    seed_text: "418220931",
    settings: { steps: 12 },
    lora_slots: [],
  });
});

describe("the ↺ chip", () => {
  it("is absent until a field is edited", async () => {
    const wrapper = await mountRun();
    expect(wrapper.findAllComponents({ name: "RunResetChip" })).toHaveLength(0);
  });

  it("carries the value the field started at, not the one now in it", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);
    await wrapper.vm.$nextTick();

    const chips = wrapper.findAllComponents({ name: "RunResetChip" });
    expect(chips).toHaveLength(1);
    // 12 is the PICTURE's own value, which is what the popup opened showing -
    // not the card's 8, and not the 16 just typed.
    expect(chips[0].props("value")).toBe(12);
    expect(chips[0].props("label")).toBe("Steps");
  });

  it("sits in the label row, so the field itself does not move", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);
    await wrapper.vm.$nextTick();
    const chip = wrapper.findComponent({ name: "RunResetChip" });
    expect(chip.element.closest(".rund-l")).not.toBe(null);
  });

  it("puts the original back and goes away", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);
    await wrapper.vm.$nextTick();

    await wrapper.findComponent({ name: "RunResetChip" }).trigger("click");
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.currentValue(steps)).toBe(12);
    expect(wrapper.findAllComponents({ name: "RunResetChip" })).toHaveLength(0);
  });

  it("goes away when the field is typed back to its original by hand", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);
    wrapper.vm.setValue(steps, 12);
    await wrapper.vm.$nextTick();
    expect(wrapper.findAllComponents({ name: "RunResetChip" })).toHaveLength(0);
  });
});

describe("switching to another stack member", () => {
  it("keeps the edits the new workflow has an address for", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);

    wrapper.vm.workflowKey = OTHER;
    await flushPromises();

    const kept = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(kept)).toBe(16);
  });

  it("names the fields that fell back to the new workflow's own values", async () => {
    const wrapper = await mountRun();
    const denoise = wrapper.vm.restFields.find(
      (f) => f.input_name === "denoise",
    );
    wrapper.vm.setValue(denoise, 0.6);

    wrapper.vm.workflowKey = OTHER;
    await flushPromises();

    expect(wrapper.vm.fellBack).toEqual(["Denoise"]);
    expect(wrapper.text()).toContain("Denoise went back to this workflow's own");

    // And it is really GONE, not merely announced: a stale address left in
    // `edits` keeps a ↺ chip on a field that no longer does anything.
    expect(Object.keys(wrapper.vm.edits)).toEqual([]);
    await wrapper.vm.submit();
    await flushPromises();
    // The body carries the new card's own parameters, and nothing addressed to
    // a slot only the old one had.
    const sent = runWorkflowCard.mock.calls[0][0].values;
    expect(sent.some((v) => v.input_name === "denoise")).toBe(false);
  });

  it("stops reading the picture's own settings once it is another card", async () => {
    // The picture was made by THIS card, so its steps=12 is what the form
    // opens showing. A stack member is a different graph: 12 says nothing
    // about it, and carrying it over would make that member's own 20 look
    // like an edit somebody made.
    const wrapper = await mountRun();
    const before = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(before)).toBe(12);

    wrapper.vm.workflowKey = OTHER;
    await flushPromises();

    const after = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(after)).toBe(20);
  });

  it("says nothing when every edit survived", async () => {
    const wrapper = await mountRun();
    const cfg = wrapper.vm.scalarFields.find((f) => f.input_name === "cfg");
    wrapper.vm.setValue(cfg, 4);

    wrapper.vm.workflowKey = OTHER;
    await flushPromises();

    expect(wrapper.vm.fellBack).toEqual([]);
  });
});

describe("a refusal the popup offers to fix", () => {
  it("re-asks after dropping the LoRA, so the Run button can come back", async () => {
    // Without the re-ask the reason stays in `reasons`, the blocker stays set
    // and the button stays disabled for ever: a fix that makes the refusal
    // permanent. The server decides `wants_lora` from `body.loras`.
    preflightWorkflowRun
      .mockResolvedValueOnce({
        ok: false,
        runs: 0,
        groups: [{ workflow_key: KEY, reasons: [{ code: "no_lora_loader" }] }],
      })
      .mockResolvedValue({ ok: true, runs: 1, groups: [] });

    const wrapper = await mountRun();
    expect(wrapper.vm.canRun).toBe(false);

    await wrapper.vm.dropLoras();
    await flushPromises();

    expect(preflightWorkflowRun).toHaveBeenCalledTimes(2);
    expect(preflightWorkflowRun.mock.calls[1][0].loras).toEqual([]);
    expect(wrapper.vm.reasons).toEqual([]);
    expect(wrapper.vm.canRun).toBe(true);
  });

  it("shows a pre-flight 4xx instead of waiting for the run to say it", async () => {
    // The route answers 400/404/422 on the dry run exactly as on the run, "so
    // the two never disagree". Swallowing it means the owner finds out by
    // pressing Run.
    preflightWorkflowRun.mockRejectedValue({
      response: { status: 400, data: { detail: "Name exactly one source." } },
    });
    const wrapper = await mountRun();
    expect(wrapper.vm.canRun).toBe(false);
    expect(wrapper.vm.runBlocker).toContain("Name exactly one source.");
  });

  it("keeps the button live when the pre-flight could not be ASKED", async () => {
    // A network failure or a 5xx is the question not being put, which is not a
    // refusal: over-blocking is its own regression.
    preflightWorkflowRun.mockRejectedValue(new Error("network down"));
    const wrapper = await mountRun();
    expect(wrapper.vm.canRun).toBe(true);
  });
});

describe("a number field that has been emptied", () => {
  it("is not the number zero", async () => {
    // `AppInput` is `type="number"`, so an emptied box hands back `""`, and
    // `Number("")` is 0. Recorded as an edit that is a silent instruction to
    // generate at zero steps.
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");

    wrapper.vm.setValue(steps, wrapper.vm.coerce(steps, ""));
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.currentValue(steps)).toBe(12);
    expect(wrapper.findAllComponents({ name: "RunResetChip" })).toHaveLength(0);

    await wrapper.vm.submit();
    await flushPromises();
    // Back to the value it started at, which the body still carries - the point
    // is that it is 12 and not the 0 an emptied number box coerces to.
    expect(sentValue(runWorkflowCard.mock.calls[0][0], "steps").value).toBe(12);
  });

  it("does not record an unparseable value either", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, wrapper.vm.coerce(steps, "twelve"));
    expect(wrapper.vm.currentValue(steps)).toBe(12);
  });
});

describe("the LoRAs a graph already loads", () => {
  /** A stock `LoraLoader`: names its file in a `lora_name` widget. */
  function filenameSlot(value, node = "7") {
    return {
      node_id: node,
      class_type: "LoraLoader",
      field: "lora_name",
      value,
      by: "filename",
      strengths: { model: 0.85 },
    };
  }

  it("shows the ordinary filename slots, which are all of them in practice", async () => {
    // `by: "digest"` is only PixlStash's own loader node. Filtering on it
    // meant every stock workflow opened the popup with no LoRAs at all.
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [filenameSlot("mira_v2.safetensors")],
    });
    const wrapper = await mountRun();

    expect(wrapper.vm.loras).toHaveLength(1);
    expect(wrapper.vm.loras[0]).toMatchObject({
      node_id: "7",
      field: "lora_name",
      by: "filename",
      graphValue: "mira_v2.safetensors",
      strength: 0.85,
    });
  });

  it("resolves the graph's filename to a shelf digest", async () => {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [filenameSlot("mira_v2.safetensors")],
    });
    const wrapper = await mountRun();
    expect(wrapper.vm.loras[0].sha256).toBe("s".repeat(64));
  });

  it("matches on the basename, since ComfyUI counts from its own folder", async () => {
    // The shelf recorded `loras/film-grain-35mm.safetensors`; the graph names
    // it relative to ComfyUI's `loras` folder.
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [filenameSlot("film-grain-35mm.safetensors")],
    });
    const wrapper = await mountRun();
    expect(wrapper.vm.loras[0].sha256).toBe("t".repeat(64));
  });

  it("still shows a LoRA the shelf cannot name, and says so", async () => {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [filenameSlot("somebody-elses.safetensors")],
    });
    const wrapper = await mountRun();

    expect(wrapper.vm.loras).toHaveLength(1);
    expect(wrapper.vm.loras[0].sha256).toBe("");
    expect(wrapper.vm.unresolvedLoras).toEqual(["somebody-elses.safetensors"]);
    expect(wrapper.text()).toContain("Not on your model shelf");
    // And its own value is in the select, or the row reads as an empty slot
    // nobody filled when in fact the graph fills it.
    expect(wrapper.vm.optionsFor(wrapper.vm.loras[0])[0].label).toContain(
      "somebody-elses.safetensors",
    );
  });

  it("refuses to guess when two shelf rows carry the same name", async () => {
    listAdapters.mockResolvedValue([
      { sha256: "a".repeat(64), filename: "dupe.safetensors" },
      { sha256: "b".repeat(64), filename: "other/dupe.safetensors" },
    ]);
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [filenameSlot("dupe.safetensors")],
    });
    const wrapper = await mountRun();
    // A coin toss over which file loads is not a resolution.
    expect(wrapper.vm.loras[0].sha256).toBe("");
  });

  it("sends NOTHING for a LoRA nobody touched", async () => {
    // The graph already names it; an override would only repeat the graph, and
    // for an unresolvable slot there is no digest to repeat it with - which
    // would refuse the run over a LoRA nobody changed.
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [
        filenameSlot("mira_v2.safetensors"),
        filenameSlot("somebody-elses.safetensors", "8"),
      ],
    });
    const wrapper = await mountRun();
    expect(wrapper.vm.loras).toHaveLength(2);

    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].loras).toEqual([]);
  });

  it("sends the row the owner swapped, and only that one", async () => {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [
        filenameSlot("mira_v2.safetensors"),
        filenameSlot("film-grain-35mm.safetensors", "8"),
      ],
    });
    const wrapper = await mountRun();
    wrapper.vm.loras[0].sha256 = "t".repeat(64);
    await wrapper.vm.$nextTick();

    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].loras).toEqual([
      {
        node_id: "7",
        field: "lora_name",
        sha256: "t".repeat(64),
        strength_model: 0.85,
      },
    ]);
  });

  it("sends a strength the owner changed on an otherwise untouched row", async () => {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_key: KEY,
      settings: {},
      lora_slots: [filenameSlot("mira_v2.safetensors")],
    });
    const wrapper = await mountRun();
    wrapper.vm.loras[0].strength = 0.4;
    await wrapper.vm.$nextTick();

    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].loras[0]).toMatchObject({
      sha256: "s".repeat(64),
      strength_model: 0.4,
    });
  });
});

describe("the picture beside the form", () => {
  it("resolves a card's cover through the api helper, not raw", async () => {
    // `covers` are API-relative and an `<img src>` bypasses Axios, so a raw
    // path is asked of the page origin and the cover is simply broken. F1b hit
    // this on the Workflows grid first; the popup draws the same paths.
    getWorkflowCard.mockResolvedValue({
      card: card({ covers: ["/pictures/thumbnails/812.webp?v=3"] }),
    });
    const wrapper = await mountRun({ kind: "card", workflowKey: KEY });

    expect(wrapper.vm.coverUrl).toBe("/api/v1/pictures/thumbnails/812.webp?v=3");
  });

  it("keeps a caller's own url as given", async () => {
    // The Workflow tab resolves it before handing it over, so the popup must
    // not put the prefix on twice.
    const wrapper = await mountRun({
      kind: "card",
      workflowKey: KEY,
      coverUrl: "/api/v1/pictures/thumbnails/9.webp",
    });
    expect(wrapper.vm.coverUrl).toBe("/api/v1/pictures/thumbnails/9.webp");
  });
});

describe("a card that carries only half a Size", () => {
  it("draws the one it has as an ordinary parameter rather than hiding it", async () => {
    // Half a Size cell would be a control that lies about what it sets; but a
    // width that is pinned and not drawn is worse, because it is then in
    // neither the pinned rows nor "All N parameters" and cannot be edited at
    // all.
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [def("Steps", "steps", 8), def("Width", "width", 832, "Latent")],
      }),
    });
    const wrapper = await mountRun();

    expect(wrapper.vm.sizeFields).toEqual([]);
    expect(wrapper.vm.restFields.map((f) => f.input_name)).toContain("width");
  });
});

describe("\"Run a workflow on these…\", which opens with no workflow chosen", () => {
  it("reads the card the FIRST time one is picked", async () => {
    // The previous value is the empty string both when the popup assigned it
    // and when the owner makes their first choice, so a plain watcher on the
    // key could not tell the two apart and left the form empty for ever.
    const wrapper = await mountRun({
      kind: "selection",
      pictureIds: [1, 2],
      pickWorkflow: true,
    });
    expect(wrapper.vm.defaults).toEqual([]);
    expect(wrapper.vm.canRun).toBe(false);

    wrapper.vm.workflowKey = KEY;
    await flushPromises();

    expect(wrapper.vm.defaults.length).toBeGreaterThan(0);
    expect(wrapper.vm.canRun).toBe(true);
  });
});

describe("the body it sends", () => {
  it("names pictures as the source and the card as the target", async () => {
    const wrapper = await mountRun();
    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.picture_ids).toEqual([42]);
    expect(body.target).toBe(KEY);
    // Exactly one source, or the route answers 400 before it reads anything.
    expect(body.workflow_key).toBeUndefined();
  });

  it("names the card itself when there is no picture behind it", async () => {
    const wrapper = await mountRun({ kind: "card", workflowKey: KEY });
    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_key).toBe(KEY);
    expect(body.picture_ids).toBeUndefined();
    expect(body.target).toBeUndefined();
  });

  it("sends an edit as an addressed override, not as a rewritten default", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);
    await wrapper.vm.submit();
    await flushPromises();

    expect(sentValue(runWorkflowCard.mock.calls[0][0], "steps")).toEqual({
      slot_label: "KSampler",
      input_name: "steps",
      value: 16,
    });
  });

  it("sends what the form SHOWS, not only what was edited", async () => {
    // The run does not start from this form: `_plan` resolves its own graph and
    // applies `body.values` alone. The card's defaults are a display figure and
    // the picture's settings are a fact about the picture; neither reaches the
    // graph on its own. Sending only the edits ran a graph that disagreed with
    // the form the owner was looking at.
    //
    // Here the picture was made at 12 steps and the card's own default is 8.
    // The form shows 12, so the run has to do 12 - not the 8 the resolved
    // source graph would otherwise carry.
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(steps)).toBe(12);
    expect(wrapper.vm.isEdited(steps)).toBe(false);

    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(sentValue(body, "steps")).toEqual({
      slot_label: "KSampler",
      input_name: "steps",
      value: 12,
    });
    // And every other parameter on the card, so nothing silently falls back.
    expect(sentValue(body, "cfg").value).toBe(2.0);
    expect(sentValue(body, "ckpt_name").value).toBe("realvisXL_v5.safetensors");
  });

  it("drops a parameter the card has no value for, which RunValue would refuse", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [def("Steps", "steps", 8), def("Refiner", "refiner", null)],
      }),
    });
    const wrapper = await mountRun();
    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(sentValue(body, "steps")).toBeTruthy();
    expect(sentValue(body, "refiner")).toBeUndefined();
  });

  it("does not put one picture setting into two slots of the same name", async () => {
    // `recipe.settings` is `{field: value}` with no slot, so a name carried by
    // two parameters cannot be attributed to either.
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [
          def("Steps", "steps", 8, "KSampler"),
          def("Steps (detailer)", "steps", 30, "KSampler2"),
        ],
      }),
    });
    const wrapper = await mountRun();
    const body = wrapper.vm.displayedValues();

    // The picture's 12 belongs to neither, so each keeps its own default.
    expect(body.find((v) => v.slot_label === "KSampler").value).toBe(8);
    expect(body.find((v) => v.slot_label === "KSampler2").value).toBe(30);
  });

  it("sends a 64-bit seed digit for digit, never as a JS Number", async () => {
    // 18446744073709551615 is 2**64-1. As a `Number` it is 18446744073709552000,
    // which is a DIFFERENT seed and a different picture — the whole reason the
    // recipe read serves `seed_text` beside `seed`.
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "fixed";
    wrapper.vm.seed = "18446744073709551615";
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.canRun).toBe(true);

    await wrapper.vm.submit();
    await flushPromises();

    expect(runWorkflowCard.mock.calls[0][0].seed).toBe("18446744073709551615");
  });

  it("refuses a seed that is not a whole number, naming the blocker", async () => {
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "fixed";
    wrapper.vm.seed = "12.5";
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.canRun).toBe(false);
    expect(wrapper.vm.runBlocker).toBe("A seed is a whole number.");
  });

  it("sends the prompt it is showing when a recipe filled the box", async () => {
    const wrapper = await mountRun();
    expect(wrapper.vm.prompt).toBe("a rainy tram platform");
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].prompt).toBe("a rainy tram platform");
  });

  it("sends null, NOT \"\", when no recipe ever filled the prompt box", async () => {
    // `_apply_prompts` short-circuits on `None` and writes on `""`, so an
    // empty string here overwrites every positive-prompt node in the graph and
    // the run generates from no prompt at all. A card and a multi-picture
    // selection both read no recipe, so the box is empty because nothing
    // filled it - which is not an instruction to blank anything.
    const wrapper = await mountRun({ kind: "card", workflowKey: KEY });
    expect(wrapper.vm.prompt).toBe("");
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].prompt).toBe(null);
  });

  it("does send \"\" when the owner clears a prompt that WAS there", async () => {
    // The other half of the same rule: emptying a box that had text in it is a
    // deliberate instruction, and it must still reach the graph.
    const wrapper = await mountRun();
    wrapper.vm.prompt = "";
    await wrapper.vm.$nextTick();
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].prompt).toBe("");
  });

  it("emits the prompts so the grid's runner can follow them", async () => {
    const wrapper = await mountRun();
    await wrapper.vm.submit();
    await flushPromises();

    expect(wrapper.emitted("run")[0][0]).toEqual({
      prompts: [{ prompt_id: "p1" }],
      pictureIds: [42],
    });
  });
});
