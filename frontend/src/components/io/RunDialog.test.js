// The Run popup (v1.12 F5): the three things about it that can silently
// invert, and one that can silently leak.
//
// * The ↺ chip. It carries the value the field STARTED at, lives in the label
//   row, and puts that value back. A chip that carries the current value, or
//   that sits beside the control, is the same pixels and the wrong thing:
//   the first resets to nothing, the second pushes the four-column grid out of
//   alignment the moment one field is edited.
// * Switching to another workflow. The edits are kept, and the ones the
//   new card has no address for are NAMED. Dropping them silently is what
//   makes a run quietly use 8 steps when the form said 16.
// * The body's source. `POST /workflows/run` takes exactly one of
//   picture_ids / saved_recipe_id / workflow_id, and a workflow chosen over
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
const setWorkflowInputs = vi.fn();
const saveFixedWorkflow = vi.fn();
const setWorkflowPins = vi.fn();
const readModelSwap = vi.fn();

const stackMemberIds = vi.fn();
vi.mock("../../utils/stackMembers", () => ({
  stackMemberIds: (...a) => stackMemberIds(...a),
}));

vi.mock("../../api/workflows", () => ({
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  runWorkflowCard: (...args) => runWorkflowCard(...args),
  setWorkflowInputs: (...args) => setWorkflowInputs(...args),
  saveFixedWorkflow: (...args) => saveFixedWorkflow(...args),
  setWorkflowPins: (...args) => setWorkflowPins(...args),
  readModelSwap: (...args) => readModelSwap(...args),
  workflowCoverUrl: (cover) => (cover?.url ? `/api/v1${cover.url}` : ""),
}));
vi.mock("../../api/comfyui", () => ({
  getPictureRecipe: (...args) => getPictureRecipe(...args),
}));
const listAdapters = vi.fn();
const fetchWorkflowSets = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));
const listSavedRecipes = vi.fn();
vi.mock("../../api/recipes", () => ({
  createSavedRecipe: vi.fn(),
  editSavedRecipe: vi.fn(),
  listSavedRecipes: (...args) => listSavedRecipes(...args),
}));
// Opening the dialog refreshes the set names. Unmocked, that is a real request
// still in flight when the environment tears down.
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn().mockResolvedValue([]),
}));
// The person picker reads the people the same way, on a workflow run alone.
const listCharacters = vi.fn();
vi.mock("../../api/characters", () => ({
  listCharacters: (...args) => listCharacters(...args),
  characterThumbnailUrl: (id) => `/api/v1/characters/${id}/thumbnail`,
}));
const push = vi.fn();
const currentRoute = { name: "home" };
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
  useRoute: () => currentRoute,
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import RunDialog from "./RunDialog.vue";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { notifySessionReset } from "../../utils/apiClient";
import { pictureThumbnailUrl } from "../../api/pictures";

const KEY = `auto:${"a".repeat(64)}`;
const OTHER = `auto:${"b".repeat(64)}`;

/** A parameter as `GET /workflows/{key}` serves it. */
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
    id: KEY,
    name: "Cinematic portrait",
    covers: [],
    models: [],
    loras: [],
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
      // Bound, not merely declared: a stub that swallows `disabled` makes
      // "not natively disabled" pass however the dialog behaves.
      AppButton: {
        props: ["disabled"],
        template: "<button :disabled='disabled'><slot /></button>",
      },
      AppBarButton: true,
      RunReasonNotice: true,
      PicturePicker: true,
      "v-icon": true,
    },
  },
};

async function mountRun(source = { kind: "picture", pictureIds: [42] }) {
  const wrapper = mount(RunDialog, {
    props: { source, context: { client_id: "test-client" } },
    ...globalOpts,
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  currentRoute.name = "home";
  getWorkflowCard.mockImplementation(async (key) =>
    key === OTHER
      ? {
          card: card({
            id: OTHER,
            name: "Cinematic portrait · detailer",
            // No `denoise`, so an edit made on the first card has no address
            // here and must be reported as having fallen back.
            defaults: [def("Steps", "steps", 20), def("CFG", "cfg", 3.5)],
          }),
        }
      : { card: card() },
  );
  listWorkflowCards.mockResolvedValue({ cards: [] });
  fetchWorkflowSets.mockResolvedValue({ hand_made: [] });
  listCharacters.mockResolvedValue([]);
  setWorkflowInputs.mockResolvedValue({ inputs: [] });
  listSavedRecipes.mockResolvedValue([]);
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
    workflow_id: KEY,
    positive_prompt: "a rainy tram platform",
    negative_prompt: null,
    seed_text: "418220931",
    settings: { steps: 12 },
    lora_slots: [],
  });
});

describe("Open in Workflows", () => {
  const openButton = (wrapper) =>
    wrapper.findAll("button").find((b) => b.text() === "Open in Workflows");

  it("closes the popup and selects the card it runs on the Workflows screen", async () => {
    const wrapper = await mountRun({
      kind: "edit",
      pictureIds: [42],
      workflowId: OTHER,
    });
    await openButton(wrapper).trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "workflows",
      query: { workflow: OTHER },
    });
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("follows a switch in the Workflow picker", async () => {
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await wrapper
      .findAllComponents({ name: "AppSelect" })
      .find((c) => c.props("label") === "Workflow")
      .vm.$emit("update:modelValue", OTHER);
    await flushPromises();
    await openButton(wrapper).trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "workflows",
      query: { workflow: OTHER },
    });
  });

  it("is not offered on the Workflows screen itself", async () => {
    currentRoute.name = "workflows";
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(openButton(wrapper)).toBeUndefined();
  });
});

