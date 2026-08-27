// The stack badge after a lifecycle change, in both directions.
//
// Reported bug: "Keep cover only" collapsed a stack of five to its cover and the
// surviving cover went on rendering a stack badge of five, forever. `stack_count`
// is DERIVED - the server computes it per stack over LIVE members in the listing
// projection, and `GET /pictures/{id}/metadata` does not carry it at all - so the
// per-card `refreshGridImage` every other realtime branch uses cannot repair a
// badge. `refreshStackFacets` is the read that can.
//
// The constraint that shapes it: `debouncedFetchAllGridImages()` would fix the
// badge and destroy the feature, because a refetch rebuilds the grid without the
// scrapheaped copies and takes the ghosted tiles - and with them the one-click
// undo they advertise - off the screen. So this asserts the badge in both
// directions AND that no grid refetch is issued and no ghost window is closed.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref, computed } from "vue";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import { useOperationStore } from "../../stores/useOperationStore.js";
import { getStackBadgeCount, shouldShowStackBadge } from "../../utils/stack.js";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

vi.mock("../../utils/apiClient", () => {
  const isAuthenticated = ref(true);
  const sessionContext = ref({ scope: "ALL" });
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
    isReadOnly: computed(() => false),
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
const COVER_ID = 101;
const COPY_IDS = [102, 103, 104, 105];
const STACK_ID = 7;

/** The grid as it looks before the collapse: one 5-deep stack, one loose card. */
function seededPictures() {
  return [
    {
      id: COVER_ID,
      stack_id: STACK_ID,
      stack_position: 0,
      stack_count: 5,
      score: 3,
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: 200,
      stack_id: null,
      stack_count: 0,
      score: 2,
      created_at: "2026-01-02T00:00:00Z",
    },
  ];
}

/** What `refreshStackFacets` reads: one row per stack, carrying its live count. */
function stackRow(count) {
  return [
    { id: COVER_ID, stack_id: STACK_ID, stack_position: 0, stack_count: count },
  ];
}

/** The grid's own list queries - the signature of a full reload. */
function gridQueryCount() {
  return apiGet.mock.calls.filter(([url]) => {
    const u = String(url ?? "");
    return u.includes("/pictures/stream") || u.includes("/pictures/count");
  }).length;
}

function cardFor(vm, id) {
  return (vm.allGridImages || []).find((img) => Number(img?.id) === id);
}

/**
 * Mount the REAL ImageGrid and seed it through its own imperative insert path,
 * which is the same `fields=grid` listing the streaming fetch uses - so the
 * mounted cards carry exactly the shape the server sends, `stack_count`
 * included.
 */
async function mountGrid() {
  const selectionStore = useSelectionStore();
  const sortStore = useSortStore();
  selectionStore.selectedCharacter = ALL_PICTURES_ID;
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];
  sortStore.selectedSort = "DATE";
  sortStore.selectedDescending = true;

  const wrapper = mount(ImageGrid, {
    shallow: true,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
      // The shallow stub has no `maybeRefreshOverlayForComfyui`, which the
      // insert path calls through a template ref; without it the call rejects
      // and vitest reports an unhandled error next to a passing assertion.
      stubs: {
        ComfyUiRunner: {
          template: "<div />",
          methods: { maybeRefreshOverlayForComfyui: () => {} },
        },
      },
    },
    props: { backendUrl: "/api/v1" },
  });
  for (let i = 0; i < 4; i += 1) await wrapper.vm.$nextTick();
  await wrapper.vm.insertGridImagesById([COVER_ID, 200]);
  for (let i = 0; i < 4; i += 1) await wrapper.vm.$nextTick();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiGet.mockImplementation((url) => {
    const u = String(url ?? "");
    if (u.includes("/pictures/count"))
      return Promise.resolve({ data: { count: 2 } });
    if (u.includes("/pictures/stream")) {
      return Promise.resolve({ data: { pictures: [], done: true } });
    }
    if (u.includes("/pictures?")) {
      return Promise.resolve({ data: seededPictures() });
    }
    return Promise.resolve({ data: [] });
  });
  apiPost.mockResolvedValue({ data: {} });
});

