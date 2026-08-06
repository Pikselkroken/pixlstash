import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

import { useVersionCheck } from "./useVersionCheck";

// A development machine declares PIXLSTASH_INSTALL_TYPE=dev, which reaches the
// frontend as the install type from /version. Two things then have to happen, or
// the machine keeps counting as a real install:
//
//   1. "dev" survives into the version-check PATH. The collector reads the path;
//      an install type it does not recognise collapses to "other", which IS
//      counted, so an unlisted bucket is worse than never setting the variable.
//   2. The update banner points at /upgrade-dev.html. The upgrade-page metric is
//      a path filter and the install type rides in the query string, where a
//      path filter cannot see it.

function mountVersionCheck(installType) {
  const api = {};
  const Host = defineComponent({
    setup() {
      Object.assign(api, useVersionCheck(installType, true));
      return () => h("div");
    },
  });
  const wrapper = mount(Host);
  return { api, wrapper };
}

/** A remote version guaranteed to be newer than whatever __APP_VERSION__ is. */
const NEWER = "999.0.0";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ json: () => Promise.resolve({ version: NEWER }) })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("useVersionCheck — install-type bucket in the request path", () => {
  it("puts dev in the path instead of collapsing it to other", async () => {
    const { wrapper } = mountVersionCheck("dev");
    await wrapper.vm.$nextTick();

    const url = fetch.mock.calls[0][0];
    expect(url).toContain("/dev.json");
    expect(url).not.toContain("/other.json");
  });

  it("still collapses a genuinely unknown type to other", async () => {
    const { wrapper } = mountVersionCheck("snap");
    await wrapper.vm.$nextTick();

    expect(fetch.mock.calls[0][0]).toContain("/other.json");
  });

  it("leaves the established buckets alone", async () => {
    const { wrapper } = mountVersionCheck("docker");
    await wrapper.vm.$nextTick();

    expect(fetch.mock.calls[0][0]).toContain("/docker.json");
  });
});

describe("useVersionCheck — where the update banner points", () => {
  it("sends a dev install to upgrade-dev.html", async () => {
    const { api, wrapper } = mountVersionCheck("dev");
    await vi.waitUntil(() => api.latestVersion.value !== null);
    await wrapper.vm.$nextTick();

    expect(api.latestVersionUrl.value).toContain("/upgrade-dev.html");
    // The upgrade-page metric matches "%/upgrade.html%" as a SQL LIKE pattern,
    // so the dev path must not contain "/upgrade.html" as a substring.
    expect(api.latestVersionUrl.value).not.toContain("/upgrade.html");
  });

  it("sends a real install to the normal upgrade page", async () => {
    const { api, wrapper } = mountVersionCheck("pip");
    await vi.waitUntil(() => api.latestVersion.value !== null);
    await wrapper.vm.$nextTick();

    expect(api.latestVersionUrl.value).toContain("/upgrade.html");
    expect(api.latestVersionUrl.value).toContain("i=pip");
  });
});
