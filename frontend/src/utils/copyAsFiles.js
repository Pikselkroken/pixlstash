// Copy pictures to the OS clipboard as files (#2419), for pasting into a file
// manager, a mail compose window, a chat or a synced folder.
//
// The files are share copies (POST /pictures/share-copies, generation data
// stripped by default), never the vault's own files, and only the desktop app
// can put files on the clipboard: main checks every path is one of those
// copies before the OS sees it (integration_architecture.md §2.5).

import { makeShareCopies } from "../api/pictures";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorMessage } from "./apiError";

// On the receipt of copied files that still carry generation data (#2417).
export const KEPT_DATA_BADGE = {
  icon: "mdi-shield-alert-outline",
  label: "The workflow, prompt and parameters went with these files.",
};

// POST /pictures/share-copies takes at most this many ids per request.
export const MAX_COPY_FILES = 500;

/** Whether this window can copy files at all: the desktop app only. */
export function canCopyFiles() {
  return (
    typeof window !== "undefined" &&
    typeof window.pixlstashDesktop?.copyFilesToClipboard === "function"
  );
}

/**
 * Stage share copies of `pictureIds` and put them on the clipboard as files,
 * then show the receipt ("Copied 6 files") or why it failed.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} [options] - passed to makeShareCopies.
 * @returns {Promise<number>} how many files were copied (0 on failure).
 */
export async function copyPicturesAsFiles(pictureIds, options = {}) {
  const noticeStore = useNoticeStore();
  const key = "copy-as-files";
  if (pictureIds.length > MAX_COPY_FILES) {
    noticeStore.error(
      `Copy up to ${MAX_COPY_FILES} pictures at a time as files. ${pictureIds.length} are selected.`,
      { key },
    );
    return 0;
  }
  try {
    const result = await makeShareCopies(pictureIds, options);
    const paths = (result?.files || []).map((f) => f.path);
    const skipped = result?.skipped?.length || 0;
    if (!paths.length) {
      noticeStore.error(
        skipped === 1
          ? "Couldn't copy that picture: its file is missing or unreadable."
          : "Couldn't copy those pictures: their files are missing or unreadable.",
        { key },
      );
      return 0;
    }
    await window.pixlstashDesktop.copyFilesToClipboard(paths);
    const n = paths.length;
    const copied = `Copied ${n} file${n === 1 ? "" : "s"}`;
    // A file the server did not strip ("kept", or "unchanged" for a format it
    // cannot strip) may carry the workflow: say so with the badge.
    const keptData = result.files.some((f) => f.metadata !== "removed");
    noticeStore.success(
      skipped ? `${copied}. ${skipped} could not be read.` : `${copied}.`,
      { key, badge: keptData ? KEPT_DATA_BADGE : null },
    );
    return n;
  } catch (err) {
    console.error("Copying pictures as files failed for", pictureIds, err);
    noticeStore.error(
      `Couldn't copy as files. ${errorMessage(err, "Try again.")}`,
      { key },
    );
    return 0;
  }
}
