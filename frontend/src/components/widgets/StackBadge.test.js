// The grid tile's stack badge.
//
// These pin the two things the badge is responsible for: that a suggestion
// never renders as an existing stack, and that a click reaches the parent so it
// can expand the stack or jump to the group in the queue.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import StackBadge from "./StackBadge.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** Mount the badge with the given props. */
function mountBadge(props = {}) {
  return mount(StackBadge, { ...globalOpts, props });
}

describe("StackBadge — when it appears", () => {
  it("renders nothing for a lone picture", () => {
    // A badge reading "1" on every single-picture tile would be noise on the
    // overwhelming majority of the grid.
    const wrapper = mountBadge({ count: 1 });
    expect(wrapper.find('[data-testid="stack-badge"]').exists()).toBe(false);
  });

  it("renders nothing when no count was supplied", () => {
    // Guards the default: a tile whose count has not loaded must not flash a
    // badge claiming "0".
    const wrapper = mountBadge();
    expect(wrapper.find('[data-testid="stack-badge"]').exists()).toBe(false);
  });

  it("shows the count from two pictures up", () => {
    // Two is the smallest real stack; anything that hid it would leave the
    // commonest stack indistinguishable from a single photo.
    const wrapper = mountBadge({ count: 2 });
    expect(wrapper.find(".sbcount").text()).toBe("2");
  });
});

describe("StackBadge — stacked vs unresolved", () => {
  it("states the stack as a fact", () => {
    // The resolved state carries no question mark: this stack exists.
    const wrapper = mountBadge({ count: 4 });
    expect(wrapper.find(".sbcount").text()).toBe("4");
    expect(wrapper.find(".sbcount").text()).not.toContain("?");
    expect(wrapper.get('[data-testid="stack-badge"]').attributes("title")).toBe(
      "Stack of 4 pictures",
    );
  });

  it("marks an unresolved group with a question mark", () => {
    // Without it, a queue suggestion looks identical to a stack that already
    // exists and the user believes pictures were merged when nothing happened.
    const wrapper = mountBadge({ count: 4, unresolved: true });
    expect(wrapper.find(".sbcount").text()).toBe("4?");
    expect(wrapper.get('[data-testid="stack-badge"]').classes()).toContain(
      "sbadge--unresolved",
    );
  });

  it("tells an unresolved group where the decision is made", () => {
    // The title is the only place the badge can say that nothing is stacked yet
    // and point at the queue.
    const wrapper = mountBadge({ count: 3, unresolved: true });
    expect(wrapper.get('[data-testid="stack-badge"]').attributes("title")).toBe(
      "3 possible duplicates, not stacked yet. Open Duplicates to decide.",
    );
  });
});

describe("StackBadge — activation", () => {
  it("emits activate when clicked", () => {
    // The parent decides what a click means; the badge only reports it.
    const wrapper = mountBadge({ count: 3 });
    wrapper.get('[data-testid="stack-badge"]').trigger("click");
    expect(wrapper.emitted("activate")).toHaveLength(1);
  });

  it("is a real button, so Enter and Space work without extra handlers", () => {
    // Keyboard activation is free on a <button> and hand-rolled on a <div>;
    // this is what stops the badge becoming mouse-only again.
    const badge = mountBadge({ count: 3 }).get('[data-testid="stack-badge"]');
    expect(badge.element.tagName).toBe("BUTTON");
    expect(badge.attributes("type")).toBe("button");
  });
});
