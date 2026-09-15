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

vi.mock("../../api/comfyui", () => ({
  listWorkflows: (...args) => listWorkflows(...args),
  getWorkflowInputs: (...args) => getWorkflowInputs(...args),
  runWorkflow: (...args) => runWorkflow(...args),
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
