import { ref, watch } from "vue";

/**
 * Manages multi-image selection, touch selection mode, and face bbox selection.
 *
 * Returns reactive state and handlers that can be destructured directly into
 * a <script setup> component. Non-reactive bookkeeping variables
 * (longPressTimer, longPressMoved, touchStartPayload) are kept private inside
 * the composable.
 */
export function useMultiSelect() {
  // ── Selection state ──────────────────────────────────────────────────────
  const selectedImageIds = ref([]);
  /**
   * ID of the last image the user explicitly selected (used as range anchor for
   * Shift-click). Exposed as a ref so that callers outside the composable can
   * read/write it with .value.
   */
  const lastSelectedImageId = ref(null);
  const cursorIdx = ref(null);
  const isImageSelected = (id) =>
    selectedImageIds.value && selectedImageIds.value.includes(id);

  // ── Touch selection mode ─────────────────────────────────────────────────
  const touchSelectMode = ref(false);
  /** ref so that handleImageCardClick (in ImageGrid) can read/clear it */
  const suppressTouchClickId = ref(null);
  /** ref so that handleThumbnailClick (in ImageGrid) can read/clear it */
  const lastPointerWasTouch = ref(false);

  let longPressTimer = null;
  let longPressMoved = false;
  let touchStartPayload = null;

  function handleTouchStart(img, idx, event) {
    if (!img.id) return;
    lastPointerWasTouch.value = true;
    longPressMoved = false;
    touchStartPayload = { img, idx };
    if (touchSelectMode.value) {
      // In select mode: tap handled in handleTouchEnd — no long-press timer needed
      return;
    }
    longPressTimer = setTimeout(() => {
      if (longPressMoved) return;
      // Haptic feedback if available
      if (navigator.vibrate) navigator.vibrate(30);
      touchSelectMode.value = true;
      selectedImageIds.value = [img.id];
      lastSelectedImageId.value = img.id;
      cursorIdx.value = idx;
      touchStartPayload = null; // consumed by long-press
      // Suppress the synthesized click that fires after the long-press touchend
      suppressTouchClickId.value = img.id;
    }, 500);
  }

  function handleTouchMove() {
    longPressMoved = true;
    clearTimeout(longPressTimer);
    longPressTimer = null;
    touchStartPayload = null;
  }

  function handleTouchEnd() {
    clearTimeout(longPressTimer);
    longPressTimer = null;

    // Short tap in select mode: toggle directly here so we never rely on
    // synthesized click events, which are unreliable after touch interactions.
    if (touchSelectMode.value && touchStartPayload && !longPressMoved) {
      const { img, idx } = touchStartPayload;
      const ids = [...selectedImageIds.value];
      const pos = ids.indexOf(img.id);
      if (pos >= 0) {
        ids.splice(pos, 1);
      } else {
        ids.push(img.id);
      }
      selectedImageIds.value = ids;
      lastSelectedImageId.value = img.id;
      cursorIdx.value = idx;
      if (ids.length === 0) exitTouchSelectMode();
      // Suppress the synthesized click so it doesn't re-toggle
      suppressTouchClickId.value = img.id;
    }
    touchStartPayload = null;
  }

  function exitTouchSelectMode() {
    touchSelectMode.value = false;
    selectedImageIds.value = [];
    lastSelectedImageId.value = null;
    cursorIdx.value = null;
  }

  // Auto-exit touch-select mode whenever selection is cleared by any code path
  watch(selectedImageIds, (ids) => {
    if (touchSelectMode.value && ids.length === 0) {
      touchSelectMode.value = false;
    }
  });

  // ── Face bbox selection ──────────────────────────────────────────────────
  const selectedFaceIds = ref([]); // Array of { imageId, faceIdx, faceId }

  function isFaceSelected(imageId, faceIdx) {
    return selectedFaceIds.value.some(
      (f) => f.imageId === imageId && f.faceIdx === faceIdx,
    );
  }

  function toggleFaceSelection(imageId, faceIdx, faceId) {
    const idx = selectedFaceIds.value.findIndex(
      (f) => f.imageId === imageId && f.faceIdx === faceIdx,
    );
    if (idx !== -1) {
      selectedFaceIds.value.splice(idx, 1);
    } else {
      selectedFaceIds.value.push({ imageId, faceIdx, faceId });
    }
  }

  function clearFaceSelection() {
    selectedFaceIds.value = [];
  }

  function onFaceBboxDragStart(event, img, faceIdx, faceId) {
    // If this face is selected, drag all selected faces; else, drag just this one
    let facesToDrag = [];
    if (isFaceSelected(img.id, faceIdx) && selectedFaceIds.value.length > 0) {
      facesToDrag = selectedFaceIds.value.map((f) => ({
        imageId: f.imageId,
        faceIdx: f.faceIdx,
        faceId: f.faceId,
      }));
    } else {
      const resolvedFaceId = faceId ?? (img.faces && img.faces[faceIdx]?.id);
      if (!resolvedFaceId) {
        return;
      }
      facesToDrag = [{ imageId: img.id, faceIdx, faceId: resolvedFaceId }];
    }

    // Ensure that additional data types are preserved in the dataTransfer object
    const existingData = {};
    for (const type of event.dataTransfer.types) {
      existingData[type] = event.dataTransfer.getData(type);
    }

    // Set the application/json data
    const dragDataStr = JSON.stringify({
      type: "face-bbox",
      faceIds: facesToDrag.map((f) => f.faceId),
      imageIds: Array.from(new Set(facesToDrag.map((f) => f.imageId))),
      faces: facesToDrag,
    });
    event.dataTransfer.setData("application/json", dragDataStr);

    // Restore other data types
    for (const [type, data] of Object.entries(existingData)) {
      if (type !== "application/json") {
        event.dataTransfer.setData(type, data);
      }
    }

    event.dataTransfer.effectAllowed = "move";
  }

  // ── Shared clear ─────────────────────────────────────────────────────────
  function clearSelection() {
    selectedImageIds.value = [];
    clearFaceSelection();
    lastSelectedImageId.value = null;
  }

  return {
    // Selection state
    selectedImageIds,
    lastSelectedImageId,
    cursorIdx,
    isImageSelected,
    // Touch mode
    touchSelectMode,
    suppressTouchClickId,
    lastPointerWasTouch,
    // Touch handlers
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
    exitTouchSelectMode,
    // Face selection
    selectedFaceIds,
    isFaceSelected,
    toggleFaceSelection,
    clearFaceSelection,
    onFaceBboxDragStart,
    // Combined clear
    clearSelection,
  };
}
