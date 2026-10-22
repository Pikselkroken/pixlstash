// The Templates tab notices a ComfyUI that started after PixlStash did.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, shallowMount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import WorkflowTemplatesPanel from "./WorkflowTemplatesPanel.vue";
import { useWorkflowTemplatesStore } from "../../stores/useWorkflowTemplatesStore";

const listTemplates = vi.fn();
vi.mock("../../api/comfyui", () => ({
  listTemplates: (...args) => listTemplates(...args),
  templateThumbnailUrl: () => "",
}));

describe("WorkflowTemplatesPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setActivePinia(createPinia());
    listTemplates.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("asks ComfyUI again every 10 s while it has not answered, and stops when closed", async () => {
    listTemplates.mockResolvedValue({ reachable: false, detail: "refused" });
    const store = useWorkflowTemplatesStore();
    await store.fetchTemplates();
    const wrapper = shallowMount(WorkflowTemplatesPanel, {
      props: { columnMin: 240, columnGap: 16 },
    });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(listTemplates).toHaveBeenCalledTimes(2);

    listTemplates.mockResolvedValue({ reachable: true, templates: [] });
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(store.reachable).toBe(true);
    // Answered: nothing more to ask.
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listTemplates).toHaveBeenCalledTimes(3);

    store.reachable = false;
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(listTemplates).toHaveBeenCalledTimes(3);
  });
});
