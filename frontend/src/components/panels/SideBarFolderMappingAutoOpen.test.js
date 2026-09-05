// "Add a library"'s "Yes, build this library" saves a `useFolderMappingStore`
// entry with `mode: "local_import"` and switches the active library, which
// ends in a full page reload (FolderMappingWizard.vue). This is the other side
// of that reload: SideBar has to notice the saved entry and reopen
// FolderMappingWizard on its own, because there is no click left to hang the
// wizard off of - the component that would have received one does not exist
// yet when the entry was saved.
//
// An ordinary reference-folder pending entry must NOT auto-open - that one
// only ever gets the "Finish organising…" row, unchanged, because its scan is
// already running server-side regardless of what this session does.

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
import SideBar from "./SideBar.vue";
import FolderMappingWizard from "../folders/FolderMappingWizard.vue";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import { useLibrariesStore } from "../../stores/useLibrariesStore";

const STORAGE_KEY = "pixlstash.pendingFolderMapping";

function respond() {
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

beforeEach(() => {
  window.localStorage.clear();
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

function activeLibraryAt(path) {
  useLibrariesStore().libraries = [
    { id: 1, name: "lib", path, is_active: true },
  ];
}

describe("a pending local_import entry", () => {
  const entry = {
    taskId: "",
    path: "/home/me/Pictures/Generations",
    label: "Generations",
    mode: "local_import",
  };

  it("auto-opens the wizard, pointed at that entry", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
    activeLibraryAt(entry.path);
    const wrapper = await mountSidebar();

    const wizard = wrapper.findComponent(FolderMappingWizard);
    expect(wizard.exists()).toBe(true);
    expect(wizard.props("open")).toBe(true);
    expect(wizard.props("resume")).toEqual(entry);

    wrapper.unmount();
  });

  it("does not auto-open against a different library", async () => {
    // A stale entry from a folder added and cancelled earlier met a
    // re-attached vault and offered to "set up" a library already set up.
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
    activeLibraryAt("/home/me/Pictures/existing-vault");
    const wrapper = await mountSidebar();

    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );
    expect(useFolderMappingStore().pending).toEqual(entry);

    wrapper.unmount();
  });

  it("does not auto-open while the library list has no path to compare", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
    const wrapper = await mountSidebar();

    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );

    wrapper.unmount();
  });

  it("auto-opens once the matching library loads after mount", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
    const wrapper = await mountSidebar();
    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );

    activeLibraryAt(entry.path + "/");
    await flushPromises();

    const wizard = wrapper.findComponent(FolderMappingWizard);
    expect(wizard.props("open")).toBe(true);
    expect(wizard.props("resume")).toEqual(entry);

    wrapper.unmount();
  });

  it("does not auto-open for a read-only session", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
    activeLibraryAt(entry.path);
    isReadOnly.value = true;
    const wrapper = await mountSidebar();

    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );

    wrapper.unmount();
  });
});

describe("an ordinary reference-folder pending entry", () => {
  it("does not auto-open - only the resume row offers it", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        taskId: "task-99",
        path: "/home/me/Pictures/External",
        label: "External",
        mode: "reference",
      }),
    );
    const wrapper = await mountSidebar();

    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );

    wrapper.unmount();
  });

  it("does not auto-open a legacy entry with no mode field either", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        taskId: "task-1",
        path: "/home/me/Pictures/Old",
        label: "Old",
      }),
    );
    const wrapper = await mountSidebar();

    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );

    wrapper.unmount();
  });
});

describe("no pending entry", () => {
  it("does not open the wizard", async () => {
    const wrapper = await mountSidebar();

    expect(wrapper.findComponent(FolderMappingWizard).props("open")).toBe(
      false,
    );

    wrapper.unmount();
  });
});

describe("the loose-pictures offer for an empty library", () => {
  // App.vue asks twice in one tick (`library-empty`, then `library-loaded`)
  // and holds the telemetry question on the second answer, so the second
  // call must resolve only once the first has decided about the wizard.
  it("resolves every caller only once the wizard has been opened", async () => {
    const path = "/home/me/Pictures";
    activeLibraryAt(path);
    const libraries = useLibrariesStore();
    libraries.hasLoadedSuccessfully = true;
    libraries.canManage = true;
    let answer;
    apiGet.mockImplementation((url) =>
      url.includes("inspect")
        ? new Promise((resolve) => {
            answer = () => resolve({ data: { picture_count: 12 } });
          })
        : Promise.resolve(respond(url)),
    );
    const wrapper = await mountSidebar();

    const first = wrapper.vm.offerLoosePictures();
    let secondSettled = false;
    const second = wrapper.vm.offerLoosePictures().then(() => {
      secondSettled = true;
    });
    await flushPromises();
    expect(secondSettled).toBe(false);
    expect(useFolderMappingStore().wizardOpen).toBe(false);

    answer();
    await Promise.all([first, second]);
    expect(useFolderMappingStore().wizardOpen).toBe(true);
    expect(useFolderMappingStore().wizardResume).toEqual({
      path,
      mode: "local_import",
    });

    wrapper.unmount();
  });
  it("resumes the read the desktop startup screen already finished", async () => {
    // The startup screen reads the folder while the GPU runtime downloads, so
    // the wizard has a completed task to open on. Starting a second read here
    // is what put a progress bar over an empty grid.
    const path = "/home/me/Pictures";
    activeLibraryAt(path);
    const libraries = useLibrariesStore();
    libraries.hasLoadedSuccessfully = true;
    libraries.canManage = true;
    const readResult = { levels: [{ depth: 1, folders: [] }] };
    const takePendingMapping = vi.fn(async () => ({ path, result: readResult }));
    window.pixlstashDesktop = { takePendingMapping };
    const wrapper = await mountSidebar();

    await wrapper.vm.offerLoosePictures();

    expect(takePendingMapping).toHaveBeenCalledTimes(1);
    expect(useFolderMappingStore().wizardResume).toEqual({
      path,
      result: readResult,
      mode: "local_import",
    });

    delete window.pixlstashDesktop;
    wrapper.unmount();
  });

  it("ignores a parked read of some other folder", async () => {
    const path = "/home/me/Pictures";
    activeLibraryAt(path);
    const libraries = useLibrariesStore();
    libraries.hasLoadedSuccessfully = true;
    libraries.canManage = true;
    window.pixlstashDesktop = {
      takePendingMapping: async () => ({
        path: "/home/me/Elsewhere",
        result: { levels: [] },
      }),
    };
    apiGet.mockImplementation((url) =>
      url.includes("inspect")
        ? Promise.resolve({ data: { picture_count: 12 } })
        : Promise.resolve(respond(url)),
    );
    const wrapper = await mountSidebar();

    await wrapper.vm.offerLoosePictures();

    expect(useFolderMappingStore().wizardResume).toEqual({
      path,
      mode: "local_import",
    });

    delete window.pixlstashDesktop;
    wrapper.unmount();
  });
});
