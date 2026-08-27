// Rotating from the grid, and the one thing about it that is easy to ship
// broken: the tile.
//
// The rotate itself is a single POST. The refresh afterwards is not: the card's
// thumbnail URL lives behind `POST /pictures/thumbnails` and **is absent from
// `/pictures/{id}/metadata` entirely**, so the per-card metadata refresh the
// grid already had cannot repair a tile on its own.
//
// The 180° case is where a half-refresh shows. A rotate rewrites the file's EXIF
// orientation tag and leaves every pixel - and both dimensions - where they
// were, so nothing derived locally can tell the browser its cached bitmap is now
// upside down. Only the server's own version can, and only if the client actually
// re-reads it. That is what the first test asserts: before vs after, same
// picture id, and the dimensions in the version back where they started.

import { afterEach, describe, it, expect, beforeEach, vi } from "vitest";
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

// jsdom never fetches a resource, so an `<img>` handed a src fires neither
// `load` nor `error` and simply never settles. `applyRotatedCards` decodes the
// new bitmap BEFORE it writes anything - that is the whole point of it, and it
// is why the tile's shape and its picture change in the same frame - so without
// a stub every rotate here would sit out its own timeout.
//
// Controllable rather than automatic: `holdBitmaps` is what lets a test observe
// the in-between state and prove the commit really is waiting.
let thumbnailDims = { thumbnail_width: 1600, thumbnail_height: 1200 };
let pendingBitmaps = [];
let holdBitmaps = false;
let RealImage;

function settleBitmaps() {
  const loading = pendingBitmaps;
  pendingBitmaps = [];
  for (const probe of loading) probe.onload?.();
}

/** Let every awaited read, decode and re-render settle. */
async function flushRotate() {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
  if (!holdBitmaps) settleBitmaps();
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
}

beforeEach(() => {
  setActivePinia(createPinia());
  pendingBitmaps = [];
  holdBitmaps = false;
  RealImage = globalThis.Image;
  globalThis.Image = class StubImage {
    set src(value) {
      this._src = value;
      pendingBitmaps.push(this);
    }
    get src() {
      return this._src;
    }
    decode() {
      return Promise.resolve();
    }
  };
  thumbnailVersion = "1600x1200";
  thumbnailDims = { thumbnail_width: 1600, thumbnail_height: 1200 };
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
            ...thumbnailDims,
          },
        },
      };
    }
    return { data: {} };
  });
});

afterEach(() => {
  globalThis.Image = RealImage;
});

describe("ImageGrid - rotate in place", () => {
  it("re-reads the thumbnail version when 180° leaves the shape alone", async () => {
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);
    const before = thumbnailOf(wrapper);

    // Two quarter-turns the same way. The dimensions are identical either side
    // of them - the server's orientation component is the only thing that moved.
    thumbnailVersion = "1200x1600o6";
    const firstTurn = wrapper.vm.rotateSelectedPictures("cw");
    await flushRotate();
    await firstTurn;
    thumbnailVersion = "1600x1200o3";
    const secondTurn = wrapper.vm.rotateSelectedPictures("cw");
    await flushRotate();
    await secondTurn;

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
    const turn = wrapper.vm.rotateSelectedPictures("ccw");
    await flushRotate();
    await turn;

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

  it("writes nothing until the new bitmap is decoded", async () => {
    // The two-paint bug, asserted at its source. A turned picture changes the
    // shape of the packed cell (from `orientation`, via /metadata) and the
    // bitmap inside it (from POST /pictures/thumbnails), and applying each as it
    // arrived is what made a rotate happen twice on screen: the cell flipped to
    // portrait first, stretching the old landscape bitmap into it, and only
    // then did the picture turn.
    //
    // So the card must not move AT ALL while the bitmap is still in flight -
    // not the URL, and not the fields the aspect ratio is derived from.
    holdBitmaps = true;
    // What the server really reports right after a rotate: `apply_orientation`
    // NULLs the stored dimensions to re-queue the bitmap, so the aspect has to
    // fall through to the raw pair turned by the orientation.
    thumbnailDims = { thumbnail_width: null, thumbnail_height: null };
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);
    const before = { ...wrapper.vm.allGridImages[0] };

    thumbnailVersion = "1200x1600o6";
    const turn = wrapper.vm.rotateSelectedPictures("cw");
    await flushRotate();

    // Both reads have answered by now - only the decode is outstanding.
    expect(pendingBitmaps.length).toBe(1);
    const held = wrapper.vm.allGridImages[0];
    expect(held.thumbnail).toBe(before.thumbnail);
    expect(held.thumbnail_width).toBe(before.thumbnail_width);
    expect(held.thumbnail_height).toBe(before.thumbnail_height);
    expect(held.orientation).toBe(before.orientation);

    settleBitmaps();
    await turn;
    await wrapper.vm.$nextTick();

    const after = wrapper.vm.allGridImages[0];
    expect(after.thumbnail).toContain("1200x1600o6");
    // Landed together: the server reports no dimensions until the background
    // regeneration runs, so the aspect falls through to the raw pair turned by
    // the orientation - the shape the regenerated bitmap will have.
    expect(after.thumbnail_width).toBeNull();
    expect(after.thumbnail_height).toBeNull();
    wrapper.unmount();
  });

  it("marks the tile in flight, with the direction it was asked to turn", async () => {
    // The gesture has no dialog and no confirmation, and the commit is now
    // deliberately a beat late (it waits for the decode above). Something has to
    // say the click was heard, and it says WHICH WAY, because that is the only
    // part of an instant, unconfirmed action a user cannot otherwise check.
    holdBitmaps = true;
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);

    const turn = wrapper.vm.rotateSelectedPictures("ccw");
    await flushRotate();
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.rotatingIconFor({ id: 42 })).toBe("mdi-file-rotate-left");

    settleBitmaps();
    await turn;
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.rotatingIconFor({ id: 42 })).toBeNull();
    wrapper.unmount();
  });

  it("clears the in-flight mark when the rotate fails", async () => {
    // Otherwise a failed gesture leaves the tile scrimmed for the rest of the
    // session, which reads as "still working" for something that has stopped.
    apiPost.mockImplementation(async (url) => {
      if (String(url ?? "").includes("/pictures/rotate")) {
        throw new Error("nope");
      }
      return { data: {} };
    });
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    seedCard(wrapper);

    await wrapper.vm.rotateSelectedPictures("cw");
    expect(wrapper.vm.rotatingIconFor({ id: 42 })).toBeNull();
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
    await flushRotate();

    expect(thumbnailOf(wrapper)).not.toBe(before);
    wrapper.unmount();
  });
});
