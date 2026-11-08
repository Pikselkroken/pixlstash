// Settings' version and What's new pill (#2145): both bring back the 2.0
// checklist and the training tour card, and the dialog gets out of the way.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    logout: vi.fn(),
    isReadOnly: ref(false),
  };
});
vi.mock("../../api/config", () => ({ patchUserConfig: vi.fn(async () => ({})) }));

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
}));
vi.mock("../widgets/AppDialog.vue", () => ({
  default: {
    props: ["open", "title"],
    template: `
      <div class="app-dialog-stub">
        <slot name="subtitle" />
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
vi.mock("./ImportFoldersSection.vue", () => ({ default: sectionStub }));
vi.mock("./ModelFoldersSection.vue", () => ({ default: sectionStub }));
vi.mock("./PrivacySection.vue", () => ({ default: sectionStub }));
vi.mock("./SharingSection.vue", () => ({ default: sectionStub }));
vi.mock("./ScrapheapSection.vue", () => ({ default: sectionStub }));
vi.mock("./SnapshotsSection.vue", () => ({ default: sectionStub }));
vi.mock("./GhostsSection.vue", () => ({ default: sectionStub }));
vi.mock("./AiToolkitSetup.vue", () => ({ default: sectionStub }));
vi.mock("./SmartScoreSection.vue", () => ({ default: sectionStub }));
vi.mock("./ComfyuiHostSection.vue", () => ({ default: sectionStub }));

import UserSettingsDialog from "./UserSettingsDialog.vue";
import { isReadOnly } from "../../utils/apiClient";
import { useWhatsChangedStore } from "../../stores/useWhatsChangedStore";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
});

describe("Settings' What's new", () => {
  for (const testid of ["settings-version", "settings-whats-new"]) {
    it(`${testid} reopens both and closes the dialog`, async () => {
      const store = useWhatsChangedStore();
      const w = mount(UserSettingsDialog, { props: { open: true } });
      await w.find(`[data-testid="${testid}"]`).trigger("click");
      expect(store.checklist).toBe(true);
      expect(store.card).toBe(true);
      expect(store.reopened).toBe(1);
      expect(w.emitted("update:open")?.at(-1)).toEqual([false]);
    });
  }

  it("a read-only session gets neither", () => {
    isReadOnly.value = true;
    const w = mount(UserSettingsDialog, { props: { open: true } });
    expect(w.find('[data-testid="settings-whats-new"]').exists()).toBe(false);
    expect(w.find('[data-testid="settings-version"]').exists()).toBe(false);
  });
});

// The rail's "PixlStash needs you!" card asks the person who runs PixlStash
// for reports, so a guest on someone else's server does not get it.
describe("Settings' report card", () => {
  it("is at the foot of the rail for the owner", () => {
    const w = mount(UserSettingsDialog, { props: { open: true } });
    const card = w.find('.settings-nav [data-testid="needs-you-box"]');
    expect(card.exists()).toBe(true);
  });

  it("is not shown in a read-only session", () => {
    isReadOnly.value = true;
    const w = mount(UserSettingsDialog, { props: { open: true } });
    expect(w.find('[data-testid="needs-you-box"]').exists()).toBe(false);
  });
});
