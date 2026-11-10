// The sidebar's Models and Workflows rows left for the title bar's workspace
// switch (#2158). What the sidebar still owes them: it offers neither row in
// either layout, for an owner or a READ session, and it still clears the
// "new models arrived" mark when the shelf opens (the mark itself is drawn on
// the title bar's Models segment now).
//
// The READ-session rule the rows carried (shown, inert, explained) moved with
// them: `components/TitleBar.test.js`. `useAppNavigationModels.test.js` covers
// the other half - that a pasted `/models` URL never mounts the shelf.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";

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

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const route = reactive({
    query: {},
    params: {},
    path: "/",
    name: "all-pictures",
  });
  const { vi: vitest } = await import("vitest");
  return {
    useRoute: () => route,
    useRouter: () => ({
      push: vitest.fn(),
      replace: vitest.fn(),
      currentRoute: { value: { query: {} } },
    }),
  };
});

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useRoute } from "vue-router";
import SideBar from "./SideBar.vue";

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: [] };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 0 } };
  return { data: [] };
}

/**
 * Mount the sidebar. `docked` renders the collapsed dock instead of the
 * expanded rail - a separate branch of the template with its own copy of both
 * destinations.
 */
async function mountSidebar({ docked = false } = {}) {
  if (docked) useSidebarStore().sidebarDocked = true;
  const wrapper = mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
  wrapper.vm.refreshSidebar();
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  sessionContext.value = null;
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the sidebar's workspace rows", () => {
  it.each([
    ["an owner", false],
    ["a READ session", true],
  ])("are gone for %s, in both layouts", async (_who, readOnly) => {
    isReadOnly.value = readOnly;
    for (const docked of [false, true]) {
      const wrapper = await mountSidebar({ docked });
      // The anchor: a render that produced no rail at all would pass the
      // absence check below for the wrong reason.
      expect(
        wrapper
          .findAll("button")
          .some(
            (b) =>
              b.text().includes("Duplicates") ||
              b.attributes("aria-label") === "Duplicates",
          ),
      ).toBe(true);
      const names = wrapper
        .findAll("button")
        .map((b) => `${b.text()} ${b.attributes("aria-label") ?? ""}`);
      expect(names.filter((n) => /\b(Models|Workflows)\b/.test(n))).toEqual([]);
      wrapper.unmount();
    }
  });
});

describe("new models that arrived while the shelf was closed", () => {
  afterEach(() => {
    useRoute().name = "all-pictures";
  });

  it("are marked seen once the shelf is opened", async () => {
    const wrapper = await mountSidebar();
    const shelf = useModelShelfStore();
    shelf.unseenArrivals = true;
    await flushPromises();
    expect(shelf.unseenArrivals).toBe(true);

    useRoute().name = "models";
    await flushPromises();
    expect(shelf.unseenArrivals).toBe(false);
    wrapper.unmount();
  });

  it("is never raised by an arrival while the shelf is open", async () => {
    useRoute().name = "models";
    const wrapper = await mountSidebar();
    const shelf = useModelShelfStore();
    shelf.unseenArrivals = true;
    await flushPromises();
    expect(shelf.unseenArrivals).toBe(false);
    wrapper.unmount();
  });
});
