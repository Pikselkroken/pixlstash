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
 * empty state instead -- including, deliberately, no None option: there is
 * nothing to turn off.
 */
function mountPicker(activePlugin, kind = "tag", plugins = PLUGINS) {
  const flag = kind === "tag" ? "supports_tags" : "supports_descriptions";
  return mount(PluginSelect, {
    props: {
      plugins: plugins.map((p) => ({ ...p, [flag]: true })),
      kind,
      settings: { [`active_${kind}_plugin`]: activePlugin },
    },
    global: {
      stubs: { TaggerPluginSettingsDialog: true, "v-tooltip": true },
    },
  });
}

const select = (w) => w.get("select");
const optionValues = (w) => w.findAll("option").map((o) => o.element.value);
const gear = (w) => w.get(".ps-row button");

async function choose(w, value) {
  await select(w).setValue(value);
  await flushPromises();
}

describe("PluginSelect options", () => {
  it("fall back to the plugin's name when it sets no display name", () => {
    const w = mountPicker("bare", "tag", [
      { name: "bare", display_name: "", is_loaded: true },
    ]);
    expect(w.findAll("option")[1].text()).toBe("bare");
    expect(gear(w).attributes("aria-label")).toBe("bare settings");
  });

  it("mark a plugin that is not loaded, so readiness shows without choosing", () => {
    const labels = mountPicker(null)
      .findAll("option")
      .map((o) => o.text());
    expect(labels[1]).toBe("WD14");
    expect(labels[2]).toBe("PixlStash — not loaded");
  });

  beforeEach(() => vi.clearAllMocks());

  it("is one dropdown, None first, then every capable plugin", () => {
    const w = mountPicker("wd14");
    expect(w.findAll('input[type="radio"]')).toHaveLength(0);
    expect(optionValues(w)).toEqual(["", "wd14", "pixlstash_tagger"]);
    expect(w.findAll("option")[0].text()).toContain("None");
  });

  it("shows the active plugin as the selected option", () => {
    expect(select(mountPicker("pixlstash_tagger")).element.value).toBe(
      "pixlstash_tagger",
    );
  });

  it("shows None when no plugin is active", () => {
    expect(select(mountPicker(null)).element.value).toBe("");
  });

  it("names a saved plugin that is no longer installed instead of going blank", () => {
    const w = mountPicker("gone");
    expect(select(w).element.value).toBe("gone");
    expect(w.findAll("option").at(-1).text()).toContain("not installed");
  });

  it("names the capability, so the two pickers do not read identically", () => {
    expect(mountPicker(null, "description").text()).toMatch(
      /no automatic captioning/i,
    );
  });
});

describe("PluginSelect choosing", () => {
  beforeEach(() => vi.clearAllMocks());

  it("selects a plugin that was not active", async () => {
    const w = mountPicker(null);
    await choose(w, "wd14");

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "wd14" },
    });
    expect(w.emitted("update:settings")[0][0].active_tag_plugin).toBe("wd14");
  });

  it("switches between plugins", async () => {
    const w = mountPicker("wd14");
    await choose(w, "pixlstash_tagger");

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: "pixlstash_tagger" },
    });
  });

  it("writes null, not an empty string, when None is chosen", async () => {
    const w = mountPicker("wd14");
    await choose(w, "");

    expect(patchUserConfig).toHaveBeenCalledWith({
      tagger_settings: { active_tag_plugin: null },
    });
    expect(w.emitted("update:settings")[0][0].active_tag_plugin).toBeNull();
  });

  it("puts the dropdown back when the save fails", async () => {
    patchUserConfig.mockRejectedValueOnce(new Error("boom"));
    const w = mountPicker("wd14");
    await choose(w, "pixlstash_tagger");

    expect(w.emitted("update:settings")).toBeUndefined();
    expect(select(w).element.value).toBe("wd14");
    expect(w.get(".ps-error").text()).toBeTruthy();
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

  it("is disabled while None is chosen, and opens nothing", async () => {
    const w = mountPicker(null);
    // aria-disabled, so the tooltip saying why stays reachable.
    expect(gear(w).attributes("aria-disabled")).toBe("true");
    expect(gear(w).attributes("aria-label")).toBe(
      "Choose a plugin to configure it",
    );
    await gear(w).trigger("click");
    expect(
      w.findComponent({ name: "TaggerPluginSettingsDialog" }).exists(),
    ).toBe(false);
  });
});

describe("PluginSelect gear for a plugin no longer installed", () => {
  it("says it is not installed and opens nothing", async () => {
    const w = mountPicker("gone");
    expect(gear(w).attributes("aria-disabled")).toBe("true");
    expect(gear(w).attributes("aria-label")).toBe("gone is not installed");
    await gear(w).trigger("click");
    expect(
      w.findComponent({ name: "TaggerPluginSettingsDialog" }).exists(),
    ).toBe(false);
  });
});

describe("PluginSelect status line", () => {
  it("says whether the chosen plugin is loaded, with its description", () => {
    expect(mountPicker("wd14").get(".ps-hint").text()).toBe(
      "Loaded · Danbooru-style tags",
    );
    expect(mountPicker("pixlstash_tagger").get(".ps-hint").text()).toBe(
      "Not loaded",
    );
  });

  it("is absent for None", () => {
    // Kept mounted, empty, so it stays a live region that can announce.
    expect(mountPicker(null).get(".ps-hint").text()).toBe("");
  });
});

describe("PluginSelect announces", () => {
  it("the status line politely and a failed save as an alert", async () => {
    patchUserConfig.mockRejectedValueOnce(new Error("boom"));
    const w = mountPicker("wd14");
    expect(w.get(".ps-hint").attributes("aria-live")).toBe("polite");
    await choose(w, "pixlstash_tagger");
    expect(w.get(".ps-error").attributes("role")).toBe("alert");
  });
});

describe("PluginSelect keeps keyboard focus through a save", () => {
  beforeEach(() => vi.clearAllMocks());

  it("puts focus back on the dropdown once the select is re-enabled", async () => {
    const w = mount(PluginSelect, {
      attachTo: document.body,
      props: {
        plugins: PLUGINS.map((p) => ({ ...p, supports_tags: true })),
        kind: "tag",
        settings: { active_tag_plugin: "wd14" },
      },
      global: { stubs: { TaggerPluginSettingsDialog: true } },
    });
    select(w).element.focus();
    // Not awaited: the save starts synchronously, and the blur has to land
    // while it is in flight, as a browser's does when the select is disabled.
    select(w).setValue("pixlstash_tagger");
    select(w).element.blur();
    expect(document.activeElement).not.toBe(select(w).element);
    await flushPromises();
    expect(document.activeElement).toBe(select(w).element);
    w.unmount();
  });
});

describe("PluginSelect with no capable plugins", () => {
  it("still names a plugin that is saved but gone", () => {
    const w = mountPicker("gone", "tag", []);
    expect(w.get(".ps-empty").text()).toContain("gone is still selected");
  });

  it("shows the empty state rather than a lone None option", () => {
    const w = mountPicker(null, "tag", []);
    expect(w.find("select").exists()).toBe(false);
    expect(w.text()).toContain("No tag plugins registered.");
  });
});
