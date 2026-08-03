import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

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
vi.mock("./ScrapheapSection.vue", () => ({ default: sectionStub }));
vi.mock("./SnapshotsSection.vue", () => ({ default: sectionStub }));
vi.mock("./SmartScoreSection.vue", () => ({ default: sectionStub }));
vi.mock("./WorkflowsSection.vue", () => ({ default: sectionStub }));

import UserSettingsDialog from "./UserSettingsDialog.vue";
import { sessionContext } from "../../utils/apiClient";

function mountDialog() {
  return mount(UserSettingsDialog, {
    props: { open: true, initialTab: "libraries" },
    global: {
      stubs: {
        VIcon: true,
      },
    },
  });
}

afterEach(() => {
  sessionContext.value = null;
});

describe("UserSettingsDialog library navigation", () => {
  it("deep-links an owner to the semantic Libraries region", async () => {
    sessionContext.value = { scope: "ALL" };
    const wrapper = mountDialog();
    await nextTick();

    const librariesNav = wrapper.find("#settings-nav-libraries");
    expect(librariesNav.attributes("aria-current")).toBe("page");
    expect(librariesNav.attributes("aria-controls")).toBe(
      "settings-pane-libraries",
    );
    expect(wrapper.find("#settings-pane-libraries").attributes("role")).toBe(
      "region",
    );
  });

  it("does not disclose the nav entry or pane in read-only/share mode", async () => {
    sessionContext.value = { scope: "READ", resource_type: "picture_set" };
    const wrapper = mountDialog();
    await nextTick();

    expect(wrapper.find("#settings-nav-libraries").exists()).toBe(false);
    expect(wrapper.find("#settings-pane-libraries").exists()).toBe(false);
    expect(wrapper.find("#settings-nav-appearance").attributes("aria-current")).toBe(
      "page",
    );
  });
});
