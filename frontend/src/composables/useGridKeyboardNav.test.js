import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref } from "vue";

// The composable imports the singleton apiClient for isReadOnly; mock it so no
// real axios instance is constructed and read-only is deterministic.
vi.mock("../utils/apiClient", () => ({
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
    showSelectionBar: ref(true),
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx: ref(cursorIdx),
    isMultiCharacterView: ref(false),
    isSetOverlapView: ref(false),
    hoveredImageIdx: ref(null),
    toolbarSelectionMenuOpen: ref(false),
    isJustifiedMode: ref(justified !== null),
    justifiedLayout: ref(justified),
  };

  const callbacks = {
    clearFaceSelection: vi.fn(),
    clearSearchQuery: vi.fn(),
    scrollCursorIntoView: vi.fn(),
    openOverlay: vi.fn(),
    deleteSelected,
    selectionBarRef: ref({ openTagInput: vi.fn() }),
    applyScoresForSelection,
    setScore: vi.fn(),
  };

  const props = { columns: 3, searchQuery: "" };
  const emit = vi.fn();
  const { handleKeyDown } = useGridKeyboardNav(deps, props, emit, callbacks);
  return { handleKeyDown, deps, deleteSelected, applyScoresForSelection };
}

function keyEvent(overrides) {
  return { preventDefault: vi.fn(), target: null, ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useGridKeyboardNav — review-overlay guard (F1)", () => {
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

describe("useGridKeyboardNav — justified vertical navigation", () => {
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
    expect(deps.selectedImageIds.value).toEqual([
      "a",
      "b",
      "c",
      "d",
      "e",
      "f",
    ]);
  });

  it("square mode keeps the uniform index ± columns arithmetic", () => {
    const { handleKeyDown, deps } = makeNav({
      images: NINE_IMAGES,
      cursorIdx: 1,
      justified: null, // square
    });
    handleKeyDown(keyEvent({ key: "ArrowDown" }));
    expect(deps.cursorIdx.value).toBe(4); // 1 + columns(3)
    handleKeyDown(keyEvent({ key: "ArrowUp" }));
    expect(deps.cursorIdx.value).toBe(1);
  });
});
