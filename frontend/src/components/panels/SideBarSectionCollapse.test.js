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

// People and Sets fold under a caret in their section heads. The caret is a
// real button that says which way it goes; the whole head toggles too, and the
// head's "+" must not.

const CHARACTERS = [{ id: 7, name: "Ada", image_count: 3, project_image_count: 3 }];
const SETS = [{ id: 4, name: "Ada at the beach", image_count: 2, project_image_count: 2 }];

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: CHARACTERS };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: SETS };
  if (u.includes("/summary")) return { data: { image_count: 3 } };
  return { data: [] };
}

async function mountSidebar() {
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

function section(wrapper, key) {
  const list = wrapper.find(`#sidebar-section-${key}`);
  const caret = wrapper.find(`[aria-controls="sidebar-section-${key}"]`);
  return {
    list,
    caret,
    head: caret.element.parentElement,
    shown: () => list.element.style.display !== "none",
  };
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

describe("collapsing People and Sets", () => {
  for (const [key, label] of [
    ["people", "People"],
    ["sets", "Sets"],
  ]) {
    it(`folds ${label} from its caret and remembers it`, async () => {
      const wrapper = await mountSidebar();
      const s = section(wrapper, key);
      expect(s.caret.exists()).toBe(true);
      expect(s.shown()).toBe(true);
      expect(s.caret.attributes("aria-expanded")).toBe("true");
      expect(s.caret.attributes("aria-label")).toBe(`Collapse ${label}`);

      await s.caret.trigger("click");
      expect(s.shown()).toBe(false);
      expect(s.caret.attributes("aria-expanded")).toBe("false");
      expect(s.caret.attributes("aria-label")).toBe(`Expand ${label}`);
      expect(
        JSON.parse(window.localStorage.getItem("pixlstash:collapsedSections"))[
          `sidebar:${key}`
        ],
      ).toBe(true);
      wrapper.unmount();

      // A fresh sidebar comes back folded.
      const again = await mountSidebar();
      expect(section(again, key).shown()).toBe(false);

      // Clicking the head itself opens it again.
      await section(again, key).head.click();
      await again.vm.$nextTick();
      expect(section(again, key).shown()).toBe(true);
      again.unmount();
    });

    it(`leaves ${label} open when its "+" is clicked`, async () => {
      const wrapper = await mountSidebar();
      const s = section(wrapper, key);
      // Starts open, so the assertion below is about the "+", not the order.
      expect(s.shown()).toBe(true);
      await wrapper
        .find(`[aria-controls="sidebar-section-${key}"] ~ * .sidebar-section-add`)
        .trigger("click");
      expect(s.shown()).toBe(true);
      wrapper.unmount();
    });
  }

  it("opens folded sections when a find starts", async () => {
    const wrapper = await mountSidebar();
    await section(wrapper, "people").caret.trigger("click");
    await section(wrapper, "sets").caret.trigger("click");
    expect(section(wrapper, "people").shown()).toBe(false);
    expect(section(wrapper, "sets").shown()).toBe(false);

    await wrapper.find(".sidebar-find-input").setValue("Ada");
    await flushPromises();
    expect(section(wrapper, "people").shown()).toBe(true);
    expect(section(wrapper, "sets").shown()).toBe(true);
    wrapper.unmount();
  });
});
