// The run panel in the inspector rail (#1307).
//
// What it must get right is what a run is sent and what it costs: the pill
// offers only workflows a selection fills and the toolbar only those that need
// none, the run count is stated before anything starts, a Picker input blocks
// the run until it is chosen, and the body names each picture where the backend
// expects it.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflows = vi.fn();
const getWorkflowInputs = vi.fn();
const runWorkflow = vi.fn();

const listAdapters = vi.fn();

vi.mock("../../api/comfyui", () => ({
  listWorkflows: (...args) => listWorkflows(...args),
  getWorkflowInputs: (...args) => getWorkflowInputs(...args),
  runWorkflow: (...args) => runWorkflow(...args),
}));

vi.mock("../../api/modelShelf", () => ({
  listAdapters: (...args) => listAdapters(...args),
}));

import WorkflowRunPanel from "./WorkflowRunPanel.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import {
  FROM_SELECTION,
  FROM_TOOLBAR,
  useWorkflowRunStore,
} from "../../stores/useWorkflowRunStore";

const PicturePickerStub = {
  name: "PicturePicker",
  props: ["open", "subtitle"],
  emits: ["pick", "close"],
  template:
    "<div v-if='open' class='picker-stub'><button class='picker-pick' @click=\"$emit('pick', { id: 42 })\">pick</button></div>",
};

const globalOpts = {
  global: {
    stubs: { "v-icon": true, Tooltip: true, PicturePicker: PicturePickerStub },
  },
};

const WORKFLOWS = [
  {
    name: "edit.json",
    display_name: "edit",
    runnable: true,
    has_selection_input: true,
    missing_placeholders: [],
  },
  {
    name: "t2i.json",
    display_name: "t2i",
    runnable: true,
    has_selection_input: false,
    missing_placeholders: ["{{image_path}}"],
  },
  {
    name: "canvas.json",
    display_name: "canvas",
    runnable: false,
    has_selection_input: true,
    missing_placeholders: [],
  },
];

async function flush(wrapper) {
  for (let i = 0; i < 3; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
  }
}

function runButton(wrapper) {
  return wrapper
    .findAll("button")
    .find((b) => /^Run (once|\d+ times)$/.test(b.text().trim()));
}

function offeredNames(wrapper) {
  return wrapper.findAll("option").map((o) => o.attributes("value"));
}

async function mountFrom(origin) {
  const store = useWorkflowRunStore();
  store.openFor(origin);
  const wrapper = mount(WorkflowRunPanel, globalOpts);
  await flush(wrapper);
  return { wrapper, store };
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  useSidebarStore().statsOpen = true;
  listWorkflows.mockReset().mockResolvedValue({ workflows: WORKFLOWS });
  getWorkflowInputs.mockReset().mockResolvedValue({
    inputs: [
      { node_id: "1", title: "Style", mode: "picker", picture_id: null },
      { node_id: "2", title: "Subject", mode: "selection", picture_id: null },
    ],
  });
  listAdapters.mockReset().mockResolvedValue([
    { sha256: "a".repeat(64), display_name: "Subject v2", filename: "s.st" },
    // No digest yet: it cannot be asked for, so it is not offered.
    { sha256: null, display_name: "Still hashing", filename: "h.st" },
  ]);
  runWorkflow.mockReset().mockResolvedValue({
    prompts: [
      { picture_id: 7, prompt_id: "a" },
      { picture_id: 8, prompt_id: "b" },
    ],
  });
});

describe("what each entry point offers", () => {
  it("offers the pill only runnable workflows a selection fills", async () => {
    const { wrapper } = await mountFrom(FROM_SELECTION);
    expect(offeredNames(wrapper)).toEqual(["edit.json"]);
  });

  it("offers the toolbar only workflows that take no selection", async () => {
    getWorkflowInputs.mockResolvedValue({ inputs: [] });
    const { wrapper } = await mountFrom(FROM_TOOLBAR);
    expect(offeredNames(wrapper)).toEqual(["t2i.json"]);
    expect(runButton(wrapper).text()).toBe("Run once");
  });
});

