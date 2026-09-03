import { onMounted, onUnmounted } from "vue";
import {
  extractSupportedImportFilesFromDataTransfer,
  isInternalImageDrag,
  isModelFile,
  isSupportedImportFile,
} from "../utils/media.js";
import { addModelFile } from "../api/modelFiles";
import { errorDetail } from "../utils/apiError";
import { useModelFoldersStore } from "../stores/useModelFoldersStore";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useReviewSessionsStore } from "../stores/useReviewSessionsStore";
import { useSelectionStore } from "../stores/useSelectionStore";
import { resolveImportTarget } from "../utils/importTarget.js";

/**
 * One notice key per DROP, never one for the verb.
 *
 * The card is sticky for as long as the copy runs, and a copy can run for
 * minutes - so a second drop arriving mid-copy is ordinary, not exotic. A
 * shared key made the second drop coalesce onto the first one's card and then
 * dismiss it, leaving a running multi-gigabyte copy with nothing on screen and
 * a success message about the other file.
 */
let modelDropSeq = 0;

/**
 * Import what is dropped or pasted anywhere in the window.
 *
 * The grid has its own drop target, so picture drops that land inside it are
 * left alone; this is the catch-all for everywhere else. Listeners are
 * registered in the capture phase so a drop is claimed before the browser
 * navigates to the file.
 *
 * A `.safetensors` is the exception that IS claimed here wherever it lands,
 * grid included: it is not a picture on any screen, and it has its own
 * destination - see `addDroppedModelFiles`.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.sidebarRef - the sidebar, which owns the
 *   import flow and knows the project a dropped file should land in.
 */
