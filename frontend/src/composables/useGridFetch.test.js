import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, reactive } from "vue";
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
// mocked above). `propOverrides` merges extra props into the harness props.
function makeHarness({
  overlayOpen = false,
  selectedSort = "DATE_TAKEN",
  propOverrides = {},
} = {}) {
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

  const props = reactive({
    backendUrl: "http://test",
    selectedSort,
    selectedCharacter: null,
    selectedSet: null,
    searchQuery: "",
    ...propOverrides,
  });

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
  return { grid, refs, startSmartScoreProgress, completeSmartScoreProgress };
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
      propOverrides: { similarityCharacter: "7", selectedDescending: true },
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

  it("trims trailing placeholders when the stream yields fewer rows than the count", async () => {
    const pics = [{ id: 21 }, { id: 22 }, { id: 23 }];
    // Count says 5, stream only ever delivers 3 — the last 2 cells would
    // otherwise remain permanent id-less spinners.
    getPictureCount.mockResolvedValue({ count: 5 });
    streamPictures.mockResolvedValue({ pictures: pics });

    const { grid, refs } = makeHarness({
      selectedSort: "CHARACTER_LIKENESS",
      propOverrides: { similarityCharacter: "7", selectedDescending: true },
    });

    await grid.fetchAllGridImages({ force: true });

    expect(refs.allGridImages.value).toHaveLength(3);
    expect(refs.allGridImages.value.every((img) => img.id != null)).toBe(true);
    // visibleEnd was sized from the count (5) and must be clamped to the
    // trimmed length.
    expect(refs.visibleEnd.value).toBe(3);
    expect(refs.lastFetchedGridImages.value).toHaveLength(3);
  });
});
