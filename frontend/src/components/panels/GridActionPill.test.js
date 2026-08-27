// The grid's single bottom-edge surface. These tests pin the structural
// promises the merge rests on, not the styling: that the two halves are real
// groups a screen reader can navigate, that the seam only exists when there are
// two contexts to separate, that expanding does not tear down the half already
// on screen, and that focus never falls to <body> when a half unmounts.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import GridActionPill from "./GridActionPill.vue";

function mountPill(props = {}, attachTo = undefined) {
  return mount(GridActionPill, {
    props: { searchActive: false, selectionActive: false, ...props },
    slots: {
      search: '<button class="search-ctl" type="button">Clear search</button>',
      selection: '<button class="sel-ctl" type="button">Delete</button>',
    },
    attachTo,
  });
}

describe("GridActionPill - structure", () => {
  it("renders nothing when neither half has anything to say", () => {
    const wrapper = mountPill();
    expect(wrapper.find(".grid-action-pill").exists()).toBe(false);
  });

  it("labels each half as its own group", () => {
    // The GROUP boundary, not the divider, is what a screen reader navigates
    // by, which is why the halves are real groups and not styled runs.
    const wrapper = mountPill({ searchActive: true, selectionActive: true });
    const groups = wrapper.findAll('[role="group"]');
    expect(groups).toHaveLength(2);
    expect(groups[0].attributes("aria-label")).toBe("Search results");
    expect(groups[1].attributes("aria-label")).toBe("Selection actions");
  });

  it("draws the seam only when there are two contexts to separate", () => {
    const searchOnly = mountPill({ searchActive: true });
    expect(searchOnly.find(".pill-seam").exists()).toBe(false);

    const selectionOnly = mountPill({ selectionActive: true });
    expect(selectionOnly.find(".pill-seam").exists()).toBe(false);

    const both = mountPill({ searchActive: true, selectionActive: true });
    expect(both.find(".pill-seam").exists()).toBe(true);
  });

  it("hides the seam from assistive tech", () => {
    // It is decoration. The group boundary already carries the meaning.
    const wrapper = mountPill({ searchActive: true, selectionActive: true });
    expect(wrapper.find(".pill-seam").attributes("aria-hidden")).toBe("true");
  });

  it("keeps the search half mounted across the expand", async () => {
    // The expand is geometry-stable by design: the pill reflows once and the
    // cue rides on the seam and the entering half. Tearing down the half the
    // user is already pointing at would defeat that whatever the CSS does.
    const wrapper = mountPill({ searchActive: true });
    const before = wrapper.find(".search-ctl").element;

    await wrapper.setProps({ selectionActive: true });
    expect(wrapper.find(".search-ctl").element).toBe(before);

    await wrapper.setProps({ selectionActive: false });
    expect(wrapper.find(".search-ctl").element).toBe(before);
  });
});

describe("GridActionPill - focus rescue", () => {
  it("moves focus to the surviving half when a half unmounts under it", async () => {
    // Esc clears the selection while focus sits on Delete. Without this, focus
    // lands on <body> and a keyboard user drops out of the tab order entirely
    // (WCAG 2.4.3).
    const root = document.createElement("div");
    document.body.appendChild(root);
    const wrapper = mountPill(
      { searchActive: true, selectionActive: true },
      root,
    );

    wrapper.find(".sel-ctl").element.focus();
    expect(document.activeElement).toBe(wrapper.find(".sel-ctl").element);

    await wrapper.setProps({ selectionActive: false });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(wrapper.find(".search-ctl").element);
    expect(wrapper.emitted("focus-escaped")).toBeUndefined();

    wrapper.unmount();
    root.remove();
  });

  it("hands focus back to the host when no half survives", async () => {
    // The pill does not know where the grid cursor is, so it asks rather than
    // guessing.
    const root = document.createElement("div");
    document.body.appendChild(root);
    const wrapper = mountPill({ selectionActive: true }, root);

    wrapper.find(".sel-ctl").element.focus();
    await wrapper.setProps({ selectionActive: false });
    await wrapper.vm.$nextTick();

    expect(wrapper.emitted("focus-escaped")).toHaveLength(1);

    wrapper.unmount();
    root.remove();
  });

  it("leaves focus alone when the unmounting half did not hold it", async () => {
    const root = document.createElement("div");
    document.body.appendChild(root);
    const wrapper = mountPill(
      { searchActive: true, selectionActive: true },
      root,
    );

    wrapper.find(".search-ctl").element.focus();
    await wrapper.setProps({ selectionActive: false });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(wrapper.find(".search-ctl").element);
    expect(wrapper.emitted("focus-escaped")).toBeUndefined();

    wrapper.unmount();
    root.remove();
  });
});
