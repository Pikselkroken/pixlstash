// The grid's thumbnail URL has exactly one author: the server.
//
// `fetchThumbnailsBatch` pre-fills `/pictures/thumbnails/<id>.webp?v=<imported_at>`
// synchronously so a tile paints before the thumbnails POST answers. That
// optimisation is legitimate and is asserted here. What was not legitimate is
// that the pre-fill then *won*: `missingThumbIds` was computed AFTER the
// pre-fill had set `thumbnail`, so every card with an `imported_at` was filtered
// out of it and the server's URL was never applied to it.
//
// `imported_at` never moves again for the life of a picture, so the consequence
// was general and much larger than any one feature: no regenerated thumbnail
// ever repaints in the grid. Not the upgrade NULL-reset in
// `thumbnail_generation_task.py`, not a reference-folder source swap, not an
// in-place rotate. `ImageUtils.thumbnail_cache_version` is documented as the
// single source of truth that makes a regenerated bitmap refetch, and the grid
// was discarding it.
//
// The fix is one rule: whenever the batch response carries a URL for a card,
// that URL is the card's URL. The placeholder is a stand-in until the answer
// arrives, never a replacement for it.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef, computed: makeComputed } = await import("vue");
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
    // Not the identity here: a share token has to reach whichever URL the card
    // ends up with, and identity would hide a path that forgot to apply it.
    appendShareToken: (url) => `${url}&st=tok`,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeComputed(() => false),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext: makeRef({ scope: "ALL" }),
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

// The server's cache version for picture 7. Only the endpoint moves it.
let thumbnailVersion = "1600x1200";
// When true the thumbnails POST hangs until the test resolves it, which is how
// "the tile painted before the answer arrived" is observable at all.
let heldThumbnailPost = null;
let holdThumbnailPost = false;
// When true the server reports no thumbnail for the picture (still generating).
let serverHasThumbnail = true;
let thumbnailPostCount = 0;

function thumbnailsPayload() {
  if (!serverHasThumbnail) return {};
  return {
    7: {
      thumbnail: `/pictures/thumbnails/7.webp?v=${thumbnailVersion}`,
      thumbnail_width: 1600,
      thumbnail_height: 1200,
    },
  };
}

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

/** Let the mount's own fetches settle before the batch under test runs. */
async function settle(wrapper) {
  await wrapper.vm.$nextTick();
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

/**
 * One card, as the grid holds it before any thumbnail work: a real
 * `imported_at` (the overwhelmingly common case) and no URL yet.
 */
function seedCard(wrapper, overrides = {}) {
  wrapper.vm.allGridImages = [
    {
      id: 7,
      idx: 0,
      format: "jpg",
      imported_at: "2026-08-01T10:00:00",
      thumbnail: null,
      tags: [],
      ...overrides,
    },
  ];
  thumbnailPostCount = 0;
}

function thumbnailOf(wrapper, id = 7) {
  return wrapper.vm.allGridImages.find((img) => img.id === id)?.thumbnail;
}

beforeEach(() => {
  setActivePinia(createPinia());
  thumbnailVersion = "1600x1200";
  heldThumbnailPost = null;
  holdThumbnailPost = false;
  serverHasThumbnail = true;
  thumbnailPostCount = 0;
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiGet.mockResolvedValue({ data: { pictures: [], count: 0, total: 0 } });
  apiPatch.mockResolvedValue({ data: {} });
  apiPost.mockImplementation(async (url) => {
    if (String(url ?? "").includes("/pictures/thumbnails")) {
      thumbnailPostCount += 1;
      if (holdThumbnailPost) {
        return new Promise((resolve) => {
          heldThumbnailPost = () => resolve({ data: thumbnailsPayload() });
        });
      }
      return { data: thumbnailsPayload() };
    }
    return { data: {} };
  });
});

describe("ImageGrid - the server owns the thumbnail URL", () => {
  it("takes the server's URL even though the pre-fill already painted one", async () => {
    // THE BUG. `imported_at` is present, so the pre-fill runs - and before the
    // fix that alone was enough to keep the server's answer out of the card for
    // the rest of its life.
    const wrapper = mountGrid();
    await settle(wrapper);
    seedCard(wrapper);

    await wrapper.vm.fetchThumbnailsBatch(0, 1);

    expect(thumbnailOf(wrapper)).toBe(
      "/api/v1/pictures/thumbnails/7.webp?v=1600x1200&st=tok",
    );
    wrapper.unmount();
  });

  it("repaints when the server's version moves under a card that already has one", async () => {
    // The regeneration case, which is what the version exists for: a rebuilt
    // bitmap (upgrade NULL-reset, source swap, in-place rotate) keeps the same
    // id and the same URL path, and only `?v=` says the bytes changed.
    const wrapper = mountGrid();
    await settle(wrapper);
    seedCard(wrapper);

    await wrapper.vm.fetchThumbnailsBatch(0, 1);
    const before = thumbnailOf(wrapper);

    thumbnailVersion = "1200x1600o6";
    await wrapper.vm.fetchThumbnailsBatch(0, 1, { force: true });

    expect(thumbnailOf(wrapper)).not.toBe(before);
    expect(thumbnailOf(wrapper)).toBe(
      "/api/v1/pictures/thumbnails/7.webp?v=1200x1600o6&st=tok",
    );
    wrapper.unmount();
  });

  it("still paints a tile before the POST answers", async () => {
    // The pre-fill is the reason the grid does not sit blank for a round trip.
    // Keep it: the fix is about who wins afterwards, not about removing it.
    const wrapper = mountGrid();
    await settle(wrapper);
    seedCard(wrapper);

    holdThumbnailPost = true;
    const batch = wrapper.vm.fetchThumbnailsBatch(0, 1);

    // Synchronously, with the request still in flight.
    expect(thumbnailOf(wrapper)).toMatch(
      /^\/api\/v1\/pictures\/thumbnails\/7\.webp\?v=\d+&st=tok$/,
    );

    heldThumbnailPost();
    await batch;

    // ...and the answer replaces it once it lands.
    expect(thumbnailOf(wrapper)).toBe(
      "/api/v1/pictures/thumbnails/7.webp?v=1600x1200&st=tok",
    );
    wrapper.unmount();
  });

  it("keeps the placeholder when the server has no thumbnail yet", async () => {
    // A picture still being processed reports no URL. Blanking the tile it just
    // painted would be a regression in the other direction, and the missing-
    // thumbnail retry only fires for cards that never got one at all.
    serverHasThumbnail = false;
    const wrapper = mountGrid();
    await settle(wrapper);
    seedCard(wrapper);

    await wrapper.vm.fetchThumbnailsBatch(0, 1);

    expect(thumbnailOf(wrapper)).toMatch(
      /^\/api\/v1\/pictures\/thumbnails\/7\.webp\?v=\d+&st=tok$/,
    );
    wrapper.unmount();
  });

  it("does not re-fetch a range it has already loaded", async () => {
    // Applying the server URL changes `allGridImages`, and the range bookkeeping
    // must not read that as work still outstanding.
    const wrapper = mountGrid();
    await settle(wrapper);
    seedCard(wrapper);

    await wrapper.vm.fetchThumbnailsBatch(0, 1);
    expect(thumbnailPostCount).toBe(1);

    await wrapper.vm.fetchThumbnailsBatch(0, 1);
    await wrapper.vm.fetchThumbnailsBatch(0, 1);
    await new Promise((r) => setTimeout(r, 0));

    expect(thumbnailPostCount).toBe(1);
    wrapper.unmount();
  });
});
