import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { useGridStore } from "../stores/useGridStore.js";
import { useSearchStore } from "../stores/useSearchStore.js";
import { useTagClipboardStore } from "../stores/useTagClipboardStore.js";

// The composable imports the singleton apiClient for isReadOnly; mock it so no
// real axios instance is constructed and read-only is deterministic.
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  isReadOnly: { value: false },
  newOperationBatchId: () => "cli-test",
  onSessionReset: () => () => {},
}));

vi.mock("../utils/copyAsFiles.js", () => ({
  canCopyFiles: vi.fn(() => true),
  copyPicturesAsFiles: vi.fn(),
}));

import { isReadOnly } from "../utils/apiClient";
import { canCopyFiles, copyPicturesAsFiles } from "../utils/copyAsFiles.js";
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
  moveStackMember = undefined,
  getOpenStackPlace = () => ({ position: 1, count: 3 }),
  scrapheap = false,
  onPasteResult = vi.fn(),
} = {}) {
  const selectedImageIds = ref(["a", "b"]);
  const deleteSelected = vi.fn();
  const applyScoresForSelection = vi.fn();
  const setScore = vi.fn();

  const deps = {
    scrollWrapper: ref(scrollWrapper),
    allGridImages: ref(images),
    rowHeight: ref(128),
    visibleStart: ref(0),
    lightboxOpen: ref(false),
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
    isScrapheap: () => scrapheap,
  };

  const openLightbox = vi.fn();
  const clearSearchQuery = vi.fn();
  const focusCursor = vi.fn();
  const toggleFavourite = vi.fn();
  const callbacks = {
    clearFaceSelection: vi.fn(),
    clearSearchQuery,
    scrollCursorIntoView: vi.fn(),
    focusCursor,
    openLightbox,
    deleteSelected,
    selectionBarRef: ref({ openTagInput: vi.fn() }),
    applyScoresForSelection,
    setScore,
    toggleFavourite,
    onPasteResult,
    ...(moveStackMember ? { moveStackMember, getOpenStackPlace } : {}),
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
  const { handleKeyDown, onGlobalKeyPress } = useGridKeyboardNav(
    deps,
    props,
    emit,
    callbacks,
  );
  return {
    handleKeyDown,
    onGlobalKeyPress,
    deps,
    deleteSelected,
    applyScoresForSelection,
    openLightbox,
    clearSearchQuery,
    focusCursor,
    toggleFavourite,
    onPasteResult,
    setScore,
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
    const { handleKeyDown, openLightbox } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 2,
      ghostedIndexes: [2],
    });
    handleKeyDown(keyEvent({ key: "Enter" }));
    expect(openLightbox).not.toHaveBeenCalled();
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

describe("useGridKeyboardNav - reordering an open stack", () => {
  // A stand-in for the grid's move: "b" belongs to an open stack, and moving it
  // later swaps it with "c" the way the real reorder lays the stack out again.
  function stackMove(deps) {
    return vi.fn((id, where) => {
      if (id !== "b") return false;
      if (where === "later") {
        deps.allGridImages.value = [{ id: "a" }, { id: "c" }, { id: "b" }];
      }
      return true;
    });
  }

  it("Ctrl+→ moves the cursor's picture and the cursor follows it", () => {
    const move = vi.fn();
    const { handleKeyDown, deps, focusCursor } = makeNav({
      cursorIdx: 1,
      moveStackMember: move,
    });
    move.mockImplementation(stackMove(deps));
    handleKeyDown(keyEvent({ key: "ArrowRight", ctrlKey: true }));
    expect(move).toHaveBeenCalledWith("b", "later");
    expect(deps.cursorIdx.value).toBe(2);
    expect(focusCursor).toHaveBeenCalledWith(2);
    // Moving a picture is not a selection change.
    expect(deps.selectedImageIds.value).toEqual(["a", "b"]);
  });

  it("Ctrl+← asks for the earlier move", () => {
    const move = vi.fn(() => true);
    const { handleKeyDown } = makeNav({ cursorIdx: 1, moveStackMember: move });
    handleKeyDown(keyEvent({ key: "ArrowLeft", metaKey: true }));
    expect(move).toHaveBeenCalledWith("b", "earlier");
  });

  it("falls back to moving the cursor alone outside an open stack", () => {
    const move = vi.fn(() => true);
    const { handleKeyDown, deps } = makeNav({
      cursorIdx: 0,
      moveStackMember: move,
      getOpenStackPlace: () => null,
    });
    handleKeyDown(keyEvent({ key: "ArrowRight", ctrlKey: true }));
    expect(move).not.toHaveBeenCalled();
    expect(deps.cursorIdx.value).toBe(1);
    expect(deps.selectedImageIds.value).toEqual(["a", "b"]);
  });

  it("leaves plain and Shift arrows to navigation", () => {
    const move = vi.fn(() => true);
    const { handleKeyDown } = makeNav({ cursorIdx: 1, moveStackMember: move });
    handleKeyDown(keyEvent({ key: "ArrowRight" }));
    handleKeyDown(keyEvent({ key: "ArrowRight", ctrlKey: true, shiftKey: true }));
    expect(move).not.toHaveBeenCalled();
  });

  it("Ctrl+Home makes the cursor's picture the cover instead of scrolling", () => {
    const move = vi.fn(() => true);
    const wrapper = { scrollTop: 500, clientHeight: 200 };
    const { onGlobalKeyPress } = makeNav({
      cursorIdx: 1,
      moveStackMember: move,
      scrollWrapper: wrapper,
    });
    onGlobalKeyPress("Home", { ctrlKey: true });
    expect(move).toHaveBeenCalledWith("b", "cover");
    expect(wrapper.scrollTop).toBe(500);
  });

  // On the cover, Ctrl+Home changes nothing, but it is still the stack's key:
  // falling through would jump the grid to the top under the user.
  it("keeps a no-op stack key off the scroll and the cursor", () => {
    const move = vi.fn(() => false);
    const wrapper = { scrollTop: 500, clientHeight: 200 };
    const { onGlobalKeyPress, handleKeyDown, deps } = makeNav({
      cursorIdx: 1,
      moveStackMember: move,
      scrollWrapper: wrapper,
    });
    onGlobalKeyPress("Home", { ctrlKey: true });
    expect(wrapper.scrollTop).toBe(500);
    handleKeyDown(keyEvent({ key: "ArrowLeft", ctrlKey: true }));
    expect(deps.cursorIdx.value).toBe(1);
  });

  it("plain Home still scrolls to the top", () => {
    const move = vi.fn(() => true);
    const wrapper = { scrollTop: 500, clientHeight: 200 };
    const { onGlobalKeyPress } = makeNav({
      cursorIdx: 1,
      moveStackMember: move,
      scrollWrapper: wrapper,
    });
    onGlobalKeyPress("Home", {});
    expect(move).not.toHaveBeenCalled();
    expect(wrapper.scrollTop).toBe(0);
  });

  // One 128px row, plus the scroller's own padding below it: End is the
  // scroller's bottom, not the row model's, or the last row is cut off.
  it("End reaches the bottom of the scroller, padding included", () => {
    const wrapper = { scrollTop: 0, clientHeight: 100, scrollHeight: 141 };
    const { onGlobalKeyPress } = makeNav({ scrollWrapper: wrapper });
    onGlobalKeyPress("End", {});
    expect(wrapper.scrollTop).toBe(41);
  });
});

describe("H favourites", () => {
  it("toggles the selection, but marks nothing in the Scrapheap", () => {
    const nav = makeNav();
    nav.handleKeyDown(keyEvent({ key: "h" }));
    expect(nav.toggleFavourite).toHaveBeenCalledWith(["a", "b"]);

    const heap = makeNav({ scrapheap: true });
    heap.handleKeyDown(keyEvent({ key: "h" }));
    expect(heap.toggleFavourite).not.toHaveBeenCalled();
  });
});

describe("useGridKeyboardNav - star keys and Ctrl+V (#2139)", () => {
  // Digits are read by position (e.code); a modifier held is never a rating.
  it("rates on a bare digit and not when Ctrl, Shift or Alt is held", () => {
    const { handleKeyDown, applyScoresForSelection } = makeNav();

    handleKeyDown(keyEvent({ key: "2", code: "Digit2", ctrlKey: true }));
    handleKeyDown(keyEvent({ key: "@", code: "Digit2", shiftKey: true }));
    handleKeyDown(keyEvent({ key: "2", code: "Digit2", altKey: true }));
    expect(applyScoresForSelection).not.toHaveBeenCalled();

    handleKeyDown(keyEvent({ key: "2", code: "Digit2" }));
    expect(applyScoresForSelection).toHaveBeenCalledWith(["a", "b"], 2);
  });

  it("a modified digit does not rate the hovered picture when nothing is selected", () => {
    const { handleKeyDown, deps, setScore } = makeNav();
    deps.selectedImageIds.value = [];
    deps.hoveredImageIdx.value = 0;

    handleKeyDown(keyEvent({ key: "2", code: "Digit2", ctrlKey: true }));
    expect(setScore).not.toHaveBeenCalled();

    handleKeyDown(keyEvent({ key: "2", code: "Digit2" }));
    expect(setScore).toHaveBeenCalledWith({ id: "a" }, 2);
  });

  it("still rates from the numpad", () => {
    const { handleKeyDown, applyScoresForSelection } = makeNav();

    handleKeyDown(keyEvent({ key: "3", code: "Numpad3" }));
    expect(applyScoresForSelection).toHaveBeenCalledWith(["a", "b"], 3);
  });

  it("Ctrl+V pastes the copied tags on the selection and refreshes the grid", async () => {
    const onPasteResult = vi.fn();
    const { handleKeyDown } = makeNav({ onPasteResult });
    const result = { pictureIds: ["a"], failed: 0 };
    const paste = vi
      .spyOn(useTagClipboardStore(), "pasteClipboard")
      .mockResolvedValue(result);
    const event = keyEvent({ key: "v", code: "KeyV", ctrlKey: true });

    handleKeyDown(event);
    await Promise.resolve();
    await Promise.resolve();

    expect(event.preventDefault).toHaveBeenCalled();
    expect(paste).toHaveBeenCalledWith(["a", "b"]);
    expect(onPasteResult).toHaveBeenCalledWith(result);
  });

  it("Ctrl+Shift+V and Ctrl+V with nothing selected are left to the browser", () => {
    const { handleKeyDown, deps } = makeNav();
    const paste = vi.spyOn(useTagClipboardStore(), "pasteClipboard").mockResolvedValue(null);

    handleKeyDown(keyEvent({ key: "V", ctrlKey: true, shiftKey: true }));
    expect(paste).not.toHaveBeenCalled();

    deps.selectedImageIds.value = [];
    handleKeyDown(keyEvent({ key: "v", ctrlKey: true }));
    expect(paste).not.toHaveBeenCalled();
  });

  it("Ctrl+V does nothing on auto-repeat or behind a modal dialog", () => {
    const { handleKeyDown } = makeNav();
    const paste = vi.spyOn(useTagClipboardStore(), "pasteClipboard").mockResolvedValue(null);
    handleKeyDown(keyEvent({ key: "v", code: "KeyV", ctrlKey: true, repeat: true }));
    const overlay = document.createElement("div");
    overlay.className = "v-overlay--active";
    overlay.innerHTML = '<div class="v-overlay__scrim"></div>';
    document.body.appendChild(overlay);
    try {
      handleKeyDown(keyEvent({ key: "v", code: "KeyV", ctrlKey: true }));
    } finally {
      overlay.remove();
    }

    expect(paste).not.toHaveBeenCalled();
  });

  it("does not paste on a read-only library", () => {
    const { handleKeyDown } = makeNav();
    const paste = vi.spyOn(useTagClipboardStore(), "pasteClipboard").mockResolvedValue(null);
    isReadOnly.value = true;
    try {
      handleKeyDown(keyEvent({ key: "v", ctrlKey: true }));
    } finally {
      isReadOnly.value = false;
    }
    expect(paste).not.toHaveBeenCalled();
  });
});

describe("useGridKeyboardNav - Ctrl/Cmd+C copies the selection as files (#2419)", () => {
  it("copies the selected picture ids with Ctrl+C and with Cmd+C", () => {
    const { handleKeyDown, deps } = makeNav();
    deps.selectedImageIds.value = ["3", "7"];
    const event = keyEvent({ key: "c", ctrlKey: true });
    handleKeyDown(event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(copyPicturesAsFiles).toHaveBeenCalledWith([3, 7]);
    handleKeyDown(keyEvent({ key: "C", metaKey: true }));
    expect(copyPicturesAsFiles).toHaveBeenCalledTimes(2);
  });

  it("leaves the key alone off the desktop, with no selection, on repeat or with Shift", () => {
    const { handleKeyDown, deps } = makeNav();
    deps.selectedImageIds.value = ["3"];
    handleKeyDown(keyEvent({ key: "c", ctrlKey: true, repeat: true }));
    handleKeyDown(keyEvent({ key: "c", ctrlKey: true, shiftKey: true }));
    handleKeyDown(keyEvent({ key: "c" }));
    canCopyFiles.mockReturnValueOnce(false);
    const browser = keyEvent({ key: "c", ctrlKey: true });
    handleKeyDown(browser);
    expect(browser.preventDefault).not.toHaveBeenCalled();
    deps.selectedImageIds.value = [];
    handleKeyDown(keyEvent({ key: "c", ctrlKey: true }));
    expect(copyPicturesAsFiles).not.toHaveBeenCalled();
  });

  it("lets the browser copy text the reader selected", () => {
    const { handleKeyDown, deps } = makeNav();
    deps.selectedImageIds.value = ["3"];
    const p = document.createElement("p");
    p.textContent = "some caption";
    document.body.appendChild(p);
    try {
      window.getSelection().selectAllChildren(p);
      handleKeyDown(keyEvent({ key: "c", ctrlKey: true }));
    } finally {
      window.getSelection().removeAllRanges();
      p.remove();
    }
    expect(copyPicturesAsFiles).not.toHaveBeenCalled();
  });
});
