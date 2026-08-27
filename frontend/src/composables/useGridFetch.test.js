import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, reactive } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { useSortStore } from "../stores/useSortStore.js";
import { useFilterStore } from "../stores/useFilterStore.js";
import { useSelectionStore } from "../stores/useSelectionStore.js";
import { useDedupStore } from "../stores/useDedupStore.js";
import { useGridFetch } from "./useGridFetch.js";
import { getPictureCount, streamPictures } from "../api/pictures";

// Mock the pictures API module so streaming-path tests can control the count
// and stream responses. The overlay-defer test below returns before any
// network call, so the mock is inert there.
vi.mock("../api/pictures", () => ({
  getPictureCount: vi.fn(),
  streamPictures: vi.fn(),
  getLikenessGroups: vi.fn(),
  faceSearch: vi.fn(),
  likenessSearch: vi.fn(),
  searchPictures: vi.fn(),
  listPicturesByIds: vi.fn(),
}));

// Build a minimal harness for useGridFetch. Covers the overlay-defer path
// (returns before any network call) and the streaming path (count + stream
// mocked above). Sort, selection and filter state come from the stores;
// `storeOverrides` writes them.
function makeHarness({
  overlayOpen = false,
  selectedSort = "DATE_TAKEN",
  storeOverrides = {},
} = {}) {
  // The composable reads the query's filter, sort and selection facets
  // straight from the stores, so every harness needs a live Pinia.
  setActivePinia(createPinia());
  const startSmartScoreProgress = vi.fn();
  const completeSmartScoreProgress = vi.fn();

  const refs = {
    allGridImages: ref([]),
    lastFetchedGridImages: ref([]),
    scrollWrapper: ref(null),
    preserveScrollOnNextFetch: ref(false),
    pendingScrollTop: ref(null),
    overlayOpen: ref(overlayOpen),
    pendingGridImages: ref(null),
    pendingOverlayGridRefresh: ref(false),
    visibleStart: ref(0),
    visibleEnd: ref(0),
    divisibleViewWindow: ref(40),
    initialRender: ref(false),
    rowHeight: ref(128),
    sharedPictureIds: ref(new Set()),
    guestConsentState: ref(null),
    guestSessionId: ref(null),
    highlightNextFetch: ref(false),
    hasLoadedOnce: ref(false),
    previousImageIds: new Set(),
    normalizedSelectedCharacterIds: ref([]),
    normalizedSelectedSetIds: ref([]),
    hasSetSelection: ref(false),
    isSetOverlapView: ref(false),
    isMultiCharacterView: ref(false),
    primarySelectedSetId: ref(null),
    smartScoreProgress: reactive({ visible: false, percent: 0, message: "" }),
    exportProgress: reactive({ visible: false, percent: 0, message: "" }),
    reverseImageSearchPictureIds: ref([]),
    faceLikenessSearchFaceId: ref(null),
  };

  const sortStore = useSortStore();
  sortStore.selectedSort = selectedSort;
  for (const [key, value] of Object.entries(storeOverrides)) {
    sortStore[key] = value;
  }

  const props = reactive({ backendUrl: "http://test" });

  const callbacks = {
    collapseStackImages: (x) => x,
    mapGridImages: (x) => x,
    syncExpandAllStacksFromFetchedImages: vi.fn(),
    refreshExpandedStacksAfterFetch: vi.fn(),
    resetThumbnailState: vi.fn(),
    triggerNewImageHighlight: vi.fn(),
    updateVisibleThumbnails: vi.fn(),
    fetchThumbnailsBatch: vi.fn(),
    maybeRefreshOverlayForComfyui: vi.fn(),
    startSmartScoreProgress,
    completeSmartScoreProgress,
    onGridFetchStart: vi.fn(),
    onGridVisibleMetadataReady: vi.fn(),
    onGridFetchDone: vi.fn(),
  };

  const grid = useGridFetch(refs, props, callbacks);
  return {
    grid,
    refs,
    callbacks,
    startSmartScoreProgress,
    completeSmartScoreProgress,
  };
}

describe("useGridFetch sort-progress lifecycle", () => {
  it("dismisses the sort progress bar when a sorted fetch is deferred for an open overlay", async () => {
    const { grid, refs, startSmartScoreProgress, completeSmartScoreProgress } =
      makeHarness({ overlayOpen: true });

    await grid.fetchAllGridImages({ force: true, showProgress: true });

    // The bar was started…
    expect(startSmartScoreProgress).toHaveBeenCalledTimes(1);
    // …the refresh was deferred to overlay-close…
    expect(refs.pendingOverlayGridRefresh.value).toBe(true);
    // …and crucially the bar was dismissed instead of being stranded forever.
    expect(completeSmartScoreProgress).toHaveBeenCalledTimes(1);
  });
});

