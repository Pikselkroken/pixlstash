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

import { afterEach, describe, it, expect, beforeEach, vi } from "vitest";
import { enableAutoUnmount, mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getWorkflowCard = vi.fn();
const listWorkflowCards = vi.fn();
const preflightWorkflowRun = vi.fn();
const runWorkflowCard = vi.fn();
const getPictureRecipe = vi.fn();
const setWorkflowInputs = vi.fn();
const setWorkflowPins = vi.fn();
const readModelSwap = vi.fn();
const getWorkflowFormInputs = vi.fn();
const getLoraChain = vi.fn();
const getWorkflowChanges = vi.fn();
const saveWorkflow = vi.fn();
const saveWorkflowAsNew = vi.fn();
const openWorkflowRun = vi.fn();
const getPixlstashNode = vi.fn();

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
  setWorkflowPins: (...args) => setWorkflowPins(...args),
  readModelSwap: (...args) => readModelSwap(...args),
  getWorkflowFormInputs: (...args) => getWorkflowFormInputs(...args),
  getLoraChain: (...args) => getLoraChain(...args),
  getWorkflowChanges: (...args) => getWorkflowChanges(...args),
  saveWorkflow: (...args) => saveWorkflow(...args),
  saveWorkflowAsNew: (...args) => saveWorkflowAsNew(...args),
  openWorkflowRun: (...args) => openWorkflowRun(...args),
  workflowCoverUrl: (cover) => (cover?.url ? `/api/v1${cover.url}` : ""),
}));
vi.mock("../../api/comfyui", () => ({
  getPictureRecipe: (...args) => getPictureRecipe(...args),
  getPixlstashNode: (...args) => getPixlstashNode(...args),
}));
const listAdapters = vi.fn();
const fetchWorkflowSets = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));
// A missing model is looked for in the model folders (#1985).
const pickUpModelFiles = vi.fn().mockResolvedValue([]);
vi.mock("../../api/modelFolders", () => ({
  pickUpModelFiles: (...args) => pickUpModelFiles(...args),
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
// The person picker and "Pictures are of" read the people the same way.
const listCharacters = vi.fn();
vi.mock("../../api/characters", () => ({
  listCharacters: (...args) => listCharacters(...args),
  characterThumbnailUrl: (id) => `/api/v1/characters/${id}/thumbnail`,
}));
/** "Pictures are of", row by row: `[name, what links them]`. */
const ofRows = (wrapper) =>
  wrapper
    .findAll('[data-testid="rund-of"] li')
    .map((row) => [
      row.find(".rund-of-name").text(),
      row.find(".rund-of-why").exists() ? row.find(".rund-of-why").text() : "",
    ]);
/** The × buttons of the LoRA rows, not of "Pictures are of". */
const loraRemoves = (wrapper) =>
  wrapper
    .findAllComponents({ name: "AppBarButton" })
    .filter((button) => !button.element.closest(".rund-of"));
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
import { useFilterStore } from "../../stores/useFilterStore";
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

const OPEN = { open: true, reason: null };

/** `GET /workflows/{id}/changes` for a workflow nothing waits on. */
function changesOf(id, overrides = {}) {
  return {
    workflow_id: id,
    version: 3,
    waiting: null,
    count: 0,
    ran: null,
    save: { open: false, reason: "Nothing to save." },
    save_as_new: OPEN,
    ran_save: null,
    ...overrides,
  };
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
      // The trigger-word buttons sit in a tip's activator slot, which the
      // stubbed Vuetify tooltip would not draw.
      Tooltip: {
        name: "Tooltip",
        props: ["text", "describe"],
        template: "<slot name='activator' :props='{}' />",
      },
      RunReasonNotice: true,
      PicturePicker: true,
      "v-icon": true,
    },
  },
};

async function mountRun(source = { kind: "picture", pictureIds: [42] }) {
  const wrapper = mount(RunDialog, {
    props: { source, context: {} },
    ...globalOpts,
  });
  await flushPromises();
  return wrapper;
}

// Every popup a test mounts is taken down again, watchers and all.
enableAutoUnmount(afterEach);

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  getWorkflowFormInputs.mockResolvedValue({ nodes: [] });
  // A workflow run by itself reads its own loaders and what waits on it.
  // Reset, not only cleared: an answer one test gave must not be the next's.
  readModelSwap.mockReset();
  readModelSwap.mockResolvedValue({ replacements: [] });
  saveWorkflowAsNew.mockReset();
  getLoraChain.mockResolvedValue({ loaders: [], lanes: [] });
  getWorkflowChanges.mockImplementation(async (id) => changesOf(id));
  saveWorkflow.mockResolvedValue({ dry_run: true, save: OPEN, save_as_new: OPEN });
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
    expect(row.text()).toContain("res_2s is not in this ComfyUI");

    wrapper.vm.choiceFixes[0].chosen = "dpmpp_2m";
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].choices).toEqual([
      { node_id: "3", field: "sampler_name", value: "dpmpp_2m" },
    ]);
  });

  it("offers a parameter ComfyUI lists the choices of as a list", async () => {
    const pin = { slot_label: "KSampler", input_name: "sampler_name" };
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [
          ...card().defaults,
          def("Sampler", "sampler_name", "res_2s"),
          def("Start", "start_at_step", 2),
        ],
      }),
      pins: [pin, { slot_label: "KSampler", input_name: "start_at_step" }],
    });
    getWorkflowFormInputs.mockResolvedValue({
      nodes: [{ node_id: "3", inputs: [{ ...pin, options: ["euler", "dpmpp_2m"] }] }],
    });
    const wrapper = await mountRun();
    expect(getWorkflowFormInputs).toHaveBeenCalledWith(KEY);
    // One list, for the sampler; the step count beside it is still typed.
    const lists = wrapper
      .findAllComponents({ name: "AppSelect" })
      .filter((list) => list.attributes("data-testid") === "rund-param-choice");
    expect(lists).toHaveLength(1);
    expect(lists[0].props("label")).toBe("Sampler");
    // The workflow's own value stays listed though ComfyUI does not offer it.
    expect(lists[0].props("options")).toEqual(["res_2s", "euler", "dpmpp_2m"]);
    lists[0].vm.$emit("update:modelValue", "dpmpp_2m");
    await flushPromises();
    const sampler = wrapper.vm.defaults.find(
      (field) => field.input_name === "sampler_name",
    );
    expect(wrapper.vm.runValue(sampler)).toBe("dpmpp_2m");
  });

  it("sets an on-or-off parameter to the value, and names an added one's node", async () => {
    const tiled = {
      ...def("mode.tiled", "mode.tiled", true, "core:up"),
      exposed: true,
      name: "Tiling",
    };
    getWorkflowCard.mockResolvedValue({
      card: card({ defaults: [...card().defaults, tiled] }),
      pins: [{ slot_label: "core:up", input_name: "mode.tiled" }],
    });
    getWorkflowFormInputs.mockResolvedValue({
      nodes: [
        {
          node_id: "8",
          title: "Final upscale",
          inputs: [{ slot_label: "core:up", input_name: "mode.tiled", options: null }],
        },
      ],
    });
    const wrapper = await mountRun();
    const [list] = wrapper
      .findAllComponents({ name: "AppSelect" })
      .filter((entry) => entry.attributes("data-testid") === "rund-param-choice");
    expect(list.props("options")).toEqual(["true", "false"]);
    const label = list.element.closest(".rund-f").querySelector(".rund-l");
    // Named as the owner named it when they added it, its node after.
    expect(label.textContent.replace(/\s+/g, " ").trim()).toBe("Tiling · Final upscale");
    list.vm.$emit("update:modelValue", "false");
    await flushPromises();
    const field = wrapper.vm.defaults.find((entry) => entry.input_name === "mode.tiled");
    // The boolean, never the word: ComfyUI reads the text "false" as on.
    expect(wrapper.vm.runValue(field)).toBe(false);
  });

  it("types every parameter when ComfyUI's choices cannot be read", async () => {
    const pin = { slot_label: "KSampler", input_name: "sampler_name" };
    getWorkflowCard.mockResolvedValue({
      card: card({
        defaults: [...card().defaults, def("Sampler", "sampler_name", "euler")],
      }),
      pins: [pin],
    });
    getWorkflowFormInputs.mockRejectedValue(new Error("down"));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = await mountRun();
    expect(wrapper.find('[data-testid="rund-param-choice"]').exists()).toBe(false);
    expect(wrapper.vm.otherRunFields.map((f) => f.input_name)).toEqual([
      "sampler_name",
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

  it("offers no LoRA this library hides, and still names one the workflow loads", async () => {
    const hidden = "s".repeat(64);
    const shown = "t".repeat(64);
    listAdapters.mockResolvedValue([
      { sha256: hidden, filename: "mira_v2.safetensors", hidden: true },
      { sha256: shown, filename: "loras/film-grain-35mm.safetensors" },
    ]);
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      settings: {},
      lora_slots: [filenameSlot("mira_v2.safetensors")],
    });
    const wrapper = await mountRun();

    // Add LoRA and every other row's list: the hidden one is not a choice.
    expect(wrapper.vm.adapterOptions.map((o) => o.value)).toEqual([shown]);
    // The row that already loads it still reads as that file, not as empty.
    expect(wrapper.vm.loras[0].sha256).toBe(hidden);
    expect(
      wrapper.vm.optionsFor(wrapper.vm.loras[0]).map((o) => o.value),
    ).toEqual([hidden, shown]);
  });

  it("says whose LoRA a recipe runs, and offers a person only with it taken off", async () => {
    listCharacters.mockResolvedValue([
      { id: 7, name: "Mira" },
      { id: 8, name: "Noor" },
    ]);
    listAdapters.mockImplementation(async ({ fileKind } = {}) =>
      fileKind
        ? []
        : [
            {
              sha256: "s".repeat(64),
              filename: "mira_v2.safetensors",
              attachments: [{ entity_type: "character", entity_id: 7 }],
            },
          ],
    );
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      settings: {},
      lora_slots: [filenameSlot("mira_v2.safetensors")],
    });
    const wrapper = await mountRun();
    const add = () =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .find((select) => select.props("label") === "Add someone");
    expect(ofRows(wrapper)).toEqual([["Mira", "by their LoRA"]]);
    expect(add().props("options").map((option) => option.label)).toEqual([
      "Add someone",
      "Noor",
    ]);

    wrapper.vm.loras[0].skipped = true;
    await wrapper.vm.$nextTick();
    expect(ofRows(wrapper)).toEqual([["No one", ""]]);
    expect(add().props("options").map((option) => option.label)).toEqual([
      "Add someone",
      "Mira",
      "Noor",
    ]);
  });

  it("lists everybody whose LoRAs a recipe runs", async () => {
    listCharacters.mockResolvedValue([
      { id: 7, name: "Mira" },
      { id: 8, name: "Noor" },
    ]);
    listAdapters.mockImplementation(async ({ fileKind } = {}) =>
      fileKind
        ? []
        : [
            {
              sha256: "s".repeat(64),
              filename: "mira_v2.safetensors",
              attachments: [
                { entity_type: "character", entity_id: 8 },
                { entity_type: "character", entity_id: 7 },
              ],
            },
          ],
    );
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      settings: {},
      lora_slots: [filenameSlot("mira_v2.safetensors")],
    });
    const wrapper = await mountRun();
    expect(ofRows(wrapper)).toEqual([
      ["Mira", "by their LoRA"],
      ["Noor", "by their LoRA"],
    ]);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].character_ids).toEqual([7, 8]);
  });

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

