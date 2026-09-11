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
        AppDialog: {
          emits: ["close", "accept"],
          // The real AppDialog turns plain Enter on its own subtree into
          // `accept` (the dialog keyboard contract); the stub only has to let
          // a test fire it.
          template:
            "<div @keydown.enter=\"$emit('accept')\"><slot /><slot name='footer' /></div>",
        },
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
          props: ["disabled", "loading", "keyHint"],
          template:
            '<button :disabled="disabled || loading" :data-key-hint="keyHint" @click="$emit(\'click\')"><slot /></button>',
        },
      },
    },
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
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

  it("saves on plain Enter, and wears the two key hints", async () => {
    setCaptionSettings.mockResolvedValue(STORED);
    const wrapper = mountDialog();
    await flushPromises();

    expect(button(wrapper, "Cancel").attributes("data-key-hint")).toBe("esc");
    expect(button(wrapper, "Save").attributes("data-key-hint")).toBe("enter");

    await wrapper.trigger("keydown.enter");
    await flushPromises();
    expect(setCaptionSettings).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("Enter does nothing while Save is disabled", async () => {
    // Same guard as the button: a read that never landed must not be saved
    // over by a keystroke either.
    getCaptionSettings.mockRejectedValue({ response: { status: 500 } });
    const wrapper = mountDialog();
    await flushPromises();

    expect(button(wrapper, "Save").attributes("disabled")).toBeDefined();
    await wrapper.trigger("keydown.enter");
    await flushPromises();
    expect(setCaptionSettings).not.toHaveBeenCalled();
  });

  it("cannot save over settings it never read", async () => {
    // A failed GET leaves both toggles false and both suffixes "", which is a
    // body the PATCH would store: one click after a transient 500 turned sync
    // off and cleared the suffixes the owner had chosen.
    getCaptionSettings.mockRejectedValue({
      response: { status: 500, data: { detail: "Database is locked" } },
    });
    const wrapper = mountDialog();
    await flushPromises();

    expect(wrapper.text()).toContain("Database is locked");
    const save = button(wrapper, "Save");
    expect(save.attributes("disabled")).toBeDefined();
    await save.trigger("click");
    await flushPromises();
    expect(setCaptionSettings).not.toHaveBeenCalled();
  });

  it("ignores a read that lands after a newer one", async () => {
    // Close and reopen before the first GET answers and the two are in flight
    // together. The older response landing last used to overwrite the form and
    // clear `loading`, so Save was enabled on settings the owner had moved on
    // from.
    const first = deferred();
    const second = deferred();
    getCaptionSettings
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const wrapper = mountDialog();
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    expect(getCaptionSettings).toHaveBeenCalledTimes(2);

    second.resolve({ ...STORED, description_suffix: "_second.txt" });
    await flushPromises();
    first.resolve({ ...STORED, description_suffix: "_first.txt" });
    await flushPromises();

    const field = wrapper.find("input[aria-label='Suffix for new description files']");
    expect(field.element.value).toBe("_second.txt");
    expect(button(wrapper, "Save").attributes("disabled")).toBeUndefined();
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
