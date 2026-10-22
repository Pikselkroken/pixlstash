// The Templates tab's Filters panel takes the search key's focus request.

import { describe, it, expect, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import WorkflowTemplateFilterMenu from "./WorkflowTemplateFilterMenu.vue";

describe("WorkflowTemplateFilterMenu", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("focuses the name field when the request that opened it came first", async () => {
    // The view bumps the request and opens the menu in one go, so the panel
    // mounts already holding it.
    const wrapper = mount(WorkflowTemplateFilterMenu, {
      props: { focusNameRequest: 1 },
      global: { stubs: { "v-icon": true, Tooltip: true } },
      attachTo: document.body,
    });
    await flushPromises();
    expect(document.activeElement).toBe(wrapper.find("input").element);
    wrapper.unmount();
  });
});
