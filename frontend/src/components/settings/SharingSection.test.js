// Settings › Sharing: whether each way out keeps the workflow (#2417).
//
// What is pinned: every row strips until told otherwise, each row reads and
// saves only its own key, and a failed save never shows "Keeps".

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i />" },
  VSwitch: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
}));

const config = vi.hoisted(() => ({ patchUserConfig: vi.fn() }));
vi.mock("../../api/config", () => config);

import SharingSection from "./SharingSection.vue";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

const WAYS = ["copy", "drag", "share"];

async function mountPane() {
  const wrapper = mount(SharingSection, {
    global: { stubs: { "v-icon": true } },
  });
  await flushPromises();
  return wrapper;
}

const state = (wrapper, way) =>
  wrapper.find(`[data-way="${way}"] .sh__state`).text();

beforeEach(() => {
  setActivePinia(createPinia());
  config.patchUserConfig.mockReset();
});

describe("shared copies", () => {
  it("strips on every way out until told otherwise", async () => {
    const wrapper = await mountPane();
    expect(WAYS.map((w) => state(wrapper, w))).toEqual([
      "Strips",
      "Strips",
      "Strips",
    ]);
  });

  it("reads each way out from the user config on its own", async () => {
    useUserPrefsStore().hydrateKeepWorkflow({ keep_workflow_drag: true });
    const wrapper = await mountPane();
    expect(WAYS.map((w) => state(wrapper, w))).toEqual([
      "Strips",
      "Keeps",
      "Strips",
    ]);
  });

  it("saves only the row that was switched", async () => {
    config.patchUserConfig.mockResolvedValue({});
    const wrapper = await mountPane();
    await wrapper.find('[data-way="copy"] input').setValue(true);
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      keep_workflow_copy: true,
    });
    expect(state(wrapper, "copy")).toBe("Keeps");
    expect(state(wrapper, "drag")).toBe("Strips");
  });

  it("keeps saying Strips when the save fails", async () => {
    config.patchUserConfig.mockRejectedValue(new Error("offline"));
    const wrapper = await mountPane();
    await wrapper.find('[data-way="share"] input').setValue(true);
    await flushPromises();
    expect(state(wrapper, "share")).toBe("Strips");
    expect(useUserPrefsStore().keepWorkflow.share).toBe(false);
    expect(wrapper.find("[role='alert']").text()).toContain(
      "previous choice was kept",
    );
  });
});
