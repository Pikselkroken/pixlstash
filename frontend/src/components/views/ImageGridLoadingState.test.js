// The grid must not say "No pictures match the current filters" until a
// fetch has actually answered with nothing. A fetch still in flight, or one
// that failed, says "Loading…" and offers no "Show all pictures".
//
// Mounts the real ImageGrid, following ImageGridLibraryEmptyState.test.js.

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
  const readOnly = makeRef(false);
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
    isReadOnly: makeComputed(() => readOnly.value),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext,
    setRequestClientId: vi.fn(),
    API_BASE_URL: "/api/v1",
    // Reached back through the module so a test can flip the session scope.
    __readOnly: readOnly,
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

function mountGrid() {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const sortStore = useSortStore();
  selectionStore.selectedCharacter = "ALL";
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];
  projectStore.projectViewMode = "global";
  projectStore.selectedProjectId = null;
  sortStore.selectedSort = "DATE";
  sortStore.selectedDescending = true;
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

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const never = () => new Promise(() => {});

function answer({ count, stream }) {
  apiGet.mockImplementation((url) => {
    const u = String(url);
    if (u.includes("/summary")) return Promise.resolve({ data: { image_count: 5 } });
    if (u.includes("/pictures/count")) return count();
    if (u.includes("/pictures/stream")) return stream();
    return Promise.resolve({ data: [] });
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiPost.mockResolvedValue({ data: {} });
});

const COUNT_FIVE = () => Promise.resolve({ data: { count: 5 } });

/** Mount, let the 80 ms first-fetch timer and the 350 ms card delay pass. */
async function settle() {
  const wrapper = mountGrid();
  await wait(800);
  await nextTick();
  return wrapper;
}

const cardText = (wrapper) => wrapper.find(".empty-state").text();
const showAllButton = (wrapper) => wrapper.find(".empty-state-action");

describe("before the grid's fetch has answered", () => {
  it("says Loading while the count is still in flight", async () => {
    answer({ count: never, stream: never });
    const wrapper = await settle();

    expect(cardText(wrapper)).toContain("Loading…");
    expect(cardText(wrapper)).not.toContain("No pictures match");
    expect(showAllButton(wrapper).exists()).toBe(false);
  });

  it("says Loading, not no-match, when the count request fails", async () => {
    answer({ count: () => Promise.reject(new Error("down")), stream: never });
    const wrapper = await settle();

    expect(cardText(wrapper)).toContain("Loading…");
    expect(cardText(wrapper)).not.toContain("No pictures match");
    expect(showAllButton(wrapper).exists()).toBe(false);
  });

  it("says Loading, not no-match, when the stream request fails", async () => {
    answer({ count: COUNT_FIVE, stream: () => Promise.reject(new Error("down")) });
    const wrapper = await settle();

    expect(cardText(wrapper)).toContain("Loading…");
    expect(cardText(wrapper)).not.toContain("No pictures match");
    expect(showAllButton(wrapper).exists()).toBe(false);
  });
});

describe("once the fetch has answered with nothing", () => {
  it("says no pictures match and offers Show all pictures", async () => {
    answer({ count: () => Promise.resolve({ data: { count: 0 } }), stream: never });
    const wrapper = await settle();

    expect(cardText(wrapper)).toContain("No pictures match the current filters");
    expect(cardText(wrapper)).not.toContain("Loading");
    expect(showAllButton(wrapper).exists()).toBe(true);
  });
});
