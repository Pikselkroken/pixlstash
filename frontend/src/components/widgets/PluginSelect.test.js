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
  // Activator always, content only while open, and afterLeave once closed, as
  // the real menu does.
  VMenu: {
    name: "VMenu",
    props: ["modelValue"],
    emits: ["update:modelValue", "afterLeave"],
    watch: {
      modelValue(open) {
        if (!open) this.$emit("afterLeave");
      },
    },
    template: `<div>
      <slot name="activator" :props="{ onClick: () => $emit('update:modelValue', !modelValue) }" />
      <div v-if="modelValue"><slot /></div>
    </div>`,
  },
  // Tooltip.vue wraps VTooltip: render the activator only, as a closed tip does.
  VTooltip: {
    name: "VTooltip",
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

vi.mock("../../api/config", () => ({
  patchUserConfig: vi.fn().mockResolvedValue({}),
}));

import { patchUserConfig } from "../../api/config";
import PluginSelect from "./PluginSelect.vue";
import Tooltip from "./Tooltip.vue";

const PLUGINS = [
  {
    name: "wd14",
    display_name: "WD14",
    is_loaded: true,
    description: "Danbooru-style tags",
  },
  { name: "pixlstash_tagger", display_name: "PixlStash", is_loaded: false },
];

/**
 * Mount the picker for one capability.
 *
 * The fixtures carry whichever `supports_*` flag *kind* filters on, because a
 * plugin that does not declare it is filtered out and the picker renders its
 * empty state instead -- including, deliberately, no None row: there is
 * nothing to turn off.
 */
function mountPicker(activePlugin, kind = "tag", plugins = PLUGINS, opts = {}) {
  const flag = kind === "tag" ? "supports_tags" : "supports_descriptions";
  return mount(PluginSelect, {
    props: {
      plugins: plugins.map((p) => ({ ...p, [flag]: true })),
      kind,
      settings: { [`active_${kind}_plugin`]: activePlugin },
    },
    global: { stubs: { TaggerPluginSettingsDialog: true } },
    ...opts,
  });
}

const trigger = (w) => w.get(".ps-trigger");
const gear = (w) => w.get(".ps-row .app-btn");
const menuRows = (w) => w.findAll('[role="menuitemradio"]');

async function openMenu(w) {
  await trigger(w).trigger("click");
}

/** Open the menu and click the row whose label contains *text*. */
async function choose(w, text) {
  await openMenu(w);
  const row = menuRows(w).find((r) => r.text().includes(text));
  await row.trigger("click");
  await flushPromises();
}

describe("PluginSelect field", () => {
  it("is a menu button, not a native select or radios", () => {
    const w = mountPicker("wd14");
    expect(w.find("select").exists()).toBe(false);
    expect(w.findAll('input[type="radio"]')).toHaveLength(0);
    expect(trigger(w).text()).toContain("WD14");
  });

  it("shows the chosen plugin with a loaded icon, and names it for a reader", () => {
    const w = mountPicker("wd14");
    expect(trigger(w).get(".ps-loaded").text()).toBe("mdi-check-circle");
    expect(trigger(w).get(".ps-loaded").classes()).toContain("ps-loaded--on");
    expect(trigger(w).attributes("aria-label")).toBe(
      "Tag plugin: WD14, loaded",
    );
  });

  it("shows an empty-circle icon for a plugin that is not loaded", () => {
    const w = mountPicker("pixlstash_tagger");
    expect(trigger(w).get(".ps-loaded").text()).toBe("mdi-circle-outline");
    expect(trigger(w).get(".ps-loaded").classes()).not.toContain(
      "ps-loaded--on",
    );
  });

  it("shows None with no icon when no plugin is active", () => {
    const w = mountPicker(null);
    expect(trigger(w).text()).toContain("None — no automatic tagging");
    expect(trigger(w).find(".ps-loaded").exists()).toBe(false);
  });

  it("names a saved plugin that is no longer installed instead of going blank", () => {
    const w = mountPicker("gone");
    expect(trigger(w).text()).toContain("gone (not installed)");
  });

  it("names the capability, so the two pickers do not read identically", () => {
    expect(mountPicker(null, "description").text()).toMatch(
      /no automatic captioning/i,
    );
  });

  it("falls back to the plugin's name when it sets no display name", () => {
    const w = mountPicker("bare", "tag", [
      { name: "bare", display_name: "", is_loaded: true },
    ]);
    expect(trigger(w).text()).toContain("bare");
    expect(gear(w).attributes("aria-label")).toBe("bare settings");
  });
});

describe("PluginSelect menu", () => {
  it("lists None first, then every capable plugin, and marks the chosen one", async () => {
    const w = mountPicker("pixlstash_tagger");
    await openMenu(w);
    const rows = menuRows(w);
    expect(rows.map((r) => r.get(".ctx-label-text").text())).toEqual([
      "None — no automatic tagging",
      "WD14",
      "PixlStash",
    ]);
    expect(rows.map((r) => r.attributes("aria-checked"))).toEqual([
      "false",
      "false",
      "true",
    ]);
    expect(rows[2].find(".ctx-check").exists()).toBe(true);
    expect(rows[1].find(".ctx-check").exists()).toBe(false);
  });

  it("shows each plugin's loaded state as an icon, None with none", async () => {
    const w = mountPicker(null);
    await openMenu(w);
    const icons = menuRows(w).map((r) => r.find(".ps-loaded"));
    expect(icons[0].exists()).toBe(false);
    expect(icons[1].text()).toBe("mdi-check-circle");
    expect(icons[1].attributes("aria-label")).toBe("Loaded");
    expect(icons[2].text()).toBe("mdi-circle-outline");
    expect(icons[2].attributes("aria-label")).toBe("Not loaded");
  });

  it("gives every plugin row a tooltip with its description and state", async () => {
    const w = mountPicker(null);
    await openMenu(w);
    const tips = menuRows(w).map((r) =>
      r.findAllComponents(Tooltip).map((t) => t.props("text")),
    );
    expect(tips).toEqual([
      [],
      ["Danbooru-style tags Loaded."],
      ["Not loaded."],
    ]);
  });

  it("gives the field the chosen plugin's tooltip", () => {
    const tip = trigger(mountPicker("wd14")).findComponent(Tooltip);
    expect(tip.props("text")).toBe("Danbooru-style tags Loaded.");
  });
});

describe("PluginSelect choosing", () => {
  beforeEach(() => vi.clearAllMocks());

  it("selects a plugin that was not active", async () => {
    const w = mountPicker(null);
    await choose(w, "WD14");

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "wd14" },
    });
    expect(w.emitted("update:settings")[0][0].active_tag_plugin).toBe("wd14");
  });

  it("switches between plugins and closes the menu", async () => {
    const w = mountPicker("wd14");
    await choose(w, "PixlStash");

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "pixlstash_tagger" },
    });
    expect(menuRows(w)).toHaveLength(0);
  });

  it("writes null when None is chosen", async () => {
    const w = mountPicker("wd14");
    await choose(w, "None");

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: null },
    });
    expect(w.emitted("update:settings")[0][0].active_tag_plugin).toBeNull();
  });

  it("does not save again when the chosen plugin is picked", async () => {
    const w = mountPicker("wd14");
    await choose(w, "WD14");
    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("keeps showing the saved plugin and alerts when the save fails", async () => {
    patchUserConfig.mockRejectedValueOnce(new Error("boom"));
    const w = mountPicker("wd14");
    await choose(w, "PixlStash");

    expect(w.emitted("update:settings")).toBeUndefined();
    expect(trigger(w).text()).toContain("WD14");
    expect(w.get(".ps-error").attributes("role")).toBe("alert");
  });

  it("puts keyboard focus back on the field once the menu closes", async () => {
    const w = mountPicker("wd14", "tag", PLUGINS, {
      attachTo: document.body,
    });
    await choose(w, "PixlStash");
    expect(document.activeElement).toBe(trigger(w).element);
    w.unmount();
  });
});

