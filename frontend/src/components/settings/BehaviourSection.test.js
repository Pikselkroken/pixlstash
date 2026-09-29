import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", () => ({
  VCard: { template: "<div><slot /></div>" },
  VCardActions: { template: "<div><slot /></div>" },
  VCardText: { template: "<div><slot /></div>" },
  VCardTitle: { template: "<div><slot /></div>" },
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template:
      '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i><slot /></i>" },
  VMenu: { template: "<div><slot name='activator' :props='{}' /></div>" },
  VTooltip: {
    template: '<slot name="activator" :props="{}" :is-active="false" />',
  },
  VSlider: { template: "<input />" },
  VSpacer: { template: "<span />" },
  VSwitch: { template: "<input />" },
}));

vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn().mockResolvedValue({}),
  patchUserConfig: vi.fn(),
}));
vi.mock("../../api/workers", () => ({ getWorkerProgress: vi.fn().mockResolvedValue({}) }));
vi.mock("../../api/taggers", () => ({
  listTaggers: vi.fn().mockResolvedValue({ plugins: [], settings: {} }),
  getLabelThresholds: vi.fn().mockResolvedValue([]),
  listTaggerPluginDiagnostics: vi.fn().mockResolvedValue({
    plugin_dirs: { user: "/home/me/.pixlstash/plugins" },
    load_errors: [],
    cli_hint: "pixlstash plugins install <name-or-path>",
    cli_available_hint: "pixlstash plugins available",
    cli_search_hint: "pixlstash plugins available <search-term>",
    cli_list_hint: "pixlstash plugins list",
  }),
}));

import BehaviourSection from "./BehaviourSection.vue";

function mountPane(stubOverrides = {}) {
  return mount(BehaviourSection, {
    props: { open: true },
    global: {
      stubs: {
        "v-dialog": {
          props: ["modelValue"],
          template: '<div v-if="modelValue"><slot /></div>',
        },
        "v-card": { template: "<div><slot /></div>" },
        "v-card-title": { template: "<div><slot /></div>" },
        "v-card-text": { template: "<div><slot /></div>" },
        "v-card-actions": { template: "<div><slot /></div>" },
        "v-spacer": { template: "<span />" },
        // Both slots, as the real one: the tagger panel's Save sits in the
        // heading's action slot.
        SettingsSection: {
          template: '<section><slot name="action" /><slot /></section>',
        },
        SettingsTwoCol: { template: "<div><slot /></div>" },
        SettingsFieldBlock: { template: "<div><slot /></div>" },
        PluginSelect: true,
        "v-tooltip": { template: "<div><slot /></div>" },
        ...stubOverrides,
      },
    },
  });
}

beforeEach(() => vi.clearAllMocks());

describe("BehaviourSection plugin installation help", () => {
  it("opens with the deployment-aware CLI hint and manual path, then closes", async () => {
    const wrapper = mountPane();
    await nextTick();
    await nextTick();

    const button = wrapper.get("button");
    expect(button.text()).toContain("How to install plugins");
    await button.trigger("click");
    expect(wrapper.get(".plugin-catalogue-link").attributes("href")).toBe(
      "https://github.com/Pikselkroken/PixlStash-plugins",
    );
    expect(wrapper.text()).toContain("pixlstash plugins available");
    expect(wrapper.text()).toContain(
      "pixlstash plugins available <search-term>",
    );
    expect(wrapper.text()).toContain("pixlstash plugins list");
    expect(wrapper.text()).toContain("pixlstash plugins install <name-or-path>");
    expect(wrapper.text()).toContain("/home/me/.pixlstash/plugins");

    const close = wrapper.findAll("button").find((b) => b.text() === "Close");
    await close.trigger("click");
    await nextTick();
    expect(wrapper.text()).not.toContain("pixlstash plugins install <name-or-path>");
  });
});

