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
function makeNav({ reviewOverlayOpen = false } = {}) {
  const selectedImageIds = ref(["a", "b"]);
  const deleteSelected = vi.fn();
  const applyScoresForSelection = vi.fn();

  const deps = {
    scrollWrapper: ref(null),
    allGridImages: ref([
      { id: "a" },
      { id: "b" },
      { id: "c" },
    ]),
    rowHeight: ref(128),
    visibleStart: ref(0),
    overlayOpen: ref(false),
    reviewOverlayOpen: ref(reviewOverlayOpen),
    showSelectionBar: ref(true),
    selectedImageIds,
    lastSelectedImageId: null,
    cursorIdx: ref(null),
    isMultiCharacterView: ref(false),
    isSetOverlapView: ref(false),
    hoveredImageIdx: ref(null),
    toolbarSelectionMenuOpen: ref(false),
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
