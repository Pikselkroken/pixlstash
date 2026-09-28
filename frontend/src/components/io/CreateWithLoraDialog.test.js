// "Create with LoRA…" from a person's or a set's menu.
//
// Guards the run body: the LoRA goes into the loader the chain read named,
// the destination is the person or set the menu was opened on, and a workflow
// on another base model is not offered at all.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflowCards = vi.fn();
const getLoraChain = vi.fn();
const preflightWorkflowRun = vi.fn();
const runWorkflowCard = vi.fn();
const listAdapters = vi.fn();
const fetchWorkflowSets = vi.fn();

vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getLoraChain: (...args) => getLoraChain(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  runWorkflowCard: (...args) => runWorkflowCard(...args),
}));
vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import CreateWithLoraDialog from "./CreateWithLoraDialog.vue";

const SDXL_WF = `auto:${"a".repeat(64)}`;
const FLUX_WF = `auto:${"b".repeat(64)}`;
const LORA = {
  id: 7,
  sha256: "e".repeat(64),
  filename: "example-subject-v2.safetensors",
  family: "sdxl",
  trigger_words: "exsubj",
};

const globalOpts = {
  global: {
    stubs: {
      AppDialog: {
        name: "AppDialog",
        template: "<div><slot /><footer><slot name='footer' /></footer></div>",
      },
      AppInput: true,
      AppTextarea: true,
      AppButton: {
        template: "<button @click=\"$emit('click')\"><slot /></button>",
      },
      "v-icon": true,
    },
  },
};

async function mountDialog(source) {
  const wrapper = mount(CreateWithLoraDialog, {
    props: { open: true, source, context: {} },
    ...globalOpts,
  });
  await flushPromises();
  return wrapper;
}

function runButton(wrapper) {
  return wrapper.findAll("button").find((b) => b.text().startsWith("Run"));
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  listAdapters.mockImplementation(({ fileKind }) =>
    Promise.resolve(fileKind === "adapter" ? [LORA] : []),
  );
  fetchWorkflowSets.mockResolvedValue({ hand_made: [] });
  listWorkflowCards.mockResolvedValue({
    cards: [
      {
        id: FLUX_WF,
        name: "Flux portrait",
        models: [{ kind: "unet", name: "flux-dev", family: "flux1" }],
        loras: [{ kind: "lora" }],
      },
      {
        id: SDXL_WF,
        name: "SDXL portrait",
        models: [{ kind: "checkpoint", name: "realvis", family: "sdxl" }],
        loras: [{ kind: "lora" }],
      },
    ],
  });
  getLoraChain.mockResolvedValue({
    loaders: [
      {
        node_id: "12",
        field: "lora_name",
        filename: "detail.safetensors",
        name: "detail",
      },
    ],
  });
  preflightWorkflowRun.mockResolvedValue({
    ok: true,
    groups: [{ reasons: [] }],
  });
  runWorkflowCard.mockResolvedValue({ prompts: [{ prompt_id: "p" }] });
});

describe("CreateWithLoraDialog", () => {
  it("reads the person's LoRAs by their id, in both attachable kinds", async () => {
    await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    expect(listAdapters).toHaveBeenCalledWith({
      fileKind: "adapter",
      characterId: 3,
    });
    expect(listAdapters).toHaveBeenCalledWith({
      fileKind: "unknown",
      characterId: 3,
    });
  });

  it("offers only the workflow whose base model fits, and says why the other is missing", async () => {
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    const radios = wrapper.findAll('[role="radio"]');
    expect(radios.map((r) => r.text())).toEqual([
      expect.stringContaining("SDXL portrait"),
    ]);
    expect(wrapper.text()).toContain("1 more workflow is not listed");
    expect(getLoraChain).toHaveBeenCalledWith(SDXL_WF);
    expect(wrapper.text()).toContain("Replaces detail in this run.");
  });

  it("runs with the LoRA in the chain's loader and files to the person", async () => {
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    await runButton(wrapper).trigger("click");
    await flushPromises();
    expect(runWorkflowCard).toHaveBeenCalledTimes(1);
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body).toMatchObject({
      workflow_id: SDXL_WF,
      prompt: null,
      loras: [
        {
          node_id: "12",
          field: "lora_name",
          sha256: LORA.sha256,
          strength_model: 1,
        },
      ],
      destination: { character_id: 3 },
    });
    expect(wrapper.emitted("run")).toBeTruthy();
  });

  it("files a set's run to the set", async () => {
    const wrapper = await mountDialog({
      entityType: "set",
      entityId: 5,
      name: "Beach",
    });
    expect(listAdapters).toHaveBeenCalledWith({
      fileKind: "adapter",
      setId: 5,
    });
    await runButton(wrapper).trigger("click");
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].destination).toEqual({ set_id: 5 });
  });

  it("does not run when the pre-flight refuses", async () => {
    preflightWorkflowRun.mockResolvedValue({
      ok: false,
      groups: [{ reasons: [{ code: "comfyui_unreachable" }] }],
    });
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    await runButton(wrapper).trigger("click");
    await flushPromises();
    expect(runWorkflowCard).not.toHaveBeenCalled();
  });

  it("says so when nothing is attached", async () => {
    listAdapters.mockResolvedValue([]);
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    expect(wrapper.text()).toContain("No LoRA is attached to Example");
    expect(getLoraChain).not.toHaveBeenCalled();
  });
});
