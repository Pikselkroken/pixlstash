// What's changed in 2.0 (#2145): App.vue decides whether this launch is the
// upgrade from the user config, /version and the grid's first count. The count
// is what decides "empty": the grid's `empty` flag waits on the empty state's
// appearance delay, so it can still read false for a library with nothing in
// it, and a fresh install must not be greeted as an upgrade.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";

const nav = vi.hoisted(() => ({ route: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({ path: "/", name: "all-pictures", params: {}, query: {} });
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: vitest.fn().mockResolvedValue(undefined),
      replace: vitest.fn().mockResolvedValue(undefined),
      currentRoute: { value: nav.route },
    }),
  };
});

vi.mock("vuetify", () => ({
  useTheme: () => ({ global: { name: { value: "dark" } } }),
}));

vi.mock("./utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    appendShareToken: (url) => url,
    isReadOnly: ref(false),
    sessionContext: ref(null),
    onSessionReset: () => () => {},
    toBackendWebSocketUrl: () => "",
    newOperationBatchId: () => "cli-test",
    operationBatchHeaders: () => undefined,
    setRequestClientId: vi.fn(),
    notifySessionReset: vi.fn(),
    activateShareToken: vi.fn(),
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: ref(true),
    login: vi.fn(),
    logout: vi.fn(),
    apiClient: {
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
  };
});

// The owner of this library has never answered the question, which is what
// makes `useAppConfig` call back and open the dialog in the first place.
vi.mock("./api/config", () => ({
  getUserConfig: vi.fn(async () => ({
    data: {
      sort: "date",
      thumbnail: 256,
      check_for_updates: false,
      telemetry_send_install_id: false,
      telemetry_consent_prompted: true,
    },
  })),
  patchUserConfig: vi.fn(async () => ({ data: {} })),
}));

vi.mock("./api/telemetry", () => ({
  getInstallId: vi.fn(async () => ({ is_new_install: true })),
}));

import App from "./App.vue";
import ImageGrid from "./components/views/ImageGrid.vue";
import { patchUserConfig } from "./api/config";
import { useWhatsChangedStore } from "./stores/useWhatsChangedStore";

// The one child App.vue calls INTO rather than only renders. A bare stub has
// no `refreshSidebar`, and the mount-time call would reject unhandled.
const SideBarStub = {
  name: "SideBar",
  template: "<div />",
  methods: {
    refreshSidebar() {},
    async offerLoosePictures() {},
    openSettingsDialog() {},
    startLocalImport() {},
  },
};

/** Mount App with every child stubbed; only App.vue's own logic runs. */
async function mountApp() {
  const wrapper = mount(App, {
    shallow: true,
    global: {
      stubs: { SideBar: SideBarStub },
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
  await flushPromises();
  return wrapper;
}

/** Answer the grid's first count, the way ImageGrid does. */
async function libraryLoaded(wrapper, payload) {
  wrapper.findComponent(ImageGrid).vm.$emit("library-loaded", payload);
  await flushPromises();
  await flushPromises();
}


beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  patchUserConfig.mockClear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ json: async () => ({ version: "2.0.0" }) })),
  );
  vi.stubGlobal("WebSocket", class {
    static OPEN = 1;
    constructor() {
      this.readyState = 1;
    }
    send() {}
    close() {}
  });
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

describe("the 2.0 upgrade test", () => {
  it("a library counted at zero is a new install, even while `empty` lags", async () => {
    const wrapper = await mountApp();
    await libraryLoaded(wrapper, { empty: false, pictures: 0 });
    expect(useWhatsChangedStore().checklist).toBe(false);
    expect(patchUserConfig).toHaveBeenCalledWith({ last_seen_version: "2.0.0" });
    wrapper.unmount();
  });

  it("a library with pictures and no stored version is the upgrade", async () => {
    const wrapper = await mountApp();
    await libraryLoaded(wrapper, { empty: false, pictures: 12 });
    expect(useWhatsChangedStore().checklistShown).toBe(true);
    expect(patchUserConfig).toHaveBeenCalledWith({
      last_seen_version: "2.0.0",
      whats_changed: { done: [], checklist: true, card: true },
    });
    wrapper.unmount();
  });
});
