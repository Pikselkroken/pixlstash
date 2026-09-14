// The shared inspector shell: the tab band drives v-model, a disabled tab stays
// in place, and a collapsed pane keeps its box but renders nothing inside it.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import AppInspector from "./AppInspector.vue";

const TABS = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta", disabled: true, title: "Nothing here" },
];

function mountInspector(props = {}) {
  return mount(AppInspector, {
    props: { label: "Stats", tabs: TABS, modelValue: "a", ...props },
    slots: { default: '<p class="body-probe">content</p>' },
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
    const beta = wrapper.findAll("button.inspector-tab")[1];
    expect(beta.attributes("disabled")).toBeDefined();
    expect(beta.attributes("title")).toBe("Nothing here");
    await beta.trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
  });

  it("lets the host replace a tab's content through the tab slot", () => {
    const wrapper = mount(AppInspector, {
      props: { label: "Stats", tabs: TABS, modelValue: "a" },
      slots: {
        tab: '<template #tab="{ tab }"><b class="probe">{{ tab.value }}</b></template>',
      },
    });
    expect(wrapper.findAll(".probe").map((b) => b.text())).toEqual(["a", "b"]);
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
});
