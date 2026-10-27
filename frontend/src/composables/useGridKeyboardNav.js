import { getPictureId } from "../utils/media.js";
import { isReadOnly } from "../utils/apiClient";
import {
  rowAtOffset,
  rowOfIndex,
  verticalNeighborIndex,
  JUSTIFIED_ROW_GAP,
} from "./useJustifiedLayout.js";
import { useGridStore } from "../stores/useGridStore";
import { useSearchStore } from "../stores/useSearchStore";
import { FAVOURITE_KEY } from "../stores/useFavouritesStore";
import { useTagClipboardStore } from "../stores/useTagClipboardStore";
import { bareDigit } from "../utils/shortcutKeys";

/**
 * Manages keyboard navigation and keyboard-driven actions for the image grid.
 *
 * Vertical navigation is layout-aware: in the uniform 'square' grid Up/Down is
 * `index ± columns` (unchanged); in 'justified' mode rows hold varying item
 * counts, so Up/Down moves to the item in the adjacent visual row whose
 * horizontal center is nearest the current item's center, and paging moves by
 * visual rows derived from the packed row offsets.
 *
 * @param {Object} deps - Reactive refs from other composables / ImageGrid.
 *   `isJustifiedMode` / `justifiedLayout` (from useVirtualScroll) are optional;
 *   when absent or false/null the uniform square-grid arithmetic is used.
 * @param {Object} props - Component props
 * @param {Function} emit - Component emit function
 * @param {Object} callbacks - Functions provided by ImageGrid
 */