describe("useGridFetch streaming path", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sends sort, descending and reference_character_id on the count request for CHARACTER_LIKENESS", async () => {
    const pics = [{ id: 11 }, { id: 12 }, { id: 13 }];
    getPictureCount.mockResolvedValue({ count: pics.length });
    streamPictures.mockResolvedValue({ pictures: pics });

    const { grid } = makeHarness({
      selectedSort: "CHARACTER_LIKENESS",
      storeOverrides: {
        selectedSimilarityCharacter: "7",
        selectedDescending: true,
      },
    });

    await grid.fetchAllGridImages({ force: true });

    expect(getPictureCount).toHaveBeenCalledTimes(1);
    const countQuery = getPictureCount.mock.calls[0][0];
    // The count must run over the same row set as the stream: for
    // CHARACTER_LIKENESS the sort changes which rows exist at all.
    expect(countQuery).toContain("sort=CHARACTER_LIKENESS");
    expect(countQuery).toContain("descending=true");
    expect(countQuery).toContain("reference_character_id=7");
  });

  it("issues a singleton character fetch while a duplicate scan is running", async () => {
    getPictureCount.mockResolvedValue({ count: 1 });
    streamPictures.mockResolvedValue({
      pictures: [{ id: 17, character_id: 7 }],
      done: true,
      next_offset: 1,
    });

    const { grid, refs, callbacks } = makeHarness();
    useSelectionStore().selectedCharacter = 7;
    // The duplicate store remains alive after leaving its destination. Its
    // progress must never become a fetch gate for the newly mounted grid.
    useDedupStore().scan = {
      status: "running",
      scanned: 5000,
      total: 12098,
    };

    await grid.fetchAllGridImages({ force: true });

    expect(getPictureCount).toHaveBeenCalledTimes(1);
    expect(getPictureCount.mock.calls[0][0]).toContain("character_id=7");
    expect(streamPictures).toHaveBeenCalledTimes(1);
    expect(streamPictures.mock.calls[0][0]).toContain("character_id=7");
    expect(refs.allGridImages.value.map((picture) => picture.id)).toEqual([17]);
    expect(callbacks.fetchThumbnailsBatch).toHaveBeenCalledWith(0, 1, {
      reason: "initial-visible-prefetch",
    });
  });

  it("trims trailing placeholders when the stream yields fewer rows than the count", async () => {
    const pics = [{ id: 21 }, { id: 22 }, { id: 23 }];
    // Count says 5, stream only ever delivers 3 - the last 2 cells would
    // otherwise remain permanent id-less spinners.
    getPictureCount.mockResolvedValue({ count: 5 });
    streamPictures.mockResolvedValue({ pictures: pics });

    const { grid, refs } = makeHarness({
      selectedSort: "CHARACTER_LIKENESS",
      storeOverrides: {
        selectedSimilarityCharacter: "7",
        selectedDescending: true,
      },
    });

    await grid.fetchAllGridImages({ force: true });

    expect(refs.allGridImages.value).toHaveLength(3);
    expect(refs.allGridImages.value.every((img) => img.id != null)).toBe(true);
    // visibleEnd was sized from the count (5) and must be clamped to the
    // trimmed length.
    expect(refs.visibleEnd.value).toBe(3);
    expect(refs.lastFetchedGridImages.value).toHaveLength(3);
  });

  // The Stacks segments moved the store and the grid refetched, but the default
  // grid path built its own param set and left stack_state out of it, so the
  // same rows came back and the control looked dead. Assert the param on both
  // the stream and the count: they must run over the same row set.
  it("sends stack_state on the stream and count requests when the Stacks filter is set", async () => {
    const pics = [{ id: 31 }, { id: 32 }];
    getPictureCount.mockResolvedValue({ count: pics.length });
    streamPictures.mockResolvedValue({ pictures: pics });

    const { grid } = makeHarness();
    useFilterStore().stackStateFilter = "unresolved";

    await grid.fetchAllGridImages({ force: true });

    expect(streamPictures).toHaveBeenCalled();
    expect(streamPictures.mock.calls[0][0]).toContain("stack_state=unresolved");
    expect(getPictureCount).toHaveBeenCalled();
    expect(getPictureCount.mock.calls[0][0]).toContain(
      "stack_state=unresolved",
    );
  });

  // "all" is the absence of the filter, not a value the backend knows.
  it("omits stack_state when the Stacks filter is on Any", async () => {
    getPictureCount.mockResolvedValue({ count: 1 });
    streamPictures.mockResolvedValue({ pictures: [{ id: 41 }] });

    const { grid } = makeHarness();
    useFilterStore().stackStateFilter = "all";

    await grid.fetchAllGridImages({ force: true });

    expect(streamPictures.mock.calls[0][0]).not.toContain("stack_state");
    expect(getPictureCount.mock.calls[0][0]).not.toContain("stack_state");
  });
});
