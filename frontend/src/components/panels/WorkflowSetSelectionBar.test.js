// The set pill's context menu is opened from code at a point (#1520), so
// v-menu has no activator to hand focus back to. It must return it itself.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import WorkflowSetSelectionBar from "./WorkflowSetSelectionBar.vue";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: true,
      "v-menu": {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: "<div><slot /></div>",
      },
    },
  },
};

describe("WorkflowSetSelectionBar", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("puts focus back on the card that opened the menu when it closes", async () => {
    const card = document.createElement("div");
    card.tabIndex = 0;
    document.body.appendChild(card);
    const wrapper = mount(WorkflowSetSelectionBar, {
      ...globalOpts,
      attachTo: document.body,
    });

    wrapper.vm.openContextMenu(10, 20, card);
    await wrapper.vm.$nextTick();
    document.activeElement?.blur?.();
    // A menu row pressed: the menu closes.
    await wrapper.find(".ctx-item").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.activeElement).toBe(card);
    wrapper.unmount();
    card.remove();
  });
});
