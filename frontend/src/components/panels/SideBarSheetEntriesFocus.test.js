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

// Mutable, so a test can put the sidebar on a route other than the default.
const nav = vi.hoisted(() => ({ route: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    query: {},
    params: {},
    path: "/",
    name: "all-pictures",
  });
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: vitest.fn(),
      replace: vitest.fn(),
      currentRoute: { value: { query: {} } },
    }),
  };
});

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import SideBar from "./SideBar.vue";

// The Sets heading's sheet entries open a full-screen dialog from code. The
// heading takes no focus, so they hand it the heading's caret to return focus
// to rather than dropping it on the page.

const SETS = [{ id: 4, name: "Ada at the beach", image_count: 2, project_image_count: 2 }];

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/picture_sets")) return { data: SETS };
  if (u.includes("/summary")) return { data: { image_count: 2 } };
  return { data: [] };
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
  document.body.innerHTML = "";
});

describe("the Sets heading's sheet entries", () => {
  for (const kind of ["location", "prop"]) {
    it(`hands the ${kind} sheet dialog the caret to return focus to`, async () => {
      const wrapper = mount(SideBar, {
        shallow: true,
        props: { backendUrl: "/api/v1" },
        attachTo: document.body,
        global: {
          config: {
            compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
          },
          stubs: { teleport: { template: "<div><slot /></div>" } },
        },
      });
      wrapper.vm.refreshSidebar();
      for (let i = 0; i < 5; i += 1) await flushPromises();
      const caret = wrapper.find('[aria-controls="sidebar-section-sets"]');
      caret.element.parentElement.dispatchEvent(
        new MouseEvent("contextmenu", { bubbles: true }),
      );
      await flushPromises();
      document.querySelector(`[data-testid="ctx-${kind}-sheets"]`).click();
      const [payload] = wrapper.emitted("item-sheets").at(-1);
      expect(payload.kind).toBe(kind);
      expect(payload.returnFocusTo).toBe(caret.element);
      wrapper.unmount();
    });
  }
});
