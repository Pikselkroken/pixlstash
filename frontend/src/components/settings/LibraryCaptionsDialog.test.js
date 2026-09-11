// Caption files - the dialog off the active library's overflow menu.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { useLibrariesStore } from "../../stores/useLibrariesStore";

vi.mock("vuetify/components", () => ({
  VCheckbox: {
    props: { modelValue: Boolean, disabled: Boolean, label: String },
    emits: ["update:modelValue"],
    template:
      '<label><input type="checkbox" :checked="modelValue" :disabled="disabled" @change="$emit(\'update:modelValue\', $event.target.checked)" />{{ label }}</label>',
  },
  VTextField: {
    props: { modelValue: String, disabled: Boolean, label: String },
    emits: ["update:modelValue"],
    template:
      '<input :aria-label="label" :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}));

const getCaptionSettings = vi.fn();
const setCaptionSettings = vi.fn();
vi.mock("../../api/serverConfig", () => ({
  getCaptionSettings: (...a) => getCaptionSettings(...a),
  setCaptionSettings: (...a) => setCaptionSettings(...a),
}));

import LibraryCaptionsDialog from "./LibraryCaptionsDialog.vue";

const STORED = {
  sync_tags: true,
  sync_descriptions: false,
  tags_suffix: ".txt",
  description_suffix: null,
  default_tags_suffix: "_tags.txt",
  default_description_suffix: "_description.txt",
};

const AppDialogStub = {
  props: ["open", "title"],
  template:
    "<div v-if='open'><h2>{{ title }}</h2><slot /><footer><slot name='footer' /></footer></div>",
};
const AppButtonStub = {
  props: ["disabled", "loading"],
  emits: ["click"],
  template:
    '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
};

function mountDialog({ canManage = true } = {}) {
  const store = useLibrariesStore();
  store.canManage = canManage;
  store.hasLoadedSuccessfully = true;
  return mount(LibraryCaptionsDialog, {
    props: { open: true },
    global: { stubs: { AppDialog: AppDialogStub, AppButton: AppButtonStub } },
  });
}

const saveButton = (w) => w.findAll("button").find((b) => b.text() === "Save");

describe("LibraryCaptionsDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getCaptionSettings.mockReset().mockResolvedValue(STORED);
    setCaptionSettings.mockReset().mockResolvedValue(STORED);
  });

  it("loads the stored settings into the form", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const boxes = wrapper.findAll("input[type=checkbox]");
    expect(boxes.map((b) => b.element.checked)).toEqual([true, false]);
    expect(wrapper.find("input[aria-label='Suffix for new tags files']").element.value).toBe(".txt");
    expect(wrapper.find("input[aria-label='Suffix for new description files']").exists()).toBe(false);
    expect(wrapper.text()).toContain("image.txt");
    expect(saveButton(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("saves all four fields and closes", async () => {
    const wrapper = mountDialog();
    await flushPromises();
    await wrapper.findAll("input[type=checkbox]")[1].setValue(true);
    await wrapper.find("input[aria-label='Suffix for new description files']").setValue(" _caption.txt ");
    await saveButton(wrapper).trigger("click");
    await flushPromises();

    expect(setCaptionSettings).toHaveBeenCalledWith({
      syncTags: true,
      syncDescriptions: true,
      tagsSuffix: ".txt",
      descriptionSuffix: "_caption.txt",
    });
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("shows a refused save and stays open", async () => {
    setCaptionSettings.mockRejectedValue({
      response: { status: 400, data: { detail: "Tags and descriptions cannot share a suffix" } },
    });
    const wrapper = mountDialog();
    await flushPromises();
    await saveButton(wrapper).trigger("click");
    await flushPromises();

    expect(wrapper.find("[role=alert]").text()).toContain("cannot share a suffix");
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("shows the locality sentence and no Save to a remote owner", async () => {
    getCaptionSettings.mockRejectedValue({ response: { status: 403 } });
    const wrapper = mountDialog();
    await flushPromises();

    expect(wrapper.text()).toContain("only be set up on the machine running PixlStash");
    expect(saveButton(wrapper)).toBeUndefined();
    expect(wrapper.find("input[type=checkbox]").exists()).toBe(false);
  });
});
