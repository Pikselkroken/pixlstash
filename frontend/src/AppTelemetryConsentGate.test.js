// When the privacy question is allowed on screen.
//
// It waits for two things: the library's first count (so the question does not
// land over a still-loading grid) and the folder-mapping wizard being closed
// (so two modals do not stack on a first run). It used to wait on a third -
// `useFolderMappingStore.pending` - and that one has no end. The entry is
// localStorage-backed, and the sidebar's auto-open deliberately refuses two
// whole classes of it: a `local_import` saved against a library that is no
// longer the active one, and a legacy `reference` entry. For those, no wizard
// ever opens, `pending` never clears, and the question was suppressed for the
// life of the install.
//
// It fails CLOSED - `telemetry_send_install_id` defaults false - so what was
// lost was the prompt, not the choice. Still a bug: the owner is never asked.

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
      check_for_updates: null,
      telemetry_send_install_id: false,
      telemetry_consent_prompted: false,
    },
  })),
  patchUserConfig: vi.fn(async () => ({ data: {} })),
}));

vi.mock("./api/telemetry", () => ({
  getInstallId: vi.fn(async () => ({ is_new_install: true })),
}));

import App from "./App.vue";
import TelemetryConsentDialog from "./components/dialogs/TelemetryConsentDialog.vue";
import ImageGrid from "./components/views/ImageGrid.vue";
import { useFolderMappingStore } from "./stores/useFolderMappingStore";

const STORAGE_KEY = "pixlstash.pendingFolderMapping";

// The one child App.vue calls INTO rather than only renders. A bare stub has
// no `refreshSidebar`, and the mount-time call would reject unhandled.
const SideBarStub = {
  name: "SideBar",
  template: "<div />",
  methods: {
    refreshSidebar() {},
    async offerLoosePictures() {},
    openSettingsDialog() {},
    openReferenceFolderEditor() {},
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
async function libraryLoaded(wrapper, { empty = false } = {}) {
  wrapper.findComponent(ImageGrid).vm.$emit("library-loaded", { empty });
  await flushPromises();
  await flushPromises();
}

const consentOpen = (wrapper) =>
  wrapper.findComponent(TelemetryConsentDialog).props("open");

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ json: async () => ({}) })),
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

describe("the privacy question", () => {
  it("opens once the library has been counted", async () => {
    const wrapper = await mountApp();
    expect(consentOpen(wrapper)).toBe(false);

    await libraryLoaded(wrapper);

    expect(consentOpen(wrapper)).toBe(true);
    wrapper.unmount();
  });

  it("still opens with a pending folder mapping nothing will ever act on", async () => {
    // A `local_import` entry for a library that is not the active one: the
    // sidebar's auto-open refuses it by design, so no wizard is coming and
    // nothing will ever clear the entry.
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        taskId: "task-7",
        path: "/home/me/Pictures/detached",
        label: "detached",
        mode: "local_import",
      }),
    );
    const wrapper = await mountApp();
    await libraryLoaded(wrapper);

    expect(useFolderMappingStore().pending).not.toBeNull();
    expect(consentOpen(wrapper)).toBe(true);
    wrapper.unmount();
  });

  it("waits while the folder-mapping wizard is actually on screen", async () => {
    const wrapper = await mountApp();
    const mapping = useFolderMappingStore();
    mapping.openWizard(null);
    await libraryLoaded(wrapper);

    expect(consentOpen(wrapper)).toBe(false);

    mapping.closeWizard();
    await flushPromises();
    expect(consentOpen(wrapper)).toBe(true);
    wrapper.unmount();
  });
});
