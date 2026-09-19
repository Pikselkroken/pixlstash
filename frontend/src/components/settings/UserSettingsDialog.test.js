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
vi.mock("./ComputeSection.vue", () => ({
  default: { template: '<div class="compute-stub" />' },
}));
vi.mock("./LibrariesSection.vue", () => ({ default: sectionStub }));
vi.mock("./PrivacySection.vue", () => ({ default: sectionStub }));
vi.mock("./ScrapheapSection.vue", () => ({ default: sectionStub }));
vi.mock("./SnapshotsSection.vue", () => ({ default: sectionStub }));
vi.mock("./SmartScoreSection.vue", () => ({ default: sectionStub }));
vi.mock("./ComfyuiHostSection.vue", () => ({
  default: {
    props: ["first"],
    template: '<div class="comfyui-host-stub" :data-first="String(first)" />',
  },
}));

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

  it("orders the rail with Libraries ahead of Scrapheap, and no layout tab", async () => {
    sessionContext.value = { scope: "ALL" };
    const wrapper = mountDialog();
    await nextTick();

    // The whole order, not an adjacency: an index comparison between two
    // entries also holds when one of them is missing from the rail entirely.
    // Backend is desktop-only and absent under jsdom; Compute stays, because
    // the ComfyUI host lives there.
    expect(
      wrapper.findAll(".settings-nav-item").map((n) => n.attributes("id")),
    ).toEqual([
      "settings-nav-appearance",
      "settings-nav-behaviour",
      "settings-nav-smart-score",
      "settings-nav-libraries",
      // No Library layout rail item: the layout is a property of whichever
      // library is *open*, so it is a dialog off the active library's overflow
      // menu rather than a tab beside the list that opens one.
      "settings-nav-scrapheap",
      "settings-nav-snapshots",
      "settings-nav-privacy",
      // Labelled "ComfyUI" here: no desktop bridge, so the pane holds only the
      // ComfyUI host. The id stays `compute`.
      "settings-nav-compute",
      "settings-nav-account",
    ]);
  });

  it("gives every rail item a pane matching its aria-controls", async () => {
    sessionContext.value = { scope: "ALL" };
    const wrapper = mountDialog();
    await nextTick();

    for (const item of wrapper.findAll(".settings-nav-item")) {
      const pane = wrapper.find(`#${item.attributes("aria-controls")}`);
      expect(pane.exists()).toBe(true);
      expect(pane.attributes("role")).toBe("region");
      expect(pane.attributes("aria-labelledby")).toBe(item.attributes("id"));
    }
  });

  it("keeps the ComfyUI host on Compute in a browser, without acceleration", async () => {
    // Acceleration needs the desktop bridge; the ComfyUI host does not, and
    // Compute is the only place left to set it.
    sessionContext.value = { scope: "ALL" };
    const wrapper = mountDialog();
    await nextTick();

    expect(wrapper.find("#settings-nav-compute").text()).toContain("ComfyUI");
    const pane = wrapper.find("#settings-pane-compute");
    // First in the pane, so it carries no divider above nothing.
    expect(pane.find(".comfyui-host-stub").attributes("data-first")).toBe(
      "true",
    );
    expect(pane.find(".compute-stub").exists()).toBe(false);
  });

  it("gives the desktop both halves of Compute, the host below acceleration", async () => {
    // `isDesktop` is read when the module is evaluated, so the bridge has to
    // be in place before the import.
    sessionContext.value = { scope: "ALL" };
    window.pixlstashDesktop = {};
    try {
      vi.resetModules();
      const { default: Desktop } = await import("./UserSettingsDialog.vue");
      const wrapper = mount(Desktop, {
        props: { open: true, initialTab: "compute" },
        global: { stubs: { VIcon: true } },
      });
      await nextTick();

      const pane = wrapper.find("#settings-pane-compute");
      expect(pane.find(".compute-stub").exists()).toBe(true);
      // Acceleration is above it, so the host section is not the pane's first.
      expect(pane.find(".comfyui-host-stub").attributes("data-first")).toBe(
        "false",
      );
      expect(wrapper.find("#settings-nav-compute").text()).toContain(
        "Compute",
      );
      expect(wrapper.find("#settings-nav-backend").exists()).toBe(true);
    } finally {
      delete window.pixlstashDesktop;
      vi.resetModules();
    }
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
