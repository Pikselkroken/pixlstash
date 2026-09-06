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
import { useSidebarStore } from "../../stores/useSidebarStore";

const ROOT = "/home/me/library/refs";
const SUB = "/home/me/library/refs/2024/summer";
// A second folder, spelled the way a Windows server's listing spells it.
const WIN_ROOT = "D:\\library\\refs";

// `status: active` with no `last_scanned` is what makes `selectedFolderScanning`
// true while this folder is selected, which is how a test sees the sidebar's
// selection without reaching into the component: it reaches
// `sidebarStore.folderScanning`, and it is driven by `selectedFolderReferenceId`
// - the half of the selection the teardown below has to clear.
const FOLDER = {
  id: 5,
  folder: ROOT,
  label: "Refs",
  active: true,
  status: "active",
  last_scanned: null,
};

const WIN_FOLDER = { id: 6, folder: WIN_ROOT, label: "Win refs", active: true };

function respond(url) {
  if (String(url).includes("/reference-folders")) {
    return { data: { folders: [FOLDER, WIN_FOLDER], in_docker: true } };
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

/** Put the app on `/ref-folder/<id>`, with or without a `?path=`. */
function routeToFolder(path, folderKey = "rf-5") {
  useViewStore().view = {
    folderKey,
    folderFilter: path
      ? { pathPrefix: path, label: path.split(/[/\\]/).pop() }
      : null,
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

  // The teardown at the top of the same watcher compares the sidebar's own
  // selection key against the route key it is leaving. Those two stopped being
  // the same string once a subfolder could be selected: the sidebar holds
  // `path-<abs path>` and the route only ever said `rf-5`, so the equality test
  // read "not mine" and left the highlight - and the scanning light behind
  // `selectedFolderReferenceId` - stuck on a folder the app had navigated away
  // from. Found by review on this branch; it applies to a hand-clicked
  // subfolder as much as to a restored one, which is why the guard compares on
  // the folder the selection belongs to rather than on the key.
  it("clears the selection when the route leaves a restored subfolder", async () => {
    const wrapper = await mountSidebar();
    const sidebarStore = useSidebarStore();
    routeToFolder(SUB);
    await flushPromises();
    expect(sidebarStore.folderScanning).toBe(true);

    useViewStore().view = null; // the route left every folder view
    await flushPromises();

    expect(sidebarStore.folderScanning).toBe(false);

    wrapper.unmount();
  });

  it("still clears the selection when the route leaves a folder root", async () => {
    const wrapper = await mountSidebar();
    const sidebarStore = useSidebarStore();
    routeToFolder(null);
    await flushPromises();
    expect(sidebarStore.folderScanning).toBe(true);

    useViewStore().view = null;
    await flushPromises();

    expect(sidebarStore.folderScanning).toBe(false);

    wrapper.unmount();
  });

  it("matches a subfolder whose separators disagree with the folder's", async () => {
    // The folder listing and the `?path=` come from the same server and
    // normally agree, but the query half is a URL: it gets shared, edited and
    // pasted. A `/` where the listing said `\` used to read as "not inside
    // this folder" and drop the reader back to the folder root.
    const slashed = `${WIN_ROOT.replace(/\\/g, "/")}/2024/summer`;
    const wrapper = await mountSidebar();
    routeToFolder(slashed, "rf-6");
    await flushPromises();

    // The payload keeps the URL's own spelling - only the comparison that got
    // it here was normalised.
    expect(lastFolderPayload(wrapper)).toEqual({
      referenceFolderId: 6,
      pathPrefix: slashed,
      label: "summer",
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