export function useGridKeyboardNav(
  {
    scrollWrapper,
    allGridImages,
    rowHeight,
    visibleStart,
    overlayOpen,
    reviewOverlayOpen,
    showSelectionBar,
    searchResultsActive,
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx,
    isMultiCharacterView,
    isSetOverlapView,
    hoveredImageIdx,
    toolbarSelectionMenuOpen,
    isJustifiedMode,
    justifiedLayout,
    isGhosted = () => false,
    // The Scrapheap shows no heart, so H marks nothing there either.
    isScrapheap = () => false,
  },
  props,
  emit,
  {
    clearFaceSelection,
    clearSearchQuery,
    scrollCursorIntoView,
    focusCursor = () => {},
    openOverlay,
    deleteSelected,
    selectionBarRef,
    applyScoresForSelection,
    setScore,
    toggleFavourite = () => {},
    moveStackMember = () => false,
    getOpenStackPlace = () => null,
    onPasteResult = () => {},
  },
) {
  const searchStore = useSearchStore();
  const gridStore = useGridStore();
  const tagClipboard = useTagClipboardStore();

  /**
   * The star rating a bare digit gives: 0-5 with no modifier held, else null.
   * Ctrl+2 is not two stars.
   */
  function starRating(event) {
    const digit = bareDigit(event);
    return digit !== null && digit <= 5 ? digit : null;
  }

  /** Ctrl+V on the grid selection: paste the copied tags. */
  function pasteOnSelection(ids) {
    Promise.resolve(tagClipboard.pasteClipboard(ids))
      .then((result) => {
        if (result?.pictureIds.length) onPasteResult(result);
      })
      .catch((error) => {
        // pasteClipboard reports its own failures; this is a failed refresh
        // of the grid afterwards.
        console.error("Refreshing the grid after a paste failed on", ids, error);
      });
  }
  // ── Scrapheap ghosts ────────────────────────────────────────────────────
  // A ghosted tile is on screen but inert: it is already in the Scrapheap and
  // is only being held there while its undo is one click away.
  //
  // The cursor SKIPS them rather than landing on them. A cursor parked on an
  // inert cell makes every following key a dead key - Space, Enter, a digit,
  // all silently doing nothing with no way to tell that from a broken feature,
  // which is the one outcome this codebase treats as unacceptable. One linear
  // scan in the direction of travel serves all four arrow keys and both paging
  // keys, in both the uniform and the justified layout, because it is a pure
  // index predicate over `allGridImages` and touches no geometry.

  /**
   * First non-ghosted index at or after `index`, travelling in `step`.
   * @returns {number|null} null when there is none (leave the cursor put).
   */
  function skipGhosts(index, step) {
    const total = allGridImages.value.length;
    if (index == null || index < 0 || index >= total) return null;
    const direction = step < 0 ? -1 : 1;
    for (let i = index; i >= 0 && i < total; i += direction) {
      if (!isGhosted(i)) return i;
    }
    return null;
  }

  /** Drop ghosted ids from a selection about to be committed. */
  function withoutGhosts(indexedIds) {
    return indexedIds
      .filter(({ index }) => !isGhosted(index))
      .map(({ id }) => id);
  }

  /** `[start, end]` of `allGridImages` as selectable ids, ghosts skipped. */
  function selectableRange(start, end) {
    return withoutGhosts(
      allGridImages.value
        .slice(start, end + 1)
        .map((img, offset) => ({ id: img?.id, index: start + offset }))
        .filter((entry) => Boolean(entry.id)),
    );
  }

  // The packed justified layout when active, else null (→ uniform grid math).
  function activeJustifiedLayout() {
    if (!isJustifiedMode?.value) return null;
    const layout = justifiedLayout?.value;
    return layout && layout.rowHeights.length > 0 ? layout : null;
  }

  /**
   * Make cover / Move earlier / Move later on the cursor's picture, when it is
   * in an open stack. The cursor follows the picture to its new place.
   *
   * @returns {boolean} whether the key was used for that. On a picture of an
   *   open stack it always is, even when the step is a no-op (the cover moved
   *   earlier): falling through would scroll or move the cursor instead.
   */
  function moveCursorStackMember(where) {
    if (isReadOnly.value || cursorIdx.value === null) return false;
    if (overlayOpen.value || reviewOverlayOpen?.value) return false;
    const img = allGridImages.value[cursorIdx.value];
    if (img?.id == null || !getOpenStackPlace(img)) return false;
    if (!moveStackMember(img.id, where)) return true;
    const newIdx = allGridImages.value.findIndex(
      (item) => getPictureId(item?.id) === getPictureId(img.id),
    );
    if (newIdx >= 0) {
      cursorIdx.value = newIdx;
      scrollCursorIntoView(newIdx);
      focusCursor(newIdx);
    }
    return true;
  }

  function onGlobalKeyPress(key, event) {
    // Ctrl+Home on a picture of an open stack makes it the cover; anywhere
    // else Home keeps scrolling to the top.
    if (
      key === "Home" &&
      (event?.ctrlKey || event?.metaKey) &&
      !event?.shiftKey &&
      !event?.altKey &&
      moveCursorStackMember("cover")
    ) {
      return;
    }
    if (scrollWrapper.value) {
      let newScrollTop = scrollWrapper.value.scrollTop;
      const total = allGridImages.value.length;
      const cols = Math.max(1, gridStore.columns || 1);
      const totalRows = Math.ceil(total / cols);
      // Justified rows don't follow cols × rowHeight; use the packed model's
      // exact pixel height so End/PageDown reach the true bottom.
      const packedLayout = activeJustifiedLayout();
      const totalHeight = packedLayout
        ? packedLayout.totalHeight
        : totalRows * rowHeight.value;
      const maxScroll = Math.max(
        0,
        totalHeight - scrollWrapper.value.clientHeight,
      );
      if (key === "Home") {
        newScrollTop = 0;
      } else if (key === "End") {
        newScrollTop = maxScroll;
      } else if (key === "PageUp") {
        newScrollTop = Math.max(
          0,
          newScrollTop - scrollWrapper.value.clientHeight,
        );
      } else if (key === "PageDown") {
        newScrollTop = Math.min(
          maxScroll,
          newScrollTop + scrollWrapper.value.clientHeight,
        );
      }
      // Only update if changed
      if (scrollWrapper.value.scrollTop !== newScrollTop) {
        scrollWrapper.value.scrollTop = newScrollTop;
      }
    }
  }

  /**
   * Whether Ctrl+V may paste. Not on auto-repeat, and not behind a modal
   * dialog, where its result could not be seen; that is the same
   * Vuetify-scrim test the global undo key uses.
   */
  function canWriteFromKeys(event) {
    if (event.repeat) return false;
    return (
      typeof document === "undefined" ||
      document.querySelector(".v-overlay--active .v-overlay__scrim") == null
    );
  }

  // Clear selection on ESC key
  function handleKeyDown(event) {
    const isEditableElement = (element) => {
      if (!(element instanceof HTMLElement)) return false;
      if (element.isContentEditable) return true;
      const tagName = element.tagName;
      if (
        tagName === "INPUT" ||
        tagName === "TEXTAREA" ||
        tagName === "SELECT"
      ) {
        return true;
      }
      if (element.getAttribute("role") === "textbox") return true;
      return false;
    };

    const target = event.target;
    if (isEditableElement(target)) {
      return;
    }
    if (
      typeof document !== "undefined" &&
      isEditableElement(document.activeElement)
    ) {
      return;
    }
    if (overlayOpen.value) return; // Ignore if the lightbox overlay is open
    // The Review Sessions overlay is a modal review surface with its own
    // keyboard handler; it stays mounted over the grid and only consumes the
    // keys it handles, so grid shortcuts (Delete/Backspace, scoring digits,
    // Ctrl+A, arrows, Enter, T) must not fire on the grid behind it.
    if (reviewOverlayOpen?.value) return;
    if (toolbarSelectionMenuOpen?.value) return; // Ignore when selection menu is open
    if (event.key === "Escape") {
      if (showSelectionBar.value) {
        // First ESC clears selection only
        selectedImageIds.value = [];
        lastSelectedImageId = null;
        cursorIdx.value = null;
        clearFaceSelection();
      } else if (isMultiCharacterView.value || isSetOverlapView.value) {
        // No images selected - ESC closes the union/intersect/overlap bar
        emit("clear-multi-selection");
      } else if (
        searchResultsActive?.value ||
        (searchStore.searchQuery && searchStore.searchQuery.trim())
      ) {
        // No selection active - ESC also clears search. The query now comes
        // from `searchStore` rather than a prop (App.vue slim-down, #661).
        // `searchResultsActive` covers the modes that have no query string
        // behind them (reverse image, similar faces, a person face search):
        // `clearSearchQuery` has always reset all of them, but the gate here
        // only ever asked about the text query, so Esc silently did nothing in
        // those modes. The pill puts an Esc keycap on the button that clears
        // them, so the key now has to actually reach it
        // (merged-grid-action-pill.md §6.1).
        clearSearchQuery();
      } else {
        selectedImageIds.value = [];
        lastSelectedImageId = null;
        cursorIdx.value = null;
        clearFaceSelection();
      }
    } else if (
      (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
      (event.ctrlKey || event.metaKey) &&
      !event.shiftKey &&
      !event.altKey &&
      moveCursorStackMember(event.key === "ArrowLeft" ? "earlier" : "later")
    ) {
      // Ctrl+←/→ on a picture of an open stack moves it within the stack;
      // anywhere else it falls through to moving the cursor alone, below.
      event.preventDefault();
    } else if (
      ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
    ) {
      event.preventDefault();
      const total = allGridImages.value.length;
      if (total === 0) return;
      const cols = Math.max(1, gridStore.columns || 1);
      let newIdx = cursorIdx.value;
      // Which way the scan runs when the landing cell turns out to be a ghost.
      const travel =
        event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
      if (newIdx === null) {
        if (selectedImageIds.value.length > 0) {
          const firstSel = allGridImages.value.findIndex(
            (img) => img && selectedImageIds.value.includes(img.id),
          );
          newIdx = firstSel >= 0 ? firstSel : 0;
        } else {
          newIdx = 0;
        }
      } else {
        const packedLayout = activeJustifiedLayout();
        if (event.key === "ArrowLeft") newIdx = Math.max(0, newIdx - 1);
        else if (event.key === "ArrowRight")
          newIdx = Math.min(total - 1, newIdx + 1);
        else if (packedLayout && event.key === "ArrowUp")
          // Justified rows hold varying item counts: move to the item in the
          // previous visual row whose center is nearest the current center.
          newIdx = verticalNeighborIndex(
            packedLayout,
            JUSTIFIED_ROW_GAP,
            newIdx,
            -1,
          );
        else if (packedLayout && event.key === "ArrowDown")
          newIdx = verticalNeighborIndex(
            packedLayout,
            JUSTIFIED_ROW_GAP,
            newIdx,
            1,
          );
        else if (event.key === "ArrowUp") newIdx = Math.max(0, newIdx - cols);
        else if (event.key === "ArrowDown")
          newIdx = Math.min(total - 1, newIdx + cols);
      }
      // Land on the first cell that is actually operable. When the whole run
      // ahead is ghosted, stay put rather than parking on an inert cell.
      const landed = skipGhosts(newIdx, travel);
      if (landed === null) return;
      newIdx = landed;
      cursorIdx.value = newIdx;
      const cursorImg = allGridImages.value[newIdx];
      if (cursorImg && cursorImg.id) {
        if (event.shiftKey) {
          const anchorIndex =
            lastSelectedImageId != null
              ? allGridImages.value.findIndex(
                  (item) =>
                    getPictureId(item?.id) ===
                    getPictureId(lastSelectedImageId),
                )
              : newIdx;
          const start = Math.min(anchorIndex, newIdx);
          const end = Math.max(anchorIndex, newIdx);
          selectedImageIds.value = selectableRange(start, end);
        } else if (!event.ctrlKey && !event.metaKey) {
          // Plain arrow: move cursor and select only this image
          selectedImageIds.value = [cursorImg.id];
          lastSelectedImageId = cursorImg.id;
        }
        // Ctrl+Arrow: move cursor without changing selection
      }
      scrollCursorIntoView(newIdx);
      focusCursor(newIdx);
    } else if (
      (event.key === "PageDown" || event.key === "PageUp") &&
      event.shiftKey &&
      cursorIdx.value !== null
    ) {
      // Shift+PageDown/Up: extend selection by a viewport's worth of rows
      event.preventDefault();
      const total = allGridImages.value.length;
      if (total === 0) return;
      const cols = Math.max(1, gridStore.columns || 1);
      const packedLayout = activeJustifiedLayout();
      let newIdx;
      if (packedLayout) {
        // Page by VISUAL rows: find the packed row one viewport height away
        // from the cursor's row via the exact row offsets, then land on the
        // nearest-center item of that row (rowsPerPage × cols is meaningless
        // when items-per-row varies).
        const direction = event.key === "PageDown" ? 1 : -1;
        const viewportHeight = scrollWrapper.value?.clientHeight || 0;
        const currentRow = rowOfIndex(packedLayout.rowStarts, cursorIdx.value);
        let rowDelta;
        if (viewportHeight > 0) {
          const targetY =
            packedLayout.rowOffsets[currentRow] + direction * viewportHeight;
          const targetRow = rowAtOffset(
            packedLayout.rowOffsets,
            Math.max(0, targetY),
          );
          rowDelta = targetRow - currentRow;
          // A viewport shorter than one row must still move.
          if (rowDelta === 0) rowDelta = direction;
        } else {
          // No measurable viewport: same 5-row fallback as the square path.
          rowDelta = direction * 5;
        }
        newIdx = verticalNeighborIndex(
          packedLayout,
          JUSTIFIED_ROW_GAP,
          cursorIdx.value,
          rowDelta,
        );
      } else {
        const rowsPerPage = scrollWrapper.value
          ? Math.max(
              1,
              Math.floor(scrollWrapper.value.clientHeight / rowHeight.value),
            )
          : 5;
        const delta = rowsPerPage * cols;
        newIdx =
          event.key === "PageDown"
            ? Math.min(total - 1, cursorIdx.value + delta)
            : Math.max(0, cursorIdx.value - delta);
      }
      const landed = skipGhosts(newIdx, event.key === "PageDown" ? 1 : -1);
      if (landed === null) return;
      newIdx = landed;
      cursorIdx.value = newIdx;
      const anchorIndex =
        lastSelectedImageId != null
          ? allGridImages.value.findIndex(
              (item) =>
                getPictureId(item?.id) === getPictureId(lastSelectedImageId),
            )
          : newIdx;
      const start = Math.min(anchorIndex, newIdx);
      const end = Math.max(anchorIndex, newIdx);
      selectedImageIds.value = selectableRange(start, end);
      scrollCursorIntoView(newIdx);
      focusCursor(newIdx);
    } else if (event.key === " ") {
      // Space: toggle selection at cursor
      if (cursorIdx.value !== null) {
        event.preventDefault();
        // Defensive: the cursor never moves onto a ghost, but it can be sitting
        // on a tile at the moment that tile becomes one.
        if (isGhosted(cursorIdx.value)) return;
        const cursorImg = allGridImages.value[cursorIdx.value];
        if (cursorImg && cursorImg.id) {
          const newSelection = [...selectedImageIds.value];
          if (newSelection.includes(cursorImg.id)) {
            selectedImageIds.value = newSelection.filter(
              (id) => id !== cursorImg.id,
            );
          } else {
            newSelection.push(cursorImg.id);
            selectedImageIds.value = newSelection;
            lastSelectedImageId = cursorImg.id;
          }
        }
      }
    } else if (event.key === "Enter") {
      // Enter: open overlay for cursor image
      if (cursorIdx.value !== null) {
        event.preventDefault();
        if (isGhosted(cursorIdx.value)) return;
        const cursorImg = allGridImages.value[cursorIdx.value];
        if (cursorImg && cursorImg.id) {
          openOverlay(cursorImg);
        }
      }
    } else if (event.key === "g" || event.key === "G") {
      // Focus the first visible image in the grid
      event.preventDefault();
      const idx = skipGhosts(visibleStart.value, 1);
      if (idx === null) return;
      const img = allGridImages.value[idx];
      if (img && img.id) {
        cursorIdx.value = idx;
        selectedImageIds.value = [img.id];
        lastSelectedImageId = img.id;
        scrollCursorIntoView(idx);
        focusCursor(idx);
      }
    } else if (event.key === "Delete" || event.key === "Backspace") {
      if (selectedImageIds.value.length > 0 && !isReadOnly.value) {
        deleteSelected();
      }
    } else if ((event.ctrlKey || event.metaKey) && event.key === "a") {
      event.preventDefault();
      // Select all images with valid IDs from allGridImages (not just visible).
      // Ghosts are excluded: "select all" has to mean "all a bulk action can
      // act on", and these are already in the Scrapheap.
      const allIds = withoutGhosts(
        allGridImages.value
          .map((img, index) => ({ id: img?.id, index }))
          .filter((entry) => Boolean(entry.id)),
      );
      selectedImageIds.value = Array.from(allIds);
      lastSelectedImageId = null;
    } else if (
      (event.key === "t" || event.key === "T") &&
      selectedImageIds.value.length > 0 &&
      !isReadOnly.value
    ) {
      event.preventDefault();
      selectionBarRef.value?.openTagInput();
    } else if (
      String(event.key).toLowerCase() === FAVOURITE_KEY &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey &&
      !overlayOpen.value &&
      !isReadOnly.value &&
      !isScrapheap()
    ) {
      // H favourites the selection, else the hovered picture, else the
      // cursor's, as the digits score them.
      const ids = selectedImageIds.value.length
        ? selectedImageIds.value.slice()
        : [
            allGridImages.value[hoveredImageIdx.value ?? cursorIdx.value]?.id,
          ].filter(Boolean);
      if (!ids.length) return;
      event.preventDefault();
      toggleFavourite(ids);
    } else if (
      (event.code === "KeyV" || event.key === "v" || event.key === "V") &&
      event.ctrlKey &&
      !event.metaKey &&
      !event.shiftKey &&
      !event.altKey &&
      selectedImageIds.value.length > 0 &&
      !isReadOnly.value &&
      canWriteFromKeys(event)
    ) {
      // Ctrl+V pastes the copied tags onto the selection.
      event.preventDefault();
      pasteOnSelection(selectedImageIds.value.slice());
    } else if (
      (hoveredImageIdx.value !== null || selectedImageIds.value.length > 0) &&
      !overlayOpen.value &&
      !isReadOnly.value &&
      starRating(event) !== null
    ) {
      // A bare 0-5 sets the star rating: the selection, else the hovered picture.
      const score = starRating(event);
      if (selectedImageIds.value.length > 0) {
        const ids = selectedImageIds.value.slice();
        applyScoresForSelection(ids, score);
        event.preventDefault();
        return;
      }
      const idx = hoveredImageIdx.value;
      const img = allGridImages.value[idx];
      if (img && img.id) {
        setScore(img, score);
        event.preventDefault();
      }
    }
  }

  return { onGlobalKeyPress, handleKeyDown };
}