describe("removing a graph LoRA", () => {
  // The owner must be able to run without a LoRA that does not exist here.
  // One gesture, ×: the row stays, struck, says Removed, and Put back takes
  // it back. What goes on the wire is `skip_loras`, and a removed row is
  // never ALSO an override in `loras`.
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

  const removes = (wrapper) =>
    wrapper
      .findAllComponents({ name: "AppBarButton" })
      .filter((c) => c.attributes("data-focus") === "remove");

  it("offers a × on a graph row, named for the LoRA it removes", async () => {
    const wrapper = await mountTwo();
    expect(removes(wrapper).map((c) => c.props("tooltip"))).toEqual([
      "Remove mira_v2",
      "Remove somebody-elses",
    ]);
    // The words that promised nothing is written are gone.
    const words = wrapper.findAll("button").map((b) => b.text().trim());
    expect(words).not.toContain("Skip");
    expect(wrapper.text()).not.toMatch(/skipped for this run/i);
  });

  it("sends a removed row in skip_loras and not in loras", async () => {
    const wrapper = await mountTwo();
    // An override on the row first, so leaving `loras` alone would be caught.
    wrapper.vm.loras[0].sha256 = "t".repeat(64);
    await wrapper.vm.$nextTick();
    const before = preflightWorkflowRun.mock.calls.length;

    await removes(wrapper)[0].trigger("click");
    await flushPromises();

    expect(wrapper.find(".rund-lora--skipped").text()).toContain("Removed");
    expect(wrapper.vm.loraLive).toBe(
      "film-grain-35mm is removed. Put back is on the same row.",
    );
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

  it("takes a removal back with Put back", async () => {
    const wrapper = await mountTwo();
    await removes(wrapper)[1].trigger("click");
    await flushPromises();
    const back = buttonReading(wrapper, "Put back");
    expect(back.attributes("aria-label")).toBe("Put somebody-elses back");
    await back.trigger("click");
    await flushPromises();

    expect(wrapper.find(".rund-lora--skipped").exists()).toBe(false);
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
      "Not in this ComfyUI. The run leaves this loader out.",
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
    // Something the workflow once made, not the run's input: beside an empty
    // Load Image it was read as the chosen picture.
    expect(wrapper.find(".rund-example").text()).toBe("Example");
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
    expect(wrapper.find(".rund-example").exists()).toBe(true);
  });

  it("shows the picture a recipe run is made from, not the card's cover", async () => {
    // The card's cover is whatever the workflow last made; beside this
    // picture's prompt it reads as a different run altogether.
    getWorkflowCard.mockResolvedValue({
      card: card({ covers: [{ url: "/pictures/thumbnails/812.webp" }] }),
    });
    const wrapper = await mountRun({ kind: "picture", pictureIds: [42] });

    expect(wrapper.vm.coverUrl).toBe(pictureThumbnailUrl(42));
    expect(wrapper.find(".rund-example").exists()).toBe(false);
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
    expect(wrapper.find(".rund-example").exists()).toBe(false);
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
      "Pinned for this workflow 5",
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

  it("says a picture's own recipe is a replay, and an edit or another workflow is not", async () => {
    // The server feeds a replay what the picture was made FROM. Sent for an
    // edit, an edit of an edit would re-edit the original instead.
    const own = await mountRun();
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].replay).toBe(true);
    await own.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].replay).toBe(true);

    // "Make more like these…" on one picture is its own recipe again too.
    await mountRun({ kind: "selection", pictureIds: [42], workflowId: KEY });
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].replay).toBe(true);

    await mountRun({ kind: "edit", pictureIds: [42], workflowId: KEY });
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].replay).toBeUndefined();

    // "Use as input for…", whose workflow the owner picks - the very one that
    // made the picture included: it runs over the picture.
    listWorkflowCards.mockResolvedValue({ cards: [card()] });
    const picked = await mountRun({
      kind: "selection",
      pictureIds: [42],
      workflowId: KEY,
      pickWorkflow: true,
    });
    expect(picked.vm.runsOwnWorkflow).toBe(true);
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].replay).toBeUndefined();

    // Opened on a workflow that did not make the picture.
    await mountRun({ kind: "picture", pictureIds: [42], workflowId: OTHER });
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].replay).toBeUndefined();
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

    it("picks up the file from a model folder and asks again", async () => {
      // Downloaded after its folder was last scanned: found, registered, and
      // the second pre-flight is the one about the shelf that now has it.
      let added;
      pickUpModelFiles
        .mockResolvedValueOnce([{ folder_id: 1, relpath: "realvisXL_v5.safetensors" }])
        .mockReturnValueOnce(new Promise((resolve) => (added = resolve)));
      preflightWorkflowRun
        .mockResolvedValueOnce(missing())
        .mockResolvedValue({ ok: true, runs: 1, groups: [{ reasons: [] }] });
      readModelSwap.mockResolvedValue({ replacements: [], replacements_reason: null });
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      const asked = preflightWorkflowRun.mock.calls.length;

      expect(pickUpModelFiles).toHaveBeenLastCalledWith(["realvisXL_v5.safetensors"], {
        add: true,
      });
      expect(wrapper.find("[data-testid='rund-adding']").text()).toContain(
        "Found a missing model in your model folders",
      );

      added([{ folder_id: 1, relpath: "realvisXL_v5.safetensors", model_id: 3 }]);
      await flushPromises();

      expect(wrapper.find("[data-testid='rund-adding']").exists()).toBe(false);
      expect(preflightWorkflowRun.mock.calls.length).toBe(asked + 1);
      expect(wrapper.vm.reasons).toEqual([]);
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
        replacements_reason: "none_loadable",
      });
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(true);
      expect(wrapper.find("[data-testid='rund-checkpoint-missing']").text()).toContain(
        "Nothing on your model shelf can be loaded by this workflow",
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
      expect(note).toContain("is known to match it, so every one it can load is listed");
      expect(note).not.toContain("base model");
    });

    it("leaves a name typed during the read in its box", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing());
      let answer;
      readModelSwap.mockReturnValue(new Promise((resolve) => (answer = resolve)));
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      // Nothing suggested while the read is out.
      expect(wrapper.vm.checkpointFixNote).toBe(
        "realvisXL_v5.safetensors is not in this ComfyUI.",
      );
      wrapper.vm.setCheckpoint("typed.safetensors");
      answer({ replacements: [{ id: 7, filename: "juggernautXL.safetensors" }] });
      await flushPromises();
      expect(wrapper.vm.checkpointFix).toBeNull();
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(true);
    });

    it("asks again after a read that failed", async () => {
      preflightWorkflowRun.mockResolvedValue(missing());
      readModelSwap.mockRejectedValue(new Error("offline"));
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      expect(wrapper.vm.checkpointFix).toBeNull();
      const failed = readModelSwap.mock.calls.length;
      readModelSwap.mockResolvedValue({ replacements: [], replacements_reason: "none_loadable" });
      await wrapper.vm.runPreflight();
      await flushPromises();
      expect(readModelSwap).toHaveBeenCalledTimes(failed + 1);
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
          label: "juggernautXL (in place of RealVisXL V5)",
        },
        { value: "dreamshaperXL.safetensors", label: "dreamshaperXL" },
      ]);
      expect(wrapper.vm.checkpointFixNote).toContain(
        "RealVisXL_V5.safetensors is not in this ComfyUI, so this workflow loads juggernautXL.safetensors in its place",
      );
      await wrapper.vm.submit();
      await flushPromises();
      expect(runWorkflowCard.mock.calls[0][0].models).toEqual([
        { address, filename: "juggernautXL.safetensors" },
      ]);
    });

    it("offers no replacement for a missing file that is not a base model", async () => {
      preflightWorkflowRun.mockResolvedValueOnce(missing("loras"));
      const wrapper = await mountRun({ kind: "card", workflowId: KEY });
      expect(wrapper.vm.checkpointFix).toBeNull();
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

  it("offers a stage the default recipe runs without, off, and keeps it when ticked", async () => {
    // `keep_stages` switches a recipe-off stage on for a run (#1849), so the
    // row is there, unticked. It is never sent as skipped: the recipe already
    // runs without it, and a stage asked off by name can refuse the run.
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
    expect(labels).toEqual(["Upscale", "Face detailer"]);
    expect(wrapper.findAll("fieldset input").map((box) => box.element.checked)).toEqual([
      false,
      true,
    ]);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("skip_stages");
    expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("keep_stages");

    await wrapper.vm.setStage("upscale", true);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[1][0].keep_stages).toEqual(["upscale"]);
    expect(runWorkflowCard.mock.calls[1][0]).not.toHaveProperty("skip_stages");
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
    // `apply_prompts` leaves the graph alone on `None` and writes on `""`, so an
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

  it("opens a card on the workflow's own prompt, so what will run is visible", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [
        { prompt: { positive_settable: true, positive_text: "the saved prompt" } },
      ],
    });
    const wrapper = await mountRun({ kind: "card", workflowId: KEY, emptyPrompt: true });
    expect(wrapper.vm.prompt).toBe("the saved prompt");
    expect(wrapper.find('[data-testid="rund-prompt-unset"]').exists()).toBe(false);
    // Typed text is the owner's: a later answer does not take it back.
    wrapper.vm.prompt = "typed";
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [{ prompt: { positive_settable: true, positive_text: "another" } }],
    });
    await wrapper.vm.runPreflight();
    await flushPromises();
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].prompt).toBe("typed");
  });

  it("offers no prompt box, and sends none, where the graph has no place for one", async () => {
    // #1832: the picture's own prompt was sent, dropped, and nothing said so.
    preflightWorkflowRun.mockResolvedValue({
      ok: true,
      runs: 1,
      groups: [{ prompt: { positive_settable: false, negative_settable: false } }],
    });
    const wrapper = await mountRun();
    expect(wrapper.find('[data-testid="rund-prompt-unset"]').text()).toContain(
      "no prompt that can be set here",
    );
    expect(
      wrapper
        .findAllComponents({ name: "AppTextarea" })
        .some((c) => c.props("label") === "Prompt"),
    ).toBe(false);
    await wrapper.vm.submit();
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].prompt).toBe(null);
    expect(runWorkflowCard.mock.calls[0][0].negative).toBe(null);
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

  it("emits the prompts so the grid can follow them", async () => {
    const wrapper = await mountRun();
    await wrapper.vm.submit();
    await flushPromises();

    expect(wrapper.emitted("run")[0][0]).toEqual({
      prompts: [{ prompt_id: "p1" }],
      pictureIds: [42],
    });
    // Only a popup the Edit tab opened hands it the run to follow.
    expect(useRunDialogStore().editRun).toBe(null);
  });

  // ComfyUI took the first of a batch and refused the next: the popup still
  // closes on what was queued, and says why the rest was not.
  it("passes on why a batch stopped part-way", async () => {
    runWorkflowCard.mockResolvedValue({
      status: "partial",
      prompts: [{ prompt_id: "p1" }],
      groups: [{ workflow_id: KEY }],
      error: "ComfyUI prompt failed: 400 Value not in list",
    });
    const wrapper = await mountRun();
    await wrapper.vm.submit();
    await flushPromises();
    expect(wrapper.emitted("run")[0][0].error).toBe(
      "ComfyUI prompt failed: 400 Value not in list",
    );
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  // What the Tasks tab calls the run, sent with it: a clone, else the saved
  // recipe, else the workflow. The backend reports it with the run in flight.
  async function labelOf(wrapper) {
    await wrapper.vm.submit();
    await flushPromises();
    return runWorkflowCard.mock.calls[0][0].label;
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

  it("says an untouched run on the picture's own seed adds nothing", async () => {
    const wrapper = await mountRun();
    const note = () => wrapper.find('[data-testid="rund-repeats-picture"]');
    // A new seed is a new picture.
    expect(note().exists()).toBe(false);
    wrapper.vm.seedMode = "keep";
    await flushPromises();
    expect(note().text()).toContain("exact duplicate is not added again");
    // Typed in, it is the same seed; another one is not.
    wrapper.vm.seedMode = "fixed";
    await flushPromises();
    expect(note().exists()).toBe(true);
    wrapper.vm.seed = "7";
    await flushPromises();
    expect(note().exists()).toBe(false);
    // Nor is a changed form the picture again.
    wrapper.vm.seedMode = "keep";
    wrapper.vm.prompt = "something else entirely";
    await flushPromises();
    expect(note().exists()).toBe(false);
  });

  it("leaves an untouched run on a kept seed for the server to name", async () => {
    // "Keep" is this picture's seed on whatever graph the server runs, and
    // only the server knows whether that graph is this picture's too.
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "keep";
    expect(await labelOf(wrapper)).toBe(null);
  });

  it("names the workflow once the form differs from the picture", async () => {
    const wrapper = await mountRun();
    wrapper.vm.seedMode = "keep";
    wrapper.vm.prompt = "something else entirely";
    expect(await labelOf(wrapper)).toBe("Cinematic portrait");
  });

  it("names the workflow for a picture another workflow is run on", async () => {
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
    // The one slot the run cannot go without is the one that glows.
    expect(gone.find(".rund-in-tile--needed").exists()).toBe(true);
    expect(wrapper.vm.runNotes).toEqual([]);
    expect(wrapper.vm.canRun).toBe(false);
    expect(wrapper.vm.runBlocker).toBe("Choose a picture for Reference first.");
  });

  it("shows what the picture was made from, and can be sent the picture instead", async () => {
    preflightWorkflowRun.mockResolvedValue(
      answer([input(SUBJECT, { mode: "selection", picture_id: 7, fill: "original" })]),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1] });

    const row = wrapper.find(".rund-in");
    expect(row.text()).toContain("The picture this one was made from");
    expect(row.find(".rund-in-img").exists()).toBe(true);
    expect(row.find(".rund-in-tile--needed").exists()).toBe(false);
    // The picture itself is not fed in, so nothing is another take of it.
    expect(wrapper.find(".rund-box").element.checked).toBe(false);
    // One bar button, the pin: nothing was picked, so nothing to clear.
    expect(row.findAllComponents({ name: "AppBarButton" })).toHaveLength(1);

    // The original stands ahead of anything the card stores, so a write to
    // the card could not move it: this run is told, and nothing is stored.
    await wrapper.vm.useSelectionHere(wrapper.vm.pictureInputs[0]);
    await flushPromises();
    expect(setWorkflowInputs).not.toHaveBeenCalled();
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].inputs).toEqual([
      { slot_label: SUBJECT, input_name: "image", picture_id: null },
    ]);
    expect(wrapper.find(".rund-in").findAllComponents({ name: "AppBarButton" })).toHaveLength(2);

    // And back: the pick is what the clear button takes away.
    await wrapper.vm.clearPick(wrapper.vm.pictureInputs[0]);
    await flushPromises();
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].inputs).toEqual([]);
  });

  it("sends the selection to one input only, however many rows are asked", async () => {
    // Two `picture_id: null` entries are a 422 that clears the rows, and with
    // them every control that could take one back.
    preflightWorkflowRun.mockResolvedValue(
      answer([
        input(SUBJECT, { picture_id: 7, fill: "original" }),
        input(REFERENCE, { picture_id: 8, fill: "original" }),
      ]),
    );
    const wrapper = await mountRun({ kind: "picture", pictureIds: [1] });
    await wrapper.vm.useSelectionHere(wrapper.vm.pictureInputs[0]);
    await flushPromises();
    await wrapper.vm.useSelectionHere(wrapper.vm.pictureInputs[1]);
    await flushPromises();
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].inputs).toEqual([
      { slot_label: REFERENCE, input_name: "image", picture_id: null },
    ]);
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
  // As `SideBar.openLoraRunFromCtx` opens it.
  const fromPerson = {
    kind: "card",
    pickWorkflow: true,
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
    // And are theirs, which their LoRA says by itself.
    expect(ofRows(wrapper)).toEqual([["Example", "by their LoRA"]]);
    expect(body.character_ids).toEqual([3]);
  });

  it("draws the person chosen and fixed, with no way to take their LoRA off", async () => {
    const wrapper = await mountRun(fromPerson);
    const fixed = wrapper.find('[data-testid="person-fixed"]');
    expect(fixed.text()).toBe("Example");
    // A statement, not a control: nobody else to pick, and no "No one".
    expect(fixed.attributes("aria-checked")).toBe("true");
    expect(wrapper.find("button[role='checkbox']").exists()).toBe(false);
    expect(wrapper.vm.addedLoras[0].person).toBe(3);
    expect(loraRemoves(wrapper)).toEqual([]);
  });

  it("takes other people beside the fixed one, who stays first and fixed", async () => {
    const OTHER_LORA = {
      sha256: "j".repeat(64),
      filename: "example-friend-krea2.safetensors",
      base_model_family: "krea2",
      attachments: [{ entity_type: "character", entity_id: 4 }],
    };
    const own = {
      ...PERSON_LORA,
      attachments: [{ entity_type: "character", entity_id: 3 }],
    };
    listCharacters.mockResolvedValue([
      { id: 3, name: "Example" },
      { id: 4, name: "Friend" },
    ]);
    listAdapters.mockImplementation(async ({ fileKind, characterId } = {}) => {
      if (characterId) return fileKind === "adapter" ? [own] : [];
      return fileKind ? [] : [own, OTHER_LORA];
    });
    getWorkflowCard.mockImplementation(async (key) => ({
      card: card({ id: key, name: key, models: model("krea2") }),
    }));
    const wrapper = await mountRun(fromPerson);
    // The fixed person is drawn once, as the statement, and never as a toggle.
    expect(wrapper.findAll('[data-testid="person-fixed"]')).toHaveLength(1);
    expect(wrapper.find('[data-person="3"]').exists()).toBe(false);
    expect(wrapper.find('[data-person="null"]').exists()).toBe(false);

    await wrapper.find('[data-person="4"]').trigger("click");
    expect(wrapper.find('[data-person="4"]').attributes("aria-checked")).toBe("true");
    expect(wrapper.vm.addedLoras.map((row) => row.person)).toEqual([3, 4]);
    // Only the added person's row can be taken off.
    expect(loraRemoves(wrapper).map((c) => c.props("tooltip"))).toEqual([
      "Remove Friend's LoRA",
    ]);
    await wrapper.vm.submit();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.add_loras.map((row) => row.sha256)).toEqual([
      PERSON_LORA.sha256,
      OTHER_LORA.sha256,
    ]);
    // Still filed to the person the popup was opened on.
    expect(body.destination.character_id).toBe(3);

    await wrapper.find('[data-person="4"]').trigger("click");
    expect(wrapper.vm.addedLoras.map((row) => row.person)).toEqual([3]);
  });

  describe("the fixed person's trigger word", () => {
    beforeEach(() => {
      // A set's LoRA has the word too, so only the person rule keeps it out.
      listAdapters.mockImplementation(async ({ fileKind, characterId, setId } = {}) =>
        (characterId || setId) && fileKind === "adapter"
          ? [{ ...PERSON_LORA, trigger_words: ["ohwx"], trigger_words_source: "recorded" }]
          : [],
      );
    });
    const workflowPrompt = (text) =>
      preflightWorkflowRun.mockResolvedValue({
        ok: true,
        runs: 1,
        groups: [{ prompt: { positive_settable: true, positive_text: text } }],
      });

    it("is already in the prompt, ahead of the workflow's own", async () => {
      workflowPrompt("a portrait");
      const wrapper = await mountRun(fromPerson);
      expect(wrapper.vm.prompt).toBe("ohwx, a portrait");
      expect(wrapper.find('[data-testid="rund-trigger"]').exists()).toBe(false);
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].prompt).toBe("ohwx, a portrait");
    });

    it("is not put in twice when the workflow's prompt names it", async () => {
      workflowPrompt("OHWX smiling");
      const wrapper = await mountRun(fromPerson);
      expect(wrapper.vm.prompt).toBe("OHWX smiling");
    });

    it("is suggested again once taken out", async () => {
      workflowPrompt("a portrait");
      const wrapper = await mountRun(fromPerson);
      wrapper.vm.prompt = "a portrait";
      await flushPromises();
      expect(wrapper.find('[data-testid="rund-trigger"]').text()).toContain("ohwx");
    });

    it("follows the LoRA the picked workflow loads", async () => {
      const ZIT = `auto:${"f".repeat(64)}`;
      listWorkflowCards.mockResolvedValue({
        cards: [
          { id: ZIT, name: "ZIT portrait", type: "txt2img", models: model("zimage") },
          { id: KREA, name: "Krea portrait", type: "txt2img", models: model("krea2") },
        ],
      });
      listAdapters.mockImplementation(async ({ fileKind, characterId } = {}) =>
        characterId && fileKind === "adapter"
          ? [
              {
                sha256: "m".repeat(64),
                base_model_family: "zimage",
                trigger_words: ["zword"],
              },
              { ...PERSON_LORA, trigger_words: ["ohwx"] },
            ]
          : [],
      );
      workflowPrompt("a portrait");
      const wrapper = await mountRun(fromPerson);
      expect(wrapper.vm.prompt).toBe("zword, a portrait");
      wrapper.vm.workflowId = KREA;
      await flushPromises();
      expect(wrapper.vm.prompt).toBe("ohwx, a portrait");
    });

    it("is never sent over a prompt the pre-flight could not read", async () => {
      preflightWorkflowRun.mockRejectedValue(new Error("network"));
      const wrapper = await mountRun(fromPerson);
      expect(wrapper.vm.prompt).toBe("");
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].prompt).toBe(null);
    });

    it("is not kept for a graph with no prompt to set", async () => {
      preflightWorkflowRun.mockResolvedValue({
        ok: true,
        runs: 1,
        groups: [{ prompt: { positive_settable: false, negative_settable: false } }],
      });
      const wrapper = await mountRun(fromPerson);
      expect(wrapper.vm.prompt).toBe("");
    });

    it("is a person's only: a set's run opens on the workflow's prompt", async () => {
      workflowPrompt("a beach");
      const wrapper = await mountRun({
        ...fromPerson,
        name: "Beach",
        lora: { entityType: "set", entityId: 5, name: "Beach" },
      });
      expect(wrapper.vm.prompt).toBe("a beach");
    });
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

  it("ranks the LoRA picker by no set hidden in this library", async () => {
    fetchWorkflowSets.mockResolvedValue({
      hand_made: [
        { id: 10, members: [] },
        { id: 11, members: [] },
      ],
      hidden_sets: ["hand:10"],
    });
    const wrapper = await mountRun({
      ...fromPerson,
      name: "Beach",
      lora: { entityType: "set", entityId: 5, name: "Beach" },
    });
    expect(wrapper.vm.handMadeSets.map((set) => set.id)).toEqual([11]);
  });

  it("offers every attached LoRA's base model and loads the one picked for", async () => {
    const ZIT = `auto:${"f".repeat(64)}`;
    const ZIT_LORA = {
      sha256: "m".repeat(64),
      filename: "example-subject-zit.safetensors",
      base_model: "Z-Image Turbo",
      base_model_family: "zimage",
    };
    listWorkflowCards.mockResolvedValue({
      cards: [
        { id: ZIT, name: "ZIT portrait", type: "txt2img", models: model("zimage") },
        { id: SDXL, name: "SDXL portrait", type: "txt2img", models: model("sdxl") },
        { id: KREA, name: "Krea portrait", type: "txt2img", models: model("krea2") },
      ],
    });
    // The Z-Image LoRA is attached first: it must not hide the Krea 2 ones.
    listAdapters.mockImplementation(async ({ fileKind, characterId } = {}) =>
      characterId && fileKind === "adapter" ? [ZIT_LORA, PERSON_LORA] : [],
    );
    const wrapper = await mountRun(fromPerson);
    expect(wrapper.vm.workflowOptions.map((o) => o.value)).toEqual([ZIT, KREA]);
    expect(wrapper.text()).toContain("Not listed: 1 for another base model.");
    expect(wrapper.vm.addedLoras[0].sha256).toBe(ZIT_LORA.sha256);

    wrapper.vm.workflowId = KREA;
    await flushPromises();
    // The row takes the LoRA made for the workflow picked, and stays theirs.
    expect(wrapper.vm.addedLoras.map((row) => [row.sha256, row.person])).toEqual([
      [PERSON_LORA.sha256, 3],
    ]);
    expect(wrapper.vm.workflowOptions.map((o) => o.value)).toEqual([ZIT, KREA]);
    await wrapper.vm.submit();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe(KREA);
    expect(body.add_loras).toEqual([
      { sha256: PERSON_LORA.sha256, strength_model: 1 },
    ]);
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
    // Noor's LoRA is for another base model: said, not offered. The tiles
    // need no sentence of their own, so that is the whole note.
    expect(tile(wrapper, 8).exists()).toBe(false);
    expect(wrapper.find('[data-testid="rund-person"] .rund-note').text()).toBe(
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

  const addSelect = (wrapper) =>
    wrapper
      .findAllComponents({ name: "AppSelect" })
      .find((select) => select.props("label") === "Add someone");
  const ofButton = (wrapper, id, focus) =>
    wrapper.find(`[data-of="${id}"] [data-focus="${focus}"]`);

  it("adds someone to the pictures, where no LoRA names one", async () => {
    const wrapper = await mountRun(fromCard);
    // Everyone, whether or not a LoRA of theirs is for this checkpoint.
    expect(addSelect(wrapper).props("options")).toEqual([
      { value: "", label: "Add someone" },
      { value: "7", label: "Mira" },
      { value: "8", label: "Noor" },
    ]);
    expect(ofRows(wrapper)).toEqual([["No one", ""]]);
    // Untouched with nobody listed: the server reads the graph for itself.
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].character_ids).toBeUndefined();

    await addSelect(wrapper).vm.$emit("update:modelValue", "8");
    expect(ofRows(wrapper)).toEqual([["Noor", "linked only"]]);
    // Off the menu once listed, and the menu shows its prompt again.
    expect(addSelect(wrapper).props("options").map((o) => o.value)).toEqual(["", "7"]);
    expect(addSelect(wrapper).props("modelValue")).toBe("");
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[1][0].character_ids).toEqual([8]);
    // Nothing is loaded for them.
    expect(runWorkflowCard.mock.calls[1][0].add_loras).toBeUndefined();

    // Taken off again, they are simply gone.
    await ofButton(wrapper, 8, "remove").trigger("click");
    expect(ofRows(wrapper)).toEqual([["No one", ""]]);
  });

  it("says nobody, not nothing, once the only LoRA person is taken off", async () => {
    const wrapper = await mountRun(fromCard);
    await tile(wrapper, 7).trigger("click");
    await ofButton(wrapper, 7, "remove").trigger("click");
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].character_ids).toEqual([]);
  });

  it("lists everyone whose LoRA the run loads, and links them all", async () => {
    const ADA = {
      sha256: "a".repeat(64),
      filename: "ada-sdxl.safetensors",
      base_model_family: "sdxl",
      attachments: of(9),
    };
    listCharacters.mockResolvedValue([
      { id: 7, name: "Mira" },
      { id: 8, name: "Noor" },
      { id: 9, name: "Ada" },
    ]);
    listAdapters.mockImplementation(async ({ fileKind } = {}) =>
      fileKind ? [] : [MIRA, KREA_ONLY, ADA],
    );
    const wrapper = await mountRun(fromCard);
    await tile(wrapper, 7).trigger("click");
    await tile(wrapper, 9).trigger("click");
    expect(ofRows(wrapper)).toEqual([
      ["Mira", "by their LoRA"],
      ["Ada", "by their LoRA"],
    ]);
    expect(wrapper.find(".rund-of .rund-note").text()).toBe(
      "Each face goes to whichever of these it looks most like.",
    );
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].character_ids).toEqual([7, 9]);
  });

  it("takes a person off the pictures and keeps their LoRA running", async () => {
    const wrapper = await mountRun(fromCard);
    await addSelect(wrapper).vm.$emit("update:modelValue", "8");
    await tile(wrapper, 7).trigger("click");
    expect(ofRows(wrapper)).toEqual([
      ["Mira", "by their LoRA"],
      ["Noor", "linked only"],
    ]);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[0][0].character_ids).toEqual([7, 8]);

    await ofButton(wrapper, 7, "remove").trigger("click");
    // Struck through, with Undo, so the gap shows.
    expect(ofRows(wrapper)).toEqual([
      ["Mira", "LoRA still runs"],
      ["Noor", "linked only"],
    ]);
    expect(wrapper.find('[data-of="7"]').classes()).toContain("rund-of-row--off");
    expect(wrapper.find(".rund-of .rund-note").exists()).toBe(false);
    await wrapper.vm.submit();
    const body = runWorkflowCard.mock.calls[1][0];
    expect(body.character_ids).toEqual([8]);
    expect(body.add_loras).toEqual([{ sha256: MIRA.sha256, strength_model: 1 }]);

    await ofButton(wrapper, 7, "undo").trigger("click");
    expect(ofRows(wrapper)[0]).toEqual(["Mira", "by their LoRA"]);
    await wrapper.vm.submit();
    expect(runWorkflowCard.mock.calls[2][0].character_ids).toEqual([7, 8]);

    // Her LoRA off again: her row goes, the one added by hand stays.
    await wrapper.find('[data-person="null"]').trigger("click");
    expect(ofRows(wrapper)).toEqual([["Noor", "linked only"]]);
  });

  it("offers nobody to assign to in a library with no people", async () => {
    listCharacters.mockResolvedValue([]);
    const wrapper = await mountRun(fromCard);
    expect(addSelect(wrapper)).toBeUndefined();
    expect(wrapper.text()).not.toContain("Pictures are of");
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

  describe("several people", () => {
    const TOVE = {
      sha256: "o".repeat(64),
      filename: "tove-sdxl.safetensors",
      base_model_family: "sdxl",
      attachments: of(9),
    };
    const TOVE_ALT = {
      sha256: "p".repeat(64),
      filename: "tove-sdxl-v2.safetensors",
      base_model_family: "sdxl",
      attachments: of(9),
    };
    const rowsOf = (wrapper) =>
      wrapper.vm.addedLoras.map((row) => [row.person, row.sha256]);
    const selects = (wrapper) =>
      wrapper
        .findAllComponents({ name: "AppSelect" })
        .filter((c) => /'s LoRA$/.test(c.props("label")));
    const checked = (wrapper, id) => tile(wrapper, id).attributes("aria-checked");

    beforeEach(() => {
      listCharacters.mockResolvedValue([
        { id: 7, name: "Mira" },
        { id: 9, name: "Tove" },
      ]);
      listAdapters.mockImplementation(async ({ fileKind } = {}) =>
        fileKind ? [] : [MIRA, TOVE, TOVE_ALT],
      );
    });

    it("adds one LoRA per person, in the order picked", async () => {
      const wrapper = await mountRun(fromCard);
      // Tove first, though Mira is drawn first.
      await tile(wrapper, 9).trigger("click");
      await tile(wrapper, 7).trigger("click");
      expect(checked(wrapper, 9)).toBe("true");
      expect(checked(wrapper, 7)).toBe("true");
      expect(checked(wrapper, "null")).toBe("false");
      wrapper.vm.addedLoras[1].strength = 0.6;
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].add_loras).toEqual([
        { sha256: TOVE.sha256, strength_model: 1 },
        { sha256: MIRA.sha256, strength_model: 0.6 },
      ]);
      // The pre-flight is asked about the same people.
      await wrapper.vm.runPreflight();
      expect(preflightWorkflowRun.mock.calls.at(-1)[0].add_loras).toHaveLength(2);
      // And a recipe saved from the run keeps both.
      expect(wrapper.vm.recipeLoras.map((row) => row.sha256)).toEqual([
        TOVE.sha256,
        MIRA.sha256,
      ]);
    });

    it("keeps people's rows ahead of a LoRA added by hand", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.addLora();
      wrapper.vm.addedLoras[0].sha256 = "s".repeat(64);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      expect(rowsOf(wrapper)).toEqual([
        [7, MIRA.sha256],
        [9, TOVE.sha256],
        [null, "s".repeat(64)],
      ]);
    });

    it("takes a person's row away when they are picked again, and nobody else's", async () => {
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      await tile(wrapper, 7).trigger("click");
      expect(checked(wrapper, 7)).toBe("false");
      expect(rowsOf(wrapper)).toEqual([[9, TOVE.sha256]]);
    });

    it("unpicks the person whose row is taken off with its ×", async () => {
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      const remove = wrapper
        .findAllComponents({ name: "AppBarButton" })
        .find((c) => c.props("tooltip") === "Remove Mira's LoRA");
      await remove.trigger("click");
      expect(checked(wrapper, 7)).toBe("false");
      expect(checked(wrapper, 9)).toBe("true");
      expect(rowsOf(wrapper)).toEqual([[9, TOVE.sha256]]);
    });

    it("puts the keyboard on the tile when a person's row is taken off", async () => {
      const wrapper = mount(RunDialog, {
        props: { source: fromCard, context: {} },
        attachTo: document.body,
        ...globalOpts,
      });
      await flushPromises();
      await tile(wrapper, 7).trigger("click");
      wrapper.vm.removeAddedLora(0);
      await flushPromises();
      // The × is gone with its row; focus must not fall to the page.
      expect(document.activeElement).toBe(tile(wrapper, 7).element);
    });

    it("offers each row that person's LoRAs, and keeps the one chosen", async () => {
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      const [mira, tove] = selects(wrapper);
      expect(mira.props("label")).toBe("Mira's LoRA");
      expect(mira.props("options").map((o) => o.value)).toEqual([MIRA.sha256]);
      expect(tove.props("options").map((o) => o.value)).toEqual([
        TOVE.sha256,
        TOVE_ALT.sha256,
      ]);
      // Tove's other LoRA is still Tove's: the row is not put back to the first.
      wrapper.vm.addedLoras[1].sha256 = TOVE_ALT.sha256;
      await flushPromises();
      expect(rowsOf(wrapper)).toEqual([
        [7, MIRA.sha256],
        [9, TOVE_ALT.sha256],
      ]);
    });

    it("takes every pick off for No one", async () => {
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      await tile(wrapper, "null").trigger("click");
      expect(checked(wrapper, "null")).toBe("true");
      expect(wrapper.vm.addedLoras).toEqual([]);
    });

    it("never asks for one file twice when two people share a LoRA", async () => {
      const SHARED = { ...MIRA, attachments: [...of(7), ...of(9)] };
      listAdapters.mockImplementation(async ({ fileKind } = {}) =>
        fileKind ? [] : [SHARED],
      );
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      // Both are picked; the one loader is both people's.
      expect(checked(wrapper, 9)).toBe("true");
      expect(rowsOf(wrapper)).toEqual([[7, SHARED.sha256]]);
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].add_loras).toHaveLength(1);
      // Unpicking the row's person leaves the file to the other.
      await tile(wrapper, 7).trigger("click");
      expect(rowsOf(wrapper)).toEqual([[9, SHARED.sha256]]);
    });

    it("does not offer a row the file another person's row holds", async () => {
      const SHARED = { ...MIRA, attachments: [...of(7), ...of(9)] };
      listAdapters.mockImplementation(async ({ fileKind } = {}) =>
        fileKind ? [] : [SHARED, TOVE],
      );
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await tile(wrapper, 9).trigger("click");
      expect(rowsOf(wrapper)).toEqual([
        [7, SHARED.sha256],
        [9, TOVE.sha256],
      ]);
      expect(selects(wrapper)[1].props("options").map((o) => o.value)).toEqual([
        TOVE.sha256,
      ]);
    });
  });

  it("is not offered on a recipe's run, which keeps its own LoRAs", async () => {
    const wrapper = await mountRun();
    expect(wrapper.find('[data-testid="rund-person"]').exists()).toBe(false);
  });

  it("does not offer a person whose LoRA has no base model recorded", async () => {
    listAdapters.mockImplementation(async ({ fileKind } = {}) =>
      fileKind ? [] : [MIRA, { ...KREA_ONLY, base_model_family: null }],
    );
    const wrapper = await mountRun(fromCard);
    expect(tile(wrapper, 7).exists()).toBe(true);
    expect(tile(wrapper, 8).exists()).toBe(false);
    expect(wrapper.text()).toContain(
      "Not listed: 1 whose LoRA has no base model recorded.",
    );
  });

  it("says why nobody is offered, in the server's words", async () => {
    listAdapters.mockImplementation(async ({ fileKind } = {}) =>
      fileKind ? [] : [KREA_ONLY],
    );
    const wrapper = await mountRun(fromCard);
    expect(tile(wrapper, 8).exists()).toBe(false);
    expect(wrapper.find('[data-testid="rund-person"] .rund-note').text()).toBe(
      "PixlStash has no LoRAs for this model.",
    );
  });

  it("offers no one on a workflow whose base model is not known", async () => {
    getWorkflowCard.mockResolvedValue({ card: card({ models: [] }) });
    const wrapper = await mountRun(fromCard);
    expect(wrapper.find('[role="checkbox"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("This workflow's base model is not known");
  });

  it("names a base model it cannot match LoRAs to, never calls it unknown", async () => {
    getWorkflowCard.mockResolvedValue({
      card: card({
        models: [
          {
            kind: "unet",
            name: "example",
            base_model: "Example Base",
            base_model_family: null,
          },
        ],
      }),
    });
    const wrapper = await mountRun(fromCard);
    expect(wrapper.find('[data-testid="rund-person"] .rund-note').text()).toBe(
      "PixlStash cannot match LoRAs to Example Base.",
    );
  });

  describe("the trigger word of the person's LoRA", () => {
    const suggestion = (wrapper) => wrapper.find('[data-testid="rund-trigger"]');
    const box = (wrapper) =>
      wrapper
        .findAllComponents({ name: "AppTextarea" })
        .find((c) => c.props("label") === "Prompt");
    const typePrompt = (wrapper, text) =>
      box(wrapper).vm.$emit("update:modelValue", text);

    beforeEach(() => {
      listAdapters.mockImplementation(async ({ fileKind } = {}) =>
        fileKind
          ? []
          : [{ ...MIRA, trigger_words: ["Mira Vale"], trigger_words_source: "character" }],
      );
    });

    it("is suggested while the prompt lacks it, and only once a person is picked", async () => {
      const wrapper = await mountRun(fromCard);
      expect(suggestion(wrapper).exists()).toBe(false);
      await tile(wrapper, 7).trigger("click");
      expect(suggestion(wrapper).text()).toContain("Mira Vale");
      // The box is told which word to mark once it is typed.
      expect(box(wrapper).props("highlight")).toEqual(["Mira Vale"]);

      // Any case counts as typed.
      await typePrompt(wrapper, "a portrait of mira vale, smiling");
      expect(suggestion(wrapper).exists()).toBe(false);

      await typePrompt(wrapper, "a portrait of mira");
      expect(suggestion(wrapper).exists()).toBe(true);

      // No one: nothing to suggest, nothing to mark.
      await wrapper.find('[data-person="null"]').trigger("click");
      expect(suggestion(wrapper).exists()).toBe(false);
      expect(box(wrapper).props("highlight")).toEqual([]);
    });

    it("puts the word first when asked, and never blocks the run", async () => {
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      await typePrompt(wrapper, "on a beach");
      expect(wrapper.vm.runBlocker).toBeFalsy();
      await suggestion(wrapper).find("button").trigger("click");
      expect(wrapper.vm.prompt).toBe("Mira Vale, on a beach");
      expect(suggestion(wrapper).exists()).toBe(false);
    });

    it("puts the word where the caret was, and the caret after it", async () => {
      const focus = vi.fn();
      const wrapper = mount(RunDialog, {
        props: { source: fromCard, context: {} },
        global: {
          stubs: {
            ...globalOpts.global.stubs,
            AppTextarea: {
              name: "AppTextarea",
              props: ["label", "highlight", "modelValue"],
              template: "<textarea />",
              // Where the box last had its caret, as it reports it.
              methods: { focus, selection: () => ({ start: 14, end: 14 }) },
            },
          },
        },
      });
      await flushPromises();
      await tile(wrapper, 7).trigger("click");
      await typePrompt(wrapper, "a portrait of on a beach");
      await suggestion(wrapper).find("button").trigger("click");
      await flushPromises();
      expect(wrapper.vm.prompt).toBe("a portrait of Mira Vale on a beach");
      expect(focus).toHaveBeenCalledWith(23);
    });

    describe("typed into the prompt", () => {
      const picked = (wrapper) => tile(wrapper, 7).attributes("aria-checked") === "true";
      const useWord = (word) =>
        listAdapters.mockImplementation(async ({ fileKind } = {}) =>
          fileKind ? [] : [{ ...MIRA, trigger_words: [word] }],
        );

      it("tells each person's trigger word in their tile's tip", async () => {
        const wrapper = await mountRun(fromCard);
        const tip = tile(wrapper, 7).findComponent({ name: "Tooltip" });
        expect(tip.props("text")).toBe("Trigger word: Mira Vale");
      });

      it("tells the word of the LoRA a pick would add, not one another's row holds", async () => {
        listCharacters.mockResolvedValue([
          { id: 7, name: "Mira" },
          { id: 9, name: "Vale" },
        ]);
        listAdapters.mockImplementation(async ({ fileKind } = {}) =>
          fileKind
            ? []
            : [
                { ...MIRA, attachments: [...of(7), ...of(9)], trigger_words: ["shared"] },
                { ...MIRA, sha256: "v".repeat(64), attachments: of(9), trigger_words: ["valeword"] },
              ],
        );
        const wrapper = await mountRun(fromCard);
        const tip = () => tile(wrapper, 9).findComponent({ name: "Tooltip" }).props("text");
        expect(tip()).toBe("Trigger word: shared");
        // Mira's row takes the shared file, so a pick of Vale adds Vale's own.
        await tile(wrapper, 7).trigger("click");
        expect(tip()).toBe("Trigger word: valeword");
        await tile(wrapper, 9).trigger("click");
        const vale = wrapper.vm.addedLoras.find((row) => row.person === 9);
        expect(vale.sha256).toBe("v".repeat(64));
      });

      it("picks the person once their word is typed past, and never unpicks", async () => {
        const wrapper = await mountRun(fromCard);
        // Still being typed: "Mira Vale" may be on its way to something else.
        await typePrompt(wrapper, "a portrait of mira vale");
        expect(picked(wrapper)).toBe(false);
        await typePrompt(wrapper, "a portrait of mira vale ");
        expect(picked(wrapper)).toBe(true);
        expect(wrapper.vm.addedLoras.map((row) => row.person)).toEqual([7]);
        // Taking the word out leaves the LoRA on.
        await typePrompt(wrapper, "a portrait");
        expect(picked(wrapper)).toBe(true);
      });

      it("does not pick again someone unpicked by hand", async () => {
        const wrapper = await mountRun(fromCard);
        await typePrompt(wrapper, "mira vale, smiling");
        await tile(wrapper, 7).trigger("click");
        expect(picked(wrapper)).toBe(false);
        await typePrompt(wrapper, "mira vale, smiling on a beach");
        expect(picked(wrapper)).toBe(false);
      });

      it("does not swap the name of someone unpicked by hand", async () => {
        useWord("ohwx");
        const wrapper = await mountRun(fromCard);
        await tile(wrapper, 7).trigger("click");
        await tile(wrapper, 7).trigger("click");
        expect(picked(wrapper)).toBe(false);
        await typePrompt(wrapper, "a photo of Mira, smiling");
        expect(wrapper.vm.prompt).toBe("a photo of Mira, smiling");
      });

      it("swaps the name, once typed past, for a trigger word of its own", async () => {
        useWord("ohwx");
        const wrapper = await mountRun(fromCard);
        await typePrompt(wrapper, "a photo of Mira");
        expect(wrapper.vm.prompt).toBe("a photo of Mira");
        await typePrompt(wrapper, "a photo of Mira, smiling");
        expect(wrapper.vm.prompt).toBe("a photo of ohwx, smiling");
        expect(picked(wrapper)).toBe(true);
        // The word is there: a name typed beside it is meant.
        await typePrompt(wrapper, "a photo of ohwx, Mira smiling");
        expect(wrapper.vm.prompt).toBe("a photo of ohwx, Mira smiling");
      });

      it("leaves a name inside a longer person's name to that person", async () => {
        listCharacters.mockResolvedValue([
          { id: 7, name: "Mira" },
          { id: 9, name: "Mira Vale" },
        ]);
        listAdapters.mockImplementation(async ({ fileKind } = {}) =>
          fileKind
            ? []
            : [
                { ...MIRA, trigger_words: ["ohwx"] },
                { ...MIRA, sha256: "v".repeat(64), attachments: of(9), trigger_words: ["mvx"] },
              ],
        );
        const wrapper = await mountRun(fromCard);
        await typePrompt(wrapper, "a photo of Mira Vale, smiling");
        expect(wrapper.vm.prompt).toBe("a photo of mvx, smiling");
        expect(picked(wrapper)).toBe(false);
        expect(tile(wrapper, 9).attributes("aria-checked")).toBe("true");
      });

      it("keeps the name where the trigger word holds it", async () => {
        const wrapper = await mountRun(fromCard);
        await typePrompt(wrapper, "a photo of Mira V");
        expect(wrapper.vm.prompt).toBe("a photo of Mira V");
      });

      const mountAt = (start) => {
        const focus = vi.fn();
        const wrapper = mount(RunDialog, {
          props: { source: fromCard, context: {} },
          global: {
            stubs: {
              ...globalOpts.global.stubs,
              AppTextarea: {
                name: "AppTextarea",
                props: ["label", "highlight", "modelValue"],
                template: "<textarea />",
                methods: { focus, selection: () => ({ start, end: start }) },
              },
            },
          },
        });
        return { wrapper, focus };
      };

      it("puts the caret back after the swapped word", async () => {
        useWord("ohwxwoman");
        // Typing, just past "Mira ".
        const { wrapper, focus } = mountAt(16);
        await flushPromises();
        await typePrompt(wrapper, "a photo of Mira smiling");
        await flushPromises();
        expect(wrapper.vm.prompt).toBe("a photo of ohwxwoman smiling");
        expect(focus).toHaveBeenCalledWith(21);
      });

      it("puts the caret after the word, not into it, for an edit inside the name", async () => {
        useWord("ohwxwoman");
        // "Mra" mended to "Mira": the caret is just past the "i".
        const { wrapper, focus } = mountAt(2);
        await flushPromises();
        await typePrompt(wrapper, "Mira smiling");
        await flushPromises();
        expect(wrapper.vm.prompt).toBe("ohwxwoman smiling");
        expect(focus).toHaveBeenCalledWith(9);
        // The word is in the prompt, so its LoRA is on the run.
        expect(picked(wrapper)).toBe(true);
      });
    });

    it("is said by a region that was there before it, and unsaid once the word is typed", async () => {
      const live = (wrapper) => wrapper.find('[data-testid="rund-trigger-live"]');
      const wrapper = await mountRun(fromCard);
      const region = live(wrapper).element;
      expect(region.getAttribute("role")).toBe("status");
      expect(region.textContent.trim()).toBe("");
      await tile(wrapper, 7).trigger("click");
      expect(region.textContent.trim()).toBe("Trigger word missing: Mira Vale.");
      // The line comes and goes with the word, so it is not what says it: a
      // reader is told of a change to a region, not of one arriving full.
      expect(suggestion(wrapper).attributes("role")).toBeUndefined();
      await typePrompt(wrapper, "mira vale on a beach");
      expect(region.textContent.trim()).toBe("");
      expect(live(wrapper).element).toBe(region);
    });

    describe("with several people picked", () => {
      const person = (id, name, word) => ({
        sha256: String(id).repeat(64),
        filename: `${name.toLowerCase()}-sdxl.safetensors`,
        base_model_family: "sdxl",
        attachments: of(id),
        trigger_words: word ? [word] : [],
      });
      const PEOPLE = [
        [1, "Ada", "ada lovelace"],
        [2, "Bea", "beaxyz"],
        [3, "Cyd", "cyd"],
        [4, "Dot", "dotty"],
      ];
      const buttons = (wrapper) => suggestion(wrapper).findAll("button");
      const pick = async (wrapper, ids) => {
        for (const id of ids) await tile(wrapper, id).trigger("click");
      };

      beforeEach(() => {
        listCharacters.mockResolvedValue(PEOPLE.map(([id, name]) => ({ id, name })));
        listAdapters.mockImplementation(async ({ fileKind } = {}) =>
          fileKind ? [] : PEOPLE.map((row) => person(...row)),
        );
      });

      it("offers one button for each word the prompt lacks, named for whose it is", async () => {
        const wrapper = await mountRun(fromCard);
        await pick(wrapper, [2, 1]);
        await typePrompt(wrapper, "two friends on a beach");
        expect(suggestion(wrapper).text()).toContain("Trigger word missing");
        // In the order picked, each labelled with the word it adds.
        expect(buttons(wrapper).map((b) => b.text())).toEqual([
          "Add beaxyz",
          "Add ada lovelace",
        ]);
        const names = ["Add beaxyz, Bea's trigger word", "Add ada lovelace, Ada's trigger word"];
        expect(buttons(wrapper).map((b) => b.attributes("aria-label"))).toEqual(names);
        const tips = suggestion(wrapper).findAllComponents({ name: "Tooltip" });
        expect(tips.map((tip) => tip.props("text"))).toEqual(names);
        // The name already says it: the tip must not describe it again.
        expect(tips.every((tip) => tip.props("describe") === false)).toBe(true);
        // Every picked person's word is marked in the box, not only the first.
        expect(box(wrapper).props("highlight")).toEqual(["beaxyz", "ada lovelace"]);

        await buttons(wrapper)[1].trigger("click");
        expect(wrapper.vm.prompt).toBe("ada lovelace, two friends on a beach");
        expect(buttons(wrapper).map((b) => b.text())).toEqual(["Add beaxyz"]);
        await buttons(wrapper)[0].trigger("click");
        expect(wrapper.vm.prompt).toBe("beaxyz, ada lovelace, two friends on a beach");
        // The line goes with the last word.
        expect(suggestion(wrapper).exists()).toBe(false);
      });

      it("keeps a button each up to three words, and one for all of them past that", async () => {
        const wrapper = await mountRun(fromCard);
        await pick(wrapper, [1, 2, 3]);
        expect(buttons(wrapper)).toHaveLength(3);
        await pick(wrapper, [4]);
        expect(buttons(wrapper).map((b) => b.text())).toEqual(["Add all 4"]);
        expect(buttons(wrapper)[0].attributes("aria-label")).toBe(
          "Add all 4 missing trigger words: ada lovelace, beaxyz, cyd, dotty",
        );
        await typePrompt(wrapper, "at a table");
        await buttons(wrapper)[0].trigger("click");
        expect(wrapper.vm.prompt).toBe("ada lovelace, beaxyz, cyd, dotty, at a table");
        expect(suggestion(wrapper).exists()).toBe(false);
      });

      it("counts the words still missing, not the people picked", async () => {
        const wrapper = await mountRun(fromCard);
        await pick(wrapper, [1, 2, 3, 4]);
        await typePrompt(wrapper, "Cyd at a table");
        expect(buttons(wrapper).map((b) => b.text())).toEqual([
          "Add ada lovelace",
          "Add beaxyz",
          "Add dotty",
        ]);
      });

      it("asks once for a word two people's LoRAs share", async () => {
        listAdapters.mockImplementation(async ({ fileKind } = {}) =>
          fileKind ? [] : [person(1, "Ada", "twins"), person(2, "Bea", "Twins")],
        );
        const wrapper = await mountRun(fromCard);
        await pick(wrapper, [1, 2]);
        expect(buttons(wrapper).map((b) => b.text())).toEqual(["Add twins"]);
      });

      it("hands the prompt box the keyboard after a word is added", async () => {
        const focus = vi.fn();
        const wrapper = mount(RunDialog, {
          props: { source: fromCard, context: {} },
          global: {
            stubs: {
              ...globalOpts.global.stubs,
              AppTextarea: {
                name: "AppTextarea",
                props: ["label", "highlight", "modelValue"],
                template: "<textarea />",
                methods: { focus },
              },
            },
          },
        });
        await flushPromises();
        await pick(wrapper, [1, 2]);
        await buttons(wrapper)[0].trigger("click");
        // One word is still missing, and the keyboard is in the box all the same.
        expect(buttons(wrapper)).toHaveLength(1);
        expect(focus).toHaveBeenCalledTimes(1);
      });
    });

    it("says nothing for a LoRA that needs no trigger word", async () => {
      listAdapters.mockImplementation(async ({ fileKind } = {}) =>
        fileKind ? [] : [{ ...MIRA, trigger_words: [], trigger_words_source: "recorded" }],
      );
      const wrapper = await mountRun(fromCard);
      await tile(wrapper, 7).trigger("click");
      expect(suggestion(wrapper).exists()).toBe(false);
    });
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


// ── Save what you ran (#1850) ────────────────────────────────────────────
//
// The popup counts what differs from the workflow it runs and offers to keep
// it. What can silently go wrong here is all of one kind: the band, the run
// and the save reading three different things. So most of this asserts that
// they read ONE (`formChanges`), and the rest that nothing is written unless
// one of the band's two buttons is pressed.
describe("Save what you ran", () => {
  const CKPT = "core:CheckpointLoader/ckpt_name";
  const VAE = "core:VAELoader/vae_name";
  const MIRA_SHA = "s".repeat(64);
  const GRAIN_SHA = "t".repeat(64);
  const fromCard = { kind: "card", workflowId: KEY };
  const picked = { ...fromCard, pickWorkflow: true };
  const THIRD = `auto:${"c".repeat(64)}`;
  const ownCard = (overrides = {}) =>
    card({
      specials: ["upscale", "face_detailer"],
      default_recipe: {
        sampled: 12,
        models: [
          { address: CKPT, kind: "checkpoint", filename: "realvisXL_v5.safetensors" },
          { address: VAE, kind: "vae", filename: "sdxl_vae.safetensors" },
        ],
        loras: [
          { filename: "mira_v2.safetensors", sha256: MIRA_SHA, strength: 0.6 },
          { filename: "loras/film-grain-35mm.safetensors", sha256: GRAIN_SHA, strength: 0.4 },
        ],
        values: [],
        stages: { upscale: true, face_detailer: false },
      },
      ...overrides,
    });
  const loader = (node, filename, sha256, strength, clip = strength) => ({
    node_id: node,
    field: "lora_name",
    filename,
    name: filename,
    strength,
    strength_clip: clip,
    sha256,
    on_shelf: true,
  });
  const CHAIN = {
    loaders: [
      // The graph has it at 0.8; the default recipe, which is what runs, at 0.6.
      loader("7", "mira_v2.safetensors", MIRA_SHA, 0.8),
      loader("8", "loras/film-grain-35mm.safetensors", GRAIN_SHA, 0.4),
      // In the graph and not in the default recipe: a run bypasses it.
      loader("9", "example-person.safetensors", "p".repeat(64), 1),
    ],
    lanes: [],
  };
  const band = (wrapper) => wrapper.find('[data-testid="rund-band"]');
  const button = (wrapper, text) =>
    wrapper.findAll("button").find((b) => b.text().trim() === text);
  const field = (wrapper, name) =>
    wrapper.vm.defaults.find((entry) => entry.input_name === name);
  const said = (wrapper) => band(wrapper).text().replace(/\s+/g, " ");
  /** Every call of the save route that would have written something. */
  const writes = () => saveWorkflow.mock.calls.filter(([, body]) => !body?.dry_run);
  const refusal = (status, detail) => ({ response: { status, data: { detail } } });
  /** A read or a write the test answers when it chooses. */
  function held() {
    let resolve, reject;
    const promise = new Promise((yes, no) => {
      resolve = yes;
      reject = no;
    });
    return { promise, resolve, reject };
  }
  /** The save's POST held open; the dry run that asks which verbs are open is not. */
  function heldSave() {
    const post = held();
    saveWorkflow.mockImplementation((_key, body) =>
      body?.dry_run ? Promise.resolve({ save: OPEN, save_as_new: OPEN }) : post.promise,
    );
    return post;
  }
  /** Presses Save with its POST held open, and lets the press settle. */
  async function pressSave(wrapper) {
    const post = heldSave();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    return post;
  }
  /** The next read of the workflow, held open until the test answers it. */
  function holdReread() {
    const reread = held();
    getWorkflowCard.mockReturnValueOnce(reread.promise);
    return reread;
  }
  const runButton = (wrapper) =>
    wrapper.findAll("button").find((b) => b.text().trim().startsWith("Run"));
  /** The sentence the Run button points a reader at, or "". */
  const runWhy = (wrapper) => {
    const id = runButton(wrapper).attributes("aria-describedby");
    return id ? wrapper.find(`[id="${id}"]`).text() : "";
  };
  /** The workflow as it reads once Steps 32 on another checkpoint is saved to it. */
  const savedCard = async (key) => {
    const own = ownCard({
      id: key,
      defaults: card().defaults.map((entry) =>
        entry.input_name === "steps" ? { ...entry, value: 32 } : entry,
      ),
    });
    own.default_recipe.models[0].filename = "juggernautXL.safetensors";
    return { card: own };
  };
  // Buttons and fields that take focus, in a popup that is in the document:
  // where the keyboard is can only be asked of those.
  const focusable = {
    AppButton: {
      ...globalOpts.global.stubs.AppButton,
      methods: {
        focus() {
          this.$el.focus();
        },
      },
    },
    AppInput: {
      name: "AppInput",
      props: ["modelValue", "label", "ariaLabel"],
      emits: ["update:modelValue", "enter"],
      template:
        "<input :value='modelValue' :aria-label='label || ariaLabel' @input=\"$emit('update:modelValue', $event.target.value)\" />",
      methods: {
        focus() {
          this.$el.focus();
        },
        select() {},
      },
    },
  };
  async function mountFocusable() {
    const wrapper = mount(RunDialog, {
      props: { source: fromCard, context: {} },
      attachTo: document.body,
      global: { stubs: { ...globalOpts.global.stubs, ...focusable } },
    });
    await flushPromises();
    return wrapper;
  }
  const nameBox = (wrapper) =>
    wrapper.find('input[aria-label="Name of the new workflow"]');

  /** Three changes, one of each kind the mock-up shows. */
  async function changeThree(wrapper) {
    wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
    await wrapper.vm.skipGraphLora(wrapper.vm.loras[1]);
    wrapper.vm.setValue(field(wrapper, "steps"), 32);
    await flushPromises();
  }

  beforeEach(() => {
    getWorkflowCard.mockImplementation(async (key) => ({ card: ownCard({ id: key }) }));
    getLoraChain.mockResolvedValue(CHAIN);
  });

  describe("the band", () => {
    it("is absent on a plain run, which asks the server nothing about saving", async () => {
      const wrapper = await mountRun(fromCard);
      expect(band(wrapper).exists()).toBe(false);
      expect(wrapper.vm.band.count).toBe(0);
      await wrapper.vm.submit();
      expect(saveWorkflow).not.toHaveBeenCalled();
    });

    it("counts what differs from the workflow and names it", async () => {
      const wrapper = await mountRun(fromCard);
      await changeThree(wrapper);
      expect(said(wrapper)).toContain("3 changes from the workflow");
      expect(band(wrapper).find(".rund-band-list").text()).toBe(
        "Checkpoint, 1 LoRA removed, Steps.",
      );
      // What stays with the run is a line of its own, which is never cut.
      expect(band(wrapper).find(".rund-band-stays").text()).toBe(
        "Prompt, seed, count and the people picked stay with the run.",
      );
      // Neutral, small, and beside them nothing amber: Run stays the one.
      expect(button(wrapper, "Save")).toBeTruthy();
      expect(button(wrapper, "Save as new…")).toBeTruthy();
    });

    it("says one change in the singular", async () => {
      const wrapper = await mountRun(fromCard);
      await wrapper.vm.setStage("upscale", false);
      await flushPromises();
      expect(said(wrapper)).toContain("1 change from the workflow");
      expect(said(wrapper)).toContain("Upscale off.");
    });

    it("never counts what belongs to the run", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.prompt = "another prompt";
      wrapper.vm.count = 4;
      wrapper.vm.seedMode = "fixed";
      wrapper.vm.seed = "7";
      wrapper.vm.addLora();
      wrapper.vm.addedLoras[0].sha256 = MIRA_SHA;
      await flushPromises();
      expect(band(wrapper).exists()).toBe(false);
      expect(wrapper.vm.formChanges).toEqual({
        models: [],
        loras: [],
        skip_loras: [],
        stages: {},
        values: [],
      });

      // Beside a real change, the added LoRA is named as staying with the run.
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(said(wrapper)).toContain("1 change from the workflow");
      expect(band(wrapper).find(".rund-band-list").text()).toBe("Steps.");
      expect(band(wrapper).find(".rund-band-stays").text()).toBe(
        "Prompt, seed, count, the people picked and the LoRA added here stay with the run.",
      );
      expect(wrapper.vm.saveChanges.loras).toEqual([]);
    });

    it("counts a Size once and sends both of its numbers", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "width"), 1024);
      await flushPromises();
      expect(said(wrapper)).toContain("1 change from the workflow");
      expect(said(wrapper)).toContain("Size.");
      expect(wrapper.vm.formChanges.values).toEqual([
        { slot_label: "EmptyLatentImage", input_name: "width", value: 1024 },
        { slot_label: "EmptyLatentImage", input_name: "height", value: 1216 },
      ]);
    });

    it("is not offered on a picture's own run, which is a recipe laid over the workflow", async () => {
      const wrapper = await mountRun();
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(band(wrapper).exists()).toBe(false);
      expect(getWorkflowChanges).not.toHaveBeenCalled();
      expect(saveWorkflow).not.toHaveBeenCalled();
    });

    it("is not offered on a saved recipe's run", async () => {
      const wrapper = await mountRun({
        kind: "card",
        workflowId: KEY,
        savedRecipe: { id: 4, name: "Rainy", prompt: "rain", overrides: { "KSampler/steps": 30 } },
      });
      expect(wrapper.vm.isEdited(field(wrapper, "steps"))).toBe(true);
      expect(band(wrapper).exists()).toBe(false);
      expect(getWorkflowChanges).not.toHaveBeenCalled();
    });

    it("is not offered where what waits on the workflow could not be read", async () => {
      getWorkflowChanges.mockRejectedValue(new Error("offline"));
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(band(wrapper).exists()).toBe(false);
    });

    it("says its arrival once, in words that do not move with the count", async () => {
      const wrapper = await mountRun(fromCard);
      expect(wrapper.vm.bandLive).toBe("");
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      const arrival = wrapper.vm.bandLive;
      expect(arrival).toContain("differs from the workflow");
      wrapper.vm.setValue(field(wrapper, "cfg"), 4);
      wrapper.vm.setValue(field(wrapper, "steps"), 33);
      await flushPromises();
      expect(wrapper.vm.bandLive).toBe(arrival);
    });
  });

  describe("one reading of the form", () => {
    it("sends in the run everything the band would save", async () => {
      const wrapper = await mountRun(fromCard);
      await changeThree(wrapper);
      wrapper.vm.loras[0].strength = 0.9;
      await wrapper.vm.setStage("upscale", false);
      await wrapper.vm.setStage("face_detailer", true);
      wrapper.vm.pickModel(wrapper.vm.modelRows[0], "ae.safetensors");
      await flushPromises();
      const changes = wrapper.vm.formChanges;
      expect(wrapper.vm.band.count).toBe(7);

      await wrapper.vm.submit();
      const body = runWorkflowCard.mock.calls[0][0];
      expect(body.models).toEqual(expect.arrayContaining(changes.models));
      expect(changes.models).toEqual([
        { address: CKPT, filename: "juggernautXL.safetensors" },
        { address: VAE, filename: "ae.safetensors" },
      ]);
      expect(body.loras).toEqual(changes.loras);
      expect(body.skip_loras).toEqual(changes.skip_loras);
      expect(body.skip_stages).toEqual(["upscale"]);
      expect(body.keep_stages).toEqual(["face_detailer"]);
      expect(changes.stages).toEqual({ upscale: false, face_detailer: true });
      for (const value of changes.values) expect(body.values).toContainEqual(value);
      // The pre-flight is asked about the same run.
      expect(preflightWorkflowRun.mock.calls.at(-1)[0].skip_loras).toEqual(
        changes.skip_loras,
      );
    });

    it("saves only the parameters the owner changed, though the run sends them all", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(writes()[0][1].changes.values).toEqual([
        { slot_label: "KSampler", input_name: "steps", value: 32 },
      ]);
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].values.length).toBeGreaterThan(1);
    });
  });

  describe("which verbs are open", () => {
    const CLOSED = {
      open: false,
      reason: "A checkpoint change is saved as a new workflow.",
    };

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    });
    afterEach(() => vi.useRealTimers());

    const settle = async () => {
      await vi.advanceTimersByTimeAsync(400);
      await flushPromises();
    };

    it("is asked of the server, as a dry run of the very save, a beat after the edit", async () => {
      const wrapper = await mountRun(fromCard);
      // Three keystrokes, each seen by the form before the next.
      for (const typed of [30, 31, 32]) {
        wrapper.vm.setValue(field(wrapper, "steps"), typed);
        await flushPromises();
      }
      // Not per keystroke.
      expect(saveWorkflow).not.toHaveBeenCalled();
      await settle();
      expect(saveWorkflow).toHaveBeenCalledTimes(1);
      expect(saveWorkflow).toHaveBeenCalledWith(KEY, {
        changes: wrapper.vm.saveChanges,
        version: 3,
        dry_run: true,
      });
    });

    it("prints the server's reason for a closed Save, and does not send one", async () => {
      saveWorkflow.mockResolvedValue({ dry_run: true, save: CLOSED, save_as_new: OPEN });
      const wrapper = await mountRun(fromCard);
      wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
      await settle();
      const why = wrapper.find('[data-testid="rund-band-why"]');
      // As sent: the rule is the server's and is not reworded here.
      expect(why.text()).toBe(CLOSED.reason);
      const save = button(wrapper, "Save");
      // Reachable, with its reason: never a dead grey button with no words.
      expect(save.attributes("disabled")).toBeUndefined();
      expect(save.attributes("aria-disabled")).toBe("true");
      expect(save.attributes("aria-describedby")).toBe(why.attributes("id"));
      await save.trigger("click");
      await flushPromises();
      expect(writes()).toEqual([]);
      // Save as new… is still open.
      expect(button(wrapper, "Save as new…").attributes("aria-disabled")).toBeUndefined();
    });

    it("keeps the newest answer when an older one lands after it", async () => {
      const answers = [];
      saveWorkflow.mockImplementation(
        () => new Promise((resolve) => answers.push(resolve)),
      );
      const wrapper = await mountRun(fromCard);
      wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
      await settle();
      wrapper.vm.pickCheckpoint("realvisXL_v5.safetensors");
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await settle();
      expect(answers).toHaveLength(2);
      // The newer question (steps alone) is answered first, the older last.
      answers[1]({ dry_run: true, save: OPEN, save_as_new: OPEN });
      await flushPromises();
      answers[0]({ dry_run: true, save: CLOSED, save_as_new: OPEN });
      await flushPromises();
      expect(wrapper.vm.saveClosed).toBe("");
      expect(wrapper.find('[data-testid="rund-band-why"]').exists()).toBe(false);
    });
  });

  describe("Save", () => {
    it("writes what the band names, against the version the popup read", async () => {
      const wrapper = await mountRun(fromCard);
      await changeThree(wrapper);
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(writes()).toEqual([
        [
          KEY,
          {
            changes: {
              models: [{ address: CKPT, filename: "juggernautXL.safetensors" }],
              loras: [],
              skip_loras: [{ node_id: "8", field: "lora_name" }],
              stages: {},
              values: [{ slot_label: "KSampler", input_name: "steps", value: 32 }],
            },
            version: 3,
          },
        ],
      ]);
    });

    it("re-reads the workflow, so the chips go and the run's own fields stay", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.prompt = "a rainy tram platform";
      wrapper.vm.count = 4;
      wrapper.vm.seedMode = "fixed";
      wrapper.vm.seed = "99";
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(wrapper.findAllComponents({ name: "RunResetChip" }).length).toBeGreaterThan(0);

      // What the workflow holds once the save has landed.
      getWorkflowCard.mockImplementation(async (key) => ({
        card: ownCard({
          id: key,
          defaults: card().defaults.map((entry) =>
            entry.input_name === "steps" ? { ...entry, value: 32 } : entry,
          ),
        }),
      }));
      getWorkflowChanges.mockImplementation(async (id) => changesOf(id, { version: 4 }));
      const reads = getWorkflowCard.mock.calls.length;
      await button(wrapper, "Save").trigger("click");
      await flushPromises();

      // The baseline was read again, not merely hidden.
      expect(getWorkflowCard.mock.calls.length).toBe(reads + 1);
      expect(wrapper.vm.baseOf(field(wrapper, "steps"))).toBe(32);
      // Only the prompt keeps its chip: it is the run's, and was not saved.
      expect(
        wrapper.findAllComponents({ name: "RunResetChip" }).map((c) => c.props("label")),
      ).toEqual(["Prompt"]);
      expect(band(wrapper).exists()).toBe(false);
      expect(wrapper.find('[data-testid="rund-band-done"]').text()).toBe(
        "Saved to this workflow.",
      );
      // Still open, on the same workflow, with what the owner typed for the run.
      expect(wrapper.emitted("close")).toBeUndefined();
      expect(wrapper.vm.activeKey).toBe(KEY);
      expect(wrapper.vm.prompt).toBe("a rainy tram platform");
      expect(wrapper.vm.count).toBe(4);
      expect(wrapper.vm.seed).toBe("99");
      // The next save is made against the version this one wrote.
      expect(wrapper.vm.changesState.version).toBe(4);
      // The Workflow tab and the Tasks tab are told.
      expect(useRunDialogStore().saved).toMatchObject({ workflowId: KEY, savedAs: "" });
    });

    it("builds the LoRA rows anew, so a saved removal or strength is not counted again", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.loras[0].strength = 0.9;
      await wrapper.vm.skipGraphLora(wrapper.vm.loras[1]);
      await flushPromises();
      expect(wrapper.vm.formChanges.loras).toHaveLength(1);
      expect(wrapper.vm.formChanges.skip_loras).toHaveLength(1);

      // What the workflow holds once the save has landed: loader 7 at 0.9 in
      // the graph and the default recipe, loader 8 gone from both.
      getWorkflowCard.mockImplementation(async (key) => {
        const own = ownCard({ id: key });
        own.default_recipe.loras = [
          { filename: "mira_v2.safetensors", sha256: MIRA_SHA, strength: 0.9 },
        ];
        return { card: own };
      });
      getLoraChain.mockResolvedValue({
        loaders: [loader("7", "mira_v2.safetensors", MIRA_SHA, 0.9)],
        lanes: [],
      });
      await button(wrapper, "Save").trigger("click");
      await flushPromises();

      expect(wrapper.vm.loras.map((row) => [row.node_id, row.strength, row.skipped])).toEqual([
        ["7", 0.9, false],
      ]);
      expect(wrapper.vm.formChanges.loras).toEqual([]);
      expect(wrapper.vm.formChanges.skip_loras).toEqual([]);
      expect(band(wrapper).exists()).toBe(false);
    });

    it("leaves nothing for the close guard to protect", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(wrapper.vm.dirty).toBe(true);
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(wrapper.vm.dirty).toBe(false);
    });

    it("prints the server's sentence when the save is refused, and keeps the changes", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      saveWorkflow.mockRejectedValue(
        refusal(409, "This workflow got a newer version since these changes were made."),
      );
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(band(wrapper).find('[role="alert"]').text()).toBe(
        "This workflow got a newer version since these changes were made.",
      );
      expect(wrapper.vm.isEdited(field(wrapper, "steps"))).toBe(true);
      expect(useRunDialogStore().saved).toBeNull();
    });

    it("says ComfyUI did not answer on a 503", async () => {
      const wrapper = await mountRun(fromCard);
      await wrapper.vm.skipGraphLora(wrapper.vm.loras[0]);
      await flushPromises();
      saveWorkflow.mockRejectedValue(refusal(503, "upstream said something else"));
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(band(wrapper).find('[role="alert"]').text()).toContain(
        "ComfyUI did not answer, so nothing was saved.",
      );
    });
  });

  describe("Save as new…", () => {

    it("turns the band into its one question, and Back gives the band back", async () => {
      const wrapper = await mountFocusable();
      wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
      await flushPromises();
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();

      expect(document.activeElement).toBe(nameBox(wrapper).element);
      // Named for what changed, never for where it came from.
      expect(nameBox(wrapper).element.value).toBe("Cinematic portrait (juggernautXL)");
      expect(nameBox(wrapper).element.value).not.toMatch(/copy|clone|fixed|edited/i);
      expect(button(wrapper, "Save")).toBeUndefined();

      await button(wrapper, "Back").trigger("click");
      await flushPromises();
      expect(nameBox(wrapper).exists()).toBe(false);
      expect(document.activeElement).toBe(button(wrapper, "Save as new…").element);
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();
    });

    it("offers a name with the picked checkpoint as its picker shows it", async () => {
      readModelSwap.mockImplementation(async (_key, { slotKind }) =>
        slotKind === "checkpoint"
          ? {
              replacements: [
                { id: 7, filename: "juggernautXL_v9.safetensors", display_name: "Juggernaut XL v9" },
              ],
            }
          : { replacements: [] },
      );
      const wrapper = await mountFocusable();
      wrapper.vm.pickCheckpoint("juggernautXL_v9.safetensors");
      await flushPromises();
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();
      // The name the owner just picked it by, not the file read as a name.
      expect(nameBox(wrapper).element.value).toBe("Cinematic portrait (Juggernaut XL v9)");
    });

    it("stores the changes under the name given and moves the popup onto the new workflow", async () => {
      saveWorkflowAsNew.mockResolvedValue({
        workflow_id: OTHER,
        name: "Portrait on Juggernaut",
        new_workflow: true,
        version: 1,
      });
      const wrapper = await mountFocusable();
      await changeThree(wrapper);
      const changes = JSON.parse(JSON.stringify(wrapper.vm.saveChanges));
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();
      await nameBox(wrapper).setValue("  Portrait on Juggernaut ");
      await button(wrapper, "Save as new workflow").trigger("click");
      await flushPromises();

      expect(saveWorkflowAsNew).toHaveBeenCalledWith(KEY, {
        name: "Portrait on Juggernaut",
        changes,
      });
      // Nothing was written over the workflow it was opened on.
      expect(writes()).toEqual([]);
      expect(wrapper.vm.activeKey).toBe(OTHER);
      expect(getWorkflowCard).toHaveBeenLastCalledWith(OTHER);
      // The new workflow holds the changes: none is an edit of it.
      expect(wrapper.vm.band.count).toBe(0);
      expect(wrapper.find('[data-testid="rund-band-done"]').text()).toBe(
        "Saved as Portrait on Juggernaut.",
      );
      expect(document.activeElement).toBe(
        wrapper.find('[data-testid="rund-band-done"]').element,
      );
      expect(useRunDialogStore().saved).toMatchObject({ workflowId: KEY, savedAs: OTHER });
    });

    it("asks for a name before it sends anything", async () => {
      const wrapper = await mountFocusable();
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();
      await nameBox(wrapper).setValue("   ");
      await button(wrapper, "Save as new workflow").trigger("click");
      await flushPromises();
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();
      expect(button(wrapper, "Save as new workflow").attributes("aria-disabled")).toBe(
        "true",
      );
    });

    it("keeps the keyboard in the name while the save is out, and beside its refusal", async () => {
      const post = held();
      saveWorkflowAsNew.mockReturnValue(post.promise);
      const wrapper = await mountFocusable();
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();
      await nameBox(wrapper).setValue("Taken");
      // Sent with Enter, from the field.
      const send = (popup) =>
        popup
          .findAllComponents({ name: "AppInput" })
          .find((input) => input.element === nameBox(popup).element)
          .vm.$emit("enter");
      send(wrapper);
      await flushPromises();
      expect(wrapper.vm.saving).toBe("new");

      // Read-only, so the name sent is the name shown; never disabled, which
      // cannot hold focus. Nor is it in the part of the popup that goes inert
      // for the save, which could not hold it either.
      expect(nameBox(wrapper).element.disabled).toBe(false);
      expect(nameBox(wrapper).element.readOnly).toBe(true);
      expect(wrapper.find(".rund").element.hasAttribute("inert")).toBe(true);
      expect(nameBox(wrapper).element.closest("[inert]")).toBeNull();
      expect(document.activeElement).toBe(nameBox(wrapper).element);
      // Escape still reaches a field that has the keys, and does not walk
      // away from a save that is out.
      await nameBox(wrapper).trigger("keydown", { key: "Escape" });
      expect(nameBox(wrapper).exists()).toBe(true);

      const refused = () => band(wrapper).find('[role="alert"]');
      post.reject(refusal(409, "A workflow is already called Taken."));
      await flushPromises();
      expect(refused().text()).toBe("A workflow is already called Taken.");
      expect(nameBox(wrapper).element.readOnly).toBe(false);
      expect(document.activeElement).toBe(nameBox(wrapper).element);

      // Refused again in the same words: the line is taken down while the
      // save is out and put up anew, which is what makes an alert speak twice.
      const once = refused().element;
      const again = held();
      saveWorkflowAsNew.mockReturnValue(again.promise);
      send(wrapper);
      await flushPromises();
      expect(refused().exists()).toBe(false);
      again.reject(refusal(409, "A workflow is already called Taken."));
      await flushPromises();
      expect(refused().text()).toBe("A workflow is already called Taken.");
      expect(refused().element).not.toBe(once);
      expect(document.activeElement).toBe(nameBox(wrapper).element);
    });
  });

  describe("Save as new…, and what is the run's", () => {
    it("keeps the pictures picked for this run, as Save does", async () => {
      const row = {
        slot_label: "LoadImage",
        input_name: "image",
        title: "Start frame",
        mode: "picker",
        fill: "request",
        picture_id: 9,
      };
      preflightWorkflowRun.mockResolvedValue({
        ok: true,
        runs: 1,
        groups: [{ reasons: [], picture_inputs: [row] }],
      });
      saveWorkflowAsNew.mockResolvedValue({ workflow_id: OTHER, name: "Mine" });
      const wrapper = await mountRun(fromCard);
      wrapper.vm.pickerFor = row;
      await wrapper.vm.onPicked({ id: 9 });
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      const pick = { slot_label: "LoadImage", input_name: "image", picture_id: 9 };
      expect(Object.values(wrapper.vm.picks)).toEqual([pick]);

      await button(wrapper, "Save as new…").trigger("click");
      wrapper.vm.newName = "Mine";
      await wrapper.vm.saveAsNew();
      await flushPromises();

      // The new workflow is this graph, slots and all: the pick is still its.
      expect(wrapper.vm.activeKey).toBe(OTHER);
      expect(Object.values(wrapper.vm.picks)).toEqual([pick]);
      expect(preflightWorkflowRun.mock.calls.at(-1)[0]).toMatchObject({
        workflow_id: OTHER,
        inputs: [pick],
      });
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0]).toMatchObject({
        workflow_id: OTHER,
        inputs: [pick],
      });
    });

    it("drops them when the owner picks another workflow, whose slots are other slots", async () => {
      const wrapper = await mountRun(picked);
      wrapper.vm.picks["LoadImage/image"] = {
        slot_label: "LoadImage",
        input_name: "image",
        picture_id: 9,
      };
      wrapper.vm.workflowId = OTHER;
      await flushPromises();
      expect(wrapper.vm.picks).toEqual({});
    });
  });

  describe("where focus is while a save is out", () => {
    const waitLine = (wrapper) => wrapper.find("footer p[tabindex='-1']");
    /** The caret in the form, which a click on a verb does not move in Safari. */
    function caretInForm(wrapper) {
      const box = wrapper.find(".rund input").element;
      box.focus();
      expect(document.activeElement).toBe(box);
    }
    async function edited() {
      const wrapper = await mountFocusable();
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      return wrapper;
    }

    it("waits in the footer when Save is clicked with the caret in the form, and is on Save after a refusal", async () => {
      const wrapper = await edited();
      caretInForm(wrapper);
      // A dispatched click, which focuses nothing: the button as Safari clicks it.
      const post = await pressSave(wrapper);
      // Not in the form, which is inert, and not on the page.
      expect(document.activeElement).toBe(waitLine(wrapper).element);
      expect(waitLine(wrapper).text()).toBe("Run waits for the save to finish.");

      post.reject(refusal(409, "This workflow got a newer version since."));
      await flushPromises();
      expect(band(wrapper).find('[role="alert"]').text()).toBe(
        "This workflow got a newer version since.",
      );
      expect(document.activeElement).toBe(button(wrapper, "Save").element);
    });

    it("does the same for the name question's own button", async () => {
      const post = held();
      saveWorkflowAsNew.mockReturnValue(post.promise);
      const wrapper = await edited();
      await button(wrapper, "Save as new…").trigger("click");
      await flushPromises();
      await nameBox(wrapper).setValue("Taken");
      caretInForm(wrapper);
      await button(wrapper, "Save as new workflow").trigger("click");
      await flushPromises();
      expect(document.activeElement).toBe(waitLine(wrapper).element);

      post.reject(refusal(409, "A workflow is already called Taken."));
      await flushPromises();
      expect(document.activeElement).toBe(
        button(wrapper, "Save as new workflow").element,
      );
    });

    it("leaves a keyboard that is on the verb where it is, through the save and its refusal", async () => {
      const wrapper = await edited();
      const save = button(wrapper, "Save").element;
      save.focus();
      const post = await pressSave(wrapper);
      expect(document.activeElement).toBe(save);
      post.reject(refusal(409, "This workflow got a newer version since."));
      await flushPromises();
      expect(document.activeElement).toBe(save);
    });

    it("is not dropped on the page when the band leaves, before the line that says Saved is there", async () => {
      const wrapper = await edited();
      button(wrapper, "Save").element.focus();
      const post = await pressSave(wrapper);
      // The POST has landed and the workflow is being read again: the band,
      // and the verb that had the keyboard, are gone.
      const reread = holdReread();
      post.resolve({ workflow_id: KEY, version: 4 });
      await flushPromises();
      expect(band(wrapper).exists()).toBe(false);
      expect(document.activeElement).toBe(waitLine(wrapper).element);
      expect(waitLine(wrapper).text()).toBe("Reading this workflow…");

      reread.resolve(await savedCard(KEY));
      await flushPromises();
      expect(document.activeElement).toBe(
        wrapper.find('[data-testid="rund-band-done"]').element,
      );
    });

    it("does not take focus from somewhere else that stays, when the band leaves", async () => {
      const wrapper = await edited();
      button(wrapper, "Save").element.focus();
      const post = await pressSave(wrapper);
      // Tabbed on to the footer while the save was out.
      const run = runButton(wrapper).element;
      run.focus();
      const reread = holdReread();
      post.resolve({ workflow_id: KEY, version: 4 });
      await flushPromises();
      expect(document.activeElement).toBe(run);
      reread.resolve(await savedCard(KEY));
      await flushPromises();
    });
  });

  describe("while a save is out", () => {
    it("holds Run back until the workflow is read again, and says why", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
      await flushPromises();
      const post = await pressSave(wrapper);
      expect(wrapper.vm.saving).toBe("save");
      expect(runButton(wrapper).attributes("aria-disabled")).toBe("true");
      expect(runWhy(wrapper)).toBe("Run waits for the save to finish.");
      await wrapper.vm.submit();

      // The POST has landed and the workflow is being read again: the edits
      // are off the form and its baseline is still the one before the save,
      // so a run now would send 8 steps over the 32 just saved.
      const reread = holdReread();
      post.resolve({ workflow_id: KEY, version: 4 });
      await flushPromises();
      expect(writes()).toHaveLength(1);
      expect(runButton(wrapper).attributes("aria-disabled")).toBe("true");
      await wrapper.vm.submit();
      expect(runWorkflowCard).not.toHaveBeenCalled();
      expect(wrapper.emitted("close")).toBeUndefined();

      reread.resolve(await savedCard(KEY));
      await flushPromises();
      expect(runButton(wrapper).attributes("aria-disabled")).toBeUndefined();
      expect(runWhy(wrapper)).toBe("");
      await wrapper.vm.submit();
      const body = runWorkflowCard.mock.calls[0][0];
      expect(sentValue(body, "steps").value).toBe(32);
      expect(body.models).toContainEqual({
        address: CKPT,
        filename: "juggernautXL.safetensors",
      });
    });

    it("takes the form out of reach, from the POST until the workflow is read again", async () => {
      const wrapper = await mountRun(fromCard);
      const form = () => wrapper.find(".rund").element;
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(form().hasAttribute("inert")).toBe(false);

      const post = await pressSave(wrapper);
      // An edit made now would be in neither the save nor the form that
      // comes back. Every control that edits is in the part that is inert.
      expect(form().hasAttribute("inert")).toBe(true);
      const controls = [
        ...wrapper.findAll("input, select, textarea"),
        ...["AppInput", "AppSelect", "AppTextarea"].flatMap((name) =>
          wrapper.findAllComponents({ name }),
        ),
      ];
      expect(controls.length).toBeGreaterThan(5);
      for (const control of controls) {
        expect(control.element.closest("[inert]")).toBe(form());
      }
      // The band and the footer are not: the save's own spinner, Cancel and
      // the sentence Run waits by stay where the keyboard can be.
      for (const live of [band(wrapper), runButton(wrapper), button(wrapper, "Cancel")]) {
        expect(live.element.closest("[inert]")).toBeNull();
      }

      const reread = holdReread();
      post.resolve({ workflow_id: KEY, version: 4 });
      await flushPromises();
      expect(form().hasAttribute("inert")).toBe(true);
      reread.resolve(await savedCard(KEY));
      await flushPromises();
      expect(form().hasAttribute("inert")).toBe(false);
    });

    it("lets nothing close the popup, or take its form somewhere else", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      const post = await pressSave(wrapper);

      const escape = { stopPropagation: vi.fn() };
      wrapper.vm.close();
      wrapper.vm.onEscape(escape);
      wrapper.vm.openInWorkflows();
      wrapper.vm.editLoras(KEY);
      expect(wrapper.emitted("close")).toBeUndefined();
      expect(push).not.toHaveBeenCalled();
      // Nor a recipe made of a form the save is still putting right.
      expect(button(wrapper, "Save as recipe").element.disabled).toBe(true);
      // An Escape this popup did not act on is not swallowed either.
      expect(escape.stopPropagation).not.toHaveBeenCalled();

      post.resolve({ workflow_id: KEY, version: 4 });
      await flushPromises();
      wrapper.vm.openInWorkflows();
      expect(wrapper.emitted("close")).toHaveLength(1);
      expect(push).toHaveBeenCalledWith({ name: "workflows", query: { workflow: KEY } });
    });
  });

  describe("pre-flights answered out of order", () => {
    const row = {
      slot_label: "LoadImage",
      input_name: "image",
      title: "Start frame",
      mode: "picker",
      fill: "request",
      picture_id: 9,
    };
    const NEWER = {
      ok: true,
      runs: 3,
      groups: [
        { reasons: [], picture_inputs: [row], prompt: { positive_text: "the newer prompt" } },
      ],
    };
    // Everything a pre-flight answer writes, each unlike the newer one's.
    const OLDER = {
      ok: false,
      runs: 1,
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ folder: "checkpoints", file: "realvisXL_v5.safetensors" }],
            },
          ],
          picture_inputs: [],
          prompt: { positive_text: "the older prompt" },
          unplaced_loras: ["mira_v2.safetensors"],
          workflow_id: KEY,
        },
      ],
    };
    const asked = () => {
      const asks = [];
      preflightWorkflowRun.mockImplementation(() => {
        const ask = held();
        asks.push(ask);
        return ask.promise;
      });
      return asks;
    };
    const newerStands = (wrapper) => {
      expect(wrapper.vm.reasons).toEqual([]);
      expect(wrapper.vm.bypassed).toEqual([]);
      expect(wrapper.vm.pictureInputs).toEqual([row]);
      expect(wrapper.vm.inputsKey).toBe(KEY);
      expect(wrapper.vm.plannedRuns).toBe(3);
      expect(wrapper.vm.graphPrompt).toEqual({ positive_text: "the newer prompt" });
      expect(wrapper.vm.preflightError).toBe("");
      // No offer for a checkpoint the newer answer does not call missing.
      expect(wrapper.vm.checkpointFix).toBeNull();
      expect(wrapper.vm.preflighting).toBe(false);
      expect(wrapper.vm.canRun).toBe(true);
    };

    it.each([
      [
        "leave what the newer one said, whatever the older one says after it",
        (ask) => ask.resolve(OLDER),
      ],
      [
        "leave it too when the older one comes back refused",
        (ask) => ask.reject(refusal(400, "Name exactly one source.")),
      ],
    ])("%s", async (_name, settleOlder) => {
      const wrapper = await mountRun(fromCard);
      const asks = asked();
      const older = wrapper.vm.runPreflight();
      const newer = wrapper.vm.runPreflight();
      asks[1].resolve(NEWER);
      await newer;
      settleOlder(asks[0]);
      await older;
      await flushPromises();
      newerStands(wrapper);
    });
  });

  describe("while the picker moves to another workflow", () => {
    beforeEach(() => {
      getWorkflowChanges.mockImplementation(async (id) =>
        changesOf(id, { version: id === OTHER ? 9 : 3 }),
      );
    });

    it("offers no save and sends no run until the workflow arrived at is read", async () => {
      const wrapper = await mountRun(picked);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await wrapper.vm.skipGraphLora(wrapper.vm.loras[1]);
      await flushPromises();
      expect(band(wrapper).exists()).toBe(true);

      const next = held();
      getWorkflowCard.mockReturnValueOnce(next.promise);
      wrapper.vm.workflowId = OTHER;
      // From the instant it moves, not from the next read's answer.
      expect(wrapper.vm.band.count).toBe(0);
      expect(wrapper.vm.canRun).toBe(false);
      await flushPromises();
      expect(band(wrapper).exists()).toBe(false);
      expect(runButton(wrapper).attributes("aria-disabled")).toBe("true");
      expect(runWhy(wrapper)).toBe("Reading this workflow…");
      // The form is still the workflow left: its removed loader, its version.
      await wrapper.vm.saveOver();
      wrapper.vm.newName = "Mine";
      await wrapper.vm.saveAsNew();
      await wrapper.vm.submit();
      await wrapper.vm.freeField(field(wrapper, "denoise"));
      expect(writes()).toEqual([]);
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();
      expect(runWorkflowCard).not.toHaveBeenCalled();
      expect(button(wrapper, "Save as recipe").element.disabled).toBe(true);
      // Nor its pin list, which is written whole.
      expect(setWorkflowPins).not.toHaveBeenCalled();

      next.resolve({ card: ownCard({ id: OTHER }) });
      await flushPromises();
      // Steps came along, as an edit the owner brought; the loader taken out
      // was a row of the workflow left, and this one's rows are its own.
      expect(said(wrapper)).toContain("1 change from the workflow");
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(writes()).toEqual([
        [
          OTHER,
          {
            changes: {
              models: [],
              loras: [],
              skip_loras: [],
              stages: {},
              values: [{ slot_label: "KSampler", input_name: "steps", value: 32 }],
            },
            version: 9,
          },
        ],
      ]);
    });

    it("is not read until its loaders and what waits on it are, as well as its card", async () => {
      const wrapper = await mountRun(picked);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      const waits = held();
      getWorkflowChanges.mockReturnValueOnce(waits.promise);
      wrapper.vm.workflowId = OTHER;
      await flushPromises();
      expect(wrapper.vm.card.id).toBe(OTHER);
      expect(band(wrapper).exists()).toBe(false);
      await wrapper.vm.submit();
      expect(runWorkflowCard).not.toHaveBeenCalled();

      waits.resolve(changesOf(OTHER, { version: 9 }));
      await flushPromises();
      expect(band(wrapper).exists()).toBe(true);
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].workflow_id).toBe(OTHER);
    });

    it("keeps the workflow it ended on when an earlier move answers last", async () => {
      const wrapper = await mountRun(picked);
      const slow = held();
      getWorkflowCard.mockReturnValueOnce(slow.promise);
      wrapper.vm.workflowId = OTHER;
      wrapper.vm.workflowId = THIRD;
      await flushPromises();
      slow.resolve({ card: ownCard({ id: OTHER, defaults: [def("Steps", "steps", 20)] }) });
      await flushPromises();
      expect(wrapper.vm.card.id).toBe(THIRD);
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
      await wrapper.vm.submit();
      const body = runWorkflowCard.mock.calls[0][0];
      expect(body.workflow_id).toBe(THIRD);
      expect(sentValue(body, "steps").value).toBe(8);
    });

    it("does not report a workflow it has since left as the one that could not be read", async () => {
      const wrapper = await mountRun(picked);
      const slow = held();
      getWorkflowCard.mockReturnValueOnce(slow.promise);
      wrapper.vm.workflowId = OTHER;
      wrapper.vm.workflowId = THIRD;
      await flushPromises();
      slow.reject(new Error("Network Error"));
      await flushPromises();
      expect(wrapper.vm.loadFailed).toBe("");
      expect(wrapper.vm.canRun).toBe(true);
    });

    it("runs nothing of a workflow that could not be read", async () => {
      const wrapper = await mountRun(picked);
      getWorkflowCard.mockRejectedValueOnce(new Error("Network Error"));
      wrapper.vm.workflowId = OTHER;
      await flushPromises();
      const failure = wrapper.find('[role="alert"]');
      expect(failure.exists()).toBe(true);
      expect(runButton(wrapper).attributes("aria-disabled")).toBe("true");
      expect(runWhy(wrapper)).toBe(failure.text());
      await wrapper.vm.submit();
      expect(runWorkflowCard).not.toHaveBeenCalled();
    });
  });

  describe("a save that landed and could not be read again", () => {
    const failure = (wrapper) => wrapper.find('[role="alert"]');

    it("is said as that, never as a save that failed, and leaves nothing stale to run", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await wrapper.vm.skipGraphLora(wrapper.vm.loras[1]);
      await flushPromises();
      getWorkflowCard.mockRejectedValueOnce(new Error("Network Error"));
      await button(wrapper, "Save").trigger("click");
      await flushPromises();

      // The write was made, and everything is told so.
      expect(writes()).toHaveLength(1);
      expect(useRunDialogStore().saved).toMatchObject({ workflowId: KEY, savedAs: "" });
      expect(wrapper.vm.bandError).toBe("");
      expect(failure(wrapper).text()).toBe(
        "Saved to this workflow. It could not be read again, so it cannot be run from here: close this and open it again.",
      );
      // No form is left to say the saved changes are unsaved, or to send the
      // values from before the save over it, or to save a second time.
      expect(band(wrapper).exists()).toBe(false);
      expect(wrapper.find(".rund").exists()).toBe(false);
      expect(runButton(wrapper).attributes("aria-disabled")).toBe("true");
      expect(runWhy(wrapper)).toBe(failure(wrapper).text());
      await wrapper.vm.submit();
      await wrapper.vm.saveOver();
      expect(runWorkflowCard).not.toHaveBeenCalled();
      expect(writes()).toHaveLength(1);
      // Closing is what is left, and it works.
      wrapper.vm.close();
      expect(wrapper.emitted("close")).toHaveLength(1);
    });

    it("does not offer Save as new again once the new workflow exists", async () => {
      saveWorkflowAsNew.mockResolvedValue({ workflow_id: OTHER, name: "Mine" });
      const wrapper = await mountRun(picked);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      await button(wrapper, "Save as new…").trigger("click");
      wrapper.vm.newName = "Mine";
      // The picker's list, read again for the row of the new workflow.
      listWorkflowCards.mockRejectedValueOnce(new Error("Network Error"));
      await wrapper.vm.saveAsNew();
      await flushPromises();

      expect(wrapper.vm.bandError).toBe("");
      expect(failure(wrapper).text()).toContain("Saved as Mine.");
      expect(useRunDialogStore().saved).toMatchObject({ workflowId: KEY, savedAs: OTHER });
      expect(band(wrapper).exists()).toBe(false);
      wrapper.vm.newName = "Mine";
      await wrapper.vm.saveAsNew();
      await wrapper.vm.submit();
      expect(saveWorkflowAsNew).toHaveBeenCalledTimes(1);
      expect(runWorkflowCard).not.toHaveBeenCalled();
    });

    it("still says Saved where only what waits on the workflow could not be read", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      wrapper.vm.pickCheckpoint("juggernautXL.safetensors");
      await flushPromises();
      getWorkflowCard.mockImplementation(savedCard);
      getWorkflowChanges.mockRejectedValue(new Error("offline"));
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      // No band without that read, and the line that says Saved is not its.
      expect(band(wrapper).exists()).toBe(false);
      expect(wrapper.find('[data-testid="rund-band-done"]').text()).toBe(
        "Saved to this workflow.",
      );
      await wrapper.vm.submit();
      expect(sentValue(runWorkflowCard.mock.calls[0][0], "steps").value).toBe(32);
    });
  });

  describe("the band's announcement", () => {
    const live = (wrapper) => wrapper.find('[data-testid="rund-band-live"]');
    const WAITS = { waiting: { values: [{ slot_label: "KSampler", input_name: "steps", value: 30 }] }, count: 1 };

    it("is made in a region that was in the document before it had anything to say", async () => {
      getWorkflowChanges.mockImplementation(async (id) => changesOf(id, WAITS));
      const reading = held();
      preflightWorkflowRun.mockReturnValueOnce(reading.promise);
      const wrapper = await mountRun(fromCard);
      // Still reading: the changes are known, the band is not drawn, and the
      // region that will say it is here and silent.
      expect(wrapper.vm.loading).toBe(true);
      const region = live(wrapper).element;
      expect(region.getAttribute("role")).toBe("status");
      expect(region.textContent.trim()).toBe("");

      reading.resolve({ ok: true, runs: 1, groups: [] });
      await flushPromises();
      expect(band(wrapper).exists()).toBe(true);
      expect(live(wrapper).element).toBe(region);
      expect(region.textContent).toContain("differs from the workflow");
    });

    it("is unsaid when the band goes, so its coming back is a change to say", async () => {
      const wrapper = await mountRun(fromCard);
      const region = live(wrapper).element;
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      const arrival = region.textContent.trim();
      expect(arrival).toContain("differs from the workflow");
      wrapper.vm.resetValue(field(wrapper, "steps"));
      await flushPromises();
      expect(region.textContent.trim()).toBe("");
      wrapper.vm.setValue(field(wrapper, "steps"), 33);
      await flushPromises();
      expect(region.textContent.trim()).toBe(arrival);
      expect(live(wrapper).element).toBe(region);
    });

    it("is said again for the next workflow, from the same region", async () => {
      getWorkflowChanges.mockImplementation(async (id) => changesOf(id, WAITS));
      const wrapper = await mountRun(picked);
      const region = live(wrapper).element;
      const arrival = region.textContent.trim();
      expect(arrival).toContain("differs from the workflow");
      const next = held();
      getWorkflowCard.mockReturnValueOnce(next.promise);
      wrapper.vm.workflowId = OTHER;
      await flushPromises();
      expect(region.textContent.trim()).toBe("");
      next.resolve({ card: ownCard({ id: OTHER }) });
      await flushPromises();
      expect(region.textContent.trim()).toBe(arrival);
      expect(live(wrapper).element).toBe(region);
    });
  });

  describe("nothing is saved unless a save is pressed", () => {
    it("runs without saving", async () => {
      const wrapper = await mountRun(fromCard);
      await changeThree(wrapper);
      await wrapper.vm.submit();
      expect(runWorkflowCard).toHaveBeenCalledTimes(1);
      expect(writes()).toEqual([]);
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();
    });

    it("closes without saving", async () => {
      const wrapper = await mountRun(fromCard);
      await changeThree(wrapper);
      wrapper.vm.close();
      wrapper.unmount();
      await flushPromises();
      expect(writes()).toEqual([]);
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();
    });
  });

  describe("the workflow's own LoRA loaders", () => {
    it("are rows at the strength the default recipe runs them, and only those it runs", async () => {
      const wrapper = await mountRun(fromCard);
      expect(getLoraChain).toHaveBeenCalledWith(KEY);
      expect(
        wrapper.vm.loras.map((row) => [row.node_id, row.sha256, row.strength]),
      ).toEqual([
        ["7", MIRA_SHA, 0.6],
        ["8", GRAIN_SHA, 0.4],
      ]);
      // Every default LoRA has its row, so no line repeats them.
      expect(wrapper.text()).not.toContain("run as it stores them");
      // Add LoRA never fills a loader the workflow left empty.
      wrapper.vm.addLora();
      expect(wrapper.vm.loras).toHaveLength(2);
      expect(wrapper.vm.addedLoras).toHaveLength(1);
    });

    it("lists every loader of a workflow with no pictures, less a person's", async () => {
      getWorkflowCard.mockResolvedValue({
        card: ownCard({
          default_recipe: { sampled: 0, models: [], loras: [], values: [], stages: {} },
        }),
      });
      listAdapters.mockResolvedValue([
        {
          sha256: "p".repeat(64),
          filename: "example-person.safetensors",
          attachments: [{ entity_type: "character", entity_id: 3 }],
        },
      ]);
      const wrapper = await mountRun(fromCard);
      expect(wrapper.vm.loras.map((row) => row.node_id)).toEqual(["7", "8"]);
    });

    it("names the default LoRAs the graph has no loader for", async () => {
      getLoraChain.mockResolvedValue({ loaders: [CHAIN.loaders[0]], lanes: [] });
      const wrapper = await mountRun(fromCard);
      expect(wrapper.text()).toContain(
        "This workflow also loads film-grain-35mm, as it stores it.",
      );
    });

    it("counts a changed strength, keeps what it was readable, and puts it back", async () => {
      const wrapper = await mountRun(fromCard);
      wrapper.vm.loras[0].strength = 0.9;
      await flushPromises();
      expect(said(wrapper)).toContain("1 LoRA changed.");
      // CLIP follows the model strength where the loader had them as one.
      expect(wrapper.vm.formChanges.loras).toEqual([
        {
          node_id: "7",
          field: "lora_name",
          sha256: MIRA_SHA,
          strength_model: 0.9,
          strength_clip: 0.9,
        },
      ]);
      const was = wrapper.find('[data-testid="rund-lora-was"]');
      const chip = was.findComponent({ name: "RunResetChip" });
      expect(chip.props("value")).toBe("mira_v2 · 0.6");
      await chip.trigger("click");
      await flushPromises();
      expect(wrapper.vm.loras[0].strength).toBe(0.6);
      expect(band(wrapper).exists()).toBe(false);
    });

    it("leaves a CLIP strength the graph set apart where it was", async () => {
      getLoraChain.mockResolvedValue({
        loaders: [loader("7", "mira_v2.safetensors", MIRA_SHA, 0.6, 0.3)],
        lanes: [],
      });
      const wrapper = await mountRun(fromCard);
      wrapper.vm.loras[0].strength = 0.9;
      await flushPromises();
      expect(wrapper.vm.formChanges.loras[0].strength_clip).toBe(0.3);
    });
  });

  describe("the model rows", () => {
    const VAES = {
      replacements: [
        { id: 4, filename: "vae/sdxl_vae.safetensors", display_name: "SDXL VAE" },
        { id: 5, filename: "ae.safetensors" },
      ],
    };
    const row = (wrapper) => wrapper.find('[data-testid="rund-model"]');
    const select = (wrapper) => row(wrapper).findComponent({ name: "AppSelect" });

    it("is a picker over what the server says the loader can load", async () => {
      readModelSwap.mockImplementation(async (_key, { slotKind }) =>
        slotKind === "vae" ? VAES : { replacements: [] },
      );
      const wrapper = await mountRun(fromCard);
      expect(readModelSwap).toHaveBeenCalledWith(KEY, {
        replacing: "sdxl_vae.safetensors",
        slotKind: "vae",
      });
      expect(row(wrapper).text()).toContain("VAE");
      // The shelf's spelling of the workflow's own file is the row as saved.
      expect(select(wrapper).props("options")).toEqual([
        { value: "sdxl_vae.safetensors", label: "SDXL VAE" },
        { value: "ae.safetensors", label: "ae" },
      ]);
      expect(select(wrapper).props("modelValue")).toBe("sdxl_vae.safetensors");
      // No file is shown with its extension.
      for (const option of select(wrapper).props("options")) {
        expect(option.label).not.toMatch(/\.safetensors/);
      }

      await select(wrapper).vm.$emit("update:modelValue", "ae.safetensors");
      await flushPromises();
      expect(said(wrapper)).toContain("1 change from the workflow");
      expect(said(wrapper)).toContain("VAE.");
      const chip = row(wrapper).findComponent({ name: "RunResetChip" });
      expect(chip.props("value")).toBe("sdxl vae");
      expect(chip.props("label")).toBe("VAE");
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].models).toEqual([
        { address: CKPT, filename: "realvisXL_v5.safetensors" },
        { address: VAE, filename: "ae.safetensors" },
      ]);
    });

    it("is read as text where nothing says what the loader can load", async () => {
      readModelSwap.mockRejectedValue(new Error("offline"));
      const wrapper = await mountRun(fromCard);
      expect(select(wrapper).exists()).toBe(false);
      expect(row(wrapper).find(".rund-fixed-value").text()).toBe("sdxl vae");
      // The checkpoint keeps its typed box.
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(true);
    });

    it("makes the checkpoint a picker too, whether or not it is missing", async () => {
      readModelSwap.mockImplementation(async (_key, { slotKind }) =>
        slotKind === "checkpoint"
          ? { replacements: [{ id: 7, filename: "juggernautXL.safetensors" }] }
          : { replacements: [] },
      );
      const wrapper = await mountRun(fromCard);
      expect(wrapper.vm.checkpointFix).toBeNull();
      expect(wrapper.vm.checkpointOptions).toEqual([
        { value: "realvisXL_v5.safetensors", label: "realvisXL v5" },
        { value: "juggernautXL.safetensors", label: "juggernautXL" },
      ]);
      expect(wrapper.find("[aria-label='Checkpoint']").exists()).toBe(false);
    });

    describe("with a checkpoint pick waiting in the Workflow tab", () => {
      const SHELF = {
        replacements: [
          { id: 7, filename: "juggernautXL.safetensors" },
          { id: 8, filename: "dreamshaper.safetensors" },
        ],
      };
      const shelfIs = (answer) =>
        readModelSwap.mockImplementation((_key, { slotKind }) =>
          slotKind === "checkpoint" ? answer : Promise.resolve({ replacements: [] }),
        );

      beforeEach(() => {
        getWorkflowChanges.mockImplementation(async (id) =>
          changesOf(id, {
            waiting: { models: [{ address: CKPT, filename: "juggernautXL.safetensors" }] },
            count: 1,
          }),
        );
      });

      it("is a picker all the same, whichever read answers first", async () => {
        // The shelf answers before the pick is laid over the form.
        shelfIs(Promise.resolve(SHELF));
        const first = await mountRun(fromCard);
        expect(first.vm.checkpointOptions.map((option) => option.value)).toEqual([
          "realvisXL_v5.safetensors",
          "juggernautXL.safetensors",
          "dreamshaper.safetensors",
        ]);

        // And after: a laid pick is not a name being typed, so it holds no
        // list back.
        const shelf = held();
        shelfIs(shelf.promise);
        const second = await mountRun(fromCard);
        expect(second.vm.checkpointValue).toBe("juggernautXL.safetensors");
        shelf.resolve(SHELF);
        await flushPromises();
        expect(second.vm.checkpointOptions).toEqual(first.vm.checkpointOptions);
        expect(second.find("[aria-label='Checkpoint']").exists()).toBe(false);
      });

      it("is in the list a replaced checkpoint is offered, though the shelf did not name it", async () => {
        getWorkflowCard.mockImplementation(async (key) => ({
          card: ownCard({ id: key }),
          model_fixes: [
            {
              slot_kind: "checkpoint",
              was: "realvisXL_v5.safetensors",
              now: "dreamshaper.safetensors",
            },
          ],
        }));
        shelfIs(Promise.resolve({ replacements: [{ id: 9, filename: "sdxl_base.safetensors" }] }));
        const wrapper = await mountRun(fromCard);
        expect(wrapper.vm.checkpointFix?.was).toBe("realvisXL_v5.safetensors");
        // A select whose value is not an option draws empty.
        expect(wrapper.vm.checkpointOptions.map((option) => option.value)).toEqual([
          "juggernautXL.safetensors",
          "dreamshaper.safetensors",
          "sdxl_base.safetensors",
        ]);
      });
    });

    it("is not drawn on a picture's own run", async () => {
      const wrapper = await mountRun();
      expect(row(wrapper).exists()).toBe(false);
    });
  });

  describe("what waits in the Workflow tab", () => {
    const WAITING = {
      models: [{ address: CKPT, filename: "juggernautXL.safetensors" }],
      loras: [
        {
          node_id: "7",
          field: "lora_name",
          sha256: MIRA_SHA,
          strength_model: 0.9,
          strength_clip: 0.9,
        },
      ],
      skip_loras: [{ node_id: "8", field: "lora_name" }],
      stages: { upscale: false },
      values: [{ slot_label: "KSampler", input_name: "steps", value: 28 }],
    };
    const setWorkflowChanges = vi.fn();

    beforeEach(() => {
      setWorkflowChanges.mockReset();
      getWorkflowChanges.mockImplementation(async (id) =>
        changesOf(id, { waiting: WAITING, count: 5, version: 6 }),
      );
    });

    it("is laid over the form: counted, run, and shown against the SAVED value", async () => {
      const wrapper = await mountRun(fromCard);
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(said(wrapper)).toContain("5 changes from the workflow");
      expect(said(wrapper)).toContain(
        "Checkpoint, 1 LoRA changed, 1 LoRA removed, Upscale off, Steps.",
      );
      // The chip names what the workflow has saved, not what waits.
      const steps = wrapper
        .findAllComponents({ name: "RunResetChip" })
        .find((chip) => chip.props("label") === "Steps");
      expect(steps.props("value")).toBe(8);
      expect(wrapper.vm.currentValue(field(wrapper, "steps"))).toBe(28);
      expect(wrapper.vm.checkpointValue).toBe("juggernautXL.safetensors");
      expect(wrapper.vm.loras[1].skipped).toBe(true);
      expect(wrapper.vm.stageOn("upscale")).toBe(false);

      // "Run with changes…" runs the unsaved graph.
      await wrapper.vm.submit();
      const body = runWorkflowCard.mock.calls[0][0];
      expect(body.models).toEqual(WAITING.models);
      expect(body.loras).toEqual(WAITING.loras);
      expect(body.skip_loras).toEqual(WAITING.skip_loras);
      expect(body.skip_stages).toEqual(["upscale"]);
      expect(body.values).toContainEqual(WAITING.values[0]);
    });

    it("is not this popup's work, so closing is not guarded", async () => {
      const wrapper = await mountRun(fromCard);
      expect(wrapper.vm.band.count).toBe(5);
      expect(wrapper.vm.dirty).toBe(false);
    });

    it("is saved against the version the read answered", async () => {
      const wrapper = await mountRun(fromCard);
      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(writes()[0][1].version).toBe(6);
    });

    it("states the form's own answer for every waiting change the owner undid", async () => {
      // The server saves what waits with the request on top. Left unsaid, each
      // of these would be saved as it waits, though the form put it back.
      const wrapper = await mountRun(fromCard);
      wrapper.vm.pickCheckpoint("realvisXL_v5.safetensors");
      wrapper.vm.resetLora(wrapper.vm.loras[0]);
      await wrapper.vm.useGraphLora(wrapper.vm.loras[1]);
      await wrapper.vm.setStage("upscale", true);
      wrapper.vm.resetValue(field(wrapper, "steps"));
      // One change of the owner's own, so there is something to save.
      wrapper.vm.setValue(field(wrapper, "cfg"), 4);
      await flushPromises();
      expect(said(wrapper)).toContain("1 change from the workflow");

      await button(wrapper, "Save").trigger("click");
      await flushPromises();
      expect(writes()[0][1].changes).toEqual({
        // The saved file for that address.
        models: [{ address: CKPT, filename: "realvisXL_v5.safetensors" }],
        // Each loader set with its own LoRA and strength, instead of changed
        // or removed.
        // The clip strength with it, by the rule a changed row follows: the
        // graph has loader 7 at 0.8 for both, so both are saved at 0.6.
        loras: [
          {
            node_id: "7",
            field: "lora_name",
            sha256: MIRA_SHA,
            strength_model: 0.6,
            strength_clip: 0.6,
          },
          {
            node_id: "8",
            field: "lora_name",
            sha256: GRAIN_SHA,
            strength_model: 0.4,
            strength_clip: 0.4,
          },
        ],
        skip_loras: [],
        // The stage's boolean.
        stages: { upscale: true },
        values: [
          { slot_label: "KSampler", input_name: "cfg", value: 4 },
          { slot_label: "KSampler", input_name: "steps", value: 8 },
        ],
      });
    });

    it("carries a waiting change it has no control for, and counts it", async () => {
      getWorkflowChanges.mockImplementation(async (id) =>
        changesOf(id, {
          waiting: {
            skip_loras: [{ node_id: "44", field: "lora_name" }],
            values: [{ slot_label: "Other", input_name: "tile", value: 512 }],
          },
          count: 2,
        }),
      );
      const wrapper = await mountRun(fromCard);
      expect(said(wrapper)).toContain("2 changes from the workflow");
      await wrapper.vm.submit();
      const body = runWorkflowCard.mock.calls[0][0];
      expect(body.skip_loras).toEqual([{ node_id: "44", field: "lora_name" }]);
      expect(body.values).toContainEqual({
        slot_label: "Other",
        input_name: "tile",
        value: 512,
      });
      // The form cannot answer for them, so a save leaves them to what waits.
      expect(wrapper.vm.saveChanges).toEqual({
        models: [],
        loras: [],
        skip_loras: [],
        stages: {},
        values: [],
      });
    });

    it("leaves a pick named by digest as it waits, rather than answering over it", async () => {
      // The form holds files, so a pick named by digest is not laid on its
      // row. Answered for all the same, a save would put the row's own file
      // back over the pick that waits.
      const pick = { address: VAE, sha256: "v".repeat(64) };
      getWorkflowChanges.mockImplementation(async (id) =>
        changesOf(id, { waiting: { models: [pick] }, count: 1 }),
      );
      const wrapper = await mountRun(fromCard);
      expect(wrapper.vm.modelEdits).toEqual({});
      expect(said(wrapper)).toContain("1 change from the workflow");
      wrapper.vm.setValue(field(wrapper, "steps"), 32);
      await flushPromises();
      expect(wrapper.vm.saveChanges.models).toEqual([]);
      await wrapper.vm.submit();
      expect(runWorkflowCard.mock.calls[0][0].models).toContainEqual(pick);
    });

    it("is read again for the workflow the picker moves to", async () => {
      listWorkflowCards.mockResolvedValue({
        cards: [
          { id: KEY, name: "One" },
          { id: OTHER, name: "Other" },
        ],
      });
      getWorkflowChanges.mockImplementation(async (id) =>
        changesOf(id, id === OTHER ? { version: 9 } : { waiting: WAITING, version: 6 }),
      );
      const wrapper = await mountRun(picked);
      expect(wrapper.vm.band.count).toBe(5);
      wrapper.vm.workflowId = OTHER;
      await flushPromises();
      expect(getLoraChain).toHaveBeenLastCalledWith(OTHER);
      expect(wrapper.vm.changesState.version).toBe(9);
      // What waited on the first workflow's loaders is not laid on this one.
      expect(wrapper.vm.loras.every((entry) => !entry.skipped)).toBe(true);
    });

    // A later save clears what the run saw waiting and keeps what was edited
    // in the tab since. Only the popup knows what it saw, so the run says it.
    describe("as the run says it saw it", () => {
      const NOTHING = { models: [], loras: [], skip_loras: [], stages: {}, values: [] };
      const waited = () => runWorkflowCard.mock.calls[0][0].waited;

      it("is the whole set the form was laid from", async () => {
        const wrapper = await mountRun(fromCard);
        await wrapper.vm.submit();
        expect(waited()).toEqual(WAITING);
      });

      it("is what was read, whatever the owner undid of it in the form", async () => {
        const wrapper = await mountRun(fromCard);
        wrapper.vm.resetValue(field(wrapper, "steps"));
        await wrapper.vm.setStage("upscale", true);
        await flushPromises();
        await wrapper.vm.submit();
        const body = runWorkflowCard.mock.calls[0][0];
        expect(sentValue(body, "steps").value).toBe(8);
        expect(body).not.toHaveProperty("skip_stages");
        expect(body.waited).toEqual(WAITING);
      });

      it("is an empty set, every list present, where nothing waited", async () => {
        getWorkflowChanges.mockImplementation(async (id) => changesOf(id));
        const wrapper = await mountRun(fromCard);
        await wrapper.vm.submit();
        expect(waited()).toEqual(NOTHING);
      });

      it("is not said on a run from pictures, which reads none of it", async () => {
        const wrapper = await mountRun();
        await wrapper.vm.submit();
        expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("waited");
      });

      it("is not said where it could not be read", async () => {
        getWorkflowChanges.mockRejectedValue(new Error("offline"));
        const wrapper = await mountRun(fromCard);
        await wrapper.vm.submit();
        expect(runWorkflowCard.mock.calls[0][0]).not.toHaveProperty("waited");
      });

      it("is the reading of the workflow the picker moved to", async () => {
        const cfg = { slot_label: "KSampler", input_name: "cfg", value: 5 };
        getWorkflowChanges.mockImplementation(async (id) =>
          changesOf(id, { waiting: id === OTHER ? { values: [cfg] } : WAITING }),
        );
        const wrapper = await mountRun(picked);
        wrapper.vm.workflowId = OTHER;
        await flushPromises();
        await wrapper.vm.submit();
        expect(runWorkflowCard.mock.calls[0][0].workflow_id).toBe(OTHER);
        expect(waited()).toEqual({ ...NOTHING, values: [cfg] });
      });

    });

    // Picked, left and picked again, the first pick's card answering last:
    // two reads of one workflow, which no check of the key can tell apart.
    describe("with an earlier read of the same workflow still out", () => {
      /** The popup back on OTHER and read, and the first read's held card. */
      async function backOnOther() {
        const wrapper = await mountRun(picked);
        const slow = held();
        getWorkflowCard.mockReturnValueOnce(slow.promise);
        wrapper.vm.workflowId = OTHER;
        wrapper.vm.workflowId = THIRD;
        await flushPromises();
        wrapper.vm.workflowId = OTHER;
        await flushPromises();
        return { wrapper, slow };
      }

      it("keeps the form the newer read made when the older one lands", async () => {
        const { wrapper, slow } = await backOnOther();
        const rows = JSON.stringify(wrapper.vm.loras);
        const reads = getWorkflowChanges.mock.calls.length;
        // Whatever the older read would go on to read is never answered.
        getWorkflowChanges.mockReturnValue(held().promise);
        getLoraChain.mockReturnValue(held().promise);
        slow.resolve({ card: ownCard({ id: OTHER, defaults: [def("Steps", "steps", 20)] }) });
        await flushPromises();

        expect(getWorkflowChanges).toHaveBeenCalledTimes(reads);
        expect(wrapper.vm.changesState.version).toBe(6);
        expect(said(wrapper)).toContain("5 changes from the workflow");
        expect(JSON.stringify(wrapper.vm.loras)).toBe(rows);
        expect(wrapper.vm.loras.map((row) => row.skipped)).toEqual([false, true]);
        expect(field(wrapper, "cfg")).toBeTruthy();
        expect(wrapper.vm.canRun).toBe(true);

        await wrapper.vm.submit();
        const body = runWorkflowCard.mock.calls[0][0];
        expect(body.loras).toEqual(WAITING.loras);
        expect(body.skip_loras).toEqual(WAITING.skip_loras);
        expect(body.models).toEqual(WAITING.models);
        expect(sentValue(body, "steps").value).toBe(28);
        expect(body.waited).toEqual(WAITING);
      });

      it("is not taken down when the older read fails", async () => {
        const { wrapper, slow } = await backOnOther();
        slow.reject(new Error("Network Error"));
        await flushPromises();
        expect(wrapper.vm.loadFailed).toBe("");
        expect(wrapper.find('[role="alert"]').exists()).toBe(false);
        expect(said(wrapper)).toContain("5 changes from the workflow");
        expect(wrapper.vm.canRun).toBe(true);
      });
    });
  });

  describe("the offer the Tasks tab makes afterwards", () => {
    it("is recorded with the band's own words when a changed run is queued", async () => {
      const wrapper = await mountRun(fromCard);
      await changeThree(wrapper);
      await wrapper.vm.submit();
      expect(useRunDialogStore().ranChanges[KEY]).toEqual({
        name: "Cinematic portrait",
        count: 3,
        text: "Checkpoint, 1 LoRA removed, Steps.",
        promptIds: ["p1"],
        finished: false,
      });
    });

    it("is taken down by the workflow's next run when that one changes nothing", async () => {
      const store = useRunDialogStore();
      store.ranWith(KEY, { name: "x", count: 1, text: "Steps.", promptIds: ["p0"] });
      const wrapper = await mountRun(fromCard);
      await wrapper.vm.submit();
      expect(store.ranChanges[KEY]).toBeUndefined();
    });
  });
});

