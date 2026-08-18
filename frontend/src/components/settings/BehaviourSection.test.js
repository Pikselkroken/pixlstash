import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
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
  }),
}));

import BehaviourSection from "./BehaviourSection.vue";

function mountPane() {
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
          props: ["prependIcon"],
          template: '<button @click="$emit(\'click\')"><slot /></button>',
        },
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
    expect(wrapper.text()).toContain("pixlstash plugins install <name-or-path>");
    expect(wrapper.text()).toContain("/home/me/.pixlstash/plugins");

    const close = wrapper.findAll("button").find((b) => b.text() === "Close");
    await close.trigger("click");
    await nextTick();
    expect(wrapper.text()).not.toContain("pixlstash plugins install <name-or-path>");
  });
});