export function useWindowFileImport({ sidebarRef }) {
  const reviewSessionsStore = useReviewSessionsStore();
  const noticeStore = useNoticeStore();
  const selectionStore = useSelectionStore();

  function isInsideImageGrid(event) {
    const target = event?.target;
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest(".image-grid, .grid-scroll-wrapper"));
  }

  function isExternalFileDragEvent(event) {
    const dataTransfer = event?.dataTransfer;
    if (!dataTransfer) return false;
    // An internal app drag (grid thumbnail → sidebar character/set/project) is
    // never a file import - bail before the files check. On the desktop shell
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

  /**
   * Everything a drop carries that anything here wants, in ONE walk.
   *
   * One walk and not two because the walk is destructive on Safari, and one
   * WALK rather than a read of `dataTransfer.files` because a dropped folder
   * is a single unreadable entry there. A trainer's output directory holds the
   * adapter *and* its samples, and reading the flat list dropped the adapter
   * silently while importing the samples.
   */
  async function partitionDrop(dataTransfer) {
    const all = await extractSupportedImportFilesFromDataTransfer(
      dataTransfer,
      {
        accept: (file) => isSupportedImportFile(file) || isModelFile(file),
      },
    );
    return {
      modelFiles: all.filter(isModelFile),
      pictureFiles: all.filter((file) => !isModelFile(file)),
    };
  }

  /**
   * Put dropped model files on the shelf, the way `Add file…` already does.
   *
   * `POST /model-files` takes a PATH, not bytes: the file is on the machine
   * running PixlStash, so the server copies it into the model store and
   * registers it - no upload. A drop hands over a `File`, which carries a name
   * and bytes but no path, and only the desktop shell can answer where it came
   * from (`webUtils.getPathForFile`, via the preload bridge). In a browser tab
   * there is nothing to resolve and nothing to send, so the drop says where the
   * gesture that does work lives instead of failing quietly.
   */
  async function addDroppedModelFiles(files) {
    const desktop =
      typeof window !== "undefined" ? window.pixlstashDesktop : null;
    const resolved = [];
    for (const file of files) {
      let path = "";
      try {
        path = desktop?.getDroppedFilePath?.(file) || "";
      } catch (err) {
        // Never fatal: one unresolvable file must not lose the others, and the
        // notice below is what the user sees either way.
        console.warn(
          `Could not resolve the path of dropped model file ${file?.name}:`,
          err,
        );
      }
      if (path) resolved.push({ name: file?.name || path, path });
    }

    if (!resolved.length) {
      noticeStore.warning(
        "A model file has to be added from the machine running PixlStash. " +
          "Open Models and use Add ▾ → Add file… to pick it there.",
        { key: `model-file-drop-${(modelDropSeq += 1)}` },
      );
      return;
    }

    const dropKey = `model-file-drop-${(modelDropSeq += 1)}`;
    /** Sticky, and re-pushed per file: a copy outlasts any countdown, and a
     * card that expires mid-copy reads as "it finished". The count is the only
     * progress there is - the route copies a whole file per request. */
    const sayCopying = (index) =>
      noticeStore.push({
        level: "info",
        text:
          resolved.length === 1
            ? `Copying ${resolved[0].name} into the model store…`
            : `Copying ${index + 1} of ${resolved.length} into the model store - ${resolved[index].name}…`,
        key: dropKey,
        timeout: 0,
      });

    const added = [];
    const failures = [];
    for (const [index, item] of resolved.entries()) {
      sayCopying(index);
      try {
        const result = await addModelFile(item.path);
        added.push(result?.filename || item.name);
      } catch (err) {
        failures.push(
          `${item.name}: ${errorDetail(err) || "could not be added"}`,
        );
      }
    }

    if (added.length) {
      // Both stores, for the reason `ModelShelf.onFilePicked` refreshes both:
      // the shelf gained a row, and the store's file count and `shelf_bytes`
      // moved with it, so the drive bands are stale too.
      // Resolved here rather than at setup: the shelf stores are for the one
      // screen, and every session installs this listener.
      await Promise.all([
        useModelShelfStore().fetchRows(),
        useModelFoldersStore().refresh({ quiet: true }),
      ]);
    }

    noticeStore.dismissByKey(dropKey);
    if (added.length) {
      noticeStore.success(
        added.length === 1
          ? `Added ${added[0]} to the shelf. The original is still where it was.`
          : `Added ${added.length} model files to the shelf. The originals are still where they were.`,
        { key: dropKey },
      );
    }
    // Reported separately rather than folded into the success line: a partial
    // drop has to name what did NOT land, or the count is the only clue.
    for (const failure of failures) {
      noticeStore.error(failure);
    }
  }

  function handleWindowDragOver(event) {
    if (!isExternalFileDragEvent(event)) return;
    // The review overlay is a modal review surface; dropping files into it
    // must never start an import. Skip preventDefault so the drag is not shown as
    // droppable here.
    if (reviewSessionsStore.overlayOpen) return;
    event.preventDefault();
  }

  async function handleWindowDrop(event) {
    if (!isExternalFileDragEvent(event)) return;
    // While the review overlay is open, swallow the drop without importing
    // (still preventDefault so the browser does not navigate to the dropped file).
    if (reviewSessionsStore.overlayOpen) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    // Decided synchronously, from the flat list, because `stopPropagation` only
    // counts while the event is still being dispatched - which is now, before
    // the walk below can tell us what a dropped folder holds. A drop of model
    // files ALONE is claimed outright, grid included, or the grid's own handler
    // would report it as unsupported while the shelf was busy accepting it.
    // Anything else is left to propagate: inside the grid its handler threads
    // the selected character into the picture import, and this one cannot.
    const dropped = Array.from(event.dataTransfer?.files || []);
    const allModels = dropped.length > 0 && dropped.every(isModelFile);
    if (allModels) event.stopPropagation();
    const insideGrid = isInsideImageGrid(event);

    // One walk, and it takes the pictures AND the model files: the flat list
    // reports a dropped folder as one unreadable entry, so a trainer's output
    // directory used to import its samples and drop the adapter in silence.
    const { modelFiles, pictureFiles } = await partitionDrop(
      event.dataTransfer,
    );

    // Started BEFORE the shelf work and never awaited with it: a model copy
    // runs for minutes, and the pictures of a mixed drop must not queue behind
    // it. Inside the grid there is nothing to start - its own handler has the
    // pictures already, with the character context this one lacks.
    if (!insideGrid && pictureFiles.length) {
      const projectId = sidebarRef.value?.currentProjectId ?? null;
      sidebarRef.value?.startLocalImport?.(pictureFiles, projectId);
    }

    if (modelFiles.length) {
      await addDroppedModelFiles(modelFiles);
      return;
    }
    if (insideGrid || pictureFiles.length) return;
    noticeStore.warning(
      "None of those files are a supported image, video or archive.",
      { key: "import-unsupported-files" },
    );
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
      // The MIME test above is the clipboard's own word for it and is wider
      // than the importer: a Photoshop file pastes as
      // `image/vnd.adobe.photoshop` and would upload in full only to be skipped
      // server-side. Same filter as the drop path.
      .filter((file) => file && isSupportedImportFile(file));
    if (!mediaFiles.length) return;
    event.preventDefault();
    const projectId = sidebarRef.value?.currentProjectId ?? null;
    // File it where you are looking. Unlike a drop, a paste has no target
    // element to read context from, so it takes it from the selection.
    sidebarRef.value?.startLocalImport?.(
      mediaFiles,
      projectId,
      resolveImportTarget(selectionStore),
    );
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
