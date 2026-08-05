// The sidebar's top-level category counts, and the zero they must not swallow.
//
// The Scrapheap badge read `categoryCounts[SCRAPHEAP] || ""` while every
// sibling badge in the same file reads `?? ""`. `||` is falsy-tested, so a
// count of exactly 0 rendered as an empty string: an empty Scrapheap looked
// identical to one whose count had not loaded.
//
// That is the distinction `utils/sidebarCounts.js` exists as a separate module
// to protect ("a real `0` is an answer and still writes", pinned by
// `sidebarCounts.test.js`), and it is the only count binding in the sidebar
// that broke it. `scrapheapIsEmpty` nearby does use a falsy test, but it gates
// a disabled context-menu item, where 0 and "not loaded" genuinely mean the
// same thing; a displayed number is the case where they do not.
//
// Both directions: a real 0 renders "0", a genuinely absent count renders
// nothing, and the sibling badges keep behaving exactly as they did.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";

const apiGet = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    apiClient: {
      get: (...args) => apiGet(...args),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
    onSessionReset: () => () => {},
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeRef(false),
    sessionContext: makeRef(null),
    login: vi.fn(),
    logout: vi.fn(),
    newOperationBatchId: () => "cli-test",
    operationBatchHeaders: () => undefined,
    setRequestClientId: vi.fn(),
    notifySessionReset: vi.fn(),
    toBackendWebSocketUrl: () => "",
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", () => {
  const stubs = new Map();
  return new Proxy(
    {},
    {
      get(_t, prop) {
        if (prop === "__esModule") return true;
        if (typeof prop !== "string") return undefined;
        if (!stubs.has(prop)) {
          stubs.set(prop, { name: prop, template: "<div><slot /></div>" });
        }
        return stubs.get(prop);
      },
      has: () => true,
    },
  );
});

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "all-pictures" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
globalThis.IntersectionObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

import SideBar from "./SideBar.vue";

const SCRAPHEAP_ID = "SCRAPHEAP";
const ALL_ID = "ALL";

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: [] };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 0 } };
  return { data: [] };
}

function mountSidebar() {
  return mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
}

/**
 * The text of a top-level category row's trailing count badge.
 *
 * Located by the row's visible label, so the assertion follows the row a user
 * reads rather than a DOM index.
 */
function countTextFor(wrapper, label) {
  const row = wrapper
    .findAll(".sidebar-list-item")
    .find((el) => el.find(".sidebar-list-label").exists() &&
      el.find(".sidebar-list-label").text() === label);
  if (!row) return null;
  const badge = row.find(".sidebar-list-count");
  return badge.exists() ? badge.text() : null;
}

/** Put a count straight into the component's own state and re-render. */
async function setCounts(wrapper, counts) {
  wrapper.vm.categoryCounts = { ...wrapper.vm.categoryCounts, ...counts };
  await wrapper.vm.$nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the Scrapheap count badge", () => {
  it("renders a real zero", async () => {
    // An empty Scrapheap is a fact worth stating, and it is the state the badge
    // spends most of its life in. Before the fix this rendered "".
    const wrapper = mountSidebar();
    await flushPromises();
    await setCounts(wrapper, { [SCRAPHEAP_ID]: 0 });

    expect(countTextFor(wrapper, "Scrapheap")).toBe("0");

    wrapper.unmount();
  });

  it("renders nothing when the count has not arrived", async () => {
    // The other direction, and the only case that may be blank: `undefined` is
    // "not answered yet", not "zero". A fix that rendered "undefined" or "0"
    // here would be its own regression.
    const wrapper = mountSidebar();
    await flushPromises();
    await setCounts(wrapper, { [SCRAPHEAP_ID]: undefined });

    expect(countTextFor(wrapper, "Scrapheap")).toBe("");

    wrapper.unmount();
  });

  it("still renders a non-zero count unchanged", async () => {
    const wrapper = mountSidebar();
    await flushPromises();
    await setCounts(wrapper, { [SCRAPHEAP_ID]: 42 });

    expect(countTextFor(wrapper, "Scrapheap")).toBe("42");

    wrapper.unmount();
  });
});

describe("the sibling category badges are unchanged", () => {
  it("All Pictures keeps rendering its zero and its number", async () => {
    // These already used `?? ""`. Asserted so the Scrapheap change is visibly a
    // convergence on the existing convention rather than a new rule.
    const wrapper = mountSidebar();
    await flushPromises();

    await setCounts(wrapper, { [ALL_ID]: 0 });
    expect(countTextFor(wrapper, "All Pictures")).toBe("0");

    await setCounts(wrapper, { [ALL_ID]: 7 });
    expect(countTextFor(wrapper, "All Pictures")).toBe("7");

    await setCounts(wrapper, { [ALL_ID]: undefined });
    expect(countTextFor(wrapper, "All Pictures")).toBe("");

    wrapper.unmount();
  });

  it("both badges agree on how they treat zero", async () => {
    // The single assertion the original divergence would have failed.
    const wrapper = mountSidebar();
    await flushPromises();
    await setCounts(wrapper, { [ALL_ID]: 0, [SCRAPHEAP_ID]: 0 });

    expect(countTextFor(wrapper, "Scrapheap")).toBe(
      countTextFor(wrapper, "All Pictures"),
    );

    wrapper.unmount();
  });
});
