// The rail's Edit parameters dialog (#2165): what its count line says.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import AppInput from "../widgets/AppInput.vue";
import WorkflowParamsDialog from "./WorkflowParamsDialog.vue";

function item(label, node, pinned = false) {
  return {
    row: { label, slot_label: node, input_name: label, value: 1, provenance: "default", pinned },
    node,
    options: [],
  };
}

const ROWS = [
  item("KSampler/seed", "KSampler", true),
  item("KSampler/cfg", "KSampler"),
  item("KSampler/steps", "KSampler", true),
  item("Resize/width", "Resize"),
];

function mountDialog() {
  return mount(WorkflowParamsDialog, {
    props: { open: true, rows: ROWS },
    global: {
      stubs: {
        AppDialog: {
          name: "AppDialog",
          props: ["open", "title", "subtitle", "size"],
          emits: ["close"],
          template: '<div><slot /><slot name="footer" /></div>',
        },
        AppButton: { template: "<button><slot /></button>" },
        WorkflowDefaultRow: { props: ["row"], template: '<div class="row-stub" />' },
      },
    },
  });
}

const count = (wrapper) =>
  wrapper.find("[data-testid='wfp-count']").text().replace(/\s+/g, " ");

describe("the Edit parameters filter", () => {
  it("is the app's AppInput, not a field of its own", () => {
    const field = mountDialog().findComponent(AppInput);
    expect(field.exists()).toBe(true);
    expect(field.props("ariaLabel")).toBe("Filter parameters");
  });
});

describe("the Edit parameters count", () => {
  it("is the workflow's parameters and how many are set each run", () => {
    expect(count(mountDialog())).toBe("4 parameters · 2 set each run");
  });

  it("says how many of them a filter leaves, beside the rows it leaves", async () => {
    const wrapper = mountDialog();
    await wrapper.find("[data-testid='wfp-filter'] input").setValue("width");
    expect(wrapper.findAll(".row-stub")).toHaveLength(1);
    // The Each run count is the workflow's still: that is what the switches change.
    expect(count(wrapper)).toBe("1 of 4 parameters · 2 set each run");
    await wrapper.find("[data-testid='wfp-filter'] input").setValue("  ");
    expect(count(wrapper)).toBe("4 parameters · 2 set each run");
  });
});
