// Folders dragged in the Folders tree, and the move they ask for (#2206).
//
// A folder carries its own private MIME type, distinct from the model rows'
// (`MODEL_COPIES_TYPE`), so a folder drag can never be taken for a file drag by
// a drop target that only knows one of them. The drop rules and the one
// decision about whether a drop asks first live here, once.

import { ref } from "vue";

import { basename, useModelFoldersStore } from "../stores/useModelFoldersStore";
import { useModelMovesStore } from "../stores/useModelMovesStore";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorMessage } from "../utils/apiError";
import { destinationName } from "./useModelCopyDrop";

export const FOLDER_TYPE = "application/x-pixlstash-model-folder";

/** The POSIX parent of a folder path; `""` for a folder directly in the root. */
export function parentPath(path) {
  const at = String(path ?? "").lastIndexOf("/");
  return at < 0 ? "" : path.slice(0, at);
}

/**
 * Whether a destination is the source directory itself or one of its
 * descendants, in the same registered folder. Anything else is a different
 * place, even when its path happens to share a prefix.
 */
export function isWithin(source, destination) {
  if (Number(source.folderId) !== Number(destination.folderId)) return false;
  const path = destination.path ?? "";
  return path === source.path || path.startsWith(`${source.path}/`);
}

/**
 * Refused as a destination for a folder: a drive or a locked folder, the
 * directory or its own descendants, and the directory it is already in (a move
 * there does nothing, and the server would answer the name as taken).
 *
 * @param {{folderId: number, path: string}} source
 * @param {{folderId: number, path: string, locked?: boolean}|null} destination
 */
export function folderDropRefused(source, destination) {
  if (!destination || destination.locked) return true;
  if (isWithin(source, destination)) return true;
  return (
    Number(destination.folderId) === Number(source.folderId) &&
    (destination.path ?? "") === parentPath(source.path)
  );
}

/**
 * What the move does on disk, for the drop tip: the same drive renames, another
 * drive copies and removes. Measured on `device_id` as the server measures it;
 * a drive that could not be measured is never guessed as "same".
 *
 * @param {{folderId: number}} source
 * @param {{folderId: number}} destination
 * @param {Map<number, Object>} deviceByFolderId
 * @returns {string}
 */
export function folderMechanismWords(source, destination, deviceByFolderId) {
  const from = deviceByFolderId?.get(Number(source.folderId))?.device_id;
  const to = deviceByFolderId?.get(Number(destination.folderId))?.device_id;
  if (!from || !to) return "May need copying, so it can take a while";
  if (from === to) return "Moved on the same drive, nothing copied";
  return "Copied to another drive, then removed";
}

/**
 * Plan a folder move, and start it when no confirmation is needed.
 *
 * This is the one place a folder move decides whether to ask first. A move on
 * one drive is a rename: nothing is copied and nothing is freed, so the job
 * starts at once. A move across drives copies, checks and removes the source,
 * and frees space on the drive it came from, so its facts are shown first. The
 * design says a drop still asks once; if the owner wants folder drops to
 * confirm too, this `if` is the only line that changes.
 *
 * Refusals throw from the plan (or from the start), with the server's reason.
 *
 * @param {{folderId: number, path: string}} source
 * @param {{folderId: number, path: string}} destination
 * @returns {Promise<{plan: Object, started: boolean}>}
 */
export async function askFolderMove(source, destination) {
  const moves = useModelMovesStore();
  const plan = await moves.planFolder(source, destination);
  if (plan.same_drive) {
    await moves.moveFolder(source, destination);
    return { plan, started: true };
  }
  return { plan, started: false };
}

/**
 * Drop handlers for one list of folder rows: the folder counterpart of
 * `useModelCopyDrop`, with the same ring and hatch.
 *
 * A destination is `{folderId, path, locked}`, and null for a drive row. A
 * drop that needs confirming opens the Move dialog on its facts; one that does
 * not is started here and reports through the corner card.
 */
export function useFolderDrop() {
  const shelf = useModelShelfStore();
  const folders = useModelFoldersStore();
  const moves = useModelMovesStore();
  const overKey = ref(null);
  const overOk = ref(false);

  function carries(event) {
    const types = Array.from(event.dataTransfer?.types ?? []);
    return types.includes(FOLDER_TYPE) && shelf.dragFolder != null;
  }

  // A move is one job, machine-wide: a folder dropped while one runs is
  // refused here, not 409'd by the server after the pointer has let go.
  function refused(destination) {
    return moves.busy || folderDropRefused(shelf.dragFolder, destination);
  }

  function onDragOver(event, key, destination) {
    if (!carries(event)) return;
    overKey.value = key;
    overOk.value = !refused(destination);
    if (!overOk.value) {
      event.dataTransfer.dropEffect = "none";
      shelf.dropTip = null;
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const source = shelf.dragFolder;
    shelf.dropTip = {
      x: event.clientX,
      y: event.clientY,
      text: `Move ${source.name} to ${destinationName(folders.folders, destination)} · ${folderMechanismWords(source, destination, folders.deviceByFolderId)}`,
    };
  }

  function onDragLeave(event, key) {
    if (overKey.value !== key) return;
    if (event.currentTarget?.contains?.(event.relatedTarget)) return;
    overKey.value = null;
    shelf.dropTip = null;
  }

  function onDrop(event, destination) {
    if (!carries(event)) return;
    const source = shelf.dragFolder;
    const ok = !refused(destination);
    overKey.value = null;
    shelf.dropTip = null;
    // Cleared here as well as on `dragend`: a row refetched away mid-drag never
    // fires its own.
    shelf.dragFolder = null;
    if (!ok) return;
    event.preventDefault();
    event.stopPropagation();
    void dropFolder(source, destination);
  }

  /** `drop-ring` or `not-droppable` on the row under the drag, else null. */
  function dropClass(key) {
    if (overKey.value !== key) return null;
    return overOk.value ? "drop-ring" : "not-droppable";
  }

  return { onDragOver, onDragLeave, onDrop, dropClass };
}

/** Plan a dropped folder's move, starting it or handing the facts to the dialog. */
async function dropFolder(source, destination) {
  const shelf = useModelShelfStore();
  try {
    const { plan, started } = await askFolderMove(
      { folderId: Number(source.folderId), path: source.path },
      { folderId: Number(destination.folderId), path: destination.path ?? "" },
    );
    if (!started) {
      shelf.requestFolderMove(source, destination, plan);
    }
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorMessage(err, `Could not move ${basename(source.path)}.`),
    });
  }
}
