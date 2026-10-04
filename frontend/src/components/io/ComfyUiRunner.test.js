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
});
