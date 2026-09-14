// The why-pills under a duplicate group.
//
// `orderEvidence` is pinned in utils/dedup.test.js; these tests pin what the
// COMPONENT is responsible for: that the counter-evidence survives truncation,
// that the for/against split is legible without colour, and that an empty
// evidence list renders nothing rather than an empty row.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupWhyPills from "./DedupWhyPills.vue";

// The one tooltip surface, reduced to what a test can read: the tip's text as
// `data-tip`, on the activator element or on a marker inside the parent. A
// describing tip claims its activator's `aria-describedby`, as the real one does.
const TooltipStub = {
  name: "Tooltip",
  props: ["text", "shortcut", "location", "disabled", "describe", "activator"],
  template: `<slot
      v-if="$slots.activator"
      name="activator"
      :props="{
        'data-tip': text || undefined,
        'aria-describedby': describe === false ? undefined : 'tooltip-stub',
      }"
    /><span v-else-if="text" class="tip" :data-tip="text" />`,
};

const globalOpts = {
  global: { stubs: { Tooltip: TooltipStub, "v-icon": true } },
};

/** Two supporting reasons with the warning last, as the server may send it. */
const MIXED = [
  { label: "Same size", against: false },
  { label: "Same camera", against: false },
  { label: "Different capture time", against: true },
];

function mountPills(props = {}) {
  return mount(DedupWhyPills, {
    ...globalOpts,
    props: { why: MIXED, ...props },
  });
}

describe("DedupWhyPills", () => {
  it("reads the counter-evidence first", () => {
    // Regression here means the row that most needs a closer look opens with
    // two reassuring green pills.
    const labels = mountPills()
      .findAll(".why-pill__label")
      .map((n) => n.text());
    expect(labels).toEqual([
      "Different capture time",
      "Same size",
      "Same camera",
    ]);
  });

  it("marks counter-evidence with its own modifier class", () => {
    // The class is what carries the red treatment; losing it makes an argument
    // against stacking look like an argument for it.
    const pills = mountPills().findAll(".why-pill");
    expect(pills[0].classes()).toContain("why-pill--neg");
    expect(pills[1].classes()).toContain("why-pill--pos");
  });

  it("truncates after ordering, never before", () => {
    // Truncating the server order first would be free to drop the warning and
    // leave a row that looks unanimously safe.
    const labels = mountPills({ limit: 2 })
      .findAll(".why-pill__label")
      .map((n) => n.text());
    expect(labels).toEqual(["Different capture time", "Same size"]);
  });

  it("shows every pill when no limit is set", () => {
    // A limit of 0 is the expanded row; silently capping it would hide evidence
    // the user opened the row to read.
    expect(mountPills({ limit: 0 }).findAll(".why-pill")).toHaveLength(3);
  });

  it("states the for/against split in words as well as colour", () => {
    // WCAG 1.4.1: colour alone must not carry the meaning.
    const pills = mountPills().findAll(".why-pill");
    expect(pills[0].find(".tip").attributes("data-tip")).toBe(
      "Different capture time. Argues against stacking.",
    );
    expect(pills[1].find(".tip").attributes("data-tip")).toBe(
      "Same size. Supports stacking.",
    );
    expect(pills[0].attributes("aria-label")).toBe(
      "Different capture time. Argues against stacking.",
    );
  });

  it("announces a qualitative mixed-stack summary with its exact detail", () => {
    const wrapper = mountPills({
      why: [
        {
          text: "All pictures differ",
          accessible_text:
            "34 groups: 34 single-picture groups.",
          against: true,
        },
      ],
    });
    const pill = wrapper.find(".why-pill");
    expect(pill.text()).toContain("All pictures differ");
    expect(pill.text()).not.toContain("34 single-picture groups");
    expect(pill.attributes("aria-label")).toBe(
      "34 groups: 34 single-picture groups. Argues against stacking.",
    );
  });

  it("hides a cached visual-match pill now that confidence owns the percentage", () => {
    const wrapper = mountPills({
      why: [
        { text: "98% visual match", against: false },
        { text: "Different resolution", against: true },
      ],
    });
    expect(wrapper.text()).not.toContain("visual match");
    expect(wrapper.text()).toContain("Different resolution");
  });

  it("renders nothing when a group has no evidence", () => {
    // An empty list would otherwise leave an empty flex row taking up space
    // under every group that carries no reasoning.
    const wrapper = mount(DedupWhyPills, { ...globalOpts, props: { why: [] } });
    expect(wrapper.find(".why-pills").exists()).toBe(false);
  });

  it("survives a missing why prop", () => {
    // The queue renders rows before the evidence has streamed in.
    const wrapper = mount(DedupWhyPills, globalOpts);
    expect(wrapper.findAll(".why-pill")).toHaveLength(0);
  });
});
