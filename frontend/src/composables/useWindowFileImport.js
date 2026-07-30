import { onMounted, onUnmounted } from "vue";
import { isInternalImageDrag } from "../utils/media.js";
import { useReviewSessionsStore } from "../stores/useReviewSessionsStore";

/**
 * Import media dropped or pasted anywhere in the window.
 *
 * The grid has its own drop target, so drops that land inside it are left
 * alone; this is the catch-all for everywhere else. Listeners are registered
 * in the capture phase so a drop is claimed before the browser navigates to
 * the file.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.sidebarRef - the sidebar, which owns the
 *   import flow and knows the project a dropped file should land in.
 */
export function useWindowFileImport({ sidebarRef }) {
  const reviewSessionsStore = useReviewSessionsStore();

  function isInsideImageGrid(event) {
    const target = event?.target;
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest(".image-grid, .grid-scroll-wrapper"));
  }

  function isExternalFileDragEvent(event) {
    const dataTransfer = event?.dataTransfer;
    if (!dataTransfer) return false;
    // An internal app drag (grid thumbnail → sidebar character/set/project) is
    // never a file import — bail before the files check. On the desktop shell
    // (Electron) such a drag ALSO populates dataTransfer.files with the dragged
    // in-page image as a real File (the web does not), so without this guard the
    // window-level import handler mistakes the assign-drag for an external file
    // drop and imports the picture instead of assigning it.
    if (isInternalImageDrag(dataTransfer)) return false;
    const files = dataTransfer.files;
    if (files && files.length > 0) return true;
    const types = dataTransfer.types ? Array.from(dataTransfer.types) : [];
    return types.includes("Files") || types.includes("application/x-moz-file");
  }

  function handleWindowDragOver(event) {
    if (!isExternalFileDragEvent(event)) return;
    // The review overlay is a modal review surface; dropping files into it
    // must never start an import. Skip preventDefault so the drag is not shown as
    // droppable here.
    if (reviewSessionsStore.overlayOpen) return;
    event.preventDefault();
  }

  function handleWindowDrop(event) {
    if (!isExternalFileDragEvent(event)) return;
    // While the review overlay is open, swallow the drop without importing
    // (still preventDefault so the browser does not navigate to the dropped file).
    if (reviewSessionsStore.overlayOpen) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    if (isInsideImageGrid(event)) {
      return;
    }
    const droppedFiles = Array.from(event.dataTransfer?.files || []);
    if (!droppedFiles.length) return;
    const projectId = sidebarRef.value?.currentProjectId ?? null;
    sidebarRef.value?.startLocalImport?.(droppedFiles, projectId);
  }

  function handleWindowPaste(event) {
    // Ignore paste events originating from editable elements (text inputs etc.)
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      target?.isContentEditable
    ) {
      return;
    }
    const items = Array.from(event.clipboardData?.items || []);
    const mediaFiles = items
      .filter(
        (item) =>
          item.kind === "file" &&
          (item.type.startsWith("image/") || item.type.startsWith("video/")),
      )
      .map((item) => item.getAsFile())
      .filter(Boolean);
    if (!mediaFiles.length) return;
    event.preventDefault();
    const projectId = sidebarRef.value?.currentProjectId ?? null;
    sidebarRef.value?.startLocalImport?.(mediaFiles, projectId);
  }

  onMounted(() => {
    window.addEventListener("dragover", handleWindowDragOver, true);
    window.addEventListener("drop", handleWindowDrop, true);
    window.addEventListener("paste", handleWindowPaste, true);
  });

  onUnmounted(() => {
    window.removeEventListener("dragover", handleWindowDragOver, true);
    window.removeEventListener("drop", handleWindowDrop, true);
    window.removeEventListener("paste", handleWindowPaste, true);
  });
}