describe("the prompt box", () => {
  it("opens on a caller's prompt, editable, over the picture's re-read", async () => {
    // The Recipes tab passes the prompt its row shows: the re-read may have
    // none, or differ, and the box must not open on something else.
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      positive_prompt: null,
      lora_slots: [],
    });
    const wrapper = await mountRun({
      kind: "picture",
      pictureIds: [42],
      prompt: "a look the row shows",
    });
    const box = wrapper
      .findAllComponents({ name: "AppTextarea" })
      .find((c) => c.props("label") === "Prompt");
    expect(box.props("modelValue")).toBe("a look the row shows");
    expect(box.props("disabled")).toBe(false);
    await box.vm.$emit("update:modelValue", "edited");
    expect(box.props("modelValue")).toBe("edited");
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

describe("switching to another workflow", () => {
  it("says a kept checkpoint fell back on a two-file workflow", async () => {
    // The first loader's address survives the switch, but the new row is
    // read-only and sends no pick: dropping the edit in silence is the bug.
    const recipe = (...files) => ({
      models: files.map((filename, i) => ({
        address: `core:${"ab"[i]}/unet_name`,
        kind: "checkpoint",
        filename,
      })),
      loras: [],
      values: [],
      stages: {},
    });
    getWorkflowCard.mockImplementation(async (key) => ({
      card:
        key === OTHER
          ? card({ id: OTHER, default_recipe: recipe("wan_high.safetensors", "wan_low.safetensors") })
          : card({ default_recipe: recipe("wan_high.safetensors") }),
    }));
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    wrapper.vm.setCheckpoint("other.safetensors");

    wrapper.vm.workflowId = OTHER;
    await flushPromises();

    expect(wrapper.vm.fellBack).toContain("Checkpoint");
    expect(wrapper.vm.checkpointEdit).toBeNull();
  });

  it("keeps the edits the new workflow has an address for", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 16);

    wrapper.vm.workflowId = OTHER;
    await flushPromises();

    const kept = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(kept)).toBe(16);
  });

  it("names the fields that fell back to the new workflow's own values", async () => {
    // Denoise set each run on this workflow, so it is a field to edit.
    const own = getWorkflowCard.getMockImplementation();
    getWorkflowCard.mockImplementation(async (key) => {
      const answer = await own(key);
      if (key !== KEY) return answer;
      const pins = answer.card.defaults
        .filter((f) => ["steps", "denoise"].includes(f.input_name))
        .map(({ slot_label, input_name }) => ({ slot_label, input_name }));
      return { ...answer, pins };
    });
    const wrapper = await mountRun();
    const denoise = wrapper.vm.otherRunFields.find(
      (f) => f.input_name === "denoise",
    );
    wrapper.vm.setValue(denoise, 0.6);

    wrapper.vm.workflowId = OTHER;
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
    // opens showing. Another workflow is a different graph: 12 says nothing
    // about it, and carrying it over would make that workflow's own 20 look
    // like an edit somebody made.
    const wrapper = await mountRun();
    const before = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(before)).toBe(12);

    wrapper.vm.workflowId = OTHER;
    await flushPromises();

    const after = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    expect(wrapper.vm.currentValue(after)).toBe(20);
  });

  it("offers the workflow alone, never a list of stack members (#1623)", async () => {
    const wrapper = await mountRun();
    expect(wrapper.vm.workflowOptions).toEqual([
      { value: KEY, label: "Cinematic portrait" },
    ]);
  });

  it("says nothing when every edit survived", async () => {
    const wrapper = await mountRun();
    const cfg = wrapper.vm.scalarFields.find((f) => f.input_name === "cfg");
    wrapper.vm.setValue(cfg, 4);

    wrapper.vm.workflowId = OTHER;
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
        groups: [{ workflow_id: KEY, reasons: [{ code: "no_lora_loader" }] }],
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

  it("offers a sampler this ComfyUI lacks a replacement, and runs with it", async () => {
    const missing = {
      code: "missing_choices",
      choices: [
        {
          node_id: "3",
          field: "sampler_name",
          value: "res_2s",
          options: ["dpmpp_2m", "euler"],
          replacement: "euler",
        },
      ],
    };
    preflightWorkflowRun
      .mockResolvedValueOnce({
        ok: false,
        runs: 0,
        groups: [{ workflow_id: KEY, reasons: [missing] }],
      })
      .mockResolvedValue({ ok: true, runs: 1, groups: [] });

    const wrapper = await mountRun();

    // Re-asked with the replacement, and the row stays once the reason clears.
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(2);
    expect(preflightWorkflowRun.mock.calls[1][0].choices).toEqual([
      { node_id: "3", field: "sampler_name", value: "euler" },
    ]);
    expect(wrapper.vm.reasons).toEqual([]);
    expect(wrapper.vm.canRun).toBe(true);
    const row = wrapper.find("[data-testid='rund-choice']");
    expect(row.text()).toContain("res_2s is not on this ComfyUI");

    wrapper.vm.choiceFixes[0].chosen = "dpmpp_2m";
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].choices).toEqual([
      { node_id: "3", field: "sampler_name", value: "dpmpp_2m" },
    ]);
  });

  it("lets the owner's own sampler pick win over the replacement", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [...card().defaults, def("Sampler", "sampler_name", "res_2s")],
      }),
    });
    preflightWorkflowRun
      .mockResolvedValueOnce({
        ok: false,
        runs: 0,
        groups: [
          {
            workflow_id: KEY,
            reasons: [
              {
                code: "missing_choices",
                choices: [
                  {
                    node_id: "3",
                    field: "sampler_name",
                    value: "res_2s",
                    options: ["dpmpp_2m", "euler"],
                    replacement: "euler",
                  },
                ],
              },
            ],
          },
        ],
      })
      .mockResolvedValue({ ok: true, runs: 1, groups: [] });
    const wrapper = await mountRun();
    expect(wrapper.vm.choiceFixes).toHaveLength(1);

    const sampler = wrapper.vm.defaults.find(
      (field) => field.input_name === "sampler_name",
    );
    // Typed a key at a time: one re-ask, for the whole name.
    const asks = preflightWorkflowRun.mock.calls.length;
    wrapper.vm.setValue(sampler, "dpmpp");
    wrapper.vm.setValue(sampler, "dpmpp_2m");
    await new Promise((resolve) => setTimeout(resolve, 350));
    await flushPromises();
    expect(preflightWorkflowRun.mock.calls.length).toBe(asks + 1);

    expect(wrapper.vm.choiceFixes).toEqual([]);
    const asked = preflightWorkflowRun.mock.calls.at(-1)[0];
    expect(asked.choices).toBeUndefined();
    // The re-ask carries the pick, not the missing value it replaced.
    expect(sentValue(asked, "sampler_name")?.value).toBe("dpmpp_2m");
  });

  it("keeps a refusal the replacement did not clear, rather than asking for ever", async () => {
    const missing = {
      code: "missing_choices",
      choices: [
        {
          node_id: "3",
          field: "scheduler",
          value: "bong_tangent",
          options: ["simple"],
          replacement: "simple",
        },
      ],
    };
    preflightWorkflowRun.mockResolvedValue({
      ok: false,
      runs: 0,
      groups: [{ workflow_id: KEY, reasons: [missing] }],
    });

    const wrapper = await mountRun();

    expect(preflightWorkflowRun).toHaveBeenCalledTimes(2);
    expect(wrapper.vm.reasons).toEqual([missing]);
    expect(wrapper.vm.canRun).toBe(false);
  });

  it("names a bypassed LoRA without blocking the run it is about (#1463)", async () => {
    // The whole point: the server WILL run this, so the notice must say so
    // before the run rather than leaving the owner to notice the character
    // LoRA was missing from the picture afterwards - and must not disable the
    // button, which would put the refusal back by another route.
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [
        {
          workflow_id: KEY,
          reasons: [],
          bypassed_loras: [{ file: "character.safetensors", folder: "loras" }],
        },
      ],
    });

    const wrapper = await mountRun();

    expect(wrapper.vm.reasons).toEqual([]);
    expect(wrapper.vm.canRun).toBe(true);
    // `RunReasonNotice` is stubbed here; what it makes of this is its own
    // test. What THIS one owns is that the notice reaches the list the popup
    // draws while staying out of the one `reasonsBlock` reads.
    expect(wrapper.vm.runNotes).toEqual([
      {
        code: "loras_bypassed",
        models: [{ file: "character.safetensors", folder: "loras" }],
      },
    ]);
  });

  it("names a replaced seed node beside a bypassed LoRA, blocking neither (#1463)", async () => {
    const replaced = { node_id: "9", class_type: "Seed (rgthree)", replacement: "seed" };
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [
        {
          workflow_id: KEY,
          reasons: [],
          bypassed_loras: [{ file: "character.safetensors", folder: "loras" }],
          replaced_nodes: [replaced],
        },
      ],
    });

    const wrapper = await mountRun();

    expect(wrapper.vm.reasons).toEqual([]);
    expect(wrapper.vm.canRun).toBe(true);
    expect(wrapper.vm.runNotes.map((note) => note.code)).toEqual([
      "loras_bypassed",
      "nodes_replaced",
    ]);
    expect(wrapper.vm.runNotes[1].nodes).toEqual([replaced]);
  });

  it("names a recipe LoRA with no loader to go in, without blocking (#1478)", async () => {
    // The run's sibling silent drop: a recipe LoRA that found no slot used to
    // vanish in `zip(slots, recipe_loras)`. It is reported now, beside the
    // bypassed ones, and it is not a reason - the run still goes ahead.
    const unplaced = {
      filename: "skin-detail-xl.safetensors",
      sha256: "c".repeat(64),
      reason: "this workflow has no third loader",
    };
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [
        {
          workflow_id: OTHER,
          reasons: [],
          bypassed_loras: [],
          unplaced_loras: [unplaced],
        },
      ],
    });

    const wrapper = await mountRun();

    expect(wrapper.vm.reasons).toEqual([]);
    expect(wrapper.vm.canRun).toBe(true);
    expect(wrapper.vm.runNotes).toEqual([
      { code: "loras_unplaced", loras: [unplaced], workflowId: OTHER },
    ]);

    // Its fix is Edit LoRAs… on THAT group's card, which closes this popup.
    wrapper
      .findComponent({ name: "RunReasonNotice" })
      .vm.$emit("edit-loras", OTHER);
    await flushPromises();
    expect(push).toHaveBeenCalledWith({
      name: "workflows",
      query: { workflow: OTHER, edit: "loras" },
    });
    expect(wrapper.emitted("close")).toBeTruthy();
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

    wrapper.vm.typeValue(steps, "");
    await wrapper.vm.$nextTick();

    // Still empty while the owner is in it: putting 12 back would undo the
    // keystroke that cleared it, and the next digit would land after the 12.
    expect(wrapper.vm.currentValue(steps)).toBe("");
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
    wrapper.vm.typeValue(steps, "twelve");
    expect(wrapper.vm.isEdited(steps)).toBe(false);
    // Leaving the box shows what the run will use.
    wrapper.vm.settleDraft(steps);
    expect(wrapper.vm.currentValue(steps)).toBe(12);
  });

  it("can be typed into again without the default coming back", async () => {
    const wrapper = await mountRun();
    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.typeValue(steps, "");
    wrapper.vm.typeValue(steps, "3");
    wrapper.vm.typeValue(steps, "30");
    expect(wrapper.vm.currentValue(steps)).toBe(30);
    await wrapper.vm.submit();
    await flushPromises();
    expect(sentValue(runWorkflowCard.mock.calls[0][0], "steps").value).toBe(30);
  });
});

describe("the Size boxes", () => {
  it("stay empty while cleared and typed into, and refill only on leaving", async () => {
    // The report: deleting the last digit put the default straight back, so
    // a new width could only be typed by selecting the old one first. The
    // real AppInput, since its round trip through the DOM is the bug.
    const wrapper = mount(RunDialog, {
      props: { source: { kind: "picture", pictureIds: [42] } },
      global: {
        stubs: { ...globalOpts.global.stubs, AppInput: false },
      },
    });
    await flushPromises();
    const box = wrapper.find('input[aria-label="Width"]');
    expect(box.element.value).toBe("832");

    // Backspaced the way a person does it: the last digit out is the edit
    // going away, which is when the default used to be written back.
    await box.setValue("83");
    await box.setValue("8");
    await box.setValue("");
    expect(box.element.value).toBe("");
    await box.setValue("1");
    await box.setValue("1024");
    expect(box.element.value).toBe("1024");

    await box.setValue("");
    await box.trigger("blur");
    expect(box.element.value).toBe("832");
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
      workflow_id: KEY,
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
      workflow_id: KEY,
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
      workflow_id: KEY,
      settings: {},
      lora_slots: [filenameSlot("film-grain-35mm.safetensors")],
    });
    const wrapper = await mountRun();
    expect(wrapper.vm.loras[0].sha256).toBe("t".repeat(64));
  });

  it("still shows a LoRA the shelf cannot name, and says so", async () => {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      settings: {},
      lora_slots: [filenameSlot("somebody-elses.safetensors")],
    });
    const wrapper = await mountRun();

    expect(wrapper.vm.loras).toHaveLength(1);
    expect(wrapper.vm.loras[0].sha256).toBe("");
    expect(wrapper.text()).toContain(
      "Not on your model shelf: PixlStash cannot identify this file.",
    );
    // And its own value is in the select, or the row reads as an empty slot
    // nobody filled when in fact the graph fills it.
    expect(wrapper.vm.optionsFor(wrapper.vm.loras[0])[0].label).toContain(
      "somebody-elses.safetensors",
    );
    // And Save as recipe is handed it, digest-less, to flag (#1478): a row
    // filtered out here was a LoRA that dialog never got to say anything about.
    expect(wrapper.vm.recipeLoras).toEqual([
      { filename: "somebody-elses.safetensors", sha256: "", strength: 0.85 },
    ]);
  });

  it("refuses to guess when two shelf rows carry the same name", async () => {
    listAdapters.mockResolvedValue([
      { sha256: "a".repeat(64), filename: "dupe.safetensors" },
      { sha256: "b".repeat(64), filename: "other/dupe.safetensors" },
    ]);
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
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
      workflow_id: KEY,
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
      workflow_id: KEY,
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
      workflow_id: KEY,
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

describe("skipping a graph LoRA for this run", () => {
  // The owner must be able to run without a LoRA that does not exist here.
  // The popup changes no workflow, so its word is Skip: the row stays, says
  // it is skipped, and Use takes it back. What goes on the wire is
  // `skip_loras`, and a skipped row is never ALSO an override in `loras`.
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

  async function mountTwo() {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      settings: {},
      lora_slots: [
        filenameSlot("mira_v2.safetensors"),
        filenameSlot("somebody-elses.safetensors", "8"),
      ],
    });
    return mountRun();
  }

  function buttonReading(wrapper, text, index = 0) {
    return wrapper.findAll("button").filter((b) => b.text().trim() === text)[
      index
    ];
  }

  it("offers Skip on a graph row, never a trash or a delete", async () => {
    const wrapper = await mountTwo();
    const words = wrapper.findAll("button").map((b) => b.text().trim());
    expect(words.filter((word) => word === "Skip")).toHaveLength(2);
    expect(wrapper.text()).not.toMatch(/delete|remove/i);
  });

  it("sends a skipped row in skip_loras and not in loras", async () => {
    const wrapper = await mountTwo();
    // An override on the row first, so leaving `loras` alone would be caught.
    wrapper.vm.loras[0].sha256 = "t".repeat(64);
    await wrapper.vm.$nextTick();
    const before = preflightWorkflowRun.mock.calls.length;

    await buttonReading(wrapper, "Skip", 0).trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Skipped for this run");
    // The removal set changed, so the pre-flight is asked again with it.
    expect(preflightWorkflowRun.mock.calls.length).toBe(before + 1);
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].skip_loras).toEqual([
      { node_id: "7", field: "lora_name" },
    ]);

    await wrapper.vm.submit();
    await flushPromises();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.skip_loras).toEqual([{ node_id: "7", field: "lora_name" }]);
    expect(body.loras).toEqual([]);
    // But a recipe saved from here still lists it: a saved recipe cannot hold
    // a skip, so every run of it loads that loader from the graph, and a list
    // leaving it out would say less than those runs do.
    expect(
      [...wrapper.vm.recipeLoras.map((lora) => lora.filename)].sort(),
    ).toEqual([
      "loras/film-grain-35mm.safetensors",
      "somebody-elses.safetensors",
    ]);
  });

  it("takes a skip back with Use", async () => {
    const wrapper = await mountTwo();
    await buttonReading(wrapper, "Skip", 1).trigger("click");
    await flushPromises();
    await buttonReading(wrapper, "Use").trigger("click");
    await flushPromises();

    expect(wrapper.text()).not.toContain("Skipped for this run");
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("skip_loras");
  });

  it("says on the row when the run already leaves its loader out", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [
        {
          workflow_id: KEY,
          reasons: [],
          bypassed_loras: [
            { file: "Somebody-Elses.safetensors", folder: "loras", requested: false },
          ],
        },
      ],
    });
    const wrapper = await mountTwo();
    const flags = wrapper
      .findAll("[data-testid='rund-lora-flag']")
      .map((flag) => flag.text().replace(/mdi-\S+/, "").trim());
    expect(flags).toEqual([
      "Not on this ComfyUI. The run leaves this loader out.",
    ]);
  });

  it("keeps the owner's skips apart from the missing files in the notices", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [
        {
          workflow_id: KEY,
          reasons: [],
          bypassed_loras: [
            { file: "gone.safetensors", folder: "loras", requested: false },
            { file: "mira_v2.safetensors", folder: "loras", requested: true },
          ],
        },
      ],
    });
    const wrapper = await mountTwo();
    expect(wrapper.vm.runNotes.map((note) => note.code)).toEqual([
      "loras_bypassed",
      "loras_skipped",
    ]);
    expect(wrapper.vm.canRun).toBe(true);
  });
});

