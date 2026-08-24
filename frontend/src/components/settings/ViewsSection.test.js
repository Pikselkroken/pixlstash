import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { useLibrariesStore } from "../../stores/useLibrariesStore";

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

/**
 * Views sits on the same locality tier as the library controls beside it, so it
 * reads the pane's shared `can_manage` rather than discovering the same fact
 * from a failed request.
 */
function setLocality({ canManage = true, loaded = true } = {}) {
  const store = useLibrariesStore();
  store.canManage = canManage;
  store.hasLoadedSuccessfully = loaded;
  return store;
}

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
    setActivePinia(createPinia());
    setLocality();
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

  it("says so from the shared can_manage, without a request that would fail", async () => {
    // The registry already answered the locality question for this whole pane.
    // Asking the views route as well would only fail the same way, more slowly.
    setLocality({ canManage: false });
    getViewsSettings.mockResolvedValue(OFF);

    const wrapper = mountPane();
    await flushPromises();

    expect(getViewsSettings).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("only available on the machine running");
    expect(wrapper.findAll("button")).toHaveLength(0);
  });

  it("still says so if can_manage said yes and the route refused anyway", async () => {
    getViewsSettings.mockRejectedValue({ response: { status: 403 } });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("only available on the machine running");
    expect(wrapper.findAll("button")).toHaveLength(0);
  });

  it("recovers when a session that was once refused is allowed again", async () => {
    // Set only on failure and never cleared, `refused` left a pane that had
    // seen one 403 showing the locality notice for the rest of the session.
    getViewsSettings.mockRejectedValueOnce({ response: { status: 403 } });

    const wrapper = mountPane();
    await flushPromises();
    expect(wrapper.text()).toContain("only available on the machine running");

    getViewsSettings.mockResolvedValue(ON);
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await flushPromises();

    expect(wrapper.text()).not.toContain("only available on the machine running");
    expect(wrapper.text()).toContain("_PixlStash Views");
  });

  it("reads the settings once can_manage arrives after the pane opened", async () => {
    // The registry read is in flight when Settings opens, so a pane that only
    // watched `open` would stay blank for a local owner.
    setLocality({ canManage: false, loaded: false });
    getViewsSettings.mockResolvedValue(ON);

    const wrapper = mountPane();
    await flushPromises();
    const store = setLocality({ canManage: true });
    await flushPromises();

    expect(getViewsSettings).toHaveBeenCalled();
    expect(store.canManage).toBe(true);
    expect(wrapper.text()).toContain("_PixlStash Views");
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
