// The one drag grip every workspace's sidebar wears. What can silently go wrong
// is the width: a drag that persists every frame, a width that escapes the
// clamp, or two sidebars holding two widths so a workspace switch moves the
// edge.

import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

/** A sidebar in miniature: draws at the shared width, holds the handle. */
const Bar = {
  components: { SidebarResizeHandle },
  setup: () => useSidebarWidth(),
  template: `<div :style="{ width: width + 'px' }"><SidebarResizeHandle /></div>`,
};

const mountBar = () =>
  mount(Bar, { global: { stubs: { Tooltip: true } }, attachTo: document.body });

const pointer = (type, clientX) =>
  window.dispatchEvent(new MouseEvent(type, { clientX }));

/** Press on the handle at x = 100. */
const grab = (handle) =>
  handle.element.dispatchEvent(
    new MouseEvent("pointerdown", { clientX: 100, bubbles: true }),
  );

let prefs;

beforeEach(() => {
  setActivePinia(createPinia());
  prefs = useUserPrefsStore();
  prefs.sidebarWidth = 240;
});

describe("SidebarResizeHandle", () => {
  it("draws the drag live and saves the width once, on release", async () => {
    const wrapper = mountBar();
    grab(wrapper.find(".sidebar-resize-handle"));
    pointer("pointermove", 130);
    pointer("pointermove", 150);
    await wrapper.vm.$nextTick();
    expect(wrapper.element.style.width).toBe("290px");
    expect(prefs.sidebarWidth).toBe(240);
    pointer("pointerup", 150);
    await wrapper.vm.$nextTick();
    expect(prefs.sidebarWidth).toBe(290);
    expect(wrapper.element.style.width).toBe("290px");
    wrapper.unmount();
  });

  it("clamps a drag past either end", async () => {
    const wrapper = mountBar();
    const handle = wrapper.find(".sidebar-resize-handle");
    grab(handle);
    pointer("pointermove", 900);
    pointer("pointerup", 900);
    expect(prefs.sidebarWidth).toBe(300);
    grab(handle);
    pointer("pointermove", -900);
    pointer("pointerup", -900);
    expect(prefs.sidebarWidth).toBe(140);
    wrapper.unmount();
  });

  it("steps with the arrow keys, further with Shift, inside the clamp", async () => {
    const wrapper = mountBar();
    const handle = wrapper.find(".sidebar-resize-handle");
    await handle.trigger("keydown", { key: "ArrowLeft" });
    expect(prefs.sidebarWidth).toBe(232);
    await handle.trigger("keydown", { key: "ArrowRight", shiftKey: true });
    expect(prefs.sidebarWidth).toBe(256);
    prefs.sidebarWidth = 150;
    await handle.trigger("keydown", { key: "ArrowLeft", shiftKey: true });
    expect(prefs.sidebarWidth).toBe(140);
    expect(handle.attributes("aria-valuenow")).toBe("140");
    wrapper.unmount();
  });

  it("moves every sidebar's edge at once, so a switch keeps the width", async () => {
    const one = mountBar();
    const two = mountBar();
    grab(one.find(".sidebar-resize-handle"));
    pointer("pointermove", 80);
    await two.vm.$nextTick();
    expect(two.element.style.width).toBe("220px");
    pointer("pointerup", 80);
    one.unmount();
    two.unmount();
  });

  it("commits a drag still under way when its sidebar goes", async () => {
    const wrapper = mountBar();
    grab(wrapper.find(".sidebar-resize-handle"));
    pointer("pointermove", 140);
    wrapper.unmount();
    expect(prefs.sidebarWidth).toBe(280);
    // The listeners went with it: a later move changes nothing.
    pointer("pointermove", 300);
    pointer("pointerup", 300);
    expect(prefs.sidebarWidth).toBe(280);
  });
});