describe("the picture beside the form", () => {
  it("resolves a card's cover through the api helper, not raw", async () => {
    // A cover's `url` is API-relative and an `<img src>` bypasses Axios, so a
    // raw path is asked of the page origin and the cover is simply broken. F1b
    // hit this on the Workflows grid first; the popup draws the same paths.
    //
    // The entry is an OBJECT (#1465). Left as a bare string it would go on
    // passing against a mock that accepted one, while the real helper returns
    // "" for a string and the popup's picture had quietly vanished.
    getWorkflowCard.mockResolvedValue({
      card: card({ covers: [{ url: "/pictures/thumbnails/812.webp?v=3" }] }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });

    expect(wrapper.vm.coverUrl).toBe("/api/v1/pictures/thumbnails/812.webp?v=3");
  });

  it("keeps a caller's own url as given", async () => {
    // The Workflow tab resolves it before handing it over, so the popup must
    // not put the prefix on twice.
    const wrapper = await mountRun({
      kind: "card",
      workflowId: KEY,
      coverUrl: "/api/v1/pictures/thumbnails/9.webp",
    });
    expect(wrapper.vm.coverUrl).toBe("/api/v1/pictures/thumbnails/9.webp");
  });

  it("shows the picture a recipe run is made from, not the card's cover", async () => {
    // The card's cover is whatever the workflow last made; beside this
    // picture's prompt it reads as a different run altogether.
    getWorkflowCard.mockResolvedValue({
      card: card({ covers: [{ url: "/pictures/thumbnails/812.webp" }] }),
    });
    const wrapper = await mountRun({ kind: "picture", pictureIds: [42] });

    expect(wrapper.vm.coverUrl).toBe(pictureThumbnailUrl(42));
  });

  it("shows a saved recipe's own picture, not the card's cover", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({ covers: [{ url: "/pictures/thumbnails/812.webp" }] }),
    });
    const wrapper = await mountRun({
      kind: "card",
      workflowId: KEY,
      savedRecipe: { id: 5, workflow_id: KEY, name: "Rainy", source_picture_id: 77 },
    });

    expect(wrapper.vm.coverUrl).toBe(pictureThumbnailUrl(77));
    expect(wrapper.vm.sourceKindLine).toBe("Saved recipe");
  });
});

describe("a card that carries only half a Size", () => {
  it("draws the one it has as an ordinary parameter rather than hiding it", async () => {
    // Half a Size cell would be a control that lies about what it sets; but a
    // width that is set each run and not drawn is worse, because it then
    // cannot be edited at all.
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [def("Steps", "steps", 8), def("Width", "width", 832, "Latent")],
      }),
    });
    const wrapper = await mountRun();

    expect(wrapper.vm.sizeFields).toEqual([]);
    expect(wrapper.vm.otherRunFields.map((f) => f.input_name)).toContain("width");
  });
});

describe("a workflow's fixed parameters", () => {
  /** `KEY` with its stored pins, everything else as the default mock. */
  function withPins(pins) {
    const own = getWorkflowCard.getMockImplementation();
    getWorkflowCard.mockImplementation(async (key) => ({
      ...(await own(key)),
      ...(key === KEY ? { pins } : {}),
    }));
  }

  it("asks for the ones set each run and lists the rest read-only, still sending them", async () => {
    withPins([{ slot_label: "KSampler", input_name: "cfg" }]);
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(wrapper.vm.scalarFields.map((f) => f.input_name)).toEqual(["cfg"]);
    expect(wrapper.vm.sizeFields).toEqual([]);
    expect(wrapper.vm.otherRunFields).toEqual([]);
    const fixed = wrapper.vm.fixedFields.map((f) => f.input_name);
    expect(fixed).toEqual(["steps", "width", "height", "ckpt_name", "denoise"]);
    const list = wrapper.find("[data-testid='rund-fixed-params']");
    expect(list.find("summary").text().replace(/\s+/g, " ")).toBe(
      "Fixed for this workflow · 5",
    );
    expect(list.text()).toContain("Steps");
    // Fixed is "every run uses the workflow's value": still in the body.
    await wrapper.vm.submit();
    await flushPromises();
    expect(sentValue(runWorkflowCard.mock.calls[0][0], "steps").value).toBe(8);
  });

  it("falls back to the shared default set when nobody has chosen", async () => {
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(wrapper.vm.scalarFields.map((f) => f.input_name)).toEqual(["steps", "cfg"]);
    expect(wrapper.vm.sizeFields).toHaveLength(2);
    expect(wrapper.vm.fixedFields.map((f) => f.input_name)).toEqual([
      "ckpt_name",
      "denoise",
    ]);
  });

  it("frees one with Set each run, writing the whole list and telling the Workflow tab", async () => {
    const steps = { slot_label: "KSampler", input_name: "steps" };
    const denoise = { slot_label: "KSampler", input_name: "denoise" };
    withPins([steps]);
    setWorkflowPins.mockImplementation(async (_key, pins) => ({ pins }));
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const field = wrapper.vm.fixedFields.find((f) => f.input_name === "denoise");
    await wrapper.vm.freeField(field);
    await flushPromises();
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [steps, denoise]);
    expect(wrapper.vm.fixedFields.map((f) => f.input_name)).not.toContain("denoise");
    expect(wrapper.vm.otherRunFields.map((f) => f.input_name)).toEqual(["denoise"]);
    expect(useRunDialogStore().pinsWritten).toEqual({
      workflowId: KEY,
      pins: [steps, denoise],
    });
  });

  it("tells the Workflow tab even when the picker moved on while the write was out", async () => {
    const denoise = { slot_label: "KSampler", input_name: "denoise" };
    withPins([]);
    let answer;
    setWorkflowPins.mockImplementation(
      (_key, pins) => new Promise((resolve) => (answer = () => resolve({ pins }))),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const field = wrapper.vm.fixedFields.find((f) => f.input_name === "denoise");
    const freeing = wrapper.vm.freeField(field);
    wrapper.vm.workflowId = OTHER;
    await flushPromises();
    answer();
    await freeing;
    expect(useRunDialogStore().pinsWritten).toEqual({ workflowId: KEY, pins: [denoise] });
  });

  it("drops a pin write answered after the session was reset", async () => {
    withPins([]);
    let answer;
    setWorkflowPins.mockImplementation(
      (_key, pins) => new Promise((resolve) => (answer = () => resolve({ pins }))),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const field = wrapper.vm.fixedFields.find((f) => f.input_name === "denoise");
    const freeing = wrapper.vm.freeField(field);
    notifySessionReset("logout");
    answer();
    await freeing;
    expect(useRunDialogStore().pinsWritten).toBeNull();
  });

  it("forgets the last pin write on a session reset", async () => {
    const store = useRunDialogStore();
    store.pinsWritten = { workflowId: KEY, pins: [] };
    notifySessionReset("logout");
    expect(store.pinsWritten).toBeNull();
  });

  it("keeps a reset on a fixed row that carries a value kept from another workflow", async () => {
    withPins([]);
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const steps = wrapper.vm.fixedFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 30);
    await flushPromises();
    const row = wrapper
      .findAll(".rund-fixed-row")
      .find((r) => r.text().startsWith("Steps"));
    expect(row.findComponent({ name: "RunResetChip" }).exists()).toBe(true);
    expect(row.find(".rund-fixed-value").text()).toBe("30");
  });

  it("never moves focus to a field of the same name outside the form", async () => {
    withPins(
      card()
        .defaults.filter((f) => f.input_name !== "denoise")
        .map(({ slot_label, input_name }) => ({ slot_label, input_name })),
    );
    setWorkflowPins.mockImplementation(async (_key, pins) => ({ pins }));
    const elsewhere = document.createElement("input");
    elsewhere.setAttribute("aria-label", "Denoise");
    document.body.append(elsewhere);
    try {
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      const field = wrapper.vm.fixedFields.find((f) => f.input_name === "denoise");
      // The last fixed one: the list goes with it, so the fallback runs.
      expect(wrapper.vm.fixedFields).toHaveLength(1);
      await wrapper.vm.freeField(field);
      await flushPromises();
      expect(document.activeElement).not.toBe(elsewhere);
    } finally {
      elsewhere.remove();
    }
  });

  it("keeps it fixed, and says so, when the write fails", async () => {
    withPins([]);
    setWorkflowPins.mockRejectedValue(new Error("offline"));
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const field = wrapper.vm.fixedFields.find((f) => f.input_name === "denoise");
    await wrapper.vm.freeField(field);
    await flushPromises();
    expect(wrapper.vm.fixedFields.map((f) => f.input_name)).toContain("denoise");
    expect(wrapper.vm.freeError).toBe("offline");
    expect(useRunDialogStore().pinsWritten).toBeNull();
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

    wrapper.vm.workflowId = KEY;
    await flushPromises();

    expect(wrapper.vm.defaults.length).toBeGreaterThan(0);
    expect(wrapper.vm.canRun).toBe(true);
  });

  it("lists the library's workflows by id, the chosen one once", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        { id: KEY, name: "Krea 2" },
        { id: `auto:${"e".repeat(64)}`, name: "Portrait" },
      ],
    });
    const wrapper = await mountRun({
      kind: "selection",
      pictureIds: [1, 2],
      pickWorkflow: true,
    });
    wrapper.vm.workflowId = KEY;
    await flushPromises();

    expect(wrapper.vm.workflowOptions).toEqual([
      { value: KEY, label: "Cinematic portrait" },
      { value: `auto:${"e".repeat(64)}`, label: "Portrait" },
    ]);
  });
});

