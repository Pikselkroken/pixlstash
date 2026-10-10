// A run can fail before `/workflows/run` has answered (a cached graph, a batch
// still submitting), so the failure event reaches the runner before the run is
// registered. Registering it then must not replace the error with "queued".

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../api/comfyui", () => ({ abortRun: vi.fn() }));
vi.mock("../../api/pictures", () => ({ getPictureMetadata: vi.fn() }));
vi.mock("../../api/stacks", () => ({ listStackPictures: vi.fn() }));
vi.mock("../../utils/apiClient", () => ({ API_BASE_URL: "" }));

import ComfyUiRunner from "./ComfyUiRunner.vue";
import { useTasksStore } from "../../stores/useTasksStore";

function mountRunner() {
  return mount(ComfyUiRunner, {
    props: {
      getPictureStackId: () => null,
      selectNewestStackMember: () => null,
    },
    global: { stubs: { Tooltip: true } },
  });
}

function failed(promptId, message) {
  return {
    key: Date.now(),
    payload: {
      plugin: "ComfyUI",
      status: "failed",
      run_id: `comfyui-${promptId}`,
      message,
    },
  };
}

function completed(promptId) {
  return {
    key: Date.now(),
    payload: {
      plugin: "ComfyUI",
      status: "completed",
      run_id: `comfyui-${promptId}`,
    },
  };
}

describe("ComfyUiRunner", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("keeps a failure that arrived before its run was registered", async () => {
    const wrapper = mountRunner();
    await wrapper.setProps({ wsPluginProgress: failed("p-1", "node 94: bad") });
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-1" }] });
    expect(wrapper.vm.progress.status).toBe("failed");
    expect(wrapper.vm.progress.message).toBe("node 94: bad");
  });

  it("still queues a run whose prompts have not failed", async () => {
    const wrapper = mountRunner();
    await wrapper.setProps({ wsPluginProgress: failed("p-1", "node 94: bad") });
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-2" }] });
    expect(wrapper.vm.progress.status).toBe("queued");
  });

  // ComfyUI tells nobody that a prompt submitted without a client id is
  // over, so the last thing its socket says is a sampler at 100%.
  it("ends a run when the backend says it is over", async () => {
    const wrapper = mountRunner();
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-1" }] });
    wrapper.vm.handleComfyuiPayload({
      type: "progress",
      data: { prompt_id: "p-1", node: "3", value: 20, max: 20 },
    });
    expect(wrapper.vm.progress.status).toBe("running");

    await wrapper.setProps({ wsPluginProgress: completed("p-1") });
    expect(wrapper.vm.progress.status).toBe("completed");
  });

  it("ends a run that was over before it was registered", async () => {
    const wrapper = mountRunner();
    await wrapper.setProps({ wsPluginProgress: completed("p-1") });
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-1" }] });
    expect(wrapper.vm.progress.status).toBe("completed");
  });

  // Every tab hears every run end.
  it("shows nothing for the end of a run it does not follow", async () => {
    const wrapper = mountRunner();
    await wrapper.setProps({ wsPluginProgress: completed("p-9") });
    expect(wrapper.vm.progress.visible).toBe(false);

    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-1" }] });
    await wrapper.setProps({ wsPluginProgress: completed("p-9") });
    expect(wrapper.vm.progress.status).toBe("queued");
  });

  // `progress` is one node's sampler. A graph with a second pass (I2I plus an
  // upscale) reports 100% at the end of its first sampler, long before the
  // prompt is done.
  it("does not finish a prompt when one sampler reaches its max", () => {
    const wrapper = mountRunner();
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-1" }] });
    wrapper.vm.handleComfyuiPayload({
      type: "progress",
      data: { prompt_id: "p-1", node: "3", value: 20, max: 20 },
    });
    expect(wrapper.vm.progress.status).toBe("running");

    wrapper.vm.handleComfyuiPayload({
      type: "executing",
      data: { prompt_id: "p-1", node: null },
    });
    expect(wrapper.vm.progress.status).toBe("completed");
  });

  it("keeps the row while another of its prompts is still active", () => {
    const wrapper = mountRunner();
    wrapper.vm.handleComfyuiRun({
      prompts: [{ prompt_id: "p-1" }, { prompt_id: "p-2" }],
    });
    wrapper.vm.handleComfyuiPayload({
      type: "execution_success",
      data: { prompt_id: "p-1" },
    });
    expect(wrapper.vm.progress.visible).toBe(true);
    expect(wrapper.vm.progress.status).toBe("running");
  });

  // A video ahead of it in the queue takes longer than the silence allowed,
  // and ComfyUI says nothing about a prompt until its turn comes.
  it("does not call a prompt dead while ComfyUI is busy with another", () => {
    const wrapper = mountRunner();
    const start = Date.now();
    wrapper.vm.handleComfyuiRun({
      prompts: [{ prompt_id: "p-1" }, { prompt_id: "p-2" }],
    });
    vi.useFakeTimers({ now: start + 6 * 60 * 1000 });
    try {
      wrapper.vm.handleComfyuiPayload({
        type: "progress",
        data: { prompt_id: "p-1", node: "3", value: 5, max: 20 },
      });
      wrapper.vm.pruneStaleComfyuiPrompts();
      expect(wrapper.vm.progress.status).toBe("running");

      // Nothing from ComfyUI at all for five minutes is still a dead run.
      wrapper.vm.pruneStaleComfyuiPrompts(Date.now() + 6 * 60 * 1000);
      expect(wrapper.vm.progress.status).toBe("failed");
    } finally {
      vi.useRealTimers();
    }
  });

  it("names the Tasks row after the prompt ComfyUI is running", async () => {
    const wrapper = mountRunner();
    const row = () => Object.values(useTasksStore().comfyuiRuns)[0];
    wrapper.vm.handleComfyuiRun({
      prompts: [
        { prompt_id: "p-1", label: "Clone picture" },
        { prompt_id: "p-2", label: "Cinematic portrait" },
      ],
    });
    await wrapper.vm.$nextTick();
    expect(row().label).toBe("Clone picture");

    // Queued behind the first: the row keeps naming what is in front.
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-3", label: "Rainy" }] });
    await wrapper.vm.$nextTick();
    expect(row().label).toBe("Clone picture");

    wrapper.vm.handleComfyuiPayload({
      type: "progress",
      data: { prompt_id: "p-2", node: "3", value: 5, max: 20 },
    });
    await wrapper.vm.$nextTick();
    expect(row().label).toBe("Cinematic portrait");
  });

  it("calls a run that names nothing ComfyUI", async () => {
    const wrapper = mountRunner();
    wrapper.vm.handleComfyuiRun({ prompts: [{ prompt_id: "p-1" }] });
    await wrapper.vm.$nextTick();
    expect(Object.values(useTasksStore().comfyuiRuns)[0].label).toBe("ComfyUI");
  });
});
