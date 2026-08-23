import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
  VSwitch: {
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template:
      '<input type="checkbox" :data-kind="label" :checked="modelValue" ' +
      '@change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
}));

const getViewsSettings = vi.fn();
const setViewsSettings = vi.fn();
vi.mock("../../api/serverConfig", () => ({
  getViewsSettings: (...args) => getViewsSettings(...args),
  setViewsSettings: (...args) => setViewsSettings(...args),
}));

import ViewsSection from "./ViewsSection.vue";

const OFF = {
  views_root: null,
  kinds: [],
  available_kinds: ["people", "sets", "projects"],
};
const ON = {
  views_root: "/home/me/Pictures/_PixlStash Views",
  kinds: ["people", "sets"],
  available_kinds: ["people", "sets", "projects"],
  last_publish: {
    link_mode: "symlink",
    folders: 4,
    links: 512,
    skipped_missing: 0,
    skipped_unlinkable: [],
    kept_by_owner: [],
  },
};

function mountPane() {
  return mount(ViewsSection, {
    props: { open: true },
    global: {
      stubs: {
        SettingsSection: { template: "<section><slot /></section>" },
        SettingsInfoCard: { template: "<aside><slot /></aside>" },
        SettingsRow: {
          props: ["label", "sub"],
          template: '<div><span class="sub">{{ sub }}</span><slot /></div>',
        },
        AppButton: {
          template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
          props: ["disabled", "loading", "variant", "size", "iconLeft"],
        },
        FolderBrowser: true,
      },
    },
  });
}

describe("ViewsSection", () => {
  beforeEach(() => {
    getViewsSettings.mockReset();
    setViewsSettings.mockReset();
  });

  it("says views are off, and offers no kind switches, until a folder is chosen", async () => {
    getViewsSettings.mockResolvedValue(OFF);

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("Not published");
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0);
  });

  it("switching a kind on publishes it, without waiting for a save button", async () => {
    // The pane has no dirty indicator, so a "save later" model would let a user
    // untick a kind, close the dialog, and change nothing at all.
    getViewsSettings.mockResolvedValue(ON);
    setViewsSettings.mockResolvedValue({ ...ON, kinds: ["people", "sets", "projects"] });

    const wrapper = mountPane();
    await flushPromises();
    const projects = wrapper.find('input[data-kind="Projects"]');
    expect(projects.element.checked).toBe(false);
    await projects.setValue(true);
    await flushPromises();

    expect(setViewsSettings).toHaveBeenCalledWith(
      "/home/me/Pictures/_PixlStash Views",
      ["people", "sets", "projects"],
    );
  });

  it("switching a kind off publishes without it", async () => {
    getViewsSettings.mockResolvedValue(ON);
    setViewsSettings.mockResolvedValue({ ...ON, kinds: ["people"] });

    const wrapper = mountPane();
    await flushPromises();
    await wrapper.find('input[data-kind="Sets"]').setValue(false);
    await flushPromises();

    expect(setViewsSettings).toHaveBeenCalledWith(
      "/home/me/Pictures/_PixlStash Views",
      ["people"],
    );
  });

  it("shows the refusal and keeps the folder the server still has recorded", async () => {
    // The server refuses the new folder and changes nothing, so the pane must
    // not be left displaying a root that was never accepted.
    getViewsSettings.mockResolvedValue(ON);
    setViewsSettings.mockRejectedValue({
      response: { data: { detail: "That folder is inside the library." } },
    });

    const wrapper = mountPane();
    await flushPromises();
    await wrapper.findAll("button").at(-1).trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("inside the library");
    expect(wrapper.find(".sub").text()).toBe(ON.views_root);
  });

  it("says the pane belongs on the other machine when the route is refused", async () => {
    // Views drives the host filesystem, so its routes are local-only. A remote
    // owner must get that sentence, not a raw permission error.
    getViewsSettings.mockRejectedValue({ response: { status: 403 } });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("only be set up from that machine");
    expect(wrapper.findAll("button")).toHaveLength(0);
  });

  it("names the owner's own files that the rebuild refused to delete", async () => {
    getViewsSettings.mockResolvedValue({
      ...ON,
      last_publish: {
        ...ON.last_publish,
        kept_by_owner: ["Sets/mira-lora-v3/irreplaceable.raw"],
      },
    });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("irreplaceable.raw");
    expect(wrapper.text()).toContain("never deletes");
  });

  it("names the folders that could not be linked rather than implying a whole tree", async () => {
    getViewsSettings.mockResolvedValue({
      ...ON,
      last_publish: {
        ...ON.last_publish,
        link_mode: "hardlink",
        skipped_unlinkable: ["People/Mira"],
      },
    });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("People/Mira");
    expect(wrapper.text()).toContain("incomplete");
    expect(wrapper.text()).toContain("hard links");
  });
});