describe("Edit with ComfyUI, the built-in edit card over the selection", () => {
  it("reads no recipe and sends the pictures to the card with the typed prompt", async () => {
    const wrapper = await mountRun({
      kind: "edit",
      pictureIds: [42],
      workflowId: KEY,
      emptyPrompt: true,
    });
    // The picture is what is edited, not a recipe to replay.
    expect(getPictureRecipe).not.toHaveBeenCalled();
    expect(wrapper.vm.title).toBe("Edit with ComfyUI");
    const box = wrapper
      .findAllComponents({ name: "AppTextarea" })
      .find((c) => c.props("label") === "Prompt");
    expect(box.props("modelValue")).toBe("");
    await box.vm.$emit("update:modelValue", "make it night time");
    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.picture_ids).toEqual([42]);
    expect(body.target).toBe(KEY);
    expect(body.prompt).toBe("make it night time");
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
    expect(body.workflow_id).toBeUndefined();
  });

  it("names the card itself when there is no picture behind it", async () => {
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe(KEY);
    expect(body).not.toHaveProperty("workflow_key");
    expect(body.picture_ids).toBeUndefined();
    expect(body.target).toBeUndefined();
  });

  it("sends the checkpoint as a model at its default-recipe address (#1623)", async () => {
    const address = "core:CheckpointLoader/ckpt_name";
    getWorkflowCard.mockResolvedValue({
      card: card({
        default_recipe: {
          models: [
            { address, kind: "checkpoint", filename: "realvisXL_v5.safetensors" },
          ],
          loras: [],
          values: [],
          stages: {},
        },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    // Untouched, the form's checkpoint still goes as the model it shows.
    expect(wrapper.vm.checkpointValue).toBe("realvisXL_v5.safetensors");
    wrapper.vm.setCheckpoint("juggernautXL.safetensors");
    await wrapper.vm.submit();
    await flushPromises();

    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.models).toEqual([
      { address, filename: "juggernautXL.safetensors" },
    ]);
    // Never twice: the ckpt_name widget is not also sent as a value.
    expect(sentValue(body, "ckpt_name")).toBeUndefined();
  });

  describe("a checkpoint this ComfyUI does not have", () => {
    const address = "core:CheckpointLoader/ckpt_name";
    const missing = (folder = "checkpoints") => ({
      ok: false,
      runs: 1,
      groups: [
        {
          reasons: [
            { code: "missing_models", models: [{ file: "realvisXL_v5.safetensors", folder }] },
          ],
        },
      ],
    });

    beforeEach(() => {
      getWorkflowCard.mockResolvedValue({
        card: card({
          default_recipe: {
            models: [{ address, kind: "checkpoint", filename: "realvisXL_v5.safetensors" }],
            loras: [],
            values: [],
            stages: {},
          },
        }),
      });
    });

    it("offers the shelf's of the same base model and runs the one picked", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing());
      readModelSwap.mockResolvedValue({
        replacements: [
          { id: 7, filename: "juggernautXL.safetensors", display_name: "Juggernaut XL" },
        ],
        replacements_reason: null,
        replacements_narrowed: true,
      });
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });

      expect(readModelSwap).toHaveBeenCalledWith(KEY, {
        replacing: "realvisXL_v5.safetensors",
        slotKind: "checkpoint",
      });
      expect(wrapper.vm.checkpointOptions.map((o) => o.value)).toEqual([
        "realvisXL_v5.safetensors",
        "juggernautXL.safetensors",
      ]);
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(false);
      expect(wrapper.find("[data-testid='rund-checkpoint-missing']").text()).toContain(
        "Pick another of the same base model",
      );

      wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
      await flushPromises();
      // Kept once offered: the pick cleared the reason, not the picker.
      expect(wrapper.vm.checkpointOptions).toHaveLength(2);
      await wrapper.vm.submit();
      await flushPromises();
      expect(runWorkflowCard.mock.calls[0][0].models).toEqual([
        { address, filename: "juggernautXL.safetensors" },
      ]);
    });

    it("says why when nothing fits, and keeps the text box", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing());
      readModelSwap.mockResolvedValue({
        replacements: [],
        replacements_reason: "none_same_base_model",
      });
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(true);
      expect(wrapper.find("[data-testid='rund-checkpoint-missing']").text()).toContain(
        "No checkpoint on your model shelf is known to have its base model",
      );
      // Asked once per file, however often the pre-flight says it again.
      preflightWorkflowRun.mockResolvedValueOnce(missing());
      await wrapper.vm.runPreflight();
      await flushPromises();
      expect(readModelSwap).toHaveBeenCalledTimes(1);
    });

    it("asks about a picture's own checkpoint as the graph's, and offers that", async () => {
      // The picture ran another file than the graph loads: the server answers
      // only for the graph's file, which itself fits and leads the offer.
      getPictureRecipe.mockResolvedValue({
        available: true,
        workflow_id: KEY,
        positive_prompt: "a rainy tram platform",
        seed_text: "1",
        settings: { ckpt_name: "picture_own.safetensors" },
        lora_slots: [],
      });
      preflightWorkflowRun.mockResolvedValueOnce({
        ok: false,
        runs: 1,
        groups: [
          {
            reasons: [
              {
                code: "missing_models",
                models: [{ file: "picture_own.safetensors", folder: "diffusion_models" }],
              },
            ],
          },
        ],
      });
      readModelSwap.mockResolvedValue({
        replacements: [{ id: 7, filename: "juggernautXL.safetensors" }],
        replacements_narrowed: true,
      });
      const wrapper = await mountRun();
      expect(readModelSwap).toHaveBeenCalledWith(KEY, {
        replacing: "realvisXL_v5.safetensors",
        slotKind: "checkpoint",
      });
      expect(wrapper.vm.checkpointOptions.map((o) => o.value)).toEqual([
        "picture_own.safetensors",
        "realvisXL_v5.safetensors",
        "juggernautXL.safetensors",
      ]);
    });

    it("does not claim a base model when nothing narrowed the offer", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing());
      readModelSwap.mockResolvedValue({
        replacements: [{ id: 7, filename: "sd15.safetensors" }],
        replacements_narrowed: false,
      });
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      const note = wrapper.find("[data-testid='rund-checkpoint-missing']").text();
      expect(note).toContain("Nothing says which base model it was");
      expect(note).not.toContain("same base model");
    });

    it("leaves a name typed during the read in its box", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing());
      let answer;
      readModelSwap.mockReturnValue(new Promise((resolve) => (answer = resolve)));
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      // Nothing suggested while the read is out.
      expect(wrapper.vm.checkpointFixNote).toBe(
        "realvisXL_v5.safetensors is not on this ComfyUI.",
      );
      wrapper.vm.setCheckpoint("typed.safetensors");
      answer({ replacements: [{ id: 7, filename: "juggernautXL.safetensors" }] });
      await flushPromises();
      expect(wrapper.vm.checkpointFix).toBeNull();
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(true);
    });

    it("asks again after a read that failed", async () => {
      preflightWorkflowRun.mockResolvedValue(missing());
      readModelSwap.mockRejectedValueOnce(new Error("offline"));
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      expect(wrapper.vm.checkpointFix).toBeNull();
      readModelSwap.mockResolvedValue({ replacements: [], replacements_reason: "none_loadable" });
      await wrapper.vm.runPreflight();
      await flushPromises();
      expect(readModelSwap).toHaveBeenCalledTimes(2);
      expect(wrapper.vm.checkpointFix?.reason).toBe("none_loadable");
      // Not narrowed, so it names no base model.
      expect(wrapper.vm.checkpointFixNote).toContain(
        "Nothing on your model shelf can be loaded by this workflow",
      );
    });

    it("shows and offers to change the saved replacement for a gone checkpoint", async () => {
      // The Workflow tab replaced the gone file: every run loads `now`, so
      // the pre-flight finds nothing missing and the row must not name `was`.
      getWorkflowCard.mockResolvedValue({
        card: card({
          default_recipe: {
            models: [{ address, kind: "checkpoint", filename: "realvisXL_v5.safetensors" }],
            loras: [],
            values: [],
            stages: {},
          },
        }),
        model_fixes: [
          {
            slot_label: "CheckpointLoader/ckpt_name",
            was: "RealVisXL_V5.safetensors",
            now: "juggernautXL.safetensors",
            slot_kind: "checkpoint",
          },
        ],
      });
      readModelSwap.mockResolvedValue({
        replacements: [{ id: 8, filename: "dreamshaperXL.safetensors" }],
        replacements_narrowed: true,
      });
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });

      expect(wrapper.vm.checkpointValue).toBe("juggernautXL.safetensors");
      expect(readModelSwap).toHaveBeenCalledWith(KEY, {
        replacing: "juggernautXL.safetensors",
        slotKind: "checkpoint",
      });
      expect(wrapper.vm.checkpointOptions).toEqual([
        {
          value: "juggernautXL.safetensors",
          label: "juggernautXL.safetensors (in place of RealVisXL_V5.safetensors)",
        },
        { value: "dreamshaperXL.safetensors", label: "dreamshaperXL.safetensors" },
      ]);
      expect(wrapper.vm.checkpointFixNote).toContain(
        "RealVisXL_V5.safetensors is not on this ComfyUI, so this workflow loads juggernautXL.safetensors in its place",
      );
      await wrapper.vm.submit();
      await flushPromises();
      expect(runWorkflowCard.mock.calls[0][0].models).toEqual([
        { address, filename: "juggernautXL.safetensors" },
      ]);
    });

    it("asks nothing for a missing file that is not a base model", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing("loras"));
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      expect(readModelSwap).not.toHaveBeenCalled();
      expect(wrapper.find("[data-testid='rund-checkpoint-missing']").exists()).toBe(false);
    });
  });

  it("lists two different checkpoints read-only and runs them as stored", async () => {
    // A Wan 2.2 high + low pair: which loader holds which file is not known
    // for sure, so no box offers to swap one, and no `models` override goes.
    getWorkflowCard.mockResolvedValue({
      card: card({
        default_recipe: {
          models: [
            { address: "core:a/unet_name", kind: "checkpoint", filename: "wan_high.safetensors" },
            { address: "core:b/unet_name", kind: "checkpoint", filename: "wan_low.safetensors" },
          ],
          loras: [],
          values: [],
          stages: {},
        },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(wrapper.find("[data-testid='rund-checkpoints']").text()).toBe(
      "wan_high.safetensors + wan_low.safetensors",
    );
    expect(wrapper.find("input[aria-label='Checkpoint']").exists()).toBe(false);
    await wrapper.vm.submit();
    await flushPromises();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.models ?? []).toEqual([]);
    expect(sentValue(body, "unet_name")).toBeUndefined();
  });

  it("keeps the checkpoint editable when one slot names no file", async () => {
    // A null filename is not a second file: the row stays one editable box.
    getWorkflowCard.mockResolvedValue({
      card: card({
        default_recipe: {
          models: [
            { address: "core:a/unet_name", kind: "checkpoint", filename: "wan_low.safetensors" },
            { address: "core:b/unet_name", kind: "checkpoint", filename: null },
          ],
          loras: [],
          values: [],
          stages: {},
        },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(wrapper.find("[data-testid='rund-checkpoints']").exists()).toBe(false);
    expect(wrapper.vm.checkpointFiles).toBeNull();
  });

  it("swaps one file on every loader that loads it", async () => {
    const models = ["core:a/unet_name", "core:b/unet_name"].map((address) => ({
      address,
      kind: "checkpoint",
      filename: "wan_low.safetensors",
    }));
    getWorkflowCard.mockResolvedValue({
      card: card({ default_recipe: { models, loras: [], values: [], stages: {} } }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    wrapper.vm.setCheckpoint("other.safetensors");
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].models).toEqual(
      models.map(({ address }) => ({ address, filename: "other.safetensors" })),
    );
  });

  it("offers seed variance as a stage, named as the rail names it", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({
        specials: ["seed_variance"],
        default_recipe: { models: [], loras: [], values: [], stages: { seed_variance: true } },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(wrapper.find("fieldset").findAll("label").map((l) => l.text())).toEqual([
      "Seed variance",
    ]);
    await wrapper.vm.setStage("seed_variance", false);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].skip_stages).toEqual(["seed_variance"]);
  });

  it("says which kind of upscale the stage is, beside its checkbox", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({
        specials: ["upscale", "intermediate_save"],
        default_recipe: {
          models: [],
          loras: [],
          values: [],
          stages: { upscale: true, intermediate_save: true },
          stage_details: { upscale: "Ultimate SD Upscale (4x-UltraSharp)" },
        },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const labels = wrapper
      .find("fieldset")
      .findAll("label")
      .map((l) => l.text().replace(/\s+/g, " "));
    expect(labels).toEqual([
      "Upscale · Ultimate SD Upscale (4x-UltraSharp)",
      "Intermediate save",
    ]);
    await wrapper.vm.setStage("intermediate_save", false);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].skip_stages).toEqual(["intermediate_save"]);
  });

  it("sends the stages the owner turned off as skip_stages (#1623)", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({
        specials: ["upscale", "face_detailer"],
        default_recipe: {
          models: [],
          loras: [],
          values: [],
          stages: { upscale: true, face_detailer: true },
        },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    // One "Stages" group, each row named by the stage alone.
    const fieldset = wrapper.find("fieldset");
    expect(fieldset.find("legend").text()).toBe("Stages");
    expect(fieldset.findAll("label").map((l) => l.text())).toEqual([
      "Upscale",
      "Face detailer",
    ]);
    await wrapper.vm.setStage("face_detailer", false);
    await flushPromises();
    // Asked again, so a stage that cannot come out is refused before Run.
    expect(preflightWorkflowRun.mock.lastCall[0].skip_stages).toEqual([
      "face_detailer",
    ]);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].skip_stages).toEqual([
      "face_detailer",
    ]);
  });

  it("offers no row for a stage the default recipe runs without", async () => {
    // The server cannot switch a recipe-off stage back on, so a tickable
    // row for it would promise a run it will not make.
    getWorkflowCard.mockResolvedValue({
      card: card({
        specials: ["upscale", "face_detailer"],
        default_recipe: {
          models: [],
          loras: [],
          values: [],
          stages: { upscale: false, face_detailer: true },
        },
      }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const labels = wrapper.findAll("fieldset label").map((l) => l.text());
    expect(labels).toEqual(["Face detailer"]);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("skip_stages");
  });

  it("sends no skip_stages while every stage is on", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({ specials: ["upscale"], default_recipe: { stages: {} } }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("skip_stages");
  });

  it("draws no Stages fieldset when the workflow has no optional stage", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({ specials: [], default_recipe: { stages: {} } }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(wrapper.find("fieldset").exists()).toBe(false);
  });

  it("puts a stage turned off back on from its reset chip", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({ specials: ["upscale"], default_recipe: { stages: {} } }),
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const row = () => wrapper.find("fieldset .rund-check");
    expect(row().findComponent({ name: "RunResetChip" }).exists()).toBe(false);
    await row().find("input").setValue(false);
    await flushPromises();
    const chip = row().find("button");
    expect(chip.attributes("aria-label")).toBe(
      "Put upscale back to on",
    );
    await chip.trigger("click");
    await flushPromises();
    expect(row().find("input").element.checked).toBe(true);
    expect(row().findComponent({ name: "RunResetChip" }).exists()).toBe(false);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("skip_stages");
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
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
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
      prompts: [{ prompt_id: "p1", label: "Cinematic portrait" }],
      pictureIds: [42],
    });
    // Only a popup the Edit tab opened hands it the run to follow.
    expect(useRunDialogStore().editRun).toBe(null);
  });

  // What the Tasks tab calls the run: a clone, else the saved recipe, else
  // the workflow.
  async function labelOf(wrapper) {
    await wrapper.vm.submit();
    await flushPromises();
    return wrapper.emitted("run")[0][0].prompts[0].label;
  }

  /** The run's answer, saying which picture's graph the server ran. */
  function ranFrom(pictureId) {
    runWorkflowCard.mockResolvedValue({
      status: "success",
      prompts: [{ prompt_id: "p1" }],
      groups: [{ workflow_id: KEY, source_picture_id: pictureId }],
    });
  }

  it("calls an untouched run of a picture, with its seed typed in, a clone", async () => {
    // The grid's own entry opens a one-picture run as a "selection".
    const wrapper = await mountRun({ kind: "selection", pictureIds: [42] });
    wrapper.vm.seedMode = "fixed";
    expect(wrapper.vm.seed).toBe("418220931");
    expect(await labelOf(wrapper)).toBe("Clone picture");
  });

  it("names the workflow when the typed seed is not the picture's", async () => {
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "fixed";
    wrapper.vm.seed = "7";
    expect(await labelOf(wrapper)).toBe("Cinematic portrait");
  });

  it("calls a kept seed a clone when the graph run was this picture's", async () => {
    ranFrom(42);
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "keep";
    expect(await labelOf(wrapper)).toBe("Clone picture");
  });

  it("names the workflow when the kept seed is another picture's", async () => {
    // "Keep" leaves the seed of the graph the server ran, and that graph is
    // the workflow's best picture's, not necessarily the one opened.
    ranFrom(7);
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "keep";
    expect(await labelOf(wrapper)).toBe("Cinematic portrait");
  });

  it("names the workflow once the form differs from the picture", async () => {
    ranFrom(42);
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "keep";
    wrapper.vm.prompt = "something else entirely";
    expect(await labelOf(wrapper)).toBe("Cinematic portrait");
  });

  it("names the workflow for a picture another workflow is run on", async () => {
    ranFrom(42);
    const wrapper = await mountRun({
      kind: "selection",
      pictureIds: [42],
      workflowId: OTHER,
      pickWorkflow: true,
    });
    wrapper.vm.seedMode = "keep";
    expect(await labelOf(wrapper)).toBe("Cinematic portrait · detailer");
  });

  it("names the workflow for several pictures, whichever graph ran", async () => {
    ranFrom(42);
    const wrapper = await mountRun({
      kind: "selection",
      pictureIds: [42, 43],
      workflowId: KEY,
    });
    wrapper.vm.seedMode = "keep";
    expect(await labelOf(wrapper)).toBe("Cinematic portrait");
  });

  it("names the saved recipe ahead of its workflow", async () => {
    const wrapper = await mountRun({
      kind: "card",
      workflowId: KEY,
      savedRecipe: { id: 5, workflow_id: KEY, name: "Rainy", source_picture_id: 77 },
    });
    expect(await labelOf(wrapper)).toBe("Rainy");
  });

  it("hands a run opened from the Edit tab to the tab to follow", async () => {
    // The stack as it stood BEFORE the run was queued: read after, a quick
    // output would already be in it and never be told apart as the result.
    const order = [];
    stackMemberIds.mockImplementation(async () => {
      order.push("stack");
      return { members: [], ids: new Set(["42", "41"]) };
    });
    runWorkflowCard.mockImplementation(async () => {
      order.push("run");
      return { prompts: [{ prompt_id: "p1" }], groups: [] };
    });
    const wrapper = await mountRun({
      kind: "selection",
      pictureIds: [42],
      fromEditTab: true,
      stack: true,
    });
    wrapper.vm.prompt = "warmer light";
    await wrapper.vm.$nextTick();
    await wrapper.vm.submit();
    await flushPromises();
    expect(order).toEqual(["stack", "run"]);
    expect(useRunDialogStore().editRun).toEqual(
      expect.objectContaining({
        prompts: [{ prompt_id: "p1" }],
        pictureId: 42,
        instruction: "warmer light",
        beforeIds: new Set(["42", "41"]),
      }),
    );
  });
});

describe("the Edit tab's hand-off when its stack cannot be read", () => {
  it("says the before-run stack is unknown rather than guessing it", async () => {
    stackMemberIds.mockRejectedValueOnce(new Error("offline"));
    const wrapper = await mountRun({
      kind: "edit",
      pictureIds: [42],
      workflowId: KEY,
      fromEditTab: true,
      stack: true,
    });
    await wrapper.vm.submit();
    await flushPromises();
    // Still queued: the stack read is for following the run, not for running it.
    expect(runWorkflowCard).toHaveBeenCalled();
    expect(useRunDialogStore().editRun.beforeIds).toBe(null);
  });
});

// ── Is this look already kept? (#1480) ─────────────────────────────────────
//
// The lightbox's Recipe tab has refused the second identical recipe since F6
// and this popup had no match state at all, so the duplicate it refuses was
// one press away from here.
describe("Save as recipe, when the look is already kept", () => {
  /** The saved row the default picture recipe's look matches. */
  const KEPT = {
    id: 12,
    name: "Rainy tram platform",
    // Trimmed and empty-LoRA'd the way `utils/recipeKey.js` keys both sides.
    prompt: "  a rainy tram platform  ",
    loras: [],
  };

  function footer(wrapper, text) {
    return wrapper.findAll("button").find((b) => b.text().trim() === text);
  }

  it("offers the save when nothing on the card keeps this look", async () => {
    listSavedRecipes.mockResolvedValue([
      { id: 1, name: "Something else", prompt: "a different prompt", loras: [] },
    ]);
    const wrapper = await mountRun();
    expect(listSavedRecipes).toHaveBeenCalledWith(KEY);
    expect(footer(wrapper, "Save as recipe")).toBeTruthy();
    expect(footer(wrapper, "Saved")).toBeFalsy();
  });

  it("says Saved, reachably and inertly, and will not open the dialog", async () => {
    listSavedRecipes.mockResolvedValue([KEPT]);
    const wrapper = await mountRun();

    const saved = footer(wrapper, "Saved");
    expect(saved).toBeTruthy();
    // `aria-disabled`, never `disabled`: the sentence saying why has to stay
    // reachable by a keyboard reader.
    expect(saved.attributes("aria-disabled")).toBe("true");
    expect(saved.attributes("disabled")).toBeUndefined();
    // The sentence itself, not merely somewhere on screen: `aria-describedby`
    // pointing at the wrong note is the failure this catches.
    const described = wrapper.get(`#${saved.attributes("aria-describedby")}`);
    expect(described.text()).toBe("Already kept as “Rainy tram platform”.");
    // And it is status, not a run refusal.
    expect(described.classes()).not.toContain("rund-note--bad");

    await saved.trigger("click");
    await flushPromises();
    expect(wrapper.vm.saveOpen).toBe(false);
  });

  it("offers the save again the moment the look is edited", async () => {
    listSavedRecipes.mockResolvedValue([KEPT]);
    const wrapper = await mountRun();
    expect(footer(wrapper, "Saved")).toBeTruthy();

    wrapper.vm.prompt = "a rainy tram platform at dusk";
    await wrapper.vm.$nextTick();
    expect(footer(wrapper, "Saved")).toBeFalsy();
    expect(footer(wrapper, "Save as recipe")).toBeTruthy();
  });

  it("offers the save for a variation the kept recipe does not hold", async () => {
    // **The look's key is prompt and LoRA names, and a recipe is more than
    // its look.** Keyed on the look alone, an inert Saved refuses to keep a
    // recipe that differs from the saved one in every parameter it carries -
    // which is not a duplicate, and on the one surface that can edit those
    // parameters it left the variation unsaveable.
    listSavedRecipes.mockResolvedValue([KEPT]);
    const wrapper = await mountRun();
    expect(footer(wrapper, "Saved")).toBeTruthy();

    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 30);
    await wrapper.vm.$nextTick();

    expect(footer(wrapper, "Saved")).toBeFalsy();
    expect(footer(wrapper, "Save as recipe")).toBeTruthy();
  });

  it("stays Saved when the kept recipe holds that very override", async () => {
    // The other direction, so the check above cannot pass by never matching.
    listSavedRecipes.mockResolvedValue([
      { ...KEPT, overrides: { "KSampler/steps": 30 } },
    ]);
    const wrapper = await mountRun();
    // Untouched, the form holds no overrides, so the kept row's one is a
    // difference and the save is offered.
    expect(footer(wrapper, "Save as recipe")).toBeTruthy();

    const steps = wrapper.vm.scalarFields.find((f) => f.input_name === "steps");
    wrapper.vm.setValue(steps, 30);
    await wrapper.vm.$nextTick();
    expect(footer(wrapper, "Saved")).toBeTruthy();
  });

  it("offers the save when the kept recipe keeps a seed", async () => {
    // A save leaves the seed off unless the owner ticks it, so the row this
    // would write is not the row that exists.
    listSavedRecipes.mockResolvedValue([
      { ...KEPT, seed: "418220931", keep_seed: true },
    ]);
    const wrapper = await mountRun();
    expect(footer(wrapper, "Save as recipe")).toBeTruthy();
  });
});

// ── The pictures a workflow takes (#1457) ──────────────────────────────────
//
// The server decides how each picture input is filled and says so per input
// (`fill`); the popup draws that and never re-derives it. What it owns: that a
// pin whose picture has gone is an empty slot and not a refusal notice, that a
// picked picture reaches the body, that the whole-set PUT is written from the
// set the pre-flight read (a row left out is a row deleted), and that the stack
// box defaults from whether a selected picture is actually fed in.
describe("the pictures a workflow takes", () => {
  const SUBJECT = "a".repeat(63) + "1";
  const REFERENCE = "a".repeat(63) + "2";

  function input(slot, overrides = {}) {
    return {
      slot_label: slot,
      input_name: "image",
      title: slot === SUBJECT ? "Subject" : "Reference",
      mode: "picker",
      pixel_sha: null,
      picture_id: null,
      picture_missing: false,
      fill: null,
      ...overrides,
    };
  }

  function answer(inputs, reasons = []) {
    return {
      ok: !reasons.length,
      runs: 3,
      groups: [{ workflow_id: KEY, reasons, picture_inputs: inputs }],
    };
  }

  const UNFILLED = {
    code: "picture_input_unfilled",
    inputs: [{ slot_label: REFERENCE, input_name: "image", title: "Reference" }],
  };

  it("draws the selection where the server fills it, and ticks the stack box", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([
        input(SUBJECT, { fill: "selection" }),
        input(REFERENCE, { mode: "fixed", pixel_sha: "f".repeat(64), picture_id: 7, fill: "fixed" }),
      ]),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1, 2, 3], workflowId: KEY });

    const rows = wrapper.findAll(".rund-in");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("Your selection: 3 pictures, one run each");
    expect(rows[1].text()).toContain("Kept for every run of this workflow");
    expect(wrapper.find(".rund-box").element.checked).toBe(true);
    expect(wrapper.vm.canRun).toBe(true);
  });

  it("leaves the stack box unticked when no selected picture is fed in", async () => {
    preflightWorkflowRun.mockResolvedValue(answer([input(SUBJECT, { fill: "graph" })]));
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1] });
    expect(wrapper.find(".rund-box").element.checked).toBe(false);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].stack).toBe(false);
  });

  it("starts from the caller's stack choice, explicit false included", async () => {
    // The Edit tab already showed this checkbox; the popup must not re-tick it.
    preflightWorkflowRun.mockResolvedValue(answer([input(SUBJECT, { fill: "selection" })]));
    const wrapper = await mountRun({
      kind: "edit",
      pictureIds: [1],
      workflowId: KEY,
      stack: false,
    });
    expect(wrapper.find(".rund-box").element.checked).toBe(false);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].stack).toBe(false);
  });

  it("sends the stack choice the owner made over the default", async () => {
    preflightWorkflowRun.mockResolvedValue(answer([input(SUBJECT, { fill: "selection" })]));
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1, 2], workflowId: KEY });
    await wrapper.find(".rund-box").setValue(false);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].stack).toBe(false);
  });

  it("shows a pin whose picture has gone as an empty slot, not a refusal", async () => {
    // Decision 7: "no picture yet", in the words of the slot, with the fix in
    // the row. A second copy under the form as a notice would read as an error.
    preflightWorkflowRun.mockResolvedValue(
      answer(
        [
          input(SUBJECT, { fill: "selection" }),
          input(REFERENCE, { mode: "fixed", pixel_sha: "f".repeat(64), picture_missing: true }),
        ],
        [UNFILLED],
      ),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1] });

    const gone = wrapper.findAll(".rund-in")[1];
    expect(gone.text()).toContain("The picture you kept here is gone. Choose another.");
    expect(gone.find(".rund-in-tile--empty").exists()).toBe(true);
    expect(wrapper.vm.runNotes).toEqual([]);
    expect(wrapper.vm.canRun).toBe(false);
    expect(wrapper.vm.runBlocker).toBe("Choose a picture for Reference first.");
  });

  it("sends only the picture picked for this run, and asks again", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([input(SUBJECT, { fill: "selection" }), input(REFERENCE)], [UNFILLED]),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1] });
    expect(preflightWorkflowRun.mock.calls[0][0].inputs).toEqual([]);

    wrapper.vm.pickerFor = wrapper.vm.pictureInputs[1];
    await wrapper.vm.onPicked({ id: 99 });
    await flushPromises();

    expect(preflightWorkflowRun).toHaveBeenCalledTimes(2);
    expect(preflightWorkflowRun.mock.calls[1][0].inputs).toEqual([
      { slot_label: REFERENCE, input_name: "image", picture_id: 99 },
    ]);
    expect(wrapper.vm.pickerFor).toBe(null);
  });

  it("pins by writing the whole set it read, every other row unchanged", async () => {
    // The PUT replaces the card's whole set, so a row the popup left out would
    // be a pin deleted. The other pin goes back by the content it was stored
    // with; only the addressed row changes.
    const otherPin = "e".repeat(64);
    preflightWorkflowRun.mockResolvedValue(
      answer([
        input(SUBJECT, { fill: "request", picture_id: 99 }),
        input(REFERENCE, { mode: "fixed", pixel_sha: otherPin, picture_id: 7, fill: "fixed" }),
      ]),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });

    await wrapper.vm.togglePin(wrapper.vm.pictureInputs[0]);
    await flushPromises();

    expect(setWorkflowInputs).toHaveBeenCalledWith(KEY, [
      { slot_label: SUBJECT, input_name: "image", mode: "fixed", pixel_sha: null, picture_id: 99 },
      { slot_label: REFERENCE, input_name: "image", mode: "fixed", pixel_sha: otherPin },
    ]);
  });

  it("keeps an unpinned picture for this run rather than emptying the slot", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([input(REFERENCE, { mode: "fixed", pixel_sha: "e".repeat(64), picture_id: 7, fill: "fixed" })]),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });

    await wrapper.vm.togglePin(wrapper.vm.pictureInputs[0]);
    await flushPromises();

    expect(setWorkflowInputs.mock.calls[0][1][0].mode).toBe("picker");
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].inputs).toEqual([
      { slot_label: REFERENCE, input_name: "image", picture_id: 7 },
    ]);
  });

  it("moves the selection by making the old Selection a picker", async () => {
    // A card has at most one Selection, so moving it is one write of the set
    // with the old one demoted - never two Selections in one PUT.
    preflightWorkflowRun.mockResolvedValue(
      answer([input(SUBJECT, { mode: "selection", fill: "selection" }), input(REFERENCE, { fill: "graph" })]),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1, 2], workflowId: KEY });

    await wrapper.vm.useSelectionHere(wrapper.vm.pictureInputs[1]);
    await flushPromises();

    expect(setWorkflowInputs.mock.calls[0][1].map((row) => row.mode)).toEqual([
      "picker",
      "selection",
    ]);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(2);
  });

  it("says so, and keeps the form, when the setup cannot be written", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([input(SUBJECT, { fill: "request", picture_id: 99 })]),
    );
    setWorkflowInputs.mockRejectedValue({
      response: { status: 400, data: { detail: "Picture 99 is not a kept picture of this library, so it cannot be pinned." } },
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });

    await wrapper.vm.togglePin(wrapper.vm.pictureInputs[0]);
    await flushPromises();

    expect(wrapper.vm.inputsError).toContain("not a kept picture");
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
  });

  it("will not pin again until the last pin's re-read has landed (#1501 review)", async () => {
    // Released early, the second PUT was built from rows that still said the
    // first input was a picker, and silently unpinned it.
    const rows = [
      input(SUBJECT, { fill: "request", picture_id: 99 }),
      input(REFERENCE, { fill: "request", picture_id: 98 }),
    ];
    preflightWorkflowRun.mockResolvedValue(answer(rows));
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });

    let release;
    preflightWorkflowRun.mockImplementationOnce(
      () => new Promise((resolve) => (release = () => resolve(answer(rows)))),
    );
    const first = wrapper.vm.togglePin(wrapper.vm.pictureInputs[0]);
    await flushPromises();
    await wrapper.vm.togglePin(wrapper.vm.pictureInputs[1]);
    expect(setWorkflowInputs).toHaveBeenCalledTimes(1);

    release();
    await first;
    await flushPromises();
    await wrapper.vm.togglePin(wrapper.vm.pictureInputs[1]);
    expect(setWorkflowInputs).toHaveBeenCalledTimes(2);
  });

  it("writes nothing under a stack member whose inputs it has not read yet", async () => {
    // The key moves at once on a switch; the rows are the old card's until
    // the new pre-flight lands. A PUT then replaced the new card's setup.
    preflightWorkflowRun.mockResolvedValue(
      answer([input(SUBJECT, { fill: "request", picture_id: 99 })]),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const oldRow = wrapper.vm.pictureInputs[0];

    preflightWorkflowRun.mockImplementation(() => new Promise(() => {}));
    wrapper.vm.workflowId = OTHER;
    await flushPromises();
    expect(wrapper.vm.pictureInputs).toEqual([]);

    await wrapper.vm.togglePin(oldRow);
    await wrapper.vm.writeSetup([]);
    expect(setWorkflowInputs).not.toHaveBeenCalled();
  });

  it("forgets the rows when a pre-flight fails, so none is written from them", async () => {
    preflightWorkflowRun.mockResolvedValueOnce(
      answer([input(SUBJECT, { fill: "request", picture_id: 99 })]),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const row = wrapper.vm.pictureInputs[0];

    preflightWorkflowRun.mockRejectedValue({
      response: { status: 404, data: { detail: "Picture 99 is not a kept picture." } },
    });
    await wrapper.vm.runPreflight();
    expect(wrapper.vm.pictureInputs).toEqual([]);

    await wrapper.vm.togglePin(row);
    expect(setWorkflowInputs).not.toHaveBeenCalled();
  });

  it("does not offer to stack a selection nothing reads", async () => {
    // Every output would join the FIRST picture's stack, which is not "the
    // ones they came from".
    preflightWorkflowRun.mockResolvedValue(answer([input(SUBJECT, { fill: "graph" })]));
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1, 2], workflowId: KEY });
    expect(wrapper.find(".rund-box").exists()).toBe(false);
  });

  it("shows the slot a refused run found empty, and says to look there", async () => {
    // Pre-flight said filled; by the run the pin's picture was binned.
    preflightWorkflowRun.mockResolvedValue(
      answer([input(REFERENCE, { mode: "fixed", pixel_sha: "e".repeat(64), picture_id: 7, fill: "fixed" })]),
    );
    runWorkflowCard.mockResolvedValue({
      status: "refused",
      prompts: [],
      groups: [
        {
          workflow_id: KEY,
          reasons: [UNFILLED],
          picture_inputs: [
            input(REFERENCE, { mode: "fixed", pixel_sha: "e".repeat(64), picture_missing: true }),
          ],
        },
      ],
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await wrapper.vm.submit();
    await flushPromises();

    expect(wrapper.vm.pictureInputs[0].picture_missing).toBe(true);
    expect(wrapper.vm.submitError).toBe(
      "Nothing was queued: a picture above still needs choosing.",
    );
  });

  it("keeps the pin toggle's name fixed and its state in aria-pressed", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([input(REFERENCE, { mode: "fixed", pixel_sha: "e".repeat(64), picture_id: 7, fill: "fixed" })]),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const pin = wrapper.findComponent({ name: "AppBarButton" });
    expect(pin.attributes("tooltip")).toBe("Keep this picture for Reference on every run");
    expect(pin.attributes("aria-pressed")).toBe("true");
    expect(pin.attributes("aria-disabled")).toBeUndefined();
  });

  it("numbers two inputs that carry the same title", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([
        input(SUBJECT, { title: "Load Image", fill: "selection" }),
        input(REFERENCE, { title: "Load Image", fill: "graph" }),
      ]),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1] });
    expect(wrapper.vm.pictureInputs.map(wrapper.vm.inputTitle)).toEqual([
      "Load Image 1",
      "Load Image 2",
    ]);
  });
});


