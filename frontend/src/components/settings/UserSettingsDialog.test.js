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
vi.mock("./ImportFoldersSection.vue", () => ({ default: sectionStub }));
vi.mock("./ModelFoldersSection.vue", () => ({ default: sectionStub }));
vi.mock("./PrivacySection.vue", () => ({ default: sectionStub }));
vi.mock("./SharingSection.vue", () => ({ default: sectionStub }));
vi.mock("./ScrapheapSection.vue", () => ({ default: sectionStub }));
vi.mock("./SnapshotsSection.vue", () => ({ default: sectionStub }));
vi.mock("./GhostsSection.vue", () => ({ default: sectionStub }));
vi.mock("./TrainingSection.vue", () => ({ default: sectionStub }));
vi.mock("./SmartScoreSection.vue", () => ({ default: sectionStub }));
vi.mock("./ComfyuiHostSection.vue", () => ({
  default: {
    props: { first: Boolean },
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

  it("groups the rail under five headings, and has no layout tab", async () => {
    sessionContext.value = { scope: "ALL" };
    const wrapper = mountDialog();
    await nextTick();

    // The whole order, headings included, not an adjacency: an index
    // comparison between two entries also holds when one of them is missing.
    // Server is desktop-only and absent under jsdom; GPU & compute and
    // ComfyUI stay, because a browser has a GPU picker and connects ComfyUI.
    // No Library layout item: the layout belongs to whichever library is
    // open, so it is a dialog off the active library's overflow menu.
    expect(
      wrapper
        .findAll(".settings-nav-heading, .settings-nav-item")
        .map((n) => n.attributes("id") || n.text()),
    ).toEqual([
      "General",
      "settings-nav-appearance",
      "settings-nav-privacy",
      "Library",
      "settings-nav-libraries",
      "settings-nav-recovery",
      "Tags",
      "settings-nav-behaviour",
      "settings-nav-smart-score",
      "Models & AI",
      "settings-nav-models",
      "settings-nav-training",
      "settings-nav-comfyui",
      "settings-nav-compute",
      "Access",
      "settings-nav-account",
    ]);
  });

  it.each([
    ["import-folders", "libraries"],
    ["scrapheap", "recovery"],
    ["snapshots", "recovery"],
    ["sharing", "privacy"],
  ])(
    "lands a link to the old %s pane on %s, at its section",
    async (oldId, pane) => {
      sessionContext.value = { scope: "ALL" };
      const scrolled = vi.fn();
      Element.prototype.scrollIntoView = scrolled;
      const wrapper = mount(UserSettingsDialog, {
        props: { open: true, initialTab: oldId },
        global: { stubs: { VIcon: true } },
        attachTo: document.body,
      });
      await nextTick();
      await nextTick();

      expect(
        wrapper.find(`#settings-nav-${pane}`).attributes("aria-current"),
      ).toBe("page");
      const anchor = wrapper.find(`#settings-anchor-${oldId}`);
      expect(anchor.exists()).toBe(true);
      expect(
        wrapper.find(`#settings-pane-${pane}`).element.contains(anchor.element),
      ).toBe(true);
      expect(scrolled.mock.contexts).toEqual([anchor.element]);
      wrapper.unmount();
      delete Element.prototype.scrollIntoView;
    },
  );

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

  it("gives the ComfyUI host its own pane in a browser", async () => {
    sessionContext.value = { scope: "ALL" };
    const wrapper = mount(UserSettingsDialog, {
      props: { open: true, initialTab: "comfyui" },
      global: { stubs: { VIcon: true } },
    });
    await nextTick();

    expect(wrapper.find("#settings-nav-comfyui").attributes("aria-current")).toBe(
      "page",
    );
    const pane = wrapper.find("#settings-pane-comfyui");
    // Alone in the pane, so it carries no divider above nothing.
    expect(pane.find(".comfyui-host-stub").attributes("data-first")).toBe(
      "true",
    );
    // The Compute pane is on a browser as well (which GPU, what it may take).
    expect(wrapper.find(".compute-stub").exists()).toBe(true);
  });

  it("splits ComfyUI and Compute into two desktop panes", async () => {
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

      expect(
        wrapper.findAll(".settings-nav-item").map((n) => n.attributes("id")),
      ).toEqual([
        "settings-nav-appearance",
        "settings-nav-privacy",
        "settings-nav-libraries",
        "settings-nav-recovery",
        "settings-nav-behaviour",
        "settings-nav-smart-score",
        "settings-nav-models",
        "settings-nav-training",
        "settings-nav-comfyui",
        "settings-nav-compute",
        "settings-nav-backend",
        "settings-nav-account",
      ]);
      expect(wrapper.find("#settings-nav-compute").attributes("aria-current")).toBe(
        "page",
      );
      const compute = wrapper.find("#settings-pane-compute");
      expect(compute.find(".compute-stub").exists()).toBe(true);
      expect(compute.find(".comfyui-host-stub").exists()).toBe(false);
      const comfyui = wrapper.find("#settings-pane-comfyui");
      expect(comfyui.find(".comfyui-host-stub").attributes("data-first")).toBe(
        "true",
      );
      expect(comfyui.find(".compute-stub").exists()).toBe(false);
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
    // One item left: a heading over it would be noise.
    expect(wrapper.findAll(".settings-nav-item")).toHaveLength(1);
    expect(wrapper.find(".settings-nav-heading").exists()).toBe(false);
  });
});
