import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const getCaptionSettings = vi.fn();
const setCaptionSettings = vi.fn();
vi.mock("../../api/serverConfig", () => ({
  getCaptionSettings: (...a) => getCaptionSettings(...a),
  setCaptionSettings: (...a) => setCaptionSettings(...a),
}));

import LibraryCaptionsDialog from "./LibraryCaptionsDialog.vue";
import { useLibrariesStore } from "../../stores/useLibrariesStore";

const STORED = {
  sync_tags: false,
  sync_descriptions: true,
  tags_suffix: null,
  description_suffix: "_caption.txt",
  default_tags_suffix: "_tags.txt",
  default_description_suffix: "_description.txt",
};

function mountDialog() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const libraries = useLibrariesStore();
  libraries.hasLoadedSuccessfully = true;
  libraries.canManage = true;
  return mount(LibraryCaptionsDialog, {
    props: { open: true },
    global: {
      plugins: [pinia],
      stubs: {
        AppDialog: { template: "<div><slot /><slot name='footer' /></div>" },
        VCheckbox: {
          props: ["modelValue", "label"],
          emits: ["update:modelValue"],
          template:
            '<label><input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />{{ label }}</label>',
        },
        VTextField: {
          props: ["modelValue", "label"],
          emits: ["update:modelValue"],
          template:
            '<input :aria-label="label" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
        AppButton: {
          props: ["disabled", "loading"],
          template:
            '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
        },
      },
    },
  });
}

function button(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text));
}

describe("LibraryCaptionsDialog", () => {
  beforeEach(() => {
    getCaptionSettings.mockReset();
    setCaptionSettings.mockReset();
    getCaptionSettings.mockResolvedValue(STORED);
  });

  it("shows the stored settings, a suffix field only for a type that is on", async () => {
    const wrapper = mountDialog();
    await flushPromises();
    const boxes = wrapper.findAll("input[type=checkbox]");
    expect(boxes.map((b) => b.element.checked)).toEqual([false, true]);
    expect(wrapper.find("input[aria-label='Suffix for new tags files']").exists()).toBe(false);
    const field = wrapper.find("input[aria-label='Suffix for new description files']");
    expect(field.element.value).toBe("_caption.txt");
    expect(wrapper.text()).toContain("image_caption.txt");
  });

  it("writes nothing until Save, then patches every field and closes", async () => {
    setCaptionSettings.mockResolvedValue({ ...STORED, sync_tags: true, tags_suffix: ".txt" });
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll("input[type=checkbox]")[0].setValue(true);
    await wrapper.find("input[aria-label='Suffix for new tags files']").setValue(".txt");
    expect(setCaptionSettings).not.toHaveBeenCalled();

    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    expect(setCaptionSettings).toHaveBeenCalledWith({
      syncTags: true,
      syncDescriptions: true,
      tagsSuffix: ".txt",
      descriptionSuffix: "_caption.txt",
    });
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("shows the server's refusal and stays open", async () => {
    setCaptionSettings.mockRejectedValue({
      response: { status: 400, data: { detail: "Sidecar suffix may only contain letters" } },
    });
    const wrapper = mountDialog();
    await flushPromises();
    await button(wrapper, "Save").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Sidecar suffix may only contain letters");
    expect(wrapper.emitted("close")).toBeFalsy();
  });
});