describe("BehaviourSection keeps the plugin pickers in sync", () => {
  beforeEach(() => vi.clearAllMocks());

  // Both pickers bind `v-model:settings`. A hand-written handler here once did
  // `taggerSettings.value = s`, which in a <script setup> template sets a
  // property called "value" ON the settings object (refs are auto-unwrapped),
  // so the parent's copy never moved.
  //
  // Asserted at the prop boundary rather than through a rendered select:
  // PluginSelect is stubbed in this suite, and what is under test is the
  // parent's binding, not the child's markup.
  it("passes an updated settings object back down to the pickers", async () => {
    const { listTaggers } = await import("../../api/taggers");
    listTaggers.mockResolvedValue({
      plugins: [{ name: "wd14", display_name: "WD14", supports_tags: true }],
      settings: { active_tag_plugin: "wd14" },
    });

    const wrapper = mountPane();
    await nextTick();
    await nextTick();
    await nextTick();

    const pickers = wrapper.findAllComponents({ name: "PluginSelect" });
    expect(pickers.length).toBeGreaterThan(0);
    expect(pickers[0].props("settings")).toEqual({ active_tag_plugin: "wd14" });

    // What PluginSelect emits when the active plugin is deselected.
    pickers[0].vm.$emit("update:settings", { active_tag_plugin: null });
    await nextTick();

    const after = wrapper.findAllComponents({ name: "PluginSelect" })[0];
    expect(after.props("settings")).toEqual({ active_tag_plugin: null });
  });

  // The description picker has its own binding, and the bug was once in both.
  // Breaking only that one would leave the tag test green, so it needs its own
  // assertion rather than riding on the tag picker's.
  it("does the same for the description picker's handler", async () => {
    const { listTaggers } = await import("../../api/taggers");
    listTaggers.mockResolvedValue({
      plugins: [
        {
          name: "florence2",
          display_name: "Florence-2",
          supports_descriptions: true,
        },
      ],
      settings: { active_description_plugin: "florence2" },
    });

    const wrapper = mountPane();
    await nextTick();
    await nextTick();
    await nextTick();

    const pickers = wrapper.findAllComponents({ name: "PluginSelect" });
    expect(pickers.length).toBeGreaterThan(1);

    pickers[1].vm.$emit("update:settings", { active_description_plugin: null });
    await nextTick();

    const after = wrapper.findAllComponents({ name: "PluginSelect" })[1];
    expect(after.props("settings")).toEqual({ active_description_plugin: null });
  });
});

describe("a plugin's saved parameters survive reopening its dialog", () => {
  beforeEach(() => vi.clearAllMocks());

  const PLUGIN = {
    name: "wd14",
    display_name: "WD14",
    supports_tags: true,
    parameter_schema: [
      { name: "threshold", label: "Threshold", type: "number", min: 0, max: 1, step: 0.01, default: 0.35 },
    ],
  };

  async function openPane() {
    const { listTaggers } = await import("../../api/taggers");
    listTaggers.mockResolvedValue({
      plugins: [PLUGIN],
      settings: { active_tag_plugin: "wd14", plugins: { wd14: { params: { threshold: 0.35 } } } },
    });
    const w = mountPane({ PluginSelect: false });
    await flushPromises();
    await nextTick();
    return w;
  }

  const gear = (w) =>
    w.get(".ps-row .app-btn");
  const form = (w) => w.findComponent({ name: "TaggerParametersUI" });

  it("shows the value you saved, not the one it opened with", async () => {
    const { patchUserConfig } = await import("../../api/config");
    patchUserConfig.mockResolvedValue({});
    const w = await openPane();

    await gear(w).trigger("click");
    await nextTick();
    expect(form(w).props("modelValue")).toEqual({ threshold: 0.35 });

    // Edit and save.
    form(w).vm.$emit("update:modelValue", { threshold: 0.9 });
    await nextTick();
    const save = w.findAll("button").find((b) => b.text() === "Save");
    await save.trigger("click");
    await flushPromises();
    await nextTick();

    // Reopen. Without the parent applying update:settings this reseeds from the
    // stale prop and the 0.9 is gone.
    await gear(w).trigger("click");
    await nextTick();
    expect(form(w).props("modelValue")).toEqual({ threshold: 0.9 });
  });

  it("does not write the old value back when you save again", async () => {
    const { patchUserConfig } = await import("../../api/config");
    patchUserConfig.mockResolvedValue({});
    const w = await openPane();

    await gear(w).trigger("click");
    await nextTick();
    form(w).vm.$emit("update:modelValue", { threshold: 0.9 });
    await nextTick();
    let save = w.findAll("button").find((b) => b.text() === "Save");
    await save.trigger("click");
    await flushPromises();
    await nextTick();

    // Reopen and save again, touching nothing. The server must not be told 0.35.
    await gear(w).trigger("click");
    await nextTick();
    save = w.findAll("button").find((b) => b.text() === "Save");
    await save.trigger("click");
    await flushPromises();

    const params = patchUserConfig.mock.calls
      .map((c) => c[0]?.tagger_settings?.plugins?.wd14?.params?.threshold)
      .filter((v) => v !== undefined);
    expect(params).toEqual([0.9, 0.9]);
  });
});


