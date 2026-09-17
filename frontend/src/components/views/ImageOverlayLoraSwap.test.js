// The overlay's "Edit with ComfyUI" menu and the model shelf: swapping a LoRA
// into the chosen workflow's own loader (#1310) and adding one to a workflow
// that has none (#1376).
//
// Mounted rather than unit-tested through the composable, because what broke in
// review was the *wiring*: deleting the swap from the run body, or the
// insertion source from the composable call, left the whole suite green.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listWorkflows = vi.fn();
const runImageToImage = vi.fn();
const getLoraInsertion = vi.fn();
const listAdapters = vi.fn();

vi.mock("../../api/comfyui", () => ({
  listWorkflows: (...a) => listWorkflows(...a),
  runImageToImage: (...a) => runImageToImage(...a),
  getLoraInsertion: (...a) => getLoraInsertion(...a),
  runTextToImage: vi.fn(),
  getPictureWorkflow: vi.fn().mockRejectedValue(new Error("no workflow")),
}));

vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...a) => listAdapters(...a),
}));

const { isReadOnly } = vi.hoisted(() => ({ isReadOnly: { value: false } }));

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn(async () => ({ data: [] })),
    post: vi.fn(async () => ({ data: {} })),
    delete: vi.fn(async () => ({ data: {} })),
  },
  appendShareToken: (u) => u,
  isReadOnly,
}));

import ImageOverlay from "./ImageOverlay.vue";

enableAutoUnmount(afterEach);

const SHA = "a".repeat(64);
const WORKFLOW = {
  name: "edit.json",
  display_name: "edit",
  valid: true,
  runnable: true,
  missing_placeholders: [],
  lora_slots: [],
};
const SLOT = {
  node_id: "3",
  class_type: "LoraLoader",
  field: "lora_name",
  value: "old.st",
  by: "filename",
};
const PLAN = {
  model: { node_id: "4", class_type: "CheckpointLoaderSimple", output: 0 },
  clip: { node_id: "4", class_type: "CheckpointLoaderSimple", output: 1 },
  rewires: [
    { node_id: "3", class_type: "KSampler", field: "model", type: "MODEL" },
  ],
};

// The menu is a v-menu; stubbed as a passthrough so its panel renders.
const STUBS = {
  "v-menu": { template: "<div><slot /></div>" },
  "v-icon": true,
  "v-tooltip": true,
  Tooltip: true,
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  ComfyUiRunner: true,
  ProgressOverlay: true,
  SaveAsDialog: true,
};

const flush = async () => {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
};

async function openOverlay() {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [{ id: 7, tags: [] }],
      backendUrl: "http://test",
      comfyuiConfigured: true,
    },
    global: { stubs: STUBS },
  });
  await wrapper.setProps({ open: true });
  await flush();
  return wrapper;
}

/** The menu's LoRA select, which is the second select in its panel. */
const loraSelect = (wrapper) => wrapper.findAll(".overlay-comfy-select")[1];

const runBody = () => runImageToImage.mock.calls[0][0];

async function run(wrapper) {
  await wrapper.find(".overlay-comfy-run").trigger("click");
  await flush();
}

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  listWorkflows.mockReset().mockResolvedValue({ workflows: [WORKFLOW] });
  runImageToImage.mockReset().mockResolvedValue({ prompts: [] });
  getLoraInsertion.mockReset().mockResolvedValue({ plan: null, reason: null });
  listAdapters
    .mockReset()
    .mockResolvedValue([{ sha256: SHA, display_name: "Subject v2" }]);
});

describe("Edit with ComfyUI - a LoRA from the shelf (#1310)", () => {
  it("sends the chosen LoRA with the run", async () => {
    listWorkflows.mockResolvedValue({
      workflows: [{ ...WORKFLOW, lora_slots: [SLOT] }],
    });
    const wrapper = await openOverlay();
    await loraSelect(wrapper).setValue(SHA);
    await run(wrapper);
    expect(runBody()).toMatchObject({ adapter_sha256: SHA });
    expect(runBody().insert_lora_loader).toBeUndefined();
  });

  it("sends none where the workflow has no slot and none can be added", async () => {
    const wrapper = await openOverlay();
    await flush();
    expect(wrapper.text()).toContain("no LoRA loader");
    await run(wrapper);
    expect(runBody().adapter_sha256).toBeUndefined();
  });
});

describe("Edit with ComfyUI - adding a loader (#1376)", () => {
  it("offers the shelf, shows the splice, and asks for it in the run", async () => {
    getLoraInsertion.mockResolvedValue({ plan: PLAN, reason: null });
    const wrapper = await openOverlay();
    await flush();
    expect(getLoraInsertion).toHaveBeenCalledWith("edit.json");
    expect(wrapper.text()).not.toContain("no LoRA loader");

    await loraSelect(wrapper).setValue(SHA);
    await flush();
    expect(wrapper.text()).toContain(
      "A LoRA loader is added after #4 CheckpointLoaderSimple, feeding #3 KSampler (model).",
    );

    await run(wrapper);
    expect(runBody()).toMatchObject({
      adapter_sha256: SHA,
      insert_lora_loader: true,
    });
  });

  it("says the backend's reason, not a sentence contradicting it", async () => {
    getLoraInsertion.mockResolvedValue({
      plan: null,
      reason: "Node 7 (Power Lora Loader (rgthree)) already loads a LoRA.",
    });
    const wrapper = await openOverlay();
    await flush();
    expect(wrapper.text()).toContain("already loads a LoRA");
    expect(wrapper.text()).not.toContain("has no LoRA loader");
  });

  it("does not ask under a read-only share link", async () => {
    isReadOnly.value = true;
    const wrapper = await openOverlay();
    await flush();
    expect(getLoraInsertion).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("no LoRA loader");
  });
});
