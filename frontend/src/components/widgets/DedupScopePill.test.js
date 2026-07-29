// The dismissible pill that says the duplicate queue is scoped to one project,
// set, character, or folder.
//
// The tests pin the two things that matter: the scope is actually stated, and
// the way out of it is a real, correctly-labelled button.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupScopePill from "./DedupScopePill.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

function mountPill(props = {}) {
  return mount(DedupScopePill, {
    ...globalOpts,
    props: { label: "Iceland 2026", ...props },
  });
}

describe("DedupScopePill", () => {
  it("states the scope it is filtered to", () => {
    // A filtered queue that does not say so is how a user concludes they have
    // no duplicates left while looking at one folder.
    expect(mountPill().text()).toContain("Iceland 2026");
  });

  it("shows a count when it has one", () => {
    // Thousands separators, because a raw 12000 is read wrong at a glance.
    expect(mountPill({ count: 12000 }).find(".scope-pill__count").text()).toBe(
      (12000).toLocaleString(),
    );
  });

  it("omits the count when it is unknown", () => {
    // A missing count must not render as a misleading "0".
    expect(mountPill().find(".scope-pill__count").exists()).toBe(false);
  });

  it("emits dismiss when the control is used", () => {
    // The pill is the only way back to the whole library.
    const wrapper = mountPill();
    wrapper.find(".scope-pill__dismiss").trigger("click");
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
  });

  it("labels dismissal by what it does, not by its glyph", () => {
    // "Close" is meaningless read out of context; the label has to name the
    // outcome (WCAG 2.4.6).
    const button = mountPill().find(".scope-pill__dismiss");
    expect(button.attributes("aria-label")).toBe(
      "Show duplicates in the whole library",
    );
  });

  it("uses a real button, typed so it cannot submit a surrounding form", () => {
    // A div with a click handler is unreachable by keyboard, and an untyped
    // button inside a form defaults to submit.
    const button = mountPill().find(".scope-pill__dismiss");
    expect(button.element.tagName).toBe("BUTTON");
    expect(button.attributes("type")).toBe("button");
  });

  it("shows a genuine zero rather than hiding it", () => {
    // A scope with nothing left in it is information, not a missing value, and
    // it is the state that tells the user the scope is done.
    expect(mountPill({ count: 0 }).find(".scope-pill__count").text()).toBe("0");
  });
});
