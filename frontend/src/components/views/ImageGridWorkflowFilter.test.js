// The *Show all N pictures* chip, held at the one place it can rot (F7, #1409).
//
// `useGridFetch.test.js` proves the param is BUILT. It cannot prove the grid
// ever rebuilds it, because it calls `fetchAllGridImages({force: true})` by
// hand — so it stayed green with the filter missing from `ImageGrid`'s filter
// watcher entirely, which is exactly how it shipped for review.
//
// **Removal is the half with nothing behind it.** Arrival works even with no
// watcher, by accident: `useWorkflowPictures` does `router.push("/")` and
// `ImageGrid` is `v-else` in `App.vue`, so it remounts and fetches anyway. The
// chip's × changes no route, so if the watcher does not name `workflowFilter`
// the strip empties and the grid keeps showing one workflow's pictures. That
// is what this file asserts, on the wire.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";

import { useFilterStore } from "../../stores/useFilterStore.js";

const { dedupStoreMock } = vi.hoisted(() => ({
  dedupStoreMock: { scan: { status: "idle" } },
}));
vi.mock("../../stores/useDedupStore.js", () => ({
  useDedupStore: () => dedupStoreMock,
}));

// One seam for every network call: all `src/api/*` modules go through this
// axios instance, so reading its GETs reads the grid's actual queries.
const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

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
const KEY = "a".repeat(64);

function mountGrid() {
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
    },
  });
}

/** The grid's own list queries since the last clear. */
const gridUrls = () =>
  apiGet.mock.calls
    .map(([url]) => String(url ?? ""))
    .filter(
      (url) =>
        url.includes("/pictures/stream") || url.includes("/pictures/count"),
    );

const settle = async (wrapper) => {
  await wrapper.vm.$nextTick();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
};

beforeEach(() => {
  setActivePinia(createPinia());
  for (const mock of [apiGet, apiPost, apiPatch, apiPut, apiDelete]) {
    mock.mockReset();
  }
  apiGet.mockResolvedValue({ data: { pictures: [], count: 0, total: 0 } });
  apiPost.mockResolvedValue({ data: [] });
  apiPatch.mockResolvedValue({ data: {} });
});

describe("the workflow chip and the grid's filter watcher", () => {
  it("refetches on one workflow's pictures when the chip is set", async () => {
    const wrapper = mountGrid();
    await settle(wrapper);
    apiGet.mockClear();

    useFilterStore().workflowFilter = { key: KEY, name: "Cinematic portrait" };
    await settle(wrapper);

    const urls = gridUrls();
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.every((url) => url.includes(`workflow_key=${KEY}`))).toBe(true);
    wrapper.unmount();
  });

  it("refetches the whole library when the chip is removed", async () => {
    const wrapper = mountGrid();
    await settle(wrapper);
    useFilterStore().workflowFilter = { key: KEY, name: "Cinematic portrait" };
    await settle(wrapper);
    apiGet.mockClear();

    // What the chip's × does.
    useFilterStore().workflowFilter = null;
    await settle(wrapper);

    const urls = gridUrls();
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.some((url) => url.includes("workflow_key"))).toBe(false);
    wrapper.unmount();
  });
});