// "Create with LoRA…" from a person's or a set's menu: the regular Run popup,
// narrowed to the workflows that fit the LoRA attached to it, with that LoRA
// added in a loader of its own and the results filed back to it.
describe("Create with LoRA", () => {
  const KREA = `auto:${"c".repeat(64)}`;
  const SDXL = `auto:${"d".repeat(64)}`;
  const UNREAD = `auto:${"e".repeat(64)}`;
  const PERSON_LORA = {
    sha256: "k".repeat(64),
    filename: "example-subject-krea2.safetensors",
    base_model: "Krea 2",
    base_model_family: "krea2",
  };
  const model = (family) => [
    { kind: "unet", name: "model", base_model_family: family },
  ];
  const fromPerson = {
    kind: "card",
    pickWorkflow: true,
    emptyPrompt: true,
    name: "Example",
    lora: { entityType: "character", entityId: 3, name: "Example" },
  };

  beforeEach(() => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        { id: SDXL, name: "SDXL portrait", type: "txt2img", models: model("sdxl") },
        { id: UNREAD, name: "Unread", type: "txt2img", models: [] },
        { id: KREA, name: "Krea portrait", type: "txt2img", models: model("krea2") },
      ],
    });
    listAdapters.mockImplementation(async ({ fileKind, characterId, setId } = {}) =>
      (characterId || setId) && fileKind === "adapter" ? [PERSON_LORA] : [],
    );
    getWorkflowCard.mockImplementation(async (key) => ({
      card: card({ id: key, name: key }),
    }));
  });

  it("narrows the picker to the LoRA's base model, unknowns after", async () => {
    const wrapper = await mountRun(fromPerson);
    expect(listAdapters).toHaveBeenCalledWith({ fileKind: "adapter", characterId: 3 });
    expect(wrapper.vm.workflowOptions).toEqual([
      { value: KREA, label: "Krea portrait" },
      { value: UNREAD, label: "Unread (base model not known)" },
    ]);
    expect(wrapper.text()).toContain("Not listed: 1 for another base model.");
    // It opens on the best fit rather than on an empty picker.
    expect(getWorkflowCard).toHaveBeenCalledWith(KREA);
  });

  it("adds the person's LoRA in a loader of its own and files to them", async () => {
    const wrapper = await mountRun(fromPerson);
    await wrapper.vm.submit();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe(KREA);
    expect(body.add_loras).toEqual([
      { sha256: PERSON_LORA.sha256, strength_model: 1 },
    ]);
    // Nothing the graph loads is addressed or replaced.
    expect(body.loras).toEqual([]);
    expect(body.destination).toEqual({
      set_id: null,
      project_id: null,
      character_id: 3,
    });
    expect(wrapper.text()).toContain("Example's reference pictures");
  });

  it("draws the person chosen and fixed, with no way to take their LoRA off", async () => {
    const wrapper = await mountRun(fromPerson);
    const fixed = wrapper.find('[data-testid="person-fixed"]');
    expect(fixed.text()).toBe("Example");
    // A statement, not a control: nobody else to pick, and no "No one".
    expect(wrapper.find('[role="radio"]').exists()).toBe(false);
    expect(wrapper.vm.addedLoras[0].person).toBe(true);
    expect(wrapper.findComponent({ name: "AppBarButton" }).exists()).toBe(false);
  });

  it("keeps a set's LoRA removable: only a person is fixed", async () => {
    const wrapper = await mountRun({
      ...fromPerson,
      name: "Beach",
      lora: { entityType: "set", entityId: 5, name: "Beach" },
    });
    expect(wrapper.find('[data-testid="rund-person"]').exists()).toBe(false);
    expect(wrapper.findComponent({ name: "AppBarButton" }).exists()).toBe(true);
  });

  it("files a set's run into that set", async () => {
    const wrapper = await mountRun({
      ...fromPerson,
      name: "Beach",
      lora: { entityType: "set", entityId: 5, name: "Beach" },
    });
    expect(listAdapters).toHaveBeenCalledWith({ fileKind: "adapter", setId: 5 });
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].destination.set_id).toBe(5);
  });

  it("never narrows by an attached LoRA other than the one on the run", async () => {
    const wrapper = await mountRun(fromPerson);
    wrapper.vm.addedLoras[0].sha256 = "z".repeat(64);
    await flushPromises();
    expect(wrapper.vm.workflowOptions.map((o) => o.value)).toEqual([
      SDXL,
      UNREAD,
      KREA,
    ]);
    expect(wrapper.text()).toContain("This LoRA is not on the Models shelf");
  });

  it("stops narrowing once the added LoRA is taken off the run", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        { id: SDXL, name: "SDXL portrait", type: "txt2img", models: model("sdxl") },
        { id: KREA, name: "Krea portrait", type: "txt2img", models: model("krea2") },
      ],
    });
    const wrapper = await mountRun(fromPerson);
    expect(wrapper.vm.workflowOptions.map((o) => o.value)).toEqual([KREA]);
    wrapper.vm.removeAddedLora(0);
    await flushPromises();
    expect(wrapper.vm.workflowOptions.map((o) => o.value)).toContain(SDXL);
    expect(wrapper.text()).not.toContain("Not listed:");
  });

  it("says so, and narrows nothing, when no LoRA is attached", async () => {
    listAdapters.mockResolvedValue([]);
    const wrapper = await mountRun(fromPerson);
    expect(wrapper.text()).toContain("No LoRA is attached to Example.");
    expect(wrapper.vm.workflowOptions.map((o) => o.value)).toEqual([
      SDXL,
      UNREAD,
      KREA,
    ]);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].add_loras).toBeUndefined();
  });
});

