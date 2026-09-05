// The deck-of-cards edges behind a collapsed stack's cover.
//
// Decoration with one rule: the number of peeking layers, and the fact that it
// stays out of the accessibility tree and out of the pointer's way.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import StackEdgeTicks from "./StackEdgeTicks.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** Mount the ticks with the given props. */
function mountTicks(props = {}) {
  return mount(StackEdgeTicks, { ...globalOpts, props });
}

describe("StackEdgeTicks - how many layers", () => {
  it("draws nothing for a lone picture", () => {
    // A single photo with a peeking edge would claim a stack that is not there.
    const wrapper = mountTicks({ count: 1 });
    expect(wrapper.find('[data-testid="stack-edge-ticks"]').exists()).toBe(
      false,
    );
    expect(wrapper.findAll(".stick")).toHaveLength(0);
  });

  it("draws nothing when no count was supplied", () => {
    // Guards the default so a tile mid-load does not render stray edges.
    expect(mountTicks().findAll(".stick")).toHaveLength(0);
  });

  it("draws one edge for a pair", () => {
    // Two pictures is one card behind the cover; a second would overstate it.
    expect(mountTicks({ count: 2 }).findAll(".stick")).toHaveLength(1);
  });

  it("caps at two edges however deep the stack is", () => {
    // A third peek is invisible at grid scale and only softens the corner, so
    // five and three must render the same deck.
    expect(mountTicks({ count: 3 }).findAll(".stick")).toHaveLength(2);
    expect(mountTicks({ count: 5 }).findAll(".stick")).toHaveLength(2);
    expect(mountTicks({ count: 200 }).findAll(".stick")).toHaveLength(2);
  });
});

describe("StackEdgeTicks - it stays decoration", () => {
  it("hides itself from assistive technology", () => {
    // The badge carries the meaning; announcing two empty layers is noise.
    expect(
      mountTicks({ count: 5 })
        .get('[data-testid="stack-edge-ticks"]')
        .attributes("aria-hidden"),
    ).toBe("true");
  });

  it("offsets each layer further than the last", () => {
    // The deck reads only if the two edges are distinguishable; identical
    // transforms would stack them into one line.
    const layers = mountTicks({ count: 5 }).findAll(".stick");
    expect(layers[0].classes()).toContain("stick--1");
    expect(layers[1].classes()).toContain("stick--2");
  });
});
