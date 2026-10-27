// Folders as drop targets for model rows dragged off the shelf (#2204).
//
// The Folders tree (in the sidebar) and the folder view's subfolder rows (in
// the shelf) both take the drop, so the rules live here once: what refuses,
// what the tip under the pointer says, and that a drop only ASKS for the move.
// The Move dialog opens pre-chosen and the reader confirms with Enter: a drop
// is one slip of the pointer, and a move has no undo.

import { ref } from "vue";

import {
  basename,
  useModelFoldersStore,
} from "../stores/useModelFoldersStore";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import {
  allAlreadyIn,
  formatModelSize,
  MODEL_COPIES_TYPE,
  moveMechanism,
} from "../utils/modelShelf";

/** "Models/Lora": the registered folder's name, then the subfolder. */
export function destinationName(folders, destination) {
  const folder = folders.find(
    (one) => Number(one.id) === Number(destination.folderId),
  );
  const root = folder
    ? folder.tool || basename(folder.path) || folder.path
    : "that folder";
  return destination.path ? `${root}/${destination.path}` : root;
}

/**
 * What the move does on disk, in words: the tip's second half.
 *
 * @param {Object} copies - from `movableCopies`.
 * @param {number} folderId - the destination's registered folder.
 * @param {Map<number, Object>} deviceByFolderId
 * @returns {string}
 */
export function mechanismWords(copies, folderId, deviceByFolderId) {
  const { how, freed, drives } = moveMechanism(
    copies,
    folderId,
    deviceByFolderId,
  );
  if (how === "rename") return "Renamed on the same drive, nothing copied";
  if (how === "copy") {
    return `Copied to another drive; ${drives.join(", ")} gets ${formatModelSize(freed)} back`;
  }
  return "May need copying, so it can take a while";
}

/**
 * Handlers for one list of folder rows.
 *
 * A destination is `{folderId, path, locked}`; null stands for a drive row,
 * which is a heading and never a place. A locked folder, a drive, and the
 * folder every dragged copy is already in refuse with the shared
 * `.not-droppable` hatch.
 */
export function useModelCopyDrop() {
  const store = useModelShelfStore();
  const folders = useModelFoldersStore();
  const overKey = ref(null);
  const overOk = ref(false);

  function carries(event) {
    const types = Array.from(event.dataTransfer?.types ?? []);
    return types.includes(MODEL_COPIES_TYPE) && store.dragCopies != null;
  }

  function refused(destination) {
    return (
      !destination ||
      destination.locked ||
      allAlreadyIn(store.dragCopies.items, destination)
    );
  }

  function onDragOver(event, key, destination) {
    if (!carries(event)) return;
    overKey.value = key;
    overOk.value = !refused(destination);
    if (!overOk.value) {
      event.dataTransfer.dropEffect = "none";
      store.dropTip = null;
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    store.dropTip = {
      x: event.clientX,
      y: event.clientY,
      text: `Move to ${destinationName(folders.folders, destination)} · ${mechanismWords(
        store.dragCopies,
        destination.folderId,
        folders.deviceByFolderId,
      )}`,
    };
  }

  function onDragLeave(event, key) {
    if (overKey.value !== key) return;
    if (event.currentTarget?.contains?.(event.relatedTarget)) return;
    overKey.value = null;
    store.dropTip = null;
  }

  function onDrop(event, destination) {
    if (!carries(event)) return;
    const copies = store.dragCopies;
    const ok = !refused(destination);
    overKey.value = null;
    store.dropTip = null;
    if (!ok) return;
    event.preventDefault();
    event.stopPropagation();
    store.requestMove(copies, {
      folderId: Number(destination.folderId),
      path: destination.path ?? "",
    });
  }

  /** `drop-ring` or `not-droppable` on the row under the drag, else null. */
  function dropClass(key) {
    if (overKey.value !== key) return null;
    return overOk.value ? "drop-ring" : "not-droppable";
  }

  return { onDragOver, onDragLeave, onDrop, dropClass };
}
