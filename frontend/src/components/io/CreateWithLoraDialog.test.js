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

  it("lets the owner pick another loader, and pre-flights that choice", async () => {
    getLoraChain.mockResolvedValue({
      loaders: [
        { node_id: "4", field: "lora_name", filename: "None", name: "None" },
        {
          node_id: "12",
          field: "lora_name",
          filename: "lightning.safetensors",
          name: "lightning",
        },
      ],
    });
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    // The empty loader by default: nothing the workflow needs is swapped out.
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].loras[0].node_id).toBe(
      "4",
    );
    expect(wrapper.text()).not.toContain("Replaces");
    expect(wrapper.text()).toContain(
      "Its other LoRA loaders run as the workflow stores them.",
    );
    const loaderRows = wrapper
      .findAll('[role="radio"]')
      .filter((r) => r.text().includes("lightning"));
    await loaderRows[0].trigger("click");
    await flushPromises();
    expect(preflightWorkflowRun.mock.calls.at(-1)[0].loras[0].node_id).toBe(
      "12",
    );
    expect(wrapper.text()).toContain("Replaces lightning in this run.");
  });

  it("puts the LoRA into every pass of a forked graph", async () => {
    getLoraChain.mockResolvedValue({
      loaders: [],
      lanes: [
        {
          loaders: [
            {
              node_id: "5",
              field: "lora_name",
              filename: "None",
              name: "None",
            },
          ],
        },
        {
          loaders: [
            {
              node_id: "8",
              field: "lora_name",
              filename: "None",
              name: "None",
            },
          ],
        },
      ],
    });
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    await runButton(wrapper).trigger("click");
    await flushPromises();
    const body = runWorkflowCard.mock.calls[0][0];
    expect(body.loras.map((l) => l.node_id)).toEqual(["5", "8"]);
  });

  it("keeps the workflow's strength when its loader already holds this LoRA", async () => {
    getLoraChain.mockResolvedValue({
      loaders: [
        {
          node_id: "3",
          field: "lora_name",
          filename: LORA.filename,
          name: "example-subject-v2",
          strength: 0.7,
        },
      ],
    });
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    await runButton(wrapper).trigger("click");
    await flushPromises();
    expect(runWorkflowCard.mock.calls[0][0].loras[0].strength_model).toBe(0.7);
  });

  it("blocks Run on a pre-flight 4xx and says why", async () => {
    preflightWorkflowRun.mockRejectedValue({
      response: {
        status: 400,
        data: { detail: "ComfyUI does not have that LoRA." },
      },
    });
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    expect(wrapper.text()).toContain("This workflow cannot run");
    await runButton(wrapper).trigger("click");
    await flushPromises();
    expect(runWorkflowCard).not.toHaveBeenCalled();
  });

  it("lets a run through when the pre-flight could not be asked", async () => {
    preflightWorkflowRun.mockRejectedValue(new Error("Network Error"));
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    await runButton(wrapper).trigger("click");
    await flushPromises();
    expect(runWorkflowCard).toHaveBeenCalledTimes(1);
  });

  it("says a person's results go to their reference pictures", async () => {
    const wrapper = await mountDialog({
      entityType: "character",
      entityId: 3,
      name: "Example",
    });
    expect(wrapper.text()).toContain(
      "Results go to Example's reference pictures.",
    );
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
