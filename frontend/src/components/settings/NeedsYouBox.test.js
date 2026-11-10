// "PixlStash needs you!": the Settings rail's link to the report page on
// pixlstash.dev, which reads `version` and `install` to prefill its wizard.
import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
}));

import NeedsYouBox from "./NeedsYouBox.vue";

function setInstallMeta(content) {
  document
    .querySelectorAll('meta[name="pixlstash-install-type"]')
    .forEach((el) => el.remove());
  if (content === null) return;
  const meta = document.createElement("meta");
  meta.setAttribute("name", "pixlstash-install-type");
  meta.setAttribute("content", content);
  document.head.appendChild(meta);
}

afterEach(() => setInstallMeta(null));

describe("NeedsYouBox", () => {
  it("links to the report page with the version and install type", () => {
    setInstallMeta("electron");
    const wrapper = mount(NeedsYouBox, { props: { version: "2.1.0" } });
    const link = wrapper.get('[data-testid="needs-you-box"]');
    const url = new URL(link.attributes("href"));
    expect(url.origin + url.pathname).toBe("https://pixlstash.dev/report.html");
    expect(url.searchParams.get("version")).toBe("2.1.0");
    expect(url.searchParams.get("install")).toBe("electron");
  });

  it("opens outside the app, without handing the page an opener", () => {
    const wrapper = mount(NeedsYouBox, { props: { version: "2.1.0" } });
    const link = wrapper.get('[data-testid="needs-you-box"]');
    expect(link.attributes("target")).toBe("_blank");
    expect(link.attributes("rel")).toContain("noopener");
  });

  it("leaves out a version it does not know", () => {
    setInstallMeta("docker");
    const wrapper = mount(NeedsYouBox, { props: { version: "" } });
    const url = new URL(
      wrapper.get('[data-testid="needs-you-box"]').attributes("href"),
    );
    expect(url.searchParams.has("version")).toBe(false);
    expect(url.searchParams.get("install")).toBe("docker");
  });

  it("says what it is for in words, and the poster has its own alt", () => {
    const wrapper = mount(NeedsYouBox, { props: { version: "2.1.0" } });
    expect(wrapper.text()).toContain("Report a bug or an idea");
    expect(wrapper.get("img").attributes("alt")).toBe("PixlStash needs you!");
  });
});
