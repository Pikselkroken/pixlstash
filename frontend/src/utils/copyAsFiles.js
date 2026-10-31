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
 * Stage share copies of `pictureIds` for a gesture that hands them out as
 * files (copy, drag), reporting why when there is nothing to hand out.
 *
 * Shared by copy-as-files and drag-out (#2420) so both name, strip and refuse
 * the same way.
 *
 * @param {Array<number|string>} pictureIds
 * @param {Object} how
 * @param {"copy"|"drag"} how.verb - for the notices.
 * @param {string} how.key - the notice key.
 * @param {Object} [how.options] - passed to makeShareCopies.
 * @returns {Promise<{paths: string[], skipped: number, files: Object[]}|null>}
 *   the staged paths, how many could not be read, and the server's file
 *   entries (each with its `metadata`); null when
 *   nothing can be handed out; a notice has said why.
 */
export async function stageShareFiles(pictureIds, { verb, key, options = {} }) {
  const noticeStore = useNoticeStore();
  const Verb = verb[0].toUpperCase() + verb.slice(1);
  if (pictureIds.length > MAX_COPY_FILES) {
    noticeStore.error(
      `${Verb} up to ${MAX_COPY_FILES} pictures at a time as files. ${pictureIds.length} are selected.`,
      { key },
    );
    return null;
  }
  try {
    const result = await makeShareCopies(pictureIds, options);
    const paths = (result?.files || []).map((f) => f.path);
    const skipped = result?.skipped?.length || 0;
    if (!paths.length) {
      noticeStore.error(
        skipped === 1
          ? `Couldn't ${verb} that picture: its file is missing or unreadable.`
          : `Couldn't ${verb} those pictures: their files are missing or unreadable.`,
        { key },
      );
      return null;
    }
    return { paths, skipped, files: result.files };
  } catch (err) {
    console.error(`Staging share copies to ${verb} failed for`, pictureIds, err);
    noticeStore.error(
      `Couldn't ${verb} as files. ${errorMessage(err, "Try again.")}`,
      { key },
    );
    return null;
  }
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
  const staged = await stageShareFiles(pictureIds, {
    verb: "copy",
    key,
    options,
  });
  if (!staged) return 0;
  const { paths, skipped, files } = staged;
  try {
    await window.pixlstashDesktop.copyFilesToClipboard(paths);
  } catch (err) {
    console.error("Copying pictures as files failed for", pictureIds, err);
    noticeStore.error(
      `Couldn't copy as files. ${errorMessage(err, "Try again.")}`,
      { key },
    );
    return 0;
  }
  const n = paths.length;
  const copied = `Copied ${n} file${n === 1 ? "" : "s"}`;
  // A file the server did not strip ("kept", or "unchanged" for a format it
  // cannot strip) may carry the workflow: say so with the badge.
  const keptData = files.some((f) => f.metadata !== "removed");
  noticeStore.success(
    skipped ? `${copied}. ${skipped} could not be read.` : `${copied}.`,
    { key, badge: keptData ? KEPT_DATA_BADGE : null },
  );
  return n;
}
