import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
}));

vi.mock("../../api/config", () => ({
  patchUserConfig: vi.fn().mockResolvedValue({}),
}));

import { patchUserConfig } from "../../api/config";
import PluginsTable from "./PluginsTable.vue";

const PLUGINS = [
  { name: "wd14", display_name: "WD14", supports_tags: true },
  { name: "pixlstash_tagger", display_name: "PixlStash", supports_tags: true },
];

/**
 * Mount the table for one capability.
 *
 * The fixtures carry whichever `supports_*` flag *kind* filters on, because a
 * plugin that does not declare it is filtered out and the table renders its
 * empty state instead -- including, deliberately, no None row: there is nothing
 * to turn off.
 */
function mountTable(activePlugin, kind = "tag") {
  const flag = kind === "tag" ? "supports_tags" : "supports_descriptions";
  return mount(PluginsTable, {
    props: {
      plugins: PLUGINS.map((p) => ({ ...p, [flag]: true })),
      kind,
      settings: { [`active_${kind}_plugin`]: activePlugin },
    },
    global: {
      stubs: { TaggerPluginSettingsDialog: true, "v-btn": true, "v-tooltip": true },
    },
  });
}

const radios = (w) => w.findAll('input[type="radio"]');
/** The None radio is the first row, ahead of every plugin. */
const noneRadio = (w) => radios(w)[0];
/** Plugin radios, in `plugins` order, with None excluded. */
const pluginRadios = (w) => radios(w).slice(1);

describe("PluginsTable active-plugin radio", () => {
  beforeEach(() => vi.clearAllMocks());

  it("selects a plugin that was not active", async () => {
    const w = mountTable(null);
    await pluginRadios(w)[0].trigger("change");
    await flushPromises();

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "wd14" },
    });
    expect(w.emitted("update:settings")[0][0].active_tag_plugin).toBe("wd14");
  });

  it("switches between plugins", async () => {
    const w = mountTable("wd14");
    await pluginRadios(w)[1].trigger("change");
    await flushPromises();

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "pixlstash_tagger" },
    });
  });
});


describe("PluginsTable None row", () => {
  beforeEach(() => vi.clearAllMocks());

  it("puts None ahead of every plugin so the off state is visible", () => {
    const w = mountTable("wd14");
    expect(radios(w)).toHaveLength(PLUGINS.length + 1);
    expect(w.text()).toContain("None");
  });

  it("is the checked row when no plugin is active", () => {
    const w = mountTable(null);
    expect(noneRadio(w).element.checked).toBe(true);
    expect(pluginRadios(w).some((r) => r.element.checked)).toBe(false);
  });

  it("is not checked while a plugin is active", () => {
    const w = mountTable("wd14");
    expect(noneRadio(w).element.checked).toBe(false);
    expect(pluginRadios(w)[0].element.checked).toBe(true);
  });

  it("clears the active plugin when chosen", async () => {
    const w = mountTable("wd14");
    await noneRadio(w).trigger("change");
    await flushPromises();

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: null },
    });
    expect(w.emitted("update:settings")[0][0].active_tag_plugin).toBeNull();
  });

  it("names the capability, so the two tables do not read identically", () => {
    expect(mountTable(null, "description").text()).toMatch(/no automatic/i);
  });
});


describe("PluginsTable re-activation is inert", () => {
  beforeEach(() => vi.clearAllMocks());

  it("does nothing when the already-active plugin is clicked again", async () => {
    const w = mountTable("wd14");
    await pluginRadios(w)[0].trigger("click");
    await flushPromises();
    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("does not clear the selection on a double click", async () => {
    const w = mountTable(null);
    const radio = pluginRadios(w)[0];
    await radio.trigger("click");
    await radio.trigger("change");
    await radio.trigger("click");
    await flushPromises();

    expect(patchUserConfig).toHaveBeenCalledTimes(1);
    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "wd14" },
    });
  });
});


describe("PluginsTable with no capable plugins", () => {
  it("shows the empty state rather than a lone None row", () => {
    const w = mount(PluginsTable, {
      props: { plugins: [], kind: "tag", settings: {} },
      global: {
        stubs: {
          TaggerPluginSettingsDialog: true,
          "v-btn": true,
          "v-tooltip": true,
        },
      },
    });
    expect(w.findAll('input[type="radio"]')).toHaveLength(0);
    expect(w.text()).toContain("No tag plugins registered.");
  });
});
