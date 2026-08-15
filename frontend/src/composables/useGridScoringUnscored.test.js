import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { setActivePinia, createPinia } from "pinia";

import { useFilterStore } from "../stores/useFilterStore.js";
import { useGridScoring } from "./useGridScoring.js";
import { applyScores } from "../api/pictures";

vi.mock("../api/pictures", () => ({
  applyScores: vi.fn(() => Promise.resolve()),
  getGuestScores: vi.fn(),
  listPicturesByIds: vi.fn(),
  submitGuestScores: vi.fn(),
}));

// With the unscored filter on, a scored picture no longer matches the view. It
// is removed from the loaded list on the optimistic path rather than by a
// refetch, so rating straight through a backlog stays smooth.
function makeHarness() {
  setActivePinia(createPinia());
  const allGridImages = ref([
    { id: 1, score: 0, idx: 0 },
    { id: 2, score: 0, idx: 1 },
    { id: 3, score: null, idx: 2 },
  ]);
  const removeImagesById = vi.fn((ids) => {
    const drop = new Set(ids.map(String));
    allGridImages.value = allGridImages.value.filter(
      (img) => !drop.has(String(img.id)),
    );
  });
  const scoring = useGridScoring({
    backendUrl: "",
    allGridImages,
    lastFetchedGridImages: ref([]),
    loadedRanges: ref([]),
    visibleStart: ref(0),
    visibleEnd: ref(3),
    renderBuffer: ref(0),
    imagesLoading: ref(false),
    overlayOpen: ref(false),
    pendingOverlayGridRefresh: ref(false),
    preserveScrollOnNextFetch: ref(false),
    skipNextWsRefresh: ref(false),
    gridContainer: ref(null),
    guestSessionId: ref(null),
    guestConsentState: ref(null),
    guestScoreMap: ref(new Map()),
    guestConsentBannerVisible: ref(false),
    pendingGuestScoreIntent: ref(null),
    emit: vi.fn(),
    debouncedFetchAllGridImages: vi.fn(),
    fetchImageInfo: vi.fn(),
    rebuildGridImagesFromLastFetch: vi.fn(),
    triggerNewImageHighlight: vi.fn(),
    updateVisibleThumbnails: vi.fn(),
    maybeRefreshOverlayForComfyui: vi.fn(),
    removeImagesById,
  });
  return { scoring, allGridImages, removeImagesById };
}

beforeEach(() => {
  applyScores.mockClear();
});

describe("scoring while the unscored filter is on", () => {
  it("drops a picture that just got a 1-5 score", async () => {
    const h = makeHarness();
    useFilterStore().unscoredOnlyFilter = true;
    await h.scoring.applyScoresByEntries([["2", 4]]);
    expect(h.removeImagesById).toHaveBeenCalledWith(["2"]);
    expect(h.allGridImages.value.map((i) => i.id)).toEqual([1, 3]);
  });

  it("keeps a picture scored back to 0, which is still unscored", async () => {
    const h = makeHarness();
    useFilterStore().unscoredOnlyFilter = true;
    await h.scoring.applyScoresByEntries([
      ["1", 0],
      ["3", 5],
    ]);
    expect(h.removeImagesById).toHaveBeenCalledWith(["3"]);
    expect(h.allGridImages.value.map((i) => i.id)).toEqual([1, 2]);
  });

  it("removes nothing when the filter is off", async () => {
    const h = makeHarness();
    await h.scoring.applyScoresByEntries([["2", 4]]);
    expect(h.removeImagesById).not.toHaveBeenCalled();
    expect(h.allGridImages.value.map((i) => i.score)).toEqual([0, 4, null]);
  });
});
