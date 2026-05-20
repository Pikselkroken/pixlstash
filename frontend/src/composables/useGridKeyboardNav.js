import { getPictureId } from "../utils/media.js";
import { isReadOnly } from "../utils/apiClient";

/**
 * Manages keyboard navigation and keyboard-driven actions for the image grid.
 *
 * @param {Object} deps - Reactive refs from other composables / ImageGrid
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
    showSelectionBar,
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx,
    isMultiCharacterView,
    isSetOverlapView,
    hoveredImageIdx,
  },
  props,
  emit,
  {
    clearFaceSelection,
    clearSearchQuery,
    scrollCursorIntoView,
    openOverlay,
    deleteSelected,
    selectionBarRef,
    applyScoresForSelection,
    setScore,
  },
) {
  function onGlobalKeyPress(key, event) {
    if (scrollWrapper.value) {
      let newScrollTop = scrollWrapper.value.scrollTop;
      const total = allGridImages.value.length;
      const cols = Math.max(1, props.columns || 1);
      const totalRows = Math.ceil(total / cols);
      const totalHeight = totalRows * rowHeight.value;
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

  // Clear selection on ESC key
  function handleKeyDown(event) {
    const isEditableElement = (element) => {
      if (!(element instanceof HTMLElement)) return false;
      if (element.isContentEditable) return true;
      const tagName = element.tagName;
      if (tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT") {
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
    if (overlayOpen.value) return; // Ignore if overlay is open
    if (event.key === "Escape") {
      if (showSelectionBar.value) {
        // First ESC clears selection only
        selectedImageIds.value = [];
        lastSelectedImageId = null;
        cursorIdx.value = null;
        clearFaceSelection();
      } else if (isMultiCharacterView.value || isSetOverlapView.value) {
        // No images selected — ESC closes the union/intersect/overlap bar
        emit("clear-multi-selection");
      } else if (props.searchQuery && props.searchQuery.trim()) {
        // No selection active — ESC also clears search
        clearSearchQuery();
      } else {
        selectedImageIds.value = [];
        lastSelectedImageId = null;
        cursorIdx.value = null;
        clearFaceSelection();
      }
    } else if (
      ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)
    ) {
      event.preventDefault();
      const total = allGridImages.value.length;
      if (total === 0) return;
      const cols = Math.max(1, props.columns || 1);
      let newIdx = cursorIdx.value;
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
        if (event.key === "ArrowLeft") newIdx = Math.max(0, newIdx - 1);
        else if (event.key === "ArrowRight")
          newIdx = Math.min(total - 1, newIdx + 1);
        else if (event.key === "ArrowUp") newIdx = Math.max(0, newIdx - cols);
        else if (event.key === "ArrowDown")
          newIdx = Math.min(total - 1, newIdx + cols);
      }
      cursorIdx.value = newIdx;
      const cursorImg = allGridImages.value[newIdx];
      if (cursorImg && cursorImg.id) {
        if (event.shiftKey) {
          const anchorIndex =
            lastSelectedImageId != null
              ? allGridImages.value.findIndex(
                  (item) =>
                    getPictureId(item?.id) === getPictureId(lastSelectedImageId),
                )
              : newIdx;
          const start = Math.min(anchorIndex, newIdx);
          const end = Math.max(anchorIndex, newIdx);
          selectedImageIds.value = allGridImages.value
            .slice(start, end + 1)
            .map((i) => i.id)
            .filter(Boolean);
        } else if (!event.ctrlKey && !event.metaKey) {
          // Plain arrow: move cursor and select only this image
          selectedImageIds.value = [cursorImg.id];
          lastSelectedImageId = cursorImg.id;
        }
        // Ctrl+Arrow: move cursor without changing selection
      }
      scrollCursorIntoView(newIdx);
    } else if (
      (event.key === "PageDown" || event.key === "PageUp") &&
      event.shiftKey &&
      cursorIdx.value !== null
    ) {
      // Shift+PageDown/Up: extend selection by a viewport's worth of rows
      event.preventDefault();
      const total = allGridImages.value.length;
      if (total === 0) return;
      const cols = Math.max(1, props.columns || 1);
      const rowsPerPage = scrollWrapper.value
        ? Math.max(
            1,
            Math.floor(scrollWrapper.value.clientHeight / rowHeight.value),
          )
        : 5;
      const delta = rowsPerPage * cols;
      const newIdx =
        event.key === "PageDown"
          ? Math.min(total - 1, cursorIdx.value + delta)
          : Math.max(0, cursorIdx.value - delta);
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
      selectedImageIds.value = allGridImages.value
        .slice(start, end + 1)
        .map((i) => i.id)
        .filter(Boolean);
      scrollCursorIntoView(newIdx);
    } else if (event.key === " ") {
      // Space: toggle selection at cursor
      if (cursorIdx.value !== null) {
        event.preventDefault();
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
        const cursorImg = allGridImages.value[cursorIdx.value];
        if (cursorImg && cursorImg.id) {
          openOverlay(cursorImg);
        }
      }
    } else if (event.key === "g" || event.key === "G") {
      // Focus the first visible image in the grid
      event.preventDefault();
      const idx = visibleStart.value;
      const img = allGridImages.value[idx];
      if (img && img.id) {
        cursorIdx.value = idx;
        selectedImageIds.value = [img.id];
        lastSelectedImageId = img.id;
      }
    } else if (event.key === "Delete" || event.key === "Backspace") {
      if (selectedImageIds.value.length > 0 && !isReadOnly.value) {
        deleteSelected();
      }
    } else if ((event.ctrlKey || event.metaKey) && event.key === "a") {
      event.preventDefault();
      // Select all images with valid IDs from allGridImages (not just visible)
      const allIds = allGridImages.value
        .filter((img) => img && img.id)
        .map((img) => img.id);
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
      (hoveredImageIdx.value !== null || selectedImageIds.value.length > 0) &&
      !overlayOpen.value &&
      !isReadOnly.value &&
      /^[1-5]$|^0$/.test(event.key)
    ) {
      // Number key pressed, set score for hovered image
      if (selectedImageIds.value.length > 0) {
        const score = parseInt(event.key, 10);
        const ids = selectedImageIds.value.slice();
        applyScoresForSelection(ids, score);
        event.preventDefault();
        return;
      }
      const idx = hoveredImageIdx.value;
      const img = allGridImages.value[idx];
      if (img && img.id) {
        let score = parseInt(event.key, 10);
        setScore(img, score);
        event.preventDefault();
      }
    }
  }

  return { onGlobalKeyPress, handleKeyDown };
}
