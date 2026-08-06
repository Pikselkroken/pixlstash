// The per-group confidence chip.
//
// The contract worth pinning is a product one, not a styling one: an exact
// match must never render as a percentage, because "100% similar" makes every
// near-duplicate suggestion in the queue look as certain as a byte-identical
// match.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupConfidencePill from "./DedupConfidencePill.vue";

const globalOpts = {
  global: {
    stubs: { "v-icon": { template: "<i><slot /></i>" } },
  },
};

function mountPill(group) {
  return mount(DedupConfidencePill, { ...globalOpts, props: { group } });
}

describe("DedupConfidencePill", () => {
  it("says Exact, and never a percentage, for an exact group", () => {
    // The whole reason this component exists: an exact match is a different
    // kind of claim from a similarity score.
    const wrapper = mountPill({ kind: "exact", confidence: 1 });
    expect(wrapper.find(".conf-pill__label").text()).toBe("Exact");
    expect(wrapper.find(".conf-pill__label").text()).not.toMatch(/%/);
  });

  it("gives the exact tier its own filled treatment", () => {
    // Rendering both tiers alike would put the two claims on equal footing.
    expect(mountPill({ kind: "exact" }).find(".conf-pill").classes()).toContain(
      "conf-pill--exact",
    );
  });

  it("renders a near group as a rounded percentage", () => {
    // The score is a measurement and reads as one.
    const wrapper = mountPill({ kind: "near", confidence: 0.943 });
    expect(wrapper.find(".conf-pill__label").text()).toBe("94% similar");
  });

  it("marks a measured near match as supporting evidence", () => {
    const pill = mountPill({ kind: "near", confidence: 0.98 }).find(
      ".conf-pill",
    );
    expect(pill.classes()).toContain("conf-pill--near");
    expect(pill.html()).toContain("mdi-check");
    expect(pill.attributes("title")).toBe(
      "98% similar. Supports stacking.",
    );
    expect(pill.attributes("aria-label")).toBe(
      "98% similar. Supports stacking.",
    );
  });

  it("falls back to neutral Similar when no score came through", () => {
    // A missing score must render neither "NaN% similar" nor a measured check.
    const pill = mountPill({ kind: "near" }).find(".conf-pill");
    expect(pill.find(".conf-pill__label").text()).toBe("Similar");
    expect(pill.classes()).toContain("conf-pill--unknown");
    expect(pill.html()).toContain("mdi-blur");
    expect(pill.html()).not.toContain("mdi-check");
  });

  it("keeps the label in the element that carries the tabular figures", () => {
    // The percentage lives in `.conf-pill__label`, which is where the
    // tabular-nums rule sits; moving the text out of it makes a scrolling
    // column of scores jitter.
    const label = mountPill({ kind: "near", confidence: 0.8 }).find(
      ".conf-pill__label",
    );
    expect(label.exists()).toBe(true);
    expect(label.text()).toBe("80% similar");
  });
});
