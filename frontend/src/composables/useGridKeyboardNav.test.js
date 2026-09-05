import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { useGridStore } from "../stores/useGridStore.js";
import { useSearchStore } from "../stores/useSearchStore.js";

// The composable imports the singleton apiClient for isReadOnly; mock it so no
// real axios instance is constructed and read-only is deterministic.
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  isReadOnly: { value: false },
}));

import { useGridKeyboardNav } from "./useGridKeyboardNav.js";

// Build a harness around handleKeyDown with the deps/callbacks it destructures.
// `reviewOverlayOpen` is the F1 wiring: when the Review Sessions overlay is up,
// grid shortcuts (Delete, scoring digits, Ctrl+A, …) must go inert.
// `images` / `cursorIdx` / `justified` / `scrollWrapper` / `lastSelectedImageId`
// exercise the layout-aware navigation paths.
function makeNav({
  reviewOverlayOpen = false,
  images = [{ id: "a" }, { id: "b" }, { id: "c" }],
  cursorIdx = null,
  justified = null,
  scrollWrapper = null,
  lastSelectedImageId = null,
  ghostedIndexes = [],
  showSelectionBar = true,
  searchResultsActive = false,
  searchQuery = "",
} = {}) {
  const selectedImageIds = ref(["a", "b"]);
  const deleteSelected = vi.fn();
  const applyScoresForSelection = vi.fn();

  const deps = {
    scrollWrapper: ref(scrollWrapper),
    allGridImages: ref(images),
    rowHeight: ref(128),
    visibleStart: ref(0),
    overlayOpen: ref(false),
    reviewOverlayOpen: ref(reviewOverlayOpen),
    showSelectionBar: ref(showSelectionBar),
    searchResultsActive: ref(searchResultsActive),
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx: ref(cursorIdx),
    isMultiCharacterView: ref(false),
    isSetOverlapView: ref(false),
    hoveredImageIdx: ref(null),
    toolbarSelectionMenuOpen: ref(false),
    isJustifiedMode: ref(justified !== null),
    justifiedLayout: ref(justified),
    isGhosted: (index) => ghostedIndexes.includes(index),
  };

  const openOverlay = vi.fn();
  const clearSearchQuery = vi.fn();
  const focusCursor = vi.fn();
  const callbacks = {
    clearFaceSelection: vi.fn(),
    clearSearchQuery,
    scrollCursorIntoView: vi.fn(),
    focusCursor,
    openOverlay,
    deleteSelected,
    selectionBarRef: ref({ openTagInput: vi.fn() }),
    applyScoresForSelection,
    setScore: vi.fn(),
  };

  // The composable takes the column count from the grid store; size level 6
  // ("huge") is the 4-column step these navigation cases are written against.
  useGridStore().sizeLevel = 6;
  // It also reads the text query from the search STORE now (App.vue slim-down,
  // #661), not from a prop, so the helper's `searchQuery` has to be seeded
  // there or the Esc-clears-search cases would exercise an empty query.
  useSearchStore().searchQuery = searchQuery;
  const props = {};
  const emit = vi.fn();
  const { handleKeyDown } = useGridKeyboardNav(deps, props, emit, callbacks);
  return {
    handleKeyDown,
    deps,
    deleteSelected,
    applyScoresForSelection,
    openOverlay,
    clearSearchQuery,
    focusCursor,
  };
}

function keyEvent(overrides) {
  return { preventDefault: vi.fn(), target: null, ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
  // The composable reads the grid's column count from the store.
  setActivePinia(createPinia());
});

describe("useGridKeyboardNav - review-overlay guard (F1)", () => {
  it("no-ops Delete, scoring digits, and Ctrl+A while the review overlay is open", () => {
    const { handleKeyDown, deps, deleteSelected, applyScoresForSelection } =
      makeNav({ reviewOverlayOpen: true });

    handleKeyDown(keyEvent({ key: "Delete" }));
    handleKeyDown(keyEvent({ key: "Backspace" }));
    handleKeyDown(keyEvent({ key: "3" }));
    handleKeyDown(keyEvent({ key: "a", ctrlKey: true }));

    expect(deleteSelected).not.toHaveBeenCalled();
    expect(applyScoresForSelection).not.toHaveBeenCalled();
    // Ctrl+A must not have rewritten the selection.
    expect(deps.selectedImageIds.value).toEqual(["a", "b"]);
  });

  it("still handles the same keys when the review overlay is closed", () => {
    const { handleKeyDown, deps, deleteSelected, applyScoresForSelection } =
      makeNav({ reviewOverlayOpen: false });

    handleKeyDown(keyEvent({ key: "Delete" }));
    expect(deleteSelected).toHaveBeenCalledTimes(1);

    handleKeyDown(keyEvent({ key: "3" }));
    expect(applyScoresForSelection).toHaveBeenCalledWith(["a", "b"], 3);

    handleKeyDown(keyEvent({ key: "a", ctrlKey: true }));
    expect(deps.selectedImageIds.value).toEqual(["a", "b", "c"]);
  });
});

