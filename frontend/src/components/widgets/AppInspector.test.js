// The shared inspector shell: the tab band drives v-model, a disabled tab stays
// in place, and a collapsed pane keeps its box but renders nothing inside it.

import { describe, it, expect } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import AppInspector from "./AppInspector.vue";
import { describedAs } from "../../testing/describedAs.js";

const TABS = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta", disabled: true, tooltip: "Nothing here" },
];

function mountInspector(props = {}) {
  return mount(AppInspector, {
    props: { label: "Stats", tabs: TABS, modelValue: "a", ...props },
    slots: { default: '<p class="body-probe">content</p>' },
    // A tab's glyph is Vuetify's, which wants a defaults instance this bare
    // mount has no reason to stand up.
    global: { stubs: { "v-icon": true } },
  });
}

describe("AppInspector", () => {
  it("marks the active tab and emits the one clicked", async () => {
    const wrapper = mountInspector();
    const tabs = wrapper.findAll("button.inspector-tab");
    expect(tabs.map((t) => t.text())).toEqual(["Alpha", "Beta"]);
    expect(tabs[0].classes()).toContain("inspector-tab--active");
    expect(tabs[0].attributes("aria-pressed")).toBe("true");
    expect(tabs[1].attributes("aria-pressed")).toBe("false");

    await wrapper.setProps({
      tabs: [TABS[0], { ...TABS[1], disabled: false }],
    });
    await wrapper.findAll("button.inspector-tab")[1].trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([["b"]]);
  });

  it("keeps a disabled tab in the band with its reason, and inert", async () => {
    const wrapper = mountInspector();
    await flushPromises();
    const beta = wrapper.findAll("button.inspector-tab")[1];
    expect(beta.attributes("disabled")).toBeDefined();
    // The reason is the tab's tooltip, which a labelled tab exposes as its
    // description (buttons.md, "Tooltips").
    expect(describedAs(beta)).toBe("Nothing here");
    await beta.trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("lights only the tab the host marks busy, and says how much work", async () => {
    // The Tasks tab's live-work light. It lives here rather than in a host so
    // every inspector offering that tab pulses the same way; a light drawn on
    // the wrong tab, or on all of them, is the way this inverts silently.
    const wrapper = mountInspector({
      tabs: [
        { value: "a", label: "Alpha", icon: "mdi-alpha" },
        {
          value: "b",
          label: "Beta",
          icon: "mdi-beta",
          busy: true,
          busyTooltip: "2 active tasks",
        },
      ],
    });
    await flushPromises();
    const tabs = wrapper.findAll("button.inspector-tab");
    expect(tabs[0].find(".inspector-tab-pulse").exists()).toBe(false);
    expect(tabs[0].find(".inspector-tab-icon--busy").exists()).toBe(false);
    expect(tabs[1].find(".inspector-tab-pulse").exists()).toBe(true);
    expect(tabs[1].find(".inspector-tab-icon--busy").exists()).toBe(true);
    expect(describedAs(tabs[1])).toBe("2 active tasks");
    // The dot is the same fact the description already carries, so it must not
    // be read out a second time as an unlabelled node.
    expect(tabs[1].find(".inspector-tab-pulse").attributes("aria-hidden")).toBe(
      "true",
    );
  });

  it("names the pane and collapses without unmounting the box", async () => {
    const wrapper = mountInspector();
    expect(wrapper.attributes("aria-label")).toBe("Stats");
    expect(wrapper.attributes("aria-hidden")).toBeUndefined();
    expect(wrapper.find(".body-probe").exists()).toBe(true);

    await wrapper.setProps({ open: false });
    expect(wrapper.find("aside.inspector").exists()).toBe(true);
    expect(wrapper.classes()).toContain("inspector--collapsed");
    expect(wrapper.find(".body-probe").exists()).toBe(false);
    // An empty zero-width pane must not stay in the landmark list.
    expect(wrapper.attributes("aria-hidden")).toBe("true");
  });

  it("keeps the lightbox pane's content mounted, only hidden, while closed", async () => {
    // The overlay reaches into its panels as the pane opens (start editing the
    // description), so closing it must not throw their state away.
    const wrapper = mountInspector({ lightbox: true, open: false, tabs: [] });
    expect(wrapper.classes()).toContain("inspector--lightbox");
    const content = wrapper.find(".inspector-content");
    const probe = wrapper.find(".body-probe").element;
    expect(probe).toBeTruthy();
    expect(content.attributes("style")).toContain("display: none");
    expect(wrapper.attributes("aria-hidden")).toBe("true");

    await wrapper.setProps({ open: true });
    // The same node, not a remount.
    expect(wrapper.find(".body-probe").element).toBe(probe);
    expect(wrapper.attributes("aria-hidden")).toBeUndefined();
    expect(
      wrapper.find(".inspector-content").attributes("style") ?? "",
    ).not.toContain("display: none");
  });
});
