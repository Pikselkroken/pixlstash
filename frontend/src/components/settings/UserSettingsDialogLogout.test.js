import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
}));
vi.mock("../widgets/AppDialog.vue", () => ({
  default: {
    props: ["open", "title"],
    template: `
      <div class="app-dialog-stub">
        <slot name="header-right" />
        <slot />
      </div>
    `,
  },
}));
vi.mock("../widgets/AppButton.vue", () => ({
  default: { template: "<button><slot /></button>" },
}));

const sectionStub = vi.hoisted(() => ({
  template: '<div class="section-stub" />',
}));
vi.mock("./AccountSection.vue", () => ({ default: sectionStub }));
vi.mock("./AppearanceSection.vue", () => ({ default: sectionStub }));
vi.mock("./BehaviourSection.vue", () => ({ default: sectionStub }));
vi.mock("./ComputeSection.vue", () => ({ default: sectionStub }));
vi.mock("./LibrariesSection.vue", () => ({ default: sectionStub }));
vi.mock("./PrivacySection.vue", () => ({ default: sectionStub }));
vi.mock("./ScrapheapSection.vue", () => ({ default: sectionStub }));
vi.mock("./SnapshotsSection.vue", () => ({ default: sectionStub }));
vi.mock("./SmartScoreSection.vue", () => ({ default: sectionStub }));
vi.mock("./WorkflowsSection.vue", () => ({ default: sectionStub }));

// `isDesktop` is read once when the module is evaluated, so each direction has
// to set the bridge BEFORE importing the component.
async function mountWithBridge(bridge) {
  if (bridge) window.pixlstashDesktop = bridge;
  else delete window.pixlstashDesktop;
  vi.resetModules();
  const { default: UserSettingsDialog } =
    await import("./UserSettingsDialog.vue");
  return mount(UserSettingsDialog, { props: { open: true } });
}

afterEach(() => {
  delete window.pixlstashDesktop;
});

describe("UserSettingsDialog log out button", () => {
  it("hides it in the desktop app, which runs on loopback", async () => {
    const wrapper = await mountWithBridge({});
    expect(wrapper.text()).not.toContain("Log out");
  });

  it("keeps it for a browser session against the included server", async () => {
    const wrapper = await mountWithBridge(null);
    expect(wrapper.text()).toContain("Log out");
  });
});
