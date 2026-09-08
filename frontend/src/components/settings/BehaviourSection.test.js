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
        SettingsSection: { template: "<section><slot /></section>" },
        SettingsTwoCol: { template: "<div><slot /></div>" },
        SettingsFieldBlock: { template: "<div><slot /></div>" },
        PluginsTable: true,
        VBtn: {
          inheritAttrs: false,
          emits: ["click"],
          props: ["prependIcon"],
          template: '<button @click="$emit(\'click\')"><slot /></button>',
        },
        // inheritAttrs:false matters: without it a native click reaches the
        // parent's @click twice -- once through the emit, once through
        // attribute fallthrough -- and every click-counting assertion doubles.
        "v-btn": {
          inheritAttrs: false,
          emits: ["click"],
          props: ["prependIcon"],
          template: '<button @click="$emit(\'click\')"><slot /></button>',
        },
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

describe("BehaviourSection keeps the plugin tables in sync", () => {
  beforeEach(() => vi.clearAllMocks());

  // `taggerSettings` is a ref, and inside a <script setup> template refs are
  // auto-unwrapped — so `taggerSettings.value = s` in a template handler sets a
  // property called "value" ON the settings object instead of replacing the
  // ref's contents. The parent's copy then never moves.
  //
  // Asserted at the prop boundary rather than through a rendered radio:
  // PluginsTable is stubbed in this suite, and what is under test is the
  // parent's handler, not the child's markup.
  it("passes an updated settings object back down to the tables", async () => {
    const { listTaggers } = await import("../../api/taggers");
    listTaggers.mockResolvedValue({
      plugins: [{ name: "wd14", display_name: "WD14", supports_tags: true }],
      settings: { active_tag_plugin: "wd14" },
    });

    const wrapper = mountPane();
    await nextTick();
    await nextTick();
    await nextTick();

    const tables = wrapper.findAllComponents({ name: "PluginsTable" });
    expect(tables.length).toBeGreaterThan(0);
    expect(tables[0].props("settings")).toEqual({ active_tag_plugin: "wd14" });

    // What PluginsTable emits when the active plugin is deselected.
    tables[0].vm.$emit("update:settings", { active_tag_plugin: null });
    await nextTick();

    const after = wrapper.findAllComponents({ name: "PluginsTable" })[0];
    expect(after.props("settings")).toEqual({ active_tag_plugin: null });
  });

  // The description table has its own copy of the same handler, and the bug
  // was in both. Reverting only that one left the suite green, so it needs its
  // own assertion rather than riding on the tag table's.
  it("does the same for the description table's handler", async () => {
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

    const tables = wrapper.findAllComponents({ name: "PluginsTable" });
    expect(tables.length).toBeGreaterThan(1);

    tables[1].vm.$emit("update:settings", { active_description_plugin: null });
    await nextTick();

    const after = wrapper.findAllComponents({ name: "PluginsTable" })[1];
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
    const w = mountPane({ PluginsTable: false });
    await flushPromises();
    await nextTick();
    return w;
  }

  const gear = (w) =>
    w.findAll(".pt-col-actions button")[0];
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

