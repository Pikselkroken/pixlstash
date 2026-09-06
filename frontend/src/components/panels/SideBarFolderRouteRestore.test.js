// The other half of the folder URL round trip (`useAppNavigationFolders.test.js`
// is the first): what the sidebar does when the route arrives.
//
// `/ref-folder/:id` is restored by the `activeFolderKey` watcher, which
// rebuilt the payload from the folder listing alone and therefore always
// selected the folder ROOT. Since a subfolder click now keeps its `?path=`,
// that watcher would have thrown it away on the very next reload - the URL
// would say the subfolder and the grid would show the whole folder.
//
// The id keeps owning `referenceFolderId`, so the grid query still carries
// `reference_folder_id`; only `pathPrefix` and the highlight key come from the
// query.

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
  const { vi: vitest } = await import("vitest");
  const route = reactive({
    query: {},
    params: {},
    path: "/",
    name: "all-pictures",
  });
  return {
    useRoute: () => route,
    useRouter: () => ({
      push: vitest.fn(),
      replace: vitest.fn(),
      currentRoute: { value: route },
    }),
  };
});

import SideBar from "./SideBar.vue";
import { useViewStore } from "../../stores/useViewStore";

const ROOT = "/home/me/library/refs";
const SUB = "/home/me/library/refs/2024/summer";

const FOLDER = { id: 5, folder: ROOT, label: "Refs", active: true };

function respond(url) {
  if (String(url).includes("/reference-folders")) {
    return { data: { folders: [FOLDER], in_docker: true } };
  }
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
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return wrapper;
}

/** The payload the sidebar last handed the grid via `select-folder`. */
function lastFolderPayload(wrapper) {
  const emitted = wrapper.emitted("select-folder");
  return emitted ? emitted[emitted.length - 1][0] : null;
}

/** Put the app on `/ref-folder/5`, with or without a `?path=`. */
function routeToFolder(path) {
  useViewStore().view = {
    folderKey: "rf-5",
    folderFilter: path ? { pathPrefix: path, label: path.split("/").pop() } : null,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("restoring /ref-folder/:id", () => {
  it("selects the subfolder the query names, keeping the folder's id", async () => {
    const wrapper = await mountSidebar();
    routeToFolder(SUB);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: SUB,
      label: "summer",
    });

    wrapper.unmount();
  });

  it("selects the folder root when the route names no path", async () => {
    const wrapper = await mountSidebar();
    routeToFolder(null);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });

  it("selects the folder root when the query only restates it", async () => {
    // The root row's own click carries `pathPrefix: folder.folder`, so the URL
    // it pushes repeats the root. That must stay the folder itself - label and
    // highlight key included - not a subfolder that happens to be the root.
    const wrapper = await mountSidebar();
    routeToFolder(`${ROOT}/`);
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });

  it("ignores a path that is not inside this folder", async () => {
    // A `?path=` left over from an "About your library" destination names a
    // folder with no id at all. Applying it under this folder's id would build
    // a grid query for a pair that does not exist.
    const wrapper = await mountSidebar();
    routeToFolder("/home/me/somewhere-else");
    await flushPromises();

    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "Refs",
    });

    wrapper.unmount();
  });
});
