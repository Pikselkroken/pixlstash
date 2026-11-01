import { ref } from "vue";
import {
  extractSupportedImportFilesFromDataTransfer,
  isFileDrag,
  isInternalImageDrag,
  isVideo,
  setInternalDragPayload,
} from "../utils/media.js";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useSelectionStore } from "../stores/useSelectionStore";
import { useProjectStore } from "../stores/useProjectStore";
import { resolveImportTarget } from "../utils/importTarget.js";
import { isReadOnly } from "../utils/apiClient";
import { stageShareFiles } from "../utils/copyAsFiles.js";
import {
  canStartShareDrag,
  isOwnShareCopyDrop,
  runShareDrag,
} from "../utils/shareDrag.js";

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
 * @param {Function} deps.prefetchFullImage - fn(img) - prefetches full image
 * @param {Function} [deps.isImageGhosted] - fn(img) - true for a tile held in
 *   the grid only while its move-to-Scrapheap can still be undone.
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
    reviewOverlayOpen,
    isImageGhosted = () => false,
  },
  props,
) {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  // Drag/drop failures are reported through the notice surface rather than a
  // blocking native alert() (docs/design/notice-surface.md §1). Called during
  // the host component's setup, so Pinia is active.
  const noticeStore = useNoticeStore();

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
      setInternalDragPayload(event.dataTransfer, {
        type: "image-ids",
        imageIds: ids,
      });
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

  function handleThumbnailPointerRelease() {
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

  function handleGridDragLeave() {
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
    if (dragSource.value === "grid" || isInternalImageDrag(e.dataTransfer)) {
      dragSource.value = null;
      return;
    }

    if (!e.dataTransfer) return;
    // Our own drag-out dropped back: the window-level handler says so.
    if (isOwnShareCopyDrop(e.dataTransfer)) return;
    // Read before the walk awaits: the drop's payload is gone after dispatch.
    // A drop with no files in it at all is not a drop of unsupported files.
    // On X11 a file manager hands a very large selection (thousands of paths)
    // over incrementally, which Chromium cannot read, so the drop arrives as
    // a scrap of text; saying "not supported" there sent the owner hunting a
    // regression in the importer.
    const carriedFiles = isFileDrag(e.dataTransfer);
    const files = await extractSupportedImportFilesFromDataTransfer(
      e.dataTransfer,
    );
    if (!files.length) {
      noticeStore.warning(
        carriedFiles
          ? "None of those files are a supported image, video or archive."
          : "That drop carried no files. A very large drag can lose them on the way in: drag fewer at a time, or use Import.",
        { key: "import-unsupported-files" },
      );
      return;
    }

    dragSource.value = null;
    // Trigger import directly in ImageGrid
    if (imageImporterRef.value && files.length) {
      // `startImport` reads `setId` / `characterId` and nothing else. This
      // used to pass `selectedCharacterId` (plus the two sentinel ids), none of
      // which it looks at, so a drop into a character or set view was filed
      // nowhere despite the comment above claiming it threaded the context.
      imageImporterRef.value.startImport(files, {
        backendUrl: props.backendUrl,
        projectId: projectStore.selectedProjectId ?? null,
        ...resolveImportTarget(selectionStore),
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
      Math.round(rect?.height || element.clientHeight || element.height || 90),
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
    setInternalDragPayload(event.dataTransfer, {
      type: "image-ids",
      imageIds,
    });
  }

  // ── Drag-out as files (#2420) ─────────────────────────────────────────────
  // In the desktop app a picture drag is an OS file drag of the original files,
  // so it lands in a file manager, a mail or a chat like a file manager's own
  // drag, and the in-app targets take it too (utils/shareDrag.js).

  function wantsShareDrag(ids) {
    return ids.length > 0 && canStartShareDrag() && !isReadOnly.value;
  }

  /** The thumbnail under the pointer as PNG bytes for the OS drag, or undefined. */
  function dragIconPng(element) {
    if (!(element instanceof HTMLImageElement) || !element.naturalWidth) {
      return undefined;
    }
    try {
      // ponytail: one fixed ghost size; size it from the tile if it looks off.
      const scale = Math.min(
        1,
        96 / Math.max(element.naturalWidth, element.naturalHeight),
      );
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(element.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(element.naturalHeight * scale));
      canvas.getContext("2d").drawImage(element, 0, 0, canvas.width, canvas.height);
      const base64 = canvas.toDataURL("image/png").split(",")[1];
      return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0)).buffer;
    } catch (err) {
      console.warn("No drag icon from the thumbnail; the app icon stands in", err);
      return undefined;
    }
  }

  function startShareDrag(img, event, ids) {
    // The OS drag replaces this one, and it can only start once the files are
    // linked under their names, which takes a request.
    event.preventDefault();
    dragSource.value = "grid";
    setDragSourceImageIds(ids);
    const icon = dragIconPng(thumbnailRefs[img.id]);
    // Let go before the copies were ready: no drag. A cancelled dragstart
    // leaves the button's own release to reach the page.
    let released = false;
    const release = () => {
      released = true;
    };
    window.addEventListener("pointerup", release, true);
    window.addEventListener("mouseup", release, true);
    const body = typeof document !== "undefined" ? document.body : null;
    if (body) body.style.cursor = "progress";
    void (async () => {
      let staged;
      try {
        // The originals, hard-linked under the copy-and-paste names, so even a
        // whole view starts at once.
        staged = await stageShareFiles(ids, { verb: "drag", key: "drag-out" });
      } finally {
        if (body) body.style.cursor = "";
        window.removeEventListener("pointerup", release, true);
        window.removeEventListener("mouseup", release, true);
      }
      if (!staged || released) {
        handleDragEnd();
        return;
      }
      if (staged.skipped) {
        noticeStore.warning(
          `${staged.skipped} could not be read and ${staged.skipped === 1 ? "was" : "were"} left out of the drag.`,
          { key: "drag-out" },
        );
      }
      try {
        await runShareDrag(ids, staged.paths, { icon, onEnd: handleDragEnd });
      } catch (err) {
        console.error("Starting the drag-out failed", { ids, err });
        noticeStore.error(
          `Couldn't start the drag. ${err?.message || err}`,
          { key: "drag-out" },
        );
      }
    })();
  }

  function handleThumbnailNativeDragStart(img, event) {
    // The tile's own capture handler already took this drag out as files.
    if (event.defaultPrevented) return;
    // No grid drag-and-drop while the Review Sessions overlay is up (it stays
    // mounted over the grid as a modal review surface).
    if (reviewOverlayOpen?.value) {
      event.preventDefault();
      return;
    }
    if (touchSelectMode.value) {
      event.preventDefault();
      return;
    }
    const selectionIds = getDragSelectionIds(img);
    if (wantsShareDrag(selectionIds)) {
      startShareDrag(img, event, selectionIds);
      return;
    }
    dragSource.value = "grid";
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
    // A ghosted tile is already in the Scrapheap; dragging it into a set or a
    // character would file a picture that is on its way out. The card carries
    // `inert`, which stops this in browsers that support it - this is the same
    // rule stated where it can be tested.
    if (isImageGhosted(img)) {
      event.preventDefault();
      return;
    }
    if (reviewOverlayOpen?.value) {
      event.preventDefault();
      return;
    }
    if (touchSelectMode.value) {
      event.preventDefault();
      return;
    }
    if (event.target && event.target.closest?.(".face-bbox-overlay")) {
      return;
    }
    const existing = event.dataTransfer.getData("application/json");
    if (existing) return;
    const selectionIds = getDragSelectionIds(img);
    if (wantsShareDrag(selectionIds)) {
      startShareDrag(img, event, selectionIds);
      return;
    }
    dragSource.value = "grid";
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
