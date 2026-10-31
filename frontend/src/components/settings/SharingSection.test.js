// Settings › Privacy & sharing: whether an export keeps the workflow (#2417).
//
// What is pinned: it strips until told otherwise, reads the remembered value,
// saves its own key, and a failed save never shows "Keeps".

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

async function mountPane() {
  const wrapper = mount(SharingSection, {
    global: { stubs: { "v-icon": true } },
  });
  await flushPromises();
  return wrapper;
}

const state = (wrapper) => wrapper.find(".sh__state").text();

beforeEach(() => {
  setActivePinia(createPinia());
  config.patchUserConfig.mockReset();
});

describe("keep the workflow when exporting", () => {
  it("strips until told otherwise", async () => {
    expect(state(await mountPane())).toBe("Strips");
  });

  it("reads the remembered value", async () => {
    useUserPrefsStore().keepWorkflowOnExport = true;
    expect(state(await mountPane())).toBe("Keeps");
  });

  it("saves its own key", async () => {
    config.patchUserConfig.mockResolvedValue({});
    const wrapper = await mountPane();
    await wrapper.find("input").setValue(true);
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      keep_workflow_on_export: true,
    });
    expect(state(wrapper)).toBe("Keeps");
  });

  it("keeps saying Strips when the save fails", async () => {
    config.patchUserConfig.mockRejectedValue(new Error("offline"));
    const wrapper = await mountPane();
    await wrapper.find("input").setValue(true);
    await flushPromises();
    expect(state(wrapper)).toBe("Strips");
    expect(useUserPrefsStore().keepWorkflowOnExport).toBe(false);
    expect(wrapper.find("[role='alert']").text()).toContain(
      "previous choice was kept",
    );
  });
});

describe("public base URL", () => {
  async function mountOpen() {
    const prefs = useUserPrefsStore();
    prefs.publicUrl = "https://old.example.com";
    const wrapper = mount(SharingSection, {
      props: { open: true },
      global: { stubs: { "v-icon": true } },
    });
    await flushPromises();
    return { wrapper, prefs, input: wrapper.find('input[placeholder^="https://"]') };
  }

  it("starts from the saved URL, and saving writes the server and the store share links read", async () => {
    config.patchUserConfig.mockResolvedValue({});
    const { prefs, input } = await mountOpen();
    expect(input.element.value).toBe("https://old.example.com");

    await input.setValue("https://new.example.com/");
    await input.trigger("blur");
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      public_url: "https://new.example.com",
    });
    expect(prefs.publicUrl).toBe("https://new.example.com");
  });

  it("keeps the store's URL when the save fails", async () => {
    config.patchUserConfig.mockRejectedValue(new Error("offline"));
    const { wrapper, prefs, input } = await mountOpen();
    await input.setValue("https://new.example.com");
    await input.trigger("blur");
    await flushPromises();
    expect(prefs.publicUrl).toBe("https://old.example.com");
    expect(wrapper.text()).toContain("Failed to save.");
  });
});
