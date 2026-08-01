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

describe("StackBadge: stack colour", () => {
  it("puts the stack colour on the glyph", () => {
    // The tint is the whole point of the badge carrying a colour: it is what
    // links a collapsed cover to the stack it stands for.
    const wrapper = mountBadge({ count: 3, tint: "hsl(220 70% 72%)" });
    // jsdom normalises the colour to rgb() on the way into the style attribute,
    // so the assertion is on the equivalent value, not the literal string.
    expect(wrapper.get(".sbico").attributes("style")).toContain(
      "rgb(134, 167, 234)",
    );
  });

  it("never tints the count", () => {
    // The number is small text and needs 4.5:1; no stack colour reaches that on
    // the badge's chip, so the hue stops at the glyph.
    const wrapper = mountBadge({ count: 3, tint: "hsl(220 70% 72%)" });
    expect(wrapper.get(".sbcount").attributes("style")).toBeUndefined();
  });

  it("deepens its chip when tinted", () => {
    // A coloured glyph on the standard 0.55 scrim measures 1.62:1 over a bright
    // photo. Tint and chip ship together or the indicator disappears.
    const wrapper = mountBadge({ count: 3, tint: "hsl(220 70% 72%)" });
    expect(wrapper.get('[data-testid="stack-badge"]').classes()).toContain(
      "sbadge--tinted",
    );
  });

  it("keeps the default glyph and chip with no tint", () => {
    const wrapper = mountBadge({ count: 3 });
    expect(wrapper.get(".sbico").attributes("style")).toBeUndefined();
    expect(wrapper.get('[data-testid="stack-badge"]').classes()).not.toContain(
      "sbadge--tinted",
    );
  });

  it("refuses a tint on an unresolved group", () => {
    // An unresolved group has no stack, so it has no stack colour. Letting a
    // caller tint one would hand a suggestion the signal that means "this
    // stack exists".
    const wrapper = mountBadge({
      count: 3,
      unresolved: true,
      tint: "hsl(220 70% 72%)",
    });
    expect(wrapper.get(".sbico").attributes("style")).toBeUndefined();
    expect(wrapper.get('[data-testid="stack-badge"]').classes()).not.toContain(
      "sbadge--tinted",
    );
  });
});

describe("StackBadge: as a disclosure trigger", () => {
  // The queue row's band is opened from this badge. A CSS-only open state says
  // nothing at all to a screen reader (WCAG 4.1.2).
  it("publishes aria-expanded when the caller says it opens something", () => {
    expect(
      mountBadge({ count: 4, expanded: false })
        .get('[data-testid="stack-badge"]')
        .attributes("aria-expanded"),
    ).toBe("false");
    expect(
      mountBadge({ count: 4, expanded: true })
        .get('[data-testid="stack-badge"]')
        .attributes("aria-expanded"),
    ).toBe("true");
  });

  // On the grid the press jumps or expands the tile itself; claiming to be a
  // disclosure there would be a lie, so the attribute is absent by default.
  it("publishes none where the press is not a disclosure", () => {
    expect(
      mountBadge({ count: 4 })
        .get('[data-testid="stack-badge"]')
        .attributes("aria-expanded"),
    ).toBeUndefined();
  });

  // The name has to describe the ACTION where the press does something other
  // than state the count the numeral already carries.
  it("lets the caller name what the press does", () => {
    expect(
      mountBadge({ count: 4, actionTitle: "Show the 4 pictures in this stack" })
        .get('[data-testid="stack-badge"]')
        .attributes("title"),
    ).toBe("Show the 4 pictures in this stack");
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
