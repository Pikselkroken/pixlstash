// Settings › Workflows import: the file is sent as it is, and a name already
// taken by a different workflow is a choice with a way out. Cancel, Escape and
// the close button must write nothing.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return { isReadOnly: ref(false) };
});

vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn(async () => ({})),
  patchUserConfig: vi.fn(async () => ({})),
}));

const importWorkflow = vi.fn();
vi.mock("../../api/comfyui", () => ({
  listWorkflows: vi.fn(async () => ({ workflows: [] })),
  deleteWorkflow: vi.fn(),
  importWorkflow: (...args) => importWorkflow(...args),
}));

import WorkflowsSection from "./WorkflowsSection.vue";

// Renders its slots only while open, which is what the choice needs.
const AppDialog = {
  props: ["open", "title"],
  emits: ["close"],
  template:
    "<div v-if='open' class='stub-dialog'><slot /><slot name='footer' /></div>",
};
const AppButton = {
  emits: ["click"],
  template: "<button @click=\"$emit('click')\"><slot /></button>",
};

const graph = { 1: { class_type: "SaveImage", inputs: {} } };

async function pickFile(wrapper) {
  const input = wrapper.find("input[type=file]");
  Object.defineProperty(input.element, "files", {
    value: [new File([JSON.stringify(graph)], "flow.json")],
    configurable: true,
  });
  await input.trigger("change");
  await flushPromises();
}

function mountSection() {
  return mount(WorkflowsSection, {
    props: { open: true },
    global: {
      stubs: {
        AppDialog,
        AppButton,
        AppInput: true,
        SettingsSection: { template: "<div><slot /></div>" },
        SettingsChipGrid: { template: "<div><slot /></div>" },
        SettingsChip: true,
        "v-icon": true,
      },
    },
  });
}

function button(wrapper, label) {
  return wrapper
    .findAll(".stub-dialog button")
    .find((b) => b.text() === label);
}

beforeEach(() => {
  importWorkflow.mockReset();
});

describe("importing from Settings", () => {
  it("sends the parsed file unchanged", async () => {
    importWorkflow.mockResolvedValue({ name: "flow.json" });
    const wrapper = mountSection();
    await pickFile(wrapper);
    expect(importWorkflow).toHaveBeenCalledWith({ name: "flow", workflow: graph });
    expect(wrapper.find(".stub-dialog").exists()).toBe(false);
  });

  it.each([
    ["Replace", { overwrite: true, keepBoth: false }],
    ["Keep both", { overwrite: false, keepBoth: true }],
  ])("a taken name asks, and %s says which", async (label, flags) => {
    importWorkflow
      .mockRejectedValueOnce({ response: { status: 409 } })
      .mockResolvedValue({ name: "flow.json" });
    const wrapper = mountSection();
    await pickFile(wrapper);
    await button(wrapper, label).trigger("click");
    await flushPromises();
    expect(importWorkflow).toHaveBeenCalledTimes(2);
    expect(importWorkflow).toHaveBeenLastCalledWith({
      name: "flow",
      workflow: graph,
      ...flags,
    });
  });

  it("Cancel writes nothing", async () => {
    importWorkflow.mockRejectedValueOnce({ response: { status: 409 } });
    const wrapper = mountSection();
    await pickFile(wrapper);
    await button(wrapper, "Cancel").trigger("click");
    await flushPromises();
    expect(importWorkflow).toHaveBeenCalledTimes(1);
    expect(wrapper.find(".stub-dialog").exists()).toBe(false);
  });
});