// ---- Justified-mode vertical navigation ------------------------------------
// Same hand-built layout as useJustifiedLayout.test.js (gap 4):
//   row 0: widths [100, 300, 100] → centers 50, 254, 458
//   row 1: widths [400, 104]      → centers 200, 456
//   row 2: widths [100, 100, 100, 100] → centers 50, 154, 258, 362
const JUSTIFIED_FIXTURE = {
  rowStarts: [0, 3, 5, 9],
  rowHeights: [240, 240, 240],
  rowOffsets: [0, 244, 488],
  itemScaledWidths: [100, 300, 100, 400, 104, 100, 100, 100, 100],
  totalHeight: 728,
};
const NINE_IMAGES = ["a", "b", "c", "d", "e", "f", "g", "h", "i"].map((id) => ({
  id,
}));

describe("useGridKeyboardNav - justified vertical navigation", () => {
  it("moves DOM focus with the keyboard cursor", () => {
    const { handleKeyDown, focusCursor } = makeNav({ cursorIdx: 0 });

    handleKeyDown(keyEvent({ key: "ArrowRight" }));

    expect(focusCursor).toHaveBeenCalledWith(1);
  });

  it("ArrowDown moves to the nearest-center item of the next visual row", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 1, // center 254 → row 1 nearest is idx 3 (center 200)
      justified: JUSTIFIED_FIXTURE,
    });
    handleKeyDown(keyEvent({ key: "ArrowDown" }));
    expect(deps.cursorIdx.value).toBe(3);
    expect(deps.selectedImageIds.value).toEqual(["d"]);
  });

  it("ArrowUp moves to the nearest-center item of the previous visual row", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 4, // center 456 → row 0 nearest is idx 2 (center 458)
      justified: JUSTIFIED_FIXTURE,
    });
    handleKeyDown(keyEvent({ key: "ArrowUp" }));
    expect(deps.cursorIdx.value).toBe(2);
  });

  it("clamps ArrowUp in the first row to item 0 and ArrowDown in the last row to the last item", () => {
    const up = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 2,
      justified: JUSTIFIED_FIXTURE,
    });
    up.handleKeyDown(keyEvent({ key: "ArrowUp" }));
    expect(up.deps.cursorIdx.value).toBe(0);

    const down = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 6,
      justified: JUSTIFIED_FIXTURE,
    });
    down.handleKeyDown(keyEvent({ key: "ArrowDown" }));
    expect(down.deps.cursorIdx.value).toBe(8);
  });

  it("Ctrl+ArrowDown moves the cursor without changing the selection", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 1,
      justified: JUSTIFIED_FIXTURE,
    });
    handleKeyDown(keyEvent({ key: "ArrowDown", ctrlKey: true }));
    expect(deps.cursorIdx.value).toBe(3);
    expect(deps.selectedImageIds.value).toEqual(["a", "b"]); // untouched
  });

  it("Shift+PageDown pages by VISUAL rows and extends the selection", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 0,
      justified: JUSTIFIED_FIXTURE,
      // One viewport of 490px spans rows 0→2 (rowOffsets 0/244/488).
      scrollWrapper: { clientHeight: 490, scrollTop: 0 },
      lastSelectedImageId: "a",
    });
    handleKeyDown(keyEvent({ key: "PageDown", shiftKey: true }));
    // Row 2's nearest-center item from center 50 is idx 5 (center 50).
    expect(deps.cursorIdx.value).toBe(5);
    expect(deps.selectedImageIds.value).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  it("square mode keeps the uniform index ± columns arithmetic", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 1,
      justified: null, // square
    });
    handleKeyDown(keyEvent({ key: "ArrowDown" }));
    expect(deps.cursorIdx.value).toBe(5); // 1 + columns(4)
    handleKeyDown(keyEvent({ key: "ArrowUp" }));
    expect(deps.cursorIdx.value).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Scrapheap ghosts
// ---------------------------------------------------------------------------
//
// A ghosted tile is on screen but inert - already in the Scrapheap, held there
// only while its undo is one click away. The cursor SKIPS them: parking on one
// would make every following key (Space, Enter, a digit) silently do nothing,
// which is indistinguishable from a broken feature.