describe("PluginSelect settings gear", () => {
  beforeEach(() => vi.clearAllMocks());

  it("configures the chosen plugin", async () => {
    const w = mountPicker("wd14");
    expect(gear(w).attributes("aria-disabled")).toBeUndefined();
    await gear(w).trigger("click");
    const dialog = w.findComponent({ name: "TaggerPluginSettingsDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("plugin").name).toBe("wd14");
  });

  it("refuses the press in its handler, not only through the dialog's v-if", async () => {
    const w = mountPicker(null);
    await gear(w).trigger("click");
    expect(w.vm.dialogOpen).toBe(false);
  });

  it("is aria-disabled while None is chosen, with a reachable reason", async () => {
    const w = mountPicker(null);
    expect(gear(w).attributes("aria-disabled")).toBe("true");
    expect(gear(w).attributes("aria-label")).toBe(
      "Choose a plugin to configure it",
    );
    await gear(w).trigger("click");
    expect(
      w.findComponent({ name: "TaggerPluginSettingsDialog" }).exists(),
    ).toBe(false);
  });

  it("says a no-longer-installed plugin is not installed", () => {
    const w = mountPicker("gone");
    expect(gear(w).attributes("aria-disabled")).toBe("true");
    expect(gear(w).attributes("aria-label")).toBe("gone is not installed");
  });
});

describe("PluginSelect with no capable plugins", () => {
  it("shows the empty state rather than a lone None row", () => {
    const w = mountPicker(null, "tag", []);
    expect(w.find(".ps-trigger").exists()).toBe(false);
    expect(w.text()).toContain("No tag plugins registered.");
  });

  it("still names a plugin that is saved but gone", () => {
    const w = mountPicker("gone", "tag", []);
    expect(w.get(".ps-empty").text()).toContain("gone is still selected");
  });
});
