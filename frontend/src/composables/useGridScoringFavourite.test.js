import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { setActivePinia, createPinia } from "pinia";

import { useFilterStore } from "../stores/useFilterStore.js";
import { useGridScoring } from "./useGridScoring.js";
import { setPicturesFavourite } from "../api/pictures";

vi.mock("../api/pictures", () => ({
  applyScores: vi.fn(() => Promise.resolve()),
  getGuestScores: vi.fn(),
  listPicturesByIds: vi.fn(),
  setPicturesFavourite: vi.fn(() => Promise.resolve({ updated_ids: [] })),
  submitGuestScores: vi.fn(),
}));

// The heart is drawn at once, put back if the server refuses, and a picture
// unfavourited under "Favourites only" leaves the loaded list.
function makeHarness() {
  setActivePinia(createPinia());
  const allGridImages = ref([
    { id: 1, favourite: true, idx: 0 },
    { id: 2, favourite: false, idx: 1 },
    { id: 3, favourite: true, idx: 2 },
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
    lightboxOpen: ref(false),
    pendingLightboxGridRefresh: ref(false),
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
    removeImagesById,
  });
  const hearts = () => allGridImages.value.map((img) => img.favourite);
  return { scoring, hearts, removeImagesById };
}

beforeEach(() => {
  setPicturesFavourite.mockClear();
});

describe("favouriting from the grid", () => {
  it("favourites a mixed selection, unfavourites an all-favourite one", async () => {
    const h = makeHarness();
    await h.scoring.toggleFavourite([1, 2]);
    expect(setPicturesFavourite).toHaveBeenLastCalledWith([1, 2], true);
    expect(h.hearts()).toEqual([true, true, true]);

    await h.scoring.toggleFavourite([1, 3]);
    expect(setPicturesFavourite).toHaveBeenLastCalledWith([1, 3], false);
    expect(h.hearts()).toEqual([false, true, false]);
  });

  it("puts each heart back as it was when the server refuses", async () => {
    const h = makeHarness();
    setPicturesFavourite.mockRejectedValueOnce(new Error("refused"));
    await h.scoring.toggleFavourite([1, 2]);
    expect(h.hearts()).toEqual([true, false, true]);
  });

  // The lightbox holds its own copy of the heart and reverts it on false.
  it("says whether the change was stored", async () => {
    const h = makeHarness();
    expect(await h.scoring.setFavourite([2], true)).toBe(true);
    setPicturesFavourite.mockRejectedValueOnce(new Error("refused"));
    expect(await h.scoring.setFavourite([2], false)).toBe(false);
  });

  it("drops an unfavourited picture only while Favourites only is on", async () => {
    const h = makeHarness();
    await h.scoring.setFavourite([1], false);
    expect(h.removeImagesById).not.toHaveBeenCalled();

    useFilterStore().favouriteOnlyFilter = true;
    await h.scoring.setFavourite([3], false);
    expect(h.removeImagesById).toHaveBeenCalledWith(["3"]);
  });
});