describe("useGridKeyboardNav - ghosted tiles", () => {
  it("skips a ghosted tile in the direction of travel", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 0,
      ghostedIndexes: [1, 2],
    });
    handleKeyDown(keyEvent({ key: "ArrowRight" }));
    expect(deps.cursorIdx.value).toBe(3);
    handleKeyDown(keyEvent({ key: "ArrowLeft" }));
    expect(deps.cursorIdx.value).toBe(0);
  });

  it("stays put when every cell ahead is ghosted", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 6,
      ghostedIndexes: [7, 8],
    });
    handleKeyDown(keyEvent({ key: "ArrowRight" }));
    expect(deps.cursorIdx.value).toBe(6);
  });

  it("skips ghosts on the vertical move too", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 0,
      ghostedIndexes: [3],
    });
    // Down lands on 3 (0 + columns), which is ghosted → scan forward to 4.
    handleKeyDown(keyEvent({ key: "ArrowDown" }));
    expect(deps.cursorIdx.value).toBe(4);
  });

  it("leaves ghosts out of a Shift+arrow range", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 0,
      lastSelectedImageId: "a",
      ghostedIndexes: [1],
    });
    handleKeyDown(keyEvent({ key: "ArrowRight", shiftKey: true }));
    // The cursor skips the ghost at 1 and lands on 2; the span 0..2 then drops
    // the ghost silently - a count that included it would be the surprise.
    expect(deps.selectedImageIds.value).toEqual(["a", "c"]);
  });

  it("leaves ghosts out of Ctrl+A", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      ghostedIndexes: [0, 5],
    });
    handleKeyDown(keyEvent({ key: "a", ctrlKey: true }));
    // "Select all" has to mean "all a bulk action can act on".
    expect(deps.selectedImageIds.value).not.toContain("a");
    expect(deps.selectedImageIds.value).not.toContain("f");
    expect(deps.selectedImageIds.value).toHaveLength(7);
  });

  it("does not open the lightbox on a ghost the cursor was already sitting on", () => {
    const { handleKeyDown, openOverlay } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 2,
      ghostedIndexes: [2],
    });
    handleKeyDown(keyEvent({ key: "Enter" }));
    expect(openOverlay).not.toHaveBeenCalled();
  });

  it("does not toggle selection with Space on a ghost under the cursor", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 2,
      ghostedIndexes: [2],
    });
    const before = [...deps.selectedImageIds.value];
    handleKeyDown(keyEvent({ key: " " }));
    expect(deps.selectedImageIds.value).toEqual(before);
  });
});

// Esc peels one layer per press: an open menu, then the selection, then the
// search. The grid action pill puts an Esc keycap on whichever button the key
// will actually reach, so the ladder has to match what the keycap promises
// (merged-grid-action-pill.md §6.1).
describe("Escape ladder", () => {
  it("clears the selection first and leaves the search alone", () => {
    const { handleKeyDown, deps, clearSearchQuery } = makeNav({
      showSelectionBar: true,
      searchQuery: "sunset",
    });
    handleKeyDown(keyEvent({ key: "Escape" }));
    expect(deps.selectedImageIds.value).toEqual([]);
    expect(clearSearchQuery).not.toHaveBeenCalled();
  });

  it("clears a text search once nothing is selected", () => {
    const { handleKeyDown, clearSearchQuery } = makeNav({
      showSelectionBar: false,
      searchQuery: "sunset",
    });
    handleKeyDown(keyEvent({ key: "Escape" }));
    expect(clearSearchQuery).toHaveBeenCalledTimes(1);
  });

  it("clears a search that has no query string behind it", () => {
    // Reverse image, similar faces and a person face search all produce results
    // with an empty `searchQuery`. The gate only ever asked about the text
    // query, so Esc silently did nothing in those modes even though
    // `clearSearchQuery` has always reset them.
    const { handleKeyDown, clearSearchQuery } = makeNav({
      showSelectionBar: false,
      searchResultsActive: true,
      searchQuery: "",
    });
    handleKeyDown(keyEvent({ key: "Escape" }));
    expect(clearSearchQuery).toHaveBeenCalledTimes(1);
  });

  it("does not clear a search that is not running", () => {
    const { handleKeyDown, clearSearchQuery } = makeNav({
      showSelectionBar: false,
      searchResultsActive: false,
      searchQuery: "",
    });
    handleKeyDown(keyEvent({ key: "Escape" }));
    expect(clearSearchQuery).not.toHaveBeenCalled();
  });
});
