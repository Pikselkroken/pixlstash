import { describe, it, expect, vi } from "vitest";
import { ref } from "vue";
import { setActivePinia, createPinia } from "pinia";

import { useGridScoring } from "./useGridScoring.js";

vi.mock("../api/pictures", () => ({
  applyScores: vi.fn(() => Promise.resolve()),
  getGuestScores: vi.fn(),
  listPicturesByIds: vi.fn(),
  submitGuestScores: vi.fn(),
}));

// The grid keeps two lists: the cards on screen and the rows it last fetched.
// Several things rebuild the first from the second (expand or collapse stacks,
// a stack-count refresh after a WS event). A score applied only to the cards
// came back as the fetched value on the next rebuild, while the backend kept
// the new one: rate a card during a fresh grid load and the stars emptied.
describe("a score survives a rebuild from the last fetch", () => {
  it("writes the score into the fetched rows too", async () => {
    setActivePinia(createPinia());
    const fetched = [
      { id: 1, score: 0, idx: 0 },
      { id: 2, score: 0, idx: 1 },
    ];
    const lastFetchedGridImages = ref(fetched.map((img) => ({ ...img })));
    const allGridImages = ref(fetched.map((img) => ({ ...img })));
    // What the real rebuild does to the scores: cards from the fetched rows.
    const rebuildGridImagesFromLastFetch = () => {
      allGridImages.value = lastFetchedGridImages.value.map((img) => ({
        ...img,
      }));
    };
    const scoring = useGridScoring({
      backendUrl: "",
      allGridImages,
      lastFetchedGridImages,
      loadedRanges: ref([]),
      visibleStart: ref(0),
      visibleEnd: ref(2),
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
      rebuildGridImagesFromLastFetch,
      triggerNewImageHighlight: vi.fn(),
      updateVisibleThumbnails: vi.fn(),
      maybeRefreshOverlayForComfyui: vi.fn(),
      removeImagesById: vi.fn(),
    });

    await scoring.setScore(allGridImages.value[1], 4);
    expect(allGridImages.value.map((i) => i.score)).toEqual([0, 4]);

    rebuildGridImagesFromLastFetch();
    expect(allGridImages.value.map((i) => i.score)).toEqual([0, 4]);
  });
});
