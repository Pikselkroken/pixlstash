// The Templates tab's Filters panel takes the search key's focus request.

import { describe, it, expect, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import WorkflowTemplateFilterMenu from "./WorkflowTemplateFilterMenu.vue";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";

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

  it("finds categories by search and ticks several, as ComfyUI's list does", async () => {
    const store = useWorkflowTemplatesStore();
    store.templates = [
      { key: "default/a", source: "builtin", categories: ["Image"] },
      { key: "default/b", source: "builtin", categories: ["Video"] },
      { key: "default/c", source: "builtin", categories: ["Video Tools"] },
    ];
    const wrapper = mount(WorkflowTemplateFilterMenu, {
      props: { open: true },
      global: { stubs: { "v-icon": true, Tooltip: true } },
      attachTo: document.body,
    });
    await wrapper.find('[data-kind="category"]').trigger("click");
    await flushPromises();
    const sub = () => wrapper.find('[data-testid="wtf-sub-category"]');
    const rows = () => sub().findAll('[data-testid="wtf-option"]');
    const labels = () => rows().map((row) => row.text());
    expect(labels()).toEqual(["Image1", "Video1", "Video Tools1"]);
    await sub().find("input").setValue("video");
    expect(labels()).toEqual(["Video1", "Video Tools1"]);
    // Several at once, as ComfyUI's own.
    await rows()[0].find("input").setValue(true);
    await rows()[1].find("input").setValue(true);
    expect(store.filters.category).toEqual(["Video", "Video Tools"]);
    // A ticked row stays whatever is typed.
    await sub().find("input").setValue("image");
    expect(labels()).toEqual(["Image1", "Video1", "Video Tools1"]);
    await rows()[1].find("input").setValue(false);
    expect(store.filters.category).toEqual(["Video Tools"]);
    wrapper.unmount();
  });
});
