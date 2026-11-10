// One row of the Workflow tab's Parameters panel: what its name says.

import { describe, it, expect } from "vitest";
import { reactive } from "vue";
import { mount } from "@vue/test-utils";

import Segmented from "../widgets/Segmented.vue";
import WorkflowDefaultRow from "./WorkflowDefaultRow.vue";

const ROW = {
  label: "resize_type.multiplier",
  slot_label: "core:a",
  input_name: "resize_type.multiplier",
  value: 4,
  provenance: "edited",
  pinned: true,
  exposed: true,
};

function mountRow(props) {
  return mount(WorkflowDefaultRow, {
    props,
    global: { stubs: { Tooltip: true, "v-icon": true } },
  });
}

describe("a parameter row's name", () => {
  it("is the setting alone, with its node and whole name in the tip", () => {
    const wrapper = mountRow({ row: ROW, node: "Resize Image/Mask" });
    const name = wrapper.find(".wfdef-name");
    expect(name.text()).toBe("multiplier Yours");
    expect(name.findComponent({ name: "Tooltip" }).props("text")).toBe(
      "Resize Image/Mask · resize_type.multiplier",
    );
    // The field is named after the row and the tip, so the node is not
    // hover-only.
    expect(wrapper.find("[data-testid='wfdef-input']").attributes("aria-label")).toBe(
      "multiplier (Resize Image/Mask · resize_type.multiplier)",
    );
  });

  it("is the name the owner gave it, where they gave one", () => {
    const wrapper = mountRow({ row: { ...ROW, name: "Scale" }, node: "Resize Image/Mask" });
    const name = wrapper.find(".wfdef-name");
    expect(name.text()).toBe("Scale Yours");
    expect(name.findComponent({ name: "Tooltip" }).props("text")).toBe(
      "Resize Image/Mask · resize_type.multiplier",
    );
  });

  it("has no tip where the row already says all of it", () => {
    const wrapper = mountRow({ row: { ...ROW, label: "cfg", exposed: false } });
    expect(wrapper.find(".wfdef-name").findComponent({ name: "Tooltip" }).exists()).toBe(
      false,
    );
  });
});

describe("a parameter row's Each run | Pinned switch", () => {
  it("asks for the pin toggle from the side that is not checked, and not from the checked one", async () => {
    // Set each run: Each run is checked, so only Pinned asks for the change.
    const eachRun = mountRow({ row: { ...ROW, pinned: true, exposed: false } });
    expect(eachRun.find("[data-testid='wfdef-each-run']").attributes("aria-checked")).toBe(
      "true",
    );
    await eachRun.find("[data-testid='wfdef-each-run']").trigger("click");
    expect(eachRun.emitted("toggle-pin")).toBeUndefined();
    await eachRun.find("[data-testid='wfdef-pinned']").trigger("click");
    expect(eachRun.emitted("toggle-pin")).toHaveLength(1);

    // Pinned: the other way round.
    const pinned = mountRow({ row: { ...ROW, pinned: false, exposed: false } });
    expect(pinned.find("[data-testid='wfdef-pinned']").attributes("aria-checked")).toBe(
      "true",
    );
    await pinned.find("[data-testid='wfdef-pinned']").trigger("click");
    expect(pinned.emitted("toggle-pin")).toBeUndefined();
    await pinned.find("[data-testid='wfdef-each-run']").trigger("click");
    expect(pinned.emitted("toggle-pin")).toHaveLength(1);
  });

  it("is the app's Segmented, with the olive side on the current state", () => {
    // Pick-one is Segmented wherever there is room (docs/design/buttons.md).
    const wrapper = mountRow({ row: { ...ROW, pinned: false } });
    const seg = wrapper.findComponent(Segmented);
    expect(seg.exists()).toBe(true);
    expect(seg.props("modelValue")).toBe("pinned");
    expect(wrapper.find("[data-testid='wfdef-pinned']").classes()).toContain("seg__opt--on");
  });

  it("is one tab stop on the checked side, and an arrow picks the other side", async () => {
    const wrapper = mountRow({ row: { ...ROW, pinned: true } });
    const eachRun = wrapper.find("[data-testid='wfdef-each-run']");
    const pinned = wrapper.find("[data-testid='wfdef-pinned']");
    expect(eachRun.attributes("tabindex")).toBe("0");
    expect(pinned.attributes("tabindex")).toBe("-1");
    await eachRun.trigger("keydown", { key: "ArrowRight" });
    expect(wrapper.emitted("toggle-pin")).toHaveLength(1);
    await eachRun.trigger("keydown", { key: "Tab" });
    expect(wrapper.emitted("toggle-pin")).toHaveLength(1);
    // Busy: the arrow is swallowed, nothing is asked for.
    await wrapper.setProps({ busy: true });
    await eachRun.trigger("keydown", { key: "ArrowLeft" });
    expect(wrapper.emitted("toggle-pin")).toHaveLength(1);
  });

  // Focus goes to the side the arrow asked for, whenever the parent flips the
  // row: after a write (the Workflow tab's) or at once, in place.
  it.each([
    ["after a write", () => {}],
    ["at once, in place", (row) => (row.pinned = !row.pinned)],
  ])("hands focus to the side asked for when the parent flips the row %s", async (_, flip) => {
    const row = reactive({ ...ROW, pinned: true });
    const wrapper = mount(WorkflowDefaultRow, {
      props: { row, "onToggle-pin": () => flip(row) },
      attachTo: document.body,
      global: { stubs: { Tooltip: true, "v-icon": true } },
    });
    const eachRun = wrapper.find("[data-testid='wfdef-each-run']");
    eachRun.element.focus();
    await eachRun.trigger("keydown", { key: "ArrowRight" });
    expect(document.activeElement).toBe(wrapper.find("[data-testid='wfdef-pinned']").element);
    wrapper.unmount();
  });
});