describe("the stack badge after a collapse and after its undo", () => {
  it("drops the badge when the cover is the only live member left", async () => {
    const wrapper = await mountGrid();
    const before = cardFor(wrapper.vm, COVER_ID);
    expect(
      before,
      "the grid must be seeded with the stack leader",
    ).toBeTruthy();
    expect(shouldShowStackBadge(before)).toBe(true);
    expect(getStackBadgeCount(before)).toBe(5);

    apiGet.mockClear();
    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      // The collapse moved four copies to the Scrapheap; only the cover is live.
      if (u.includes("/pictures?"))
        return Promise.resolve({ data: stackRow(1) });
      return Promise.resolve({ data: [] });
    });

    await wrapper.vm.refreshStackFacets([COVER_ID]);
    await wrapper.vm.$nextTick();

    const after = cardFor(wrapper.vm, COVER_ID);
    expect(getStackBadgeCount(after)).toBe(1);
    // `shouldShowStackBadge` is exactly what the template's `v-if` evaluates.
    expect(shouldShowStackBadge(after)).toBe(false);
    // Loose neighbours are untouched: this patches stacks, not the grid.
    expect(cardFor(wrapper.vm, 200)).toBeTruthy();

    wrapper.unmount();
  });

  it("brings the badge back after an undo, from the restored copies' own ids", async () => {
    const wrapper = await mountGrid();

    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      if (u.includes("/pictures?"))
        return Promise.resolve({ data: stackRow(1) });
      return Promise.resolve({ data: [] });
    });
    await wrapper.vm.refreshStackFacets([COVER_ID]);
    await wrapper.vm.$nextTick();
    expect(shouldShowStackBadge(cardFor(wrapper.vm, COVER_ID))).toBe(false);

    // The undo announces the pictures it did NOT move (the cover), but the read
    // resolves per STACK, so the restored copies' own ids repair the cover too.
    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      if (u.includes("/pictures?"))
        return Promise.resolve({ data: stackRow(5) });
      return Promise.resolve({ data: [] });
    });
    await wrapper.vm.refreshStackFacets(COPY_IDS);
    await wrapper.vm.$nextTick();

    const back = cardFor(wrapper.vm, COVER_ID);
    expect(shouldShowStackBadge(back)).toBe(true);
    expect(getStackBadgeCount(back)).toBe(5);

    wrapper.unmount();
  });

  it("never refetches the grid, so a live ghost window keeps its tiles", async () => {
    const wrapper = await mountGrid();
    const operationStore = useOperationStore();
    // The copies are ghosted: their tiles stay on screen while undo is one click
    // away, and the receipt's clock owns that window.
    expect(operationStore.markGhosted(COPY_IDS)).toBe(true);

    apiGet.mockClear();
    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      if (u.includes("/pictures?"))
        return Promise.resolve({ data: stackRow(1) });
      return Promise.resolve({ data: [] });
    });

    await wrapper.vm.refreshStackFacets([COVER_ID]);
    await wrapper.vm.$nextTick();

    // A grid refetch is the thing that would end the window early.
    expect(
      gridQueryCount(),
      `grid refetched: ${JSON.stringify(apiGet.mock.calls.map(([u]) => u))}`,
    ).toBe(0);
    // And nothing was handed to the grid for collapse: the window is still open
    // and the receipt's clock, not this read, decides when it closes.
    expect(operationStore.collapsingPictureIds).toEqual([]);
    // Exactly one read, for the covers the announcement named.
    expect(apiGet.mock.calls.length).toBe(1);

    wrapper.unmount();
  });

  it("leaves a ghost set alone whose tiles are mounted in this view", async () => {
    // The stack-facet patch does rebuild the grid (that is how a card's derived
    // `stackCount` is re-derived), and the ghost machine treats a rebuild that
    // LOSES a ghosted tile as "this view no longer holds it". A ghost still
    // mounted must therefore survive the rebuild untouched, ghosted, with its
    // undo still on the receipt's clock. Ghosting a mounted card is the direct
    // way to assert that; an expanded stack's copies are the same case, and an
    // unexpanded stack's copies are not mounted at all, so there is nothing on
    // screen for the window to hold.
    const wrapper = await mountGrid();
    const operationStore = useOperationStore();
    expect(operationStore.markGhosted([200])).toBe(true);

    apiGet.mockClear();
    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      if (u.includes("/pictures?"))
        return Promise.resolve({ data: stackRow(1) });
      return Promise.resolve({ data: [] });
    });

    await wrapper.vm.refreshStackFacets([COVER_ID]);
    for (let i = 0; i < 4; i += 1) await wrapper.vm.$nextTick();

    // Still ghosted, still mounted, still nothing queued for collapse.
    expect(operationStore.ghostPictureIds).toEqual([200]);
    expect(operationStore.collapsingPictureIds).toEqual([]);
    expect(cardFor(wrapper.vm, 200)).toBeTruthy();
    // And the badge on the cover is gone all the same.
    expect(shouldShowStackBadge(cardFor(wrapper.vm, COVER_ID))).toBe(false);

    wrapper.unmount();
  });

  it("does not touch the grid at all when the count has not moved", async () => {
    const wrapper = await mountGrid();
    apiGet.mockClear();
    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      if (u.includes("/pictures?"))
        return Promise.resolve({ data: stackRow(5) });
      return Promise.resolve({ data: [] });
    });

    const before = wrapper.vm.allGridImages;
    await wrapper.vm.refreshStackFacets([COVER_ID]);
    await wrapper.vm.$nextTick();

    // Same array identity: no rebuild, so nothing watching `allGridImages` reads
    // this as "the grid changed under you" (the ghost machine is one of them).
    expect(wrapper.vm.allGridImages).toBe(before);
    expect(getStackBadgeCount(cardFor(wrapper.vm, COVER_ID))).toBe(5);

    wrapper.unmount();
  });

  it("leaves the badge alone when the read fails, and says so", async () => {
    const wrapper = await mountGrid();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    apiGet.mockImplementation((url) => {
      const u = String(url ?? "");
      if (u.includes("/pictures?")) return Promise.reject(new Error("offline"));
      return Promise.resolve({ data: [] });
    });

    await wrapper.vm.refreshStackFacets([COVER_ID]);
    await wrapper.vm.$nextTick();

    // A failed read must not invent a count. Stale is recoverable; wrong is not.
    expect(getStackBadgeCount(cardFor(wrapper.vm, COVER_ID))).toBe(5);
    expect(errors).toHaveBeenCalled();
    errors.mockRestore();

    wrapper.unmount();
  });
});