describe("a LoRA row's ×, for the keyboard", () => {
  // The × goes with its row, and focus with it: to the page, unless it is
  // handed on. To the × that moved up, else to Add LoRA.
  async function mountReal() {
    const wrapper = mount(RunDialog, {
      props: { source: { kind: "card", workflowId: KEY }, context: {} },
      attachTo: document.body,
      global: {
        stubs: {
          ...globalOpts.global.stubs,
          AppBarButton: { template: "<button type='button'><slot /></button>" },
        },
      },
    });
    await flushPromises();
    return wrapper;
  }
  const removes = (wrapper) => wrapper.findAll(".rund-lora-act");

  it("goes to the next row's ×, and to Add LoRA after the last", async () => {
    const wrapper = await mountReal();
    wrapper.vm.addLora();
    wrapper.vm.addLora();
    await flushPromises();
    await removes(wrapper)[0].trigger("click");
    await flushPromises();
    expect(removes(wrapper)).toHaveLength(1);
    expect(document.activeElement).toBe(removes(wrapper)[0].element);
    await removes(wrapper)[0].trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(
      wrapper.find('[data-focus="add-lora"]').element,
    );
  });
});

// The footer's Open in ComfyUI: this run, in the owner's ComfyUI, where a save
// makes a NEW workflow. What can silently go wrong: the workflow opens without
// the form's changes (the body is not the run's), or the tab is opened after
// the request, which a popup blocker refuses.
describe("Open in ComfyUI", () => {
  const fromCard = { kind: "card", workflowId: KEY };
  const RUN = "c".repeat(32);
  const LINK = `http://127.0.0.1:8188/?pixlstash_run=${RUN}&pixlstash_of=${encodeURIComponent(KEY)}`;
  let open;
  let tab;
  const openButton = (wrapper) =>
    wrapper.find('[data-testid="rund-open-comfyui"]');
  const openError = (wrapper) => wrapper.find('[data-testid="rund-open-error"]');

  beforeEach(() => {
    tab = { opener: window, location: { replace: vi.fn() }, close: vi.fn() };
    open = vi.spyOn(window, "open").mockImplementation(() => tab);
    getPixlstashNode.mockReset();
    getPixlstashNode.mockResolvedValue({
      can_open_workflows: true,
      saves_back: true,
      opens_runs: true,
    });
    openWorkflowRun.mockReset();
    openWorkflowRun.mockResolvedValue({ opening: RUN, workflow_id: KEY });
    useFilterStore().comfyuiUrl = "http://127.0.0.1:8188/";
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.pixlstashDesktop;
  });

  it("is not drawn without a ComfyUI to open", async () => {
    useFilterStore().comfyuiUrl = "";
    const wrapper = await mountRun(fromCard);
    expect(openButton(wrapper).exists()).toBe(false);
    expect(getPixlstashNode).not.toHaveBeenCalled();
  });

  it("holds the run the form would send, and sends a new tab to it", async () => {
    const wrapper = await mountRun(fromCard);
    wrapper.vm.setValue(wrapper.vm.scalarFields[0], 33);
    await flushPromises();

    // The server has not answered yet when the tab has to be open: a
    // `window.open` after the answer is what a popup blocker refuses.
    let answer;
    openWorkflowRun.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    await openButton(wrapper).trigger("click");
    await flushPromises();
    expect(open).toHaveBeenCalledWith("", "_blank");
    // Cut loose from this page before it goes anywhere.
    expect(tab.opener).toBeNull();
    expect(tab.location.replace).not.toHaveBeenCalled();
    answer({ opening: RUN, workflow_id: KEY });
    await flushPromises();

    const [body] = openWorkflowRun.mock.calls[0];
    expect(body).toEqual({ ...wrapper.vm.runBody(), count: 1 });
    expect(body.workflow_id).toBe(KEY);
    expect(body.values.map((v) => v.value)).toContain(33);
    expect(tab.location.replace).toHaveBeenCalledWith(LINK);
    expect(tab.close).not.toHaveBeenCalled();
    // The run is still to make: nothing is queued and the popup stays.
    expect(runWorkflowCard).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(openError(wrapper).exists()).toBe(false);
    expect(openButton(wrapper).attributes("tooltip")).toContain(
      "makes a new workflow",
    );
  });

  it("sends the tab to the address it checked, whatever the setting is by then", async () => {
    const wrapper = await mountRun(fromCard);
    let answer;
    openWorkflowRun.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    await openButton(wrapper).trigger("click");
    // Cleared while the request is out: no link could be made of it now.
    useFilterStore().comfyuiUrl = "";
    answer({ opening: RUN, workflow_id: KEY });
    await flushPromises();
    expect(tab.location.replace).toHaveBeenCalledTimes(1);
    expect(tab.location.replace).toHaveBeenCalledWith(LINK);
    expect(tab.close).not.toHaveBeenCalled();
  });

  it("is refused, and says why, when ComfyUI's nodes open no run", async () => {
    getPixlstashNode.mockResolvedValue({
      can_open_workflows: true,
      saves_back: true,
      opens_runs: false,
    });
    const wrapper = await mountRun(fromCard);
    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    expect(button.attributes("tooltip")).toContain(
      "Update the ComfyUI-PixlStash nodes",
    );

    await button.trigger("click");
    await flushPromises();
    expect(open).not.toHaveBeenCalled();
    expect(openWorkflowRun).not.toHaveBeenCalled();
  });

  it("asks again when the ComfyUI address arrives or changes after the popup", async () => {
    useFilterStore().comfyuiUrl = "";
    getPixlstashNode.mockResolvedValue({ opens_runs: false });
    const wrapper = await mountRun(fromCard);
    expect(getPixlstashNode).not.toHaveBeenCalled();

    useFilterStore().comfyuiUrl = "http://127.0.0.1:8188/";
    await flushPromises();
    expect(getPixlstashNode).toHaveBeenCalledTimes(1);
    expect(openButton(wrapper).attributes("aria-disabled")).toBe("true");

    // Another ComfyUI, whose nodes do open a run: the old answer is not its.
    getPixlstashNode.mockResolvedValue({ opens_runs: true });
    useFilterStore().comfyuiUrl = "http://127.0.0.1:8189/";
    await flushPromises();
    expect(getPixlstashNode).toHaveBeenCalledTimes(2);
    expect(openButton(wrapper).attributes("aria-disabled")).toBeUndefined();
  });

  it("is left to try when ComfyUI cannot be asked", async () => {
    getPixlstashNode.mockResolvedValue({ opens_runs: null });
    const wrapper = await mountRun(fromCard);
    expect(openButton(wrapper).attributes("aria-disabled")).toBeUndefined();
  });

  it("says what the server refused, and closes the tab it opened", async () => {
    openWorkflowRun.mockRejectedValue({
      response: { data: { detail: "PixlStash has no graph for this workflow." } },
    });
    const wrapper = await mountRun(fromCard);
    await openButton(wrapper).trigger("click");
    await flushPromises();
    expect(openError(wrapper).text()).toBe(
      "PixlStash has no graph for this workflow.",
    );
    expect(tab.close).toHaveBeenCalled();
    expect(tab.location.replace).not.toHaveBeenCalled();
  });

  it("asks nothing of the server when the browser blocks the tab", async () => {
    open.mockImplementation(() => null);
    const wrapper = await mountRun(fromCard);
    await openButton(wrapper).trigger("click");
    await flushPromises();
    expect(openError(wrapper).text()).toContain("blocked the new tab");
    expect(openWorkflowRun).not.toHaveBeenCalled();
  });

  it("asks nothing of the server for an address that is not a web address", async () => {
    useFilterStore().comfyuiUrl = "file:///home/me/comfy";
    const wrapper = await mountRun(fromCard);
    await openButton(wrapper).trigger("click");
    await flushPromises();
    expect(openError(wrapper).text()).toContain("not a web address");
    expect(open).not.toHaveBeenCalled();
    expect(openWorkflowRun).not.toHaveBeenCalled();
  });

  it("goes through the desktop shell's bridge, which window.open cannot", async () => {
    const openComfyui = vi.fn().mockResolvedValue(true);
    window.pixlstashDesktop = { openComfyui };
    const wrapper = await mountRun(fromCard);
    await openButton(wrapper).trigger("click");
    await flushPromises();
    expect(openComfyui).toHaveBeenCalledWith(LINK);
    expect(open).not.toHaveBeenCalled();
    expect(openError(wrapper).exists()).toBe(false);

    openComfyui.mockResolvedValue(false);
    await openButton(wrapper).trigger("click");
    await flushPromises();
    expect(openError(wrapper).text()).toBe("Could not open ComfyUI.");
  });

  it("names the workflow the picker chose over a picture", async () => {
    getPictureRecipe.mockResolvedValue({
      available: true,
      workflow_id: KEY,
      settings: {},
      lora_slots: [],
    });
    const wrapper = await mountRun();
    await openButton(wrapper).trigger("click");
    await flushPromises();
    const [body] = openWorkflowRun.mock.calls[0];
    expect(body.picture_ids).toEqual([42]);
    expect(body.target).toBe(KEY);
  });
});
