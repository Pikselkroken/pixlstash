import { describe, it, expect, vi } from "vitest";
import { ref, reactive } from "vue";
import { useGridFetch } from "./useGridFetch.js";

// Build a minimal harness for useGridFetch focused on the overlay-defer path.
// When the overlay is open, a sorted fetch hits the streaming branch and defers
// the refresh to overlay-close, returning before any network call — so this
// harness needs no apiClient mock.
function makeHarness({ overlayOpen = false, selectedSort = "DATE_TAKEN" } = {}) {
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