describe("the person a workflow run alone is of", () => {
  const of = (id) => [{ entity_type: "character", entity_id: id }];
  const MIRA = {
    sha256: "m".repeat(64),
    filename: "mira-sdxl.safetensors",
    base_model_family: "sdxl",
    attachments: of(7),
  };
  const KREA_ONLY = {
    sha256: "n".repeat(64),
    filename: "noor-krea2.safetensors",
    base_model_family: "krea2",
    attachments: of(8),
  };
  const sdxlCard = (overrides = {}) =>
    card({
      models: [{ kind: "checkpoint", name: "realvis", base_model_family: "sdxl" }],
      ...overrides,
    });
  const fromCard = { kind: "card", workflowId: KEY };
  const tile = (wrapper, id) => wrapper.find(`[data-person="${id}"]`);

  beforeEach(() => {
    listCharacters.mockResolvedValue([
      { id: 7, name: "Mira" },
      { id: 8, name: "Noor" },
    ]);
    listAdapters.mockImplementation(async ({ fileKind } = {}) =>
      fileKind ? [] : [MIRA, KREA_ONLY],
    );
    getWorkflowCard.mockImplementation(async (key) => ({
      card:
        key === OTHER
          ? card({
              id: OTHER,
              models: [{ kind: "unet", name: "krea", base_model_family: "krea2" }],
            })
          : sdxlCard(),
    }));
  });

  it("offers the people whose LoRA works with the checkpoint, and no one first", async () => {
    const wrapper = await mountRun(fromCard);
    expect(wrapper.find('[data-person="null"]').attributes("aria-checked")).toBe("true");
    expect(tile(wrapper, 7).text()).toBe("Mira");
    // The face is asked of the API, not of the page's own origin.
    expect(tile(wrapper, 7).find("img").attributes("src")).toBe(
      "/api/v1/characters/7/thumbnail",
    );
    // Noor's LoRA is for another base model: said, not offered.
    expect(tile(wrapper, 8).exists()).toBe(false);
    expect(wrapper.text()).toContain(
      "Not listed: 1 whose LoRA is for another base model.",
    );
    // Nobody picked: the run says nothing about a person.
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].add_loras).toBeUndefined();
  });

  it("adds the picked person's LoRA, and takes it off again for no one", async () => {
    const wrapper = await mountRun(fromCard);
    await tile(wrapper, 7).trigger("click");
    expect(tile(wrapper, 7).attributes("aria-checked")).toBe("true");
    await wrapper.vm.submit();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe(KEY);
    expect(body.add_loras).toEqual([{ sha256: MIRA.sha256, strength_model: 1 }]);

    await wrapper.find('[data-person="null"]').trigger("click");
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[1][0].add_loras).toBeUndefined();
  });

  it("keeps the picked person's LoRA in a recipe saved from the run", async () => {
    const wrapper = await mountRun(fromCard);
    expect(wrapper.vm.recipeLoras).toEqual([]);
    await tile(wrapper, 7).trigger("click");
    expect(wrapper.vm.recipeLoras).toEqual([
      { filename: MIRA.filename, sha256: MIRA.sha256, strength: 1 },
    ]);
  });

  it("unpicks the person when the run is told to go without LoRAs", async () => {
    const wrapper = await mountRun(fromCard);
    await tile(wrapper, 7).trigger("click");
    await wrapper.vm.dropLoras();
    expect(wrapper.find('[data-person="null"]').attributes("aria-checked")).toBe("true");
    expect(wrapper.vm.addedLoras).toEqual([]);
  });

  it("takes a pick off when the workflow changes to another base model", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        { id: KEY, name: "SDXL" },
        { id: OTHER, name: "Krea" },
      ],
    });
    const wrapper = await mountRun({ ...fromCard, pickWorkflow: true });
    await tile(wrapper, 7).trigger("click");
    wrapper.vm.workflowId = OTHER;
    await flushPromises();
    // Mira's LoRA is SDXL; on the Krea workflow Noor is the one offered.
    expect(tile(wrapper, 7).exists()).toBe(false);
    expect(tile(wrapper, 8).exists()).toBe(true);
    expect(wrapper.vm.addedLoras).toEqual([]);
  });

  it("is not offered on a recipe's run, which keeps its own LoRAs", async () => {
    const wrapper = await mountRun();
    expect(wrapper.find('[data-testid="rund-person"]').exists()).toBe(false);
  });

  it("is absent where nobody has a LoRA", async () => {
    listAdapters.mockResolvedValue([]);
    const wrapper = await mountRun(fromCard);
    expect(wrapper.find('[data-testid="rund-person"]').exists()).toBe(false);
  });
});

