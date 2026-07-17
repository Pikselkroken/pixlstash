import { ref } from "vue";
import {
  extractSupportedImportFilesFromDataTransfer,
  isFileDrag,
  isVideo,
} from "../utils/media.js";

/**
 * Manages all drag-and-drop interactions in the image grid:
 * - File-import drag overlay (external files dropped onto the grid)
 * - Thumbnail native drag (dragging images out of the grid)
 * - Stack reorder drag state (stackReorderDrag, hover indicators)
 *
 * @param {Object} deps - Reactive dependencies from other composables/refs
 * @param {import("vue").Ref} deps.selectedImageIds - from useMultiSelect
 * @param {import("vue").Ref} deps.touchSelectMode - from useMultiSelect
 * @param {import("vue").Ref} deps.imageImporterRef - DOM ref to ImageImporter
 * @param {Object} deps.thumbnailRefs - { [id]: HTMLImageElement }
 * @param {Object} deps.dragPreviewRefs - { [id]: HTMLImageElement }
 * @param {Function} deps.prefetchFullImage - fn(img) — prefetches full image
 * @param {Object} props - component props (backendUrl, selectedCharacter, selectedProjectId)
 */
export function useGridDragDrop(
  {
    selectedImageIds,
    touchSelectMode,
    imageImporterRef,
    thumbnailRefs,
    dragPreviewRefs,
    prefetchFullImage,
  },
  props,
) {
  // ── Drag overlay state ────────────────────────────────────────────────────
  const dragOverlayVisible = ref(false);
  const dragOverlayMessage = "Drop files here to import";
  const dragOverlayDepth = ref(0);

  // ── Drag source tracking ─────────────────────────────────────────────────
  const dragSource = ref(null);
  const dragSourceImageIds = ref(new Set());

  function setDragSourceImageIds(ids) {
    const next = new Set(
      Array.isArray(ids) ? ids.map((id) => String(id)).filter(Boolean) : [],
    );
    dragSourceImageIds.value = next;
  }

  function clearDragSourceImageIds() {
    dragSourceImageIds.value = new Set();
  }

  function isDragSourceImage(img) {
    if (!img?.id) return false;
    return dragSourceImageIds.value.has(String(img.id));
  }

  // ── Stack reorder hover state ─────────────────────────────────────────────
  const stackReorderDrag = ref(null);
  const stackReorderHoverId = ref(null);
  const stackReorderHoverSide = ref(null);

  function setStackReorderHoverId(value) {
    stackReorderHoverId.value = value ? String(value) : null;
  }

  function setStackReorderHoverSide(value) {
    stackReorderHoverSide.value =
      value === "left" || value === "right" ? value : null;
  }

  function isStackReorderTarget(img) {
    if (!img?.id) return false;
    return stackReorderHoverId.value === String(img.id);
  }

  function isStackReorderTargetSide(img, side) {
    if (!isStackReorderTarget(img)) return false;
    return stackReorderHoverSide.value === side;
  }

  // ── Selection drag helpers ────────────────────────────────────────────────
  function getDragSelectionIds(img) {
    if (
      img &&
      selectedImageIds.value &&
      selectedImageIds.value.length > 1 &&
      selectedImageIds.value.includes(img.id)
    ) {
      return selectedImageIds.value.slice();
    }
    return img && img.id ? [img.id] : [];
  }

  function setupMultiExportDrag(event, ids) {
    if (!event?.dataTransfer || !Array.isArray(ids) || ids.length < 2) return;
    try {
      const dragData = { type: "image-ids", imageIds: ids };
      event.dataTransfer.setData("application/json", JSON.stringify(dragData));
    } catch (err) {
      console.error("[ERROR] Failed to set drag data:", err);
    }
  }

  function prepareThumbnailNativeDrag(img, event) {
    if (!img || !event) return;
    const selectionIds = getDragSelectionIds(img);
    if (selectionIds.length > 1) return;
    prefetchFullImage(img);
    if (event.pointerType === "mouse" && event.button !== 0) return;
  }

  function handleThumbnailPointerRelease(event) {
    if (dragSource.value === "grid") return;
  }

  // ── Grid file import drag ─────────────────────────────────────────────────
  function handleGridDragEnter(e) {
    // Ignore drags that originate from within the grid itself (e.g. reordering
    // images). Chrome reports "Files" in dataTransfer.types for <img> element
    // drags, which would otherwise trigger the import overlay incorrectly.
    if (dragSource.value === "grid") return;
    if (!e.dataTransfer) return;
    const types = e.dataTransfer.types ? Array.from(e.dataTransfer.types) : [];
    if (!isFileDrag(e.dataTransfer) && types.length > 0) return;
    dragOverlayDepth.value += 1;
    dragOverlayVisible.value = true;
    e.preventDefault();
  }

  function handleGridDragOver(e) {
    if (dragSource.value === "grid") return;
    if (!e.dataTransfer) return;
    const types = e.dataTransfer.types ? Array.from(e.dataTransfer.types) : [];
    if (!isFileDrag(e.dataTransfer) && types.length > 0) return;
    if (!dragOverlayVisible.value) {
      dragOverlayVisible.value = true;
    }
    e.preventDefault();
  }

  function handleGridDragLeave(e) {
    dragOverlayDepth.value = Math.max(0, dragOverlayDepth.value - 1);
    if (dragOverlayDepth.value === 0) {
      dragOverlayVisible.value = false;
    }
  }

  function clearGridDragOverlay() {
    dragOverlayDepth.value = 0;
    dragOverlayVisible.value = false;
  }

  async function handleGridDrop(e) {
    clearGridDragOverlay();

    // Ignore drag-and-drop if the source is the grid itself
    if (
      dragSource.value === "grid" ||
      e.dataTransfer.types.includes("application/json")
    ) {
      dragSource.value = null;
      return;
    }

    if (!e.dataTransfer) return;
    const files = await extractSupportedImportFilesFromDataTransfer(
      e.dataTransfer,
    );
    if (!files.length) {
      alert("No supported files found.");
      return;
    }

    dragSource.value = null;
    // Trigger import directly in ImageGrid
    if (imageImporterRef.value && files.length) {
      imageImporterRef.value.startImport(files, {
        backendUrl: props.backendUrl,
        selectedCharacterId: props.selectedCharacter,
        allPicturesId: "ALL",
        unassignedPicturesId: "UNASSIGNED",
        projectId: props.selectedProjectId ?? null,
      });
    }
  }

  // ── Thumbnail native drag ─────────────────────────────────────────────────
  function buildDragGhostElement(element) {
    if (typeof document === "undefined" || !element) return null;
    const rect = element.getBoundingClientRect?.();
    const width = Math.max(
      1,
      Math.round(rect?.width || element.clientWidth || element.width || 160),
    );
    const height = Math.max(
      1,
      Math.round(
        rect?.height || element.clientHeight || element.height || 90,
      ),
    );
    const computed =
      typeof window !== "undefined" && element instanceof Element
        ? window.getComputedStyle(element)
        : null;
    const radius = computed?.borderRadius || "0px";
    const ghost = document.createElement("div");
    ghost.style.width = `${width}px`;
    ghost.style.height = `${height}px`;
    ghost.style.borderRadius = radius;
    ghost.style.overflow = "hidden";
    ghost.style.backgroundColor = "transparent";
    ghost.style.opacity = "1";
    ghost.style.filter = "none";
    ghost.style.position = "fixed";
    ghost.style.left = "-9999px";
    ghost.style.top = "-9999px";
    ghost.style.pointerEvents = "none";
    ghost.style.zIndex = "9999";

    if (element instanceof HTMLImageElement) {
      const clone = element.cloneNode(true);
      clone.style.width = "100%";
      clone.style.height = "100%";
      clone.style.objectFit = "cover";
      clone.style.borderRadius = "inherit";
      clone.style.opacity = "1";
      clone.style.filter = "none";
      ghost.appendChild(clone);
    } else if (element instanceof HTMLVideoElement) {
      const src = element.currentSrc || element.poster || "";
      ghost.style.background = src
        ? `url("${src}") center / cover no-repeat`
        : "transparent";
    }

    document.body.appendChild(ghost);
    return { ghost, width, height };
  }

  function setDragImageFromElement(event, element) {
    if (!element || !event?.dataTransfer?.setDragImage) return;
    const ghostData = buildDragGhostElement(element);
    const width =
      ghostData?.width || element.clientWidth || element.width || 160;
    const height =
      ghostData?.height || element.clientHeight || element.height || 90;
    const dragEl = ghostData?.ghost || element;
    event.dataTransfer.setDragImage(
      dragEl,
      Math.max(1, width / 2),
      Math.max(1, height / 2),
    );
    if (ghostData?.ghost) {
      requestAnimationFrame(() => {
        if (ghostData.ghost?.parentNode) {
          ghostData.ghost.parentNode.removeChild(ghostData.ghost);
        }
      });
    }
  }

  function setDragDataForImageIds(event, imageIds) {
    if (!event?.dataTransfer) return;
    event.dataTransfer.setData(
      "application/json",
      JSON.stringify({ type: "image-ids", imageIds }),
    );
  }

  function handleThumbnailNativeDragStart(img, event) {
    if (touchSelectMode.value) {
      event.preventDefault();
      return;
    }
    dragSource.value = "grid";
    const selectionIds = getDragSelectionIds(img);
    if (selectionIds.length > 1) {
      setDragSourceImageIds(selectionIds);
      setupMultiExportDrag(event, selectionIds);
      return;
    }
    setDragSourceImageIds([img.id]);
    const target = event?.target;
    if (target instanceof HTMLImageElement) {
      setDragImageFromElement(event, target);
    }
    setDragDataForImageIds(event, [img.id]);
  }

  function handleDragEnd() {
    dragSource.value = null;
    stackReorderDrag.value = null;
    clearDragSourceImageIds();
    setStackReorderHoverId(null);
    setStackReorderHoverSide(null);
  }

  function handleContainerDragStart(img, event) {
    if (!img || !event?.dataTransfer) return;
    if (touchSelectMode.value) {
      event.preventDefault();
      return;
    }
    if (event.target && event.target.closest?.(".face-bbox-overlay")) {
      return;
    }
    const existing = event.dataTransfer.getData("application/json");
    if (existing) return;
    dragSource.value = "grid";
    const selectionIds = getDragSelectionIds(img);
    if (selectionIds.length > 1) {
      setDragSourceImageIds(selectionIds);
      setupMultiExportDrag(event, selectionIds);
      return;
    }
    setDragSourceImageIds([img.id]);
    const thumbEl = thumbnailRefs[img.id];
    if (thumbEl instanceof HTMLImageElement) {
      setDragImageFromElement(event, thumbEl);
    }
    if (isVideo(img)) {
      const previewEl = dragPreviewRefs[img.id];
      if (previewEl instanceof HTMLImageElement) {
        setDragImageFromElement(event, previewEl);
      }
    }
    setDragDataForImageIds(event, [img.id]);
  }

  return {
    // Drag overlay
    dragOverlayVisible,
    dragOverlayMessage,
    dragOverlayDepth,
    // Drag source
    dragSource,
    dragSourceImageIds,
    setDragSourceImageIds,
    clearDragSourceImageIds,
    isDragSourceImage,
    // Stack reorder hover
    stackReorderDrag,
    stackReorderHoverId,
    stackReorderHoverSide,
    setStackReorderHoverId,
    setStackReorderHoverSide,
    isStackReorderTarget,
    isStackReorderTargetSide,
    // Drag helpers (needed by template event handlers)
    prepareThumbnailNativeDrag,
    handleThumbnailPointerRelease,
    // Grid file import drag
    handleGridDragEnter,
    handleGridDragOver,
    handleGridDragLeave,
    clearGridDragOverlay,
    handleGridDrop,
    // Thumbnail native drag
    handleThumbnailNativeDragStart,
    handleDragEnd,
    handleContainerDragStart,
  };
}
