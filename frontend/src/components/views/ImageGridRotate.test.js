// Rotating from the grid, and the one thing about it that is easy to ship
// broken: the tile.
//
// The rotate itself is a single POST. The refresh afterwards is not: the card's
// thumbnail URL lives behind `POST /pictures/thumbnails` and **is absent from
// `/pictures/{id}/metadata` entirely**, so the per-card metadata refresh the
// grid already had cannot repair a tile on its own.
//
// The 180° case is where a half-refresh shows. A rotate rewrites the file's EXIF
// orientation tag and leaves every pixel — and both dimensions — where they
// were, so nothing derived locally can tell the browser its cached bitmap is now
// upside down. Only the server's own version can, and only if the client actually
// re-reads it. That is what the first test asserts: before vs after, same
// picture id, and the dimensions in the version back where they started.

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
    appendShareToken: (url) => url,
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

// The server's version for picture 42, as the thumbnails endpoint reports it.
// Only the endpoint moves it; nothing in the client may construct one.
let thumbnailVersion = "1600x1200";

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

/** Seed one mounted card, exactly as the grid's own pre-fill leaves it. */
function seedCard(wrapper, overrides = {}) {
  wrapper.vm.allGridImages = [
    {
      id: 42,
      idx: 0,
      format: "jpg",
      imported_at: "2026-08-01T10:00:00",
      // The pre-filled URL: keyed on imported_at, which a rotate never moves.
      thumbnail: "/api/v1/pictures/thumbnails/42.webp?v=1754042400",
      thumbnail_width: 1600,
      thumbnail_height: 1200,
      tags: [],
      ...overrides,
    },
  ];
  wrapper.vm.selectedImageIds = [42];
}

function thumbnailOf(wrapper, id = 42) {
  return wrapper.vm.allGridImages.find((img) => img.id === id)?.thumbnail;
}

/** The rotate requests that reached the wire. */
function rotateCalls() {
  return apiPost.mock.calls
    .filter(([url]) => String(url ?? "").includes("/pictures/rotate"))
    .map(([, body]) => body);
}

beforeEach(() => {
  setActivePinia(createPinia());
  thumbnailVersion = "1600x1200";
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiGet.mockResolvedValue({ data: { pictures: [], count: 0, total: 0 } });
  apiPatch.mockResolvedValue({ data: {} });
  apiPost.mockImplementation(async (url) => {
    const path = String(url ?? "");
    if (path.includes("/pictures/rotate")) {
      return {
        data: {
          rotated_picture_ids: [42],
          unsupported_picture_ids: [],
          skipped_picture_ids: [],
          batch_id: "srv-1",
        },
      };
    }
    if (path.includes("/pictures/thumbnails")) {
      return {
        data: {
          42: {
            thumbnail: `/pictures/thumbnails/42.webp?v=${thumbnailVersion}`,
            thumbnail_width: 1600,
            thumbnail_height: 1200,
          },
        },
      };
    }
    return { data: {} };
  });
});

describe("ImageGrid — rotate in place", () => {
  it("re-reads the thumbnail version when 180° leaves the shape alone", async () => {
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);
    const before = thumbnailOf(wrapper);

    // Two quarter-turns the same way. The dimensions are identical either side
    // of them — the server's orientation component is the only thing that moved.
    thumbnailVersion = "1200x1600o6";
    await wrapper.vm.rotateSelectedPictures("cw");
    thumbnailVersion = "1600x1200o3";
    await wrapper.vm.rotateSelectedPictures("cw");

    const after = thumbnailOf(wrapper);
    expect(rotateCalls()).toEqual([
      { picture_ids: [42], direction: "cw" },
      { picture_ids: [42], direction: "cw" },
    ]);
    // The card's URL genuinely differs, so the browser cannot serve the tile it
    // painted before the rotate. The dimensions in it are back where they
    // started, which is exactly why the version cannot be built from them.
    expect(after).not.toBe(before);
    expect(after).toContain("1600x1200o3");

    wrapper.unmount();
  });

  it("takes the server's version verbatim rather than stamping one", async () => {
    // A client-side buster would work here and defeat thumbnail caching for
    // every other picture in the library. The URL must be the one the server
    // handed over, with nothing appended.
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);

    thumbnailVersion = "1200x1600o6";
    await wrapper.vm.rotateSelectedPictures("ccw");

    expect(thumbnailOf(wrapper)).toBe(
      "/api/v1/pictures/thumbnails/42.webp?v=1200x1600o6",
    );
    wrapper.unmount();
  });

  it("does nothing with an empty selection", async () => {
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    wrapper.vm.selectedImageIds = [];

    await wrapper.vm.rotateSelectedPictures("cw");

    expect(rotateCalls()).toEqual([]);
    wrapper.unmount();
  });

  it("leaves the tile alone when the server rotated nothing", async () => {
    // Every id refused (all of them gone from the library). Re-reading the
    // thumbnails would be a round-trip for a bitmap that did not move.
    apiPost.mockImplementation(async (url) => {
      if (String(url ?? "").includes("/pictures/rotate")) {
        return {
          data: {
            rotated_picture_ids: [],
            unsupported_picture_ids: [],
            skipped_picture_ids: [42],
          },
        };
      }
      return { data: {} };
    });
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);
    const before = thumbnailOf(wrapper);

    await wrapper.vm.rotateSelectedPictures("cw");

    expect(thumbnailOf(wrapper)).toBe(before);
    expect(
      apiPost.mock.calls.filter(([u]) =>
        String(u ?? "").includes("/pictures/thumbnails"),
      ),
    ).toHaveLength(0);
    wrapper.unmount();
  });

  it("refreshes the tile when the lightbox reports a bytes change", async () => {
    // The overlay owns its own picture and rotates it directly; `overlay-change`
    // with `fields.pixels` is how the card behind it learns to re-read.
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);
    const before = thumbnailOf(wrapper);

    thumbnailVersion = "1200x1600o6";
    wrapper.vm.handleOverlayChange({ imageId: 42, fields: { pixels: true } });
    // The handler fans out two awaited reads; let both settle.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(thumbnailOf(wrapper)).not.toBe(before);
    wrapper.unmount();
  });
});
