// Folders as drop targets for model rows dragged off the shelf (#2204).
//
// The Folders tree (in the sidebar) takes the drop, and the rules live here:
// what refuses, what the tip under the pointer says, and that a drop only ASKS
// for the move.
// The Move dialog opens pre-chosen and the reader confirms with Enter: a drop
// is one slip of the pointer, and a move has no undo.

import { ref } from "vue";

import {
  folderLabels,
  useModelFoldersStore,
} from "../stores/useModelFoldersStore";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import {
  allAlreadyIn,
  comfyuiName,
  copyKey,
  formatModelSize,
  MODEL_COPIES_TYPE,
  moveMechanism,
} from "../utils/modelShelf";

/** "Models/Lora": the registered folder's name, then the subfolder. */
export function destinationName(folders, destination) {
  const root =
    folderLabels(folders).get(Number(destination.folderId)) ?? "that folder";
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
 * What a move does to the names ComfyUI lists the copies under (#2205), or
 * `""` when no name changes. A workflow saved in ComfyUI under the old name no
 * longer finds the file, so the tip says so before the drop.
 *
 * @param {Object} copies - from `movableCopies`, with its `kinds`.
 * @param {{folderId: number, path?: string}} destination
 * @param {Object} reads - `useModelFoldersStore().comfyuiReads`.
 * @returns {string}
 */
export function comfyuiNameWords(copies, destination, reads) {
  if (!reads?.known) return "";
  const changed = [];
  for (const item of copies?.items ?? []) {
    const kind = copies.kinds?.get(copyKey(item)) ?? "";
    const file = String(item.relpath ?? "").replace(/\\/g, "/").split("/").pop();
    const moved = {
      folder_id: destination.folderId,
      relpath: destination.path ? `${destination.path}/${file}` : file,
    };
    const before = comfyuiName(item, kind, reads);
    const after = comfyuiName(moved, kind, reads);
    if (before !== after) changed.push({ file, before, after });
  }
  if (!changed.length) return "";
  const [{ file, before, after }] = changed;
  const more = changed.length > 1 ? ` (and ${changed.length - 1} more)` : "";
  if (after == null) return `ComfyUI will no longer list ${file}${more}`;
  if (before == null) {
    return after === file
      ? `ComfyUI will list ${file}${more}`
      : `ComfyUI will list ${file} as ${after}${more}`;
  }
  return `ComfyUI: ${before} → ${after}${more}`;
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
    const comfyui = comfyuiNameWords(
      store.dragCopies,
      destination,
      folders.comfyuiReads,
    );
    store.dropTip = {
      x: event.clientX,
      y: event.clientY,
      text: `Move to ${destinationName(folders.folders, destination)} · ${mechanismWords(
        store.dragCopies,
        destination.folderId,
        folders.deviceByFolderId,
      )}${comfyui ? ` · ${comfyui}` : ""}`,
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
    // Cleared here as well as on `dragend`: a row refetched away mid-drag
    // never fires its own.
    store.dragCopies = null;
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
