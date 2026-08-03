import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("../composables/useBreadcrumb", () => ({
  useBreadcrumb: () => ({ breadcrumb: [], navigateBreadcrumb: vi.fn() }),
}));
vi.mock("../composables/useVersionCheck", () => ({
  useVersionCheck: () => ({
    latestVersion: { value: "" },
    latestVersionUrl: { value: "" },
    latestSecurityLevel: { value: "" },
    updateAvailable: { value: false },
    updateDismissed: { value: false },
    isHighSecurity: { value: false },
    securityUpdateTitle: { value: "" },
    dismissUpdateAlert: vi.fn(),
  }),
}));

describe("TitleBar active library", () => {
  let originalDesktop;

  beforeEach(() => {
    originalDesktop = window.pixlstashDesktop;
    window.pixlstashDesktop = {};
    vi.resetModules();
  });

  afterEach(() => {
    window.pixlstashDesktop = originalDesktop;
  });

  it("keeps the owner identity visible and deep-links to Libraries", async () => {
    const { default: TitleBar } = await import("./TitleBar.vue");
    const wrapper = mount(TitleBar, {
      props: { activeLibraryName: "Family Photos" },
      global: {
        stubs: {
          VIcon: true,
          WordmarkLogo: { template: "<span>PixlStash</span>" },
        },
      },
    });

    const indicator = wrapper.find(".titlebar-library");
    expect(indicator.text()).toContain("Family Photos");
    await indicator.trigger("click");
    expect(wrapper.emitted("open-libraries")).toHaveLength(1);
  });

  it("does not render a library identity when the owner prop is withheld", async () => {
    const { default: TitleBar } = await import("./TitleBar.vue");
    const wrapper = mount(TitleBar, {
      props: { activeLibraryName: "" },
      global: { stubs: { VIcon: true, WordmarkLogo: true } },
    });
    expect(wrapper.find(".titlebar-library").exists()).toBe(false);
  });

  it("keeps a visible deep-link in browser chrome", async () => {
    window.pixlstashDesktop = undefined;
    vi.resetModules();
    const { default: BrowserTitleBar } = await import("./TitleBar.vue");
    const wrapper = mount(BrowserTitleBar, {
      props: { activeLibraryName: "Browser library" },
      global: { stubs: { VIcon: true, WordmarkLogo: true } },
    });

    expect(wrapper.find(".titlebar").exists()).toBe(false);
    const indicator = wrapper.find(".browser-library-chrome__button");
    expect(indicator.text()).toContain("Browser library");
    await indicator.trigger("click");
    expect(wrapper.emitted("open-libraries")).toHaveLength(1);
  });

  it("does not disclose a browser identity when the owner prop is withheld", async () => {
    window.pixlstashDesktop = undefined;
    vi.resetModules();
    const { default: BrowserTitleBar } = await import("./TitleBar.vue");
    const wrapper = mount(BrowserTitleBar, {
      props: { activeLibraryName: "" },
      global: { stubs: { VIcon: true, WordmarkLogo: true } },
    });

    expect(wrapper.find(".browser-library-chrome").exists()).toBe(false);
  });
});
