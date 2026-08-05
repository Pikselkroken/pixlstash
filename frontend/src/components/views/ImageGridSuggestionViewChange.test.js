// Navigating away from "Suggest more pictures of <person>" (#636) must take the
// search WITH it, grid included.
//
// Reported bug: changing view made the action pill disappear but left the grid
// showing the suggestions, so the view never actually changed. Cause was
// watcher order. `fetchAllGridImages` picks its fetchMode synchronously (there
// is no await before it reads the search refs), and Vue runs pre-flush watchers
// in creation order, so the view-change watcher that refetches ran FIRST and
// re-issued the face search, and the separate clearing watcher declared ~1700
// lines later then unmounted the pill. Grid full of a search, no bar to explain
// or dismiss it.
//
// The fix folds the clearing into the fetching watcher, ahead of the fetch, so
// this asserts on the wire: after a view change the grid issues its ordinary
// list query and does NOT re-post /pictures/face-search.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useDedupStore } from "../../stores/useDedupStore.js";
import { ref } from "vue";

const { dedupStoreMock } = vi.hoisted(() => ({
  dedupStoreMock: { scan: { status: "idle" } },
}));

vi.mock("../../stores/useDedupStore.js", () => ({
  useDedupStore: () => dedupStoreMock,
}));

// One seam for every network call: all `src/api/*` modules go through this
// axios instance, so counting its GETs counts the grid's actual queries.
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
    apiClient: {
      get: (...args) => apiGet(...args),
      post: (...args) => apiPost(...args),
      patch: (...args) => apiPatch(...args),
      put: (...args) => apiPut(...args),
      delete: (...args) => apiDelete(...args),
    },
    activateShareToken: vi.fn(),
    // Added upstream (#661): components subscribe to session resets, and a
    // mock without it throws on import rather than at the call site.
    onSessionReset: () => () => {},
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
// from node_modules. ImageGrid only needs these to exist as components; a proxy
// hands back a trivial stub for whatever name is imported anywhere in its tree.
vi.mock("vuetify/components", () => {
  const stubs = new Map();
  return new Proxy(
    {},
    {
      get(_target, prop) {
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

/** The grid's ordinary list queries: the signature of a normal view load. */
function gridQueryCount() {
  return apiGet.mock.calls.filter(([url]) => {
    const u = String(url ?? "");
    return u.includes("/pictures/stream") || u.includes("/pictures/count");
  }).length;
}

/** Face-search POSTs, i.e. the suggestion mode actually running. */
function faceSearchCount() {
  return apiPost.mock.calls.filter(([url]) =>
    String(url ?? "").includes("/pictures/face-search"),
  ).length;
}

function mountGrid(props = {}) {
  return mount(ImageGrid, {
    shallow: true,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
    props: {
      backendUrl: "/api/v1",
      allPicturesId: ALL_PICTURES_ID,
      unassignedPicturesId: "UNASSIGNED",
      scrapheapPicturesId: "SCRAPHEAP",
      selectedCharacter: ALL_PICTURES_ID,
      selectedSet: null,
      selectedSetIds: [],
      projectViewMode: "global",
      selectedProjectId: null,
      selectedSort: "DATE",
      selectedDescending: true,
      ...props,
    },
  });
}

/** Arm the suggestion search and let its fetch settle. */
async function armSuggestion(wrapper) {
  wrapper.vm.suggestPicturesForCharacter({ id: 9, name: "Walter" });
  await wrapper.vm.$nextTick();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiGet.mockResolvedValue({ data: { pictures: [], count: 0, total: 0 } });
  apiPost.mockResolvedValue({ data: [] });
  apiPatch.mockResolvedValue({ data: {} });
});

describe("the person suggestion search is dropped by a view change", () => {
  it("arms the search and runs it", async () => {
    // Baseline: without this the other assertions could pass vacuously.
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    apiPost.mockClear();

    await armSuggestion(wrapper);

    expect(faceSearchCount()).toBeGreaterThan(0);
    wrapper.unmount();
  });

  it("stops re-running the search once the view changes", async () => {
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    await armSuggestion(wrapper);

    apiGet.mockClear();
    apiPost.mockClear();
    // The view lives in the selection store now (#661), so changing a prop
    // would fire nothing and every assertion below would pass vacuously.
    useSelectionStore().selectedCharacter = 42;
    await wrapper.vm.$nextTick();
    await new Promise((resolve) => setTimeout(resolve, 0));

    // The bug: this fetch re-issued the face search, because it read
    // faceSearchCharacter before the clearing watcher had run.
    expect(
      faceSearchCount(),
      "the view-change fetch re-ran the person search",
    ).toBe(0);
    // And the new view has to actually load.
    expect(
      gridQueryCount(),
      "the new view issued no list query of its own",
    ).toBeGreaterThan(0);

    wrapper.unmount();
  });

  it("keeps the search when the arming click selects that same person", async () => {
    // Opening the sidebar's person menu can itself select the person, so the
    // watcher compares against the view the search was armed from. Without
    // that, arming would immediately cancel itself.
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    // The menu click selects the person, THEN arms the search, so the armed
    // view snapshot is (9, null) and the watcher that fires for that very
    // selection change must recognise it and keep the search.
    useSelectionStore().selectedCharacter = 9;
    await armSuggestion(wrapper);
    apiPost.mockClear();
    await wrapper.vm.$nextTick();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.vm.searchResultsActive).toBe(true);
    wrapper.unmount();
  });
});

describe("completing the person suggestion search", () => {
  it("returns to the view underneath when the refreshed suggestions are empty", async () => {
    let suggestionRequestCount = 0;
    apiPost.mockImplementation((url) => {
      const requestUrl = String(url ?? "");
      if (requestUrl.includes("/pictures/face-search")) {
        suggestionRequestCount += 1;
        return Promise.resolve({
          data:
            suggestionRequestCount === 1
              ? [
                  {
                    picture_id: 101,
                    face_id: 201,
                    likeness: 0.91,
                    reference_likeness: [0.91],
                  },
                ]
              : [],
        });
      }
      return Promise.resolve({ data: {} });
    });
    apiGet.mockImplementation((url) => {
      const requestUrl = String(url ?? "");
      if (requestUrl.includes("/pictures?id=101")) {
        return Promise.resolve({
          data: [{ id: 101, format: "JPG", width: 100, height: 100 }],
        });
      }
      return Promise.resolve({ data: { pictures: [], count: 0, total: 0 } });
    });

    const wrapper = mountGrid();
    useSelectionStore().selectedCharacter = 42;
    await wrapper.vm.$nextTick();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await armSuggestion(wrapper);

    expect(wrapper.vm.faceSearchAssignIds).toEqual([101]);
    apiGet.mockClear();

    await wrapper.vm.handleAssignFaceSearchResults();

    expect(suggestionRequestCount).toBe(2);
    const assignment = apiPost.mock.calls.find(([url]) =>
      String(url ?? "").includes("/characters/9/faces"),
    );
    expect(assignment?.[1]).toEqual({
      face_assignments: [{ picture_id: 101, face_id: 201 }],
    });
    expect(wrapper.vm.searchResultsActive).toBe(false);
    expect(useSelectionStore().selectedCharacter).toBe(42);
    const gridUrls = apiGet.mock.calls.map(([url]) => String(url ?? ""));
    expect(
      gridUrls.some(
        (url) =>
          (url.includes("/pictures/count") ||
            url.includes("/pictures/stream")) &&
          url.includes("character_id=42"),
      ),
    ).toBe(true);

    wrapper.unmount();
  });
});

describe("character navigation while duplicate scanning continues", () => {
  it("issues the singleton character grid request without waiting for the scan", async () => {
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    apiGet.mockClear();
    useDedupStore().scan = {
      status: "running",
      scanned: 5000,
      total: 12098,
    };

    useSelectionStore().selectedCharacter = 42;
    await wrapper.vm.$nextTick();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const gridUrls = apiGet.mock.calls
      .map(([url]) => String(url ?? ""))
      .filter(
        (url) =>
          url.includes("/pictures/count") || url.includes("/pictures/stream"),
      );
    expect(gridUrls.some((url) => url.includes("character_id=42"))).toBe(true);
    wrapper.unmount();
  });
});
