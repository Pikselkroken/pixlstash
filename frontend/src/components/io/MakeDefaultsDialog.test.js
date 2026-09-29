// "Make these the defaults…" (#1653 §2.5): every difference ticked on open,
// the first box focused, and only the ticked rows confirmed.

import { describe, it, expect, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import MakeDefaultsDialog from "./MakeDefaultsDialog.vue";

const ROWS = [
  { slot_label: "core:K", input_name: "steps", label: "Steps", from: "30", to: 40 },
  { slot_label: "core:K", input_name: "cfg", label: "CFG", from: "7", to: 5 },
];

describe("MakeDefaultsDialog", () => {
  it("opens with every row ticked and the first focused, and confirms the ticked", async () => {
    const wrapper = mount(MakeDefaultsDialog, {
      props: { open: true, rows: ROWS },
      global: { stubs: { teleport: true } },
      attachTo: document.body,
    });
    await flushPromises();
    const boxes = wrapper.findAll(".mkd-box");
    expect(boxes.map((box) => box.element.checked)).toEqual([true, true]);
    expect(document.activeElement).toBe(boxes[0].element);

    await boxes[0].setValue(false);
    await wrapper.findAll("button").find((el) => el.text().startsWith("Make 1 default")).trigger("click");
    expect(wrapper.emitted("confirm")).toEqual([[[ROWS[1]]]]);
    wrapper.unmount();
  });
});