describe("a run from the selection", () => {
  it("states the count, waits for the picker, and sends each picture where it goes", async () => {
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    const runner = vi.fn();
    store.attachRunner(runner);
    store.selectionIds = [7, 8];
    store.context = { client_id: "tab-1", character_id: 3 };
    await flush(wrapper);

    expect(runButton(wrapper).text()).toBe("Run 2 times");
    expect(runButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("Choose a picture for Style #1.");

    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Choose")
      .trigger("click");
    await wrapper.find(".picker-pick").trigger("click");
    await flush(wrapper);
    expect(runButton(wrapper).attributes("disabled")).toBeUndefined();

    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow).toHaveBeenCalledTimes(1);
    const [name, body] = runWorkflow.mock.calls[0];
    expect(name).toBe("edit.json");
    expect(body.picture_ids).toEqual([7, 8]);
    expect(body.pictures).toEqual([{ node_id: "1", picture_id: 42 }]);
    expect(body.client_id).toBe("tab-1");
    // The view context is for a run with no selection; these outputs stack.
    expect(body.character_id).toBeUndefined();
    expect(runner).toHaveBeenCalledWith({
      prompts: [
        { picture_id: 7, prompt_id: "a" },
        { picture_id: 8, prompt_id: "b" },
      ],
    });
    expect(wrapper.text()).toContain("Started 2 runs in ComfyUI.");
  });

  it("will not run with nothing selected, and follows the selection", async () => {
    getWorkflowInputs.mockResolvedValue({
      inputs: [
        { node_id: "2", title: "Subject", mode: "selection", picture_id: null },
      ],
    });
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    expect(runButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("Select the pictures to run it on.");
    store.selectionIds = [7, 8, 9];
    await flush(wrapper);
    expect(runButton(wrapper).attributes("disabled")).toBeUndefined();
    expect(runButton(wrapper).text()).toBe("Run 3 times");
  });

  it("blocks a fixed input whose picture has left the library", async () => {
    getWorkflowInputs.mockResolvedValue({
      inputs: [
        {
          node_id: "1",
          title: "Style",
          mode: "fixed",
          picture_id: null,
          picture_missing: true,
        },
        { node_id: "2", title: "Subject", mode: "selection", picture_id: null },
      ],
    });
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    expect(runButton(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("has left the library");
  });
});

describe("a run from the toolbar", () => {
  it("sends the view context and no selection", async () => {
    getWorkflowInputs.mockResolvedValue({ inputs: [] });
    runWorkflow.mockResolvedValue({ prompts: [{ prompt_id: "a" }] });
    const { wrapper, store } = await mountFrom(FROM_TOOLBAR);
    store.selectionIds = [7, 8];
    store.context = { client_id: "tab-1", character_id: 3 };
    await flush(wrapper);
    expect(runButton(wrapper).text()).toBe("Run once");
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    const [name, body] = runWorkflow.mock.calls[0];
    expect(name).toBe("t2i.json");
    expect(body.picture_ids).toBeUndefined();
    expect(body.character_id).toBe(3);
    expect(body.client_id).toBe("tab-1");
  });
});

describe("a batch ComfyUI stopped partway", () => {
  it("follows the runs that started and says the rest did not", async () => {
    getWorkflowInputs.mockResolvedValue({
      inputs: [
        { node_id: "2", title: "Subject", mode: "selection", picture_id: null },
      ],
    });
    runWorkflow.mockResolvedValue({
      status: "partial",
      prompts: [{ picture_id: 7, prompt_id: "a" }],
      error: "ComfyUI prompt failed",
    });
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    const runner = vi.fn();
    store.attachRunner(runner);
    store.selectionIds = [7, 8];
    await flush(wrapper);
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runner).toHaveBeenCalledWith({
      prompts: [{ picture_id: 7, prompt_id: "a" }],
    });
    expect(wrapper.text()).toContain(
      "Started 1 run in ComfyUI. The rest did not start: ComfyUI prompt failed",
    );
  });
});

describe("requests that race", () => {
  const SELECTION_ONLY = {
    inputs: [
      { node_id: "2", title: "Subject", mode: "selection", picture_id: null },
    ],
  };

  it("sends one run for a double click", async () => {
    getWorkflowInputs.mockResolvedValue(SELECTION_ONLY);
    let finish;
    runWorkflow.mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    const button = runButton(wrapper);
    await button.trigger("click");
    await button.trigger("click");
    await flush(wrapper);
    expect(runWorkflow).toHaveBeenCalledTimes(1);
    finish({ prompts: [{ picture_id: 7, prompt_id: "a" }] });
    await flush(wrapper);
  });

  it("keeps the inputs of the workflow now chosen when an older read lands last", async () => {
    listWorkflows.mockResolvedValue({
      workflows: [
        WORKFLOWS[0],
        { ...WORKFLOWS[0], name: "other.json", display_name: "other" },
      ],
    });
    let finishFirst;
    getWorkflowInputs.mockImplementation((name) =>
      name === "edit.json"
        ? new Promise((resolve) => {
            finishFirst = resolve;
          })
        : Promise.resolve(SELECTION_ONLY),
    );
    const { wrapper } = await mountFrom(FROM_SELECTION);
    await wrapper.find("select").setValue("other.json");
    await flush(wrapper);
    finishFirst({
      inputs: [
        { node_id: "9", title: "Stale", mode: "picker", picture_id: null },
      ],
    });
    await flush(wrapper);
    expect(wrapper.text()).toContain("Subject");
    expect(wrapper.text()).not.toContain("Stale");
  });
});

describe("a LoRA from the shelf (#1310)", () => {
  const WITH_SLOTS = {
    inputs: [
      { node_id: "2", title: "Subject", mode: "selection", picture_id: null },
    ],
    lora_slots: [
      {
        node_id: "3",
        class_type: "LoraLoader",
        field: "lora_name",
        value: "add-detail.safetensors",
        by: "filename",
      },
    ],
  };

  it("offers the shelf's hashed LoRAs and sends the chosen one", async () => {
    getWorkflowInputs.mockResolvedValue(WITH_SLOTS);
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    const select = wrapper.findAll("select")[1];
    expect(select.findAll("option").map((o) => o.text())).toEqual([
      "Keep the workflow's own",
      "Subject v2",
    ]);

    // The default leaves the workflow's own LoRA where it is.
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][1].adapter_sha256).toBeUndefined();

    await select.setValue("a".repeat(64));
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[1][1].adapter_sha256).toBe("a".repeat(64));
  });

  it("asks which loader when the workflow chains two, and sends it", async () => {
    getWorkflowInputs.mockResolvedValue({
      ...WITH_SLOTS,
      lora_slots: [
        ...WITH_SLOTS.lora_slots,
        {
          node_id: "4",
          class_type: "LoraLoader",
          field: "lora_name",
          value: "watercolour.safetensors",
          by: "filename",
        },
      ],
    });
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    const slots = wrapper.findAll("select")[2];
    expect(slots.findAll("option").map((o) => o.text())).toEqual([
      "#3 add-detail.safetensors",
      "#4 watercolour.safetensors",
    ]);

    await wrapper.findAll("select")[1].setValue("a".repeat(64));
    // Not touching the loader select sends its default, the first slot - never
    // an empty node the backend would refuse.
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][1]).toMatchObject({
      lora_node_id: "3",
      lora_field: "lora_name",
    });

    await slots.setValue("lora_name@4");
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[1][1]).toMatchObject({
      lora_node_id: "4",
      lora_field: "lora_name",
    });
  });

  it("tells a stacker's slots apart, since they share one node", async () => {
    getWorkflowInputs.mockResolvedValue({
      ...WITH_SLOTS,
      lora_slots: [
        { node_id: "7", class_type: "CR LoRA Stack", field: "lora_name_1", value: "style.st", by: "filename" },
        { node_id: "7", class_type: "CR LoRA Stack", field: "lora_name_2", value: "character.st", by: "filename" },
      ],
    });
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    const slots = wrapper.findAll("select")[2];
    expect(slots.findAll("option").map((o) => o.text())).toEqual([
      "#7 style.st · lora_name_1",
      "#7 character.st · lora_name_2",
    ]);
    await wrapper.findAll("select")[1].setValue("a".repeat(64));
    await slots.setValue("lora_name_2@7");
    expect(slots.element.value).toBe("lora_name_2@7");
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][1]).toMatchObject({
      lora_node_id: "7",
      lora_field: "lora_name_2",
    });
  });

  it("drops the LoRA choice when another workflow with slots is chosen", async () => {
    listWorkflows.mockResolvedValue({
      workflows: [
        WORKFLOWS[0],
        { ...WORKFLOWS[0], name: "other.json", display_name: "other" },
      ],
    });
    getWorkflowInputs.mockImplementation((name) =>
      Promise.resolve({
        ...WITH_SLOTS,
        lora_slots: [{ ...WITH_SLOTS.lora_slots[0], node_id: name === "edit.json" ? "3" : "12" }],
      }),
    );
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    await wrapper.findAll("select")[1].setValue("a".repeat(64));
    await wrapper.find("select").setValue("other.json");
    await flush(wrapper);
    expect(wrapper.findAll("select")[1].element.value).toBe("");
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][1].adapter_sha256).toBeUndefined();
    // The shelf was read once, not once per workflow.
    expect(listAdapters).toHaveBeenCalledTimes(1);
  });

  it("claims nothing about LoRA loaders while the inputs are unread or failed", async () => {
    getWorkflowInputs.mockRejectedValue(new Error("gone"));
    const { wrapper } = await mountFrom(FROM_SELECTION);
    await flush(wrapper);
    expect(wrapper.text()).toContain("gone");
    expect(wrapper.text()).not.toContain("no LoRA loader");
  });

  it("reads the shelf again after a failed read", async () => {
    listWorkflows.mockResolvedValue({
      workflows: [
        WORKFLOWS[0],
        { ...WORKFLOWS[0], name: "other.json", display_name: "other" },
      ],
    });
    getWorkflowInputs.mockImplementation((name) =>
      Promise.resolve({
        ...WITH_SLOTS,
        lora_slots: [{ ...WITH_SLOTS.lora_slots[0], node_id: name === "edit.json" ? "3" : "12" }],
      }),
    );
    listAdapters.mockRejectedValueOnce(new Error("shelf is closed"));
    const { wrapper } = await mountFrom(FROM_SELECTION);
    await flush(wrapper);
    expect(wrapper.text()).toContain("Your LoRAs could not be read");
    await wrapper.find("select").setValue("other.json");
    await flush(wrapper);
    expect(listAdapters).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).not.toContain("Your LoRAs could not be read");
  });

  it("names no loader when the workflow has only one", async () => {
    getWorkflowInputs.mockResolvedValue(WITH_SLOTS);
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    expect(wrapper.findAll("select")).toHaveLength(2);
    await wrapper.findAll("select")[1].setValue("a".repeat(64));
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][1].lora_node_id).toBeUndefined();
  });

  it("says a workflow with no LoRA loader has nothing to swap", async () => {
    getWorkflowInputs.mockResolvedValue({ ...WITH_SLOTS, lora_slots: [] });
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    expect(wrapper.text()).toContain("no LoRA loader");
    expect(wrapper.findAll("select")).toHaveLength(1);
    expect(listAdapters).not.toHaveBeenCalled();
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][1].adapter_sha256).toBeUndefined();
  });

  it("does not send a LoRA to a workflow that has moved on to no slots", async () => {
    listWorkflows.mockResolvedValue({
      workflows: [
        WORKFLOWS[0],
        { ...WORKFLOWS[0], name: "other.json", display_name: "other" },
      ],
    });
    getWorkflowInputs.mockImplementation((name) =>
      Promise.resolve(
        name === "edit.json"
          ? WITH_SLOTS
          : { ...WITH_SLOTS, lora_slots: [] },
      ),
    );
    const { wrapper, store } = await mountFrom(FROM_SELECTION);
    store.selectionIds = [7];
    await flush(wrapper);
    await wrapper.findAll("select")[1].setValue("a".repeat(64));
    await wrapper.find("select").setValue("other.json");
    await flush(wrapper);
    await runButton(wrapper).trigger("click");
    await flush(wrapper);
    expect(runWorkflow.mock.calls[0][0]).toBe("other.json");
    expect(runWorkflow.mock.calls[0][1].adapter_sha256).toBeUndefined();
  });

  it("says so when the shelf cannot be read", async () => {
    getWorkflowInputs.mockResolvedValue(WITH_SLOTS);
    listAdapters.mockRejectedValue(new Error("shelf is closed"));
    const { wrapper } = await mountFrom(FROM_SELECTION);
    await flush(wrapper);
    expect(wrapper.text()).toContain("Your LoRAs could not be read");
  });
});
