// The sidebar's Scrapheap context menu must actually open the delete-forever
// confirm, not just navigate.
//
// Reported bug: "Empty Scrapheap" in the sidebar context menu did nothing except
// open the Scrapheap. The sidebar switches the view and then App.vue calls
// ImageGrid.confirmEmptyScrapheap() on the next tick - but the view switch runs
// a PRE-FLUSH watcher that resets the grid (`allGridImages = []`) and sets
// `imagesLoading = true` synchronously, and pre-flush watchers run before
// nextTick callbacks. So the request always landed while `scrapheapEmptyDisabled`
// was true and was dropped on the floor.
//
// The fix is that the confirm gates only on a purge already running, and lets the
// AUTHORITATIVE server preview (POST /pictures/scrapheap/delete-preview) decide
// what is in the heap - the grid's loaded rows can undercount it.
//
// This mounts the REAL ImageGrid.vue and reproduces the exact call ordering the
// sidebar produces. Both directions are asserted: the preview is requested from
// the mid-fetch entry point, and the destructive dialog still stays shut when the
// server says there is nothing to delete (fail-safe is its own regression).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import { ref, nextTick } from "vue";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

// Async factory with a local `await import("vue")`: the store imports above
// pull in apiClient, so this factory runs BEFORE the file's own top-level `vue`
// import has initialised. Closing over that binding throws "Cannot access
// __vi_import__ before initialization".
vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef, computed: makeComputed } = await import("vue");
  const isAuthenticated = makeRef(true);
  const sessionContext = makeRef({ scope: "ALL" });
  return {
    onSessionReset: () => () => {},
    apiClient: {
      get: (...args) => apiGet(...args),
      post: (...args) => apiPost(...args),
      patch: (...args) => apiPatch(...args),
      put: (...args) => apiPut(...args),
      delete: (...args) => apiDelete(...args),
    },
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated,
    isReadOnly: makeComputed(() => false),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext,
    setRequestClientId: vi.fn(),
    API_BASE_URL: "/api/v1",
  };
});

// The `vuetify/components` barrel pulls component CSS that Vitest cannot load
// from node_modules; hand back a trivial stub for whatever name is imported.
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});



vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

import ImageGrid from "./ImageGrid.vue";

const ALL_PICTURES_ID = "ALL";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

/** The authoritative destruction preview the confirm dialog is built from. */
function previewCalls() {
  return apiPost.mock.calls.filter(([url]) =>
    String(url ?? "").includes("/pictures/scrapheap/delete-preview"),
  );
}

// Selection, project and sort state live in the stores now; the old
// `mountGrid({ selectedCharacter: ... })` prop overrides write them instead.
function mountGrid(state = {}) {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const sortStore = useSortStore();
  selectionStore.selectedCharacter = ALL_PICTURES_ID;
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];
  projectStore.projectViewMode = "global";
  projectStore.selectedProjectId = null;
  sortStore.selectedSort = "DATE";
  sortStore.selectedDescending = true;
  for (const [key, value] of Object.entries(state)) {
    if (key in projectStore) projectStore[key] = value;
    else if (key in sortStore) sortStore[key] = value;
    else selectionStore[key] = value;
  }

  return mount(ImageGrid, {
    shallow: true,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
    props: { backendUrl: "/api/v1" },
  });
}

/**
 * Reproduce the sidebar gesture: switch the view to the Scrapheap, then ask for
 * the confirm on the next tick, exactly as SideBar.emptyScrapheapFromCtx() plus
 * App.handleEmptyScrapheapFromSidebar() do.
 *
 * The view-switch fetch is held open (the mocked reads never settle) because
 * landing mid-fetch is the whole point of the reproduction: with mocks that
 * resolve immediately the grid finishes loading inside the awaits above and the
 * bug cannot occur.
 */
async function emptyScrapheapFromSidebar(wrapper) {
  apiGet.mockReturnValue(new Promise(() => {}));
  useSelectionStore().selectedCharacter = SCRAPHEAP_PICTURES_ID;
  await nextTick();
  wrapper.vm.confirmEmptyScrapheap();
  await nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiGet.mockResolvedValue({ data: { pictures: [], count: 0, total: 0 } });
  apiPost.mockResolvedValue({ data: {} });
});

describe('sidebar "Empty Scrapheap" opens the delete-forever confirm', () => {
  it("requests the destruction preview even though the grid is still fetching", async () => {
    apiPost.mockResolvedValue({
      data: {
        confirm_token: "tok-1",
        total_count: 4,
        protected_count: 0,
        unprotected_count: 4,
        locked_count: 0,
        protected: [],
      },
    });
    const wrapper = mountGrid();
    await nextTick();

    await emptyScrapheapFromSidebar(wrapper);

    // The grid really is mid-fetch at this point - the condition that used to
    // swallow the request. If this ever stops holding, the test no longer covers
    // the reported bug.
    expect(wrapper.vm.imagesLoading).toBe(true);
    expect(wrapper.vm.filteredGridCount).toBe(0);
    expect(previewCalls()).toHaveLength(1);
    expect(previewCalls()[0][1]).toEqual({ ids: null });

    // The dialog opens once the preview resolves.
    await vi.waitFor(() => expect(wrapper.vm.deleteForeverOpen).toBe(true));
    expect(wrapper.vm.deleteForeverMode).toBe("all");
    expect(wrapper.vm.deleteForeverTotalCount).toBe(4);

    wrapper.unmount();
  });

  it("keeps the destructive dialog shut when the server reports an empty heap", async () => {
    apiPost.mockResolvedValue({
      data: {
        confirm_token: "tok-2",
        total_count: 0,
        protected_count: 0,
        unprotected_count: 0,
        locked_count: 0,
        protected: [],
      },
    });
    const wrapper = mountGrid();
    await nextTick();

    await emptyScrapheapFromSidebar(wrapper);

    expect(previewCalls()).toHaveLength(1);
    // Authoritative "nothing to delete" - never open a destructive confirm on it.
    await vi.waitFor(() => expect(wrapper.vm.deleteForeverLoading).toBe(false));
    expect(wrapper.vm.deleteForeverOpen).toBe(false);

    wrapper.unmount();
  });

  it("fails safe and does not open the confirm when the preview errors", async () => {
    apiPost.mockRejectedValue(new Error("network down"));
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    const wrapper = mountGrid();
    await nextTick();

    await emptyScrapheapFromSidebar(wrapper);

    await vi.waitFor(() => expect(wrapper.vm.deleteForeverLoading).toBe(false));
    expect(wrapper.vm.deleteForeverOpen).toBe(false);
    expect(apiDelete).not.toHaveBeenCalled();

    consoleError.mockRestore();
    wrapper.unmount();
  });

  it("ignores a second request while a purge is already running", async () => {
    const wrapper = mountGrid({ selectedCharacter: SCRAPHEAP_PICTURES_ID });
    await nextTick();
    apiPost.mockClear();

    wrapper.vm.scrapheapEmptying = true;
    await nextTick();
    wrapper.vm.confirmEmptyScrapheap();
    await nextTick();

    expect(previewCalls()).toHaveLength(0);

    wrapper.unmount();
  });
});