describe("the PixlStash tagger's settings sit in the pane", () => {
  beforeEach(() => vi.clearAllMocks());

  const TAGGER = {
    name: "pixlstash_tagger",
    display_name: "PixlStash Tagger",
    supports_tags: true,
    parameter_schema: [
      { name: "threshold_offset", label: "Threshold offset", type: "number", min: -0.5, max: 0.5, step: 0.01, default: 0 },
    ],
  };
  const WD14 = { name: "wd14", display_name: "WD14", supports_tags: true, parameter_schema: [] };

  async function openPane(active) {
    const { listTaggers } = await import("../../api/taggers");
    listTaggers.mockResolvedValue({
      plugins: [TAGGER, WD14],
      settings: {
        active_tag_plugin: active,
        plugins: { pixlstash_tagger: { params: { threshold_offset: 0.1 } } },
      },
    });
    const w = mountPane();
    await flushPromises();
    await nextTick();
    return w;
  }

  const panel = (w) => w.findComponent({ name: "TaggerPluginSettingsPanel" });
  const form = (w) => w.findComponent({ name: "TaggerParametersUI" });

  it("shows while it is the active tag plugin, seeded with its saved params", async () => {
    const w = await openPane("pixlstash_tagger");
    expect(panel(w).exists()).toBe(true);
    expect(form(w).props("modelValue")).toEqual({ threshold_offset: 0.1 });
    // Only the tag picker: the PixlStash tagger does not caption.
    const pickers = w.findAllComponents({ name: "PluginSelect" });
    expect(pickers.map((p) => p.props("inlineSettingsFor"))).toEqual([
      "pixlstash_tagger",
      "",
    ]);
  });

  it("is absent while another plugin, or None, is chosen", async () => {
    expect(panel(await openPane("wd14")).exists()).toBe(false);
    expect(panel(await openPane(null)).exists()).toBe(false);
  });

  it("saves the edited params and hands them to the pickers", async () => {
    const { patchUserConfig } = await import("../../api/config");
    patchUserConfig.mockResolvedValue({});
    const w = await openPane("pixlstash_tagger");

    form(w).vm.$emit("update:modelValue", { threshold_offset: -0.2 });
    await nextTick();
    const save = w.findAll("button").find((b) => b.text() === "Save");
    await save.trigger("click");
    await flushPromises();

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: {
        plugins: { pixlstash_tagger: { params: { threshold_offset: -0.2 } } },
      },
    });
    const picker = w.findAllComponents({ name: "PluginSelect" })[0];
    expect(
      picker.props("settings").plugins.pixlstash_tagger.params,
    ).toEqual({ threshold_offset: -0.2 });
    expect(w.text()).toContain("Saved.");
  });

  it("says the form holds unsaved edits, and alerts rather than claiming Saved on failure", async () => {
    const { patchUserConfig } = await import("../../api/config");
    patchUserConfig.mockRejectedValueOnce(new Error("boom"));
    const w = await openPane("pixlstash_tagger");
    const save = () => w.findAll("button").find((b) => b.text() === "Save");
    expect(save().attributes("disabled")).toBeDefined();

    form(w).vm.$emit("update:modelValue", { threshold_offset: -0.2 });
    await nextTick();
    expect(w.text()).toContain("Unsaved changes");
    await save().trigger("click");
    await flushPromises();

    expect(w.find('[role="alert"]').exists()).toBe(true);
    expect(w.text()).not.toContain("Saved.");
    expect(w.text()).toContain("Unsaved changes");
  });

  // The pane polls is_loaded every few seconds and replaces each plugin object.
  // Reseeding the form on that would silently discard what the user typed.
  it("keeps an unsaved edit when the loaded-state poll replaces the plugin", async () => {
    const w = await openPane("pixlstash_tagger");
    form(w).vm.$emit("update:modelValue", { threshold_offset: -0.2 });
    await nextTick();

    w.vm.taggerPlugins = w.vm.taggerPlugins.map((p) => ({ ...p, is_loaded: true }));
    await nextTick();

    expect(form(w).props("modelValue")).toEqual({ threshold_offset: -0.2 });
  });

  it("keeps an unsaved edit when the settings object is replaced by an equal copy", async () => {
    const w = await openPane("pixlstash_tagger");
    form(w).vm.$emit("update:modelValue", { threshold_offset: -0.2 });
    await nextTick();

    w.vm.taggerSettings = JSON.parse(JSON.stringify(w.vm.taggerSettings));
    w.vm.taggerPlugins = JSON.parse(JSON.stringify(w.vm.taggerPlugins));
    await nextTick();

    expect(form(w).props("modelValue")).toEqual({ threshold_offset: -0.2 });
  });

  it("does reseed when the saved params really change", async () => {
    const w = await openPane("pixlstash_tagger");
    w.vm.taggerSettings = {
      ...w.vm.taggerSettings,
      plugins: { pixlstash_tagger: { params: { threshold_offset: 0.3 } } },
    };
    await nextTick();

    expect(form(w).props("modelValue")).toEqual({ threshold_offset: 0.3 });
  });
});