describe("Add LoRA", () => {
  it("adds a loader of its own when the graph has no free slot", async () => {
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    const add = wrapper.findAll("button").find((b) => b.text() === "Add LoRA");
    expect(add).toBeTruthy();
    await add.trigger("click");
    // Nothing chosen yet: blocked with a reason rather than sent empty.
    expect(wrapper.vm.runBlocker).toBe(
      "Choose a LoRA for each added row, or remove it.",
    );
    // A row with nothing picked is no LoRA to save as part of the look.
    expect(wrapper.vm.recipeLoras).toEqual([]);
    wrapper.vm.addedLoras[0].sha256 = "s".repeat(64);
    await flushPromises();
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].add_loras).toEqual([
      { sha256: "s".repeat(64), strength_model: 1 },
    ]);
  });
});

describe("Save fixed workflow", () => {
  const saveButton = (wrapper) =>
    wrapper.findAll("button").find((b) => b.text() === "Save fixed workflow");
  const swapping = {
    ok: true,
    runs: 1,
    groups: [
      {
        reasons: [],
        swapped_loaders: [
          {
            node_id: "7",
            class_type: "LoraLoader",
            file: "loras/example-subject.safetensors",
            sha256: "s".repeat(64),
          },
        ],
      },
    ],
  };

  it("is not offered when the run changes no node", async () => {
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(saveButton(wrapper)).toBeUndefined();
  });

  it("is offered when a loader is swapped, and says why", async () => {
    preflightWorkflowRun.mockResolvedValue(swapping);
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    expect(saveButton(wrapper)).toBeTruthy();
    expect(wrapper.vm.bypassed.map((note) => note.code)).toContain(
      "loras_fetched",
    );
  });

  it("saves a copy and moves the popup onto it", async () => {
    preflightWorkflowRun.mockResolvedValueOnce(swapping);
    saveFixedWorkflow.mockResolvedValue({
      name: "Cinematic portrait (fixed).json",
      workflow_id: OTHER,
      changes: [
        "Loads example-subject through the ComfyUI-PixlStash LoRA loader.",
      ],
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await saveButton(wrapper).trigger("click");
    await flushPromises();
    expect(saveFixedWorkflow).toHaveBeenCalledWith(KEY);
    expect(getWorkflowCard).toHaveBeenLastCalledWith(OTHER);
    expect(wrapper.text()).toContain("Saved as Cinematic portrait (fixed).");
    // The copy loads the LoRA as saved, so nothing is left to fix.
    expect(saveButton(wrapper)).toBeUndefined();
  });

  it("does not count a swap for a LoRA the form asked for", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ...swapping,
      groups: [
        {
          ...swapping.groups[0],
          swapped_loaders: [{ ...swapping.groups[0].swapped_loaders[0], requested: true }],
        },
      ],
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    // Said, since the run does it; not offered, since the copy would not.
    expect(wrapper.vm.bypassed.map((note) => note.code)).toContain("loras_fetched");
    expect(saveButton(wrapper)).toBeUndefined();
  });

  it("does not move the popup when it moved while the copy was written", async () => {
    preflightWorkflowRun.mockResolvedValueOnce(swapping);
    let finish;
    saveFixedWorkflow.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await saveButton(wrapper).trigger("click");
    wrapper.vm.workflowId = OTHER;
    await flushPromises();
    finish({ name: "Cinematic portrait (fixed).json", workflow_id: "auto:fixed" });
    await flushPromises();
    expect(getWorkflowCard).not.toHaveBeenCalledWith("auto:fixed");
    expect(wrapper.text()).not.toContain("Saved as");
  });

  it("says why when the save is refused", async () => {
    preflightWorkflowRun.mockResolvedValue(swapping);
    saveFixedWorkflow.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: "Nothing in this workflow needs fixing on this ComfyUI.",
        },
      },
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY });
    await saveButton(wrapper).trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Nothing in this workflow needs fixing");
  });
});
