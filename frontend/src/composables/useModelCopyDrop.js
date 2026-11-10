// Folders as drop targets for model rows dragged off the shelf (#2204).
//
// The Folders tree (in the sidebar) takes the drop, and the rules live here:
// what refuses, and what the tip under the pointer says. The drop hands the
// move to `ModelShelf`, which starts a rename on one drive at once, as a file
// manager does, and asks first only for a copy across drives.
//
// The shelf drags with the pointer, not HTML5 drag-and-drop, so a target hears
// `model-copies-over` (with the pointer in `detail`), `model-copies-leave` and
// `model-copies-drop`, and takes the drop by `preventDefault` on the first.

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

  function refused(destination) {
    return (
      !destination ||
      destination.locked ||
      allAlreadyIn(store.dragCopies.items, destination)
    );
  }

  function onDragOver(event, key, destination) {
    if (store.dragCopies == null) return;
    overKey.value = key;
    overOk.value = !refused(destination);
    // Refused, the shelf's tip goes on saying what is carried.
    if (!overOk.value) return;
    event.preventDefault();
    const comfyui = comfyuiNameWords(
      store.dragCopies,
      destination,
      folders.comfyuiReads,
    );
    store.dropTip = {
      x: event.detail.x,
      y: event.detail.y,
      text: `Move to ${destinationName(folders.folders, destination)} · ${mechanismWords(
        store.dragCopies,
        destination.folderId,
        folders.deviceByFolderId,
      )}${comfyui ? ` · ${comfyui}` : ""}`,
    };
  }

  function onDragLeave(key) {
    if (overKey.value === key) overKey.value = null;
  }

  function onDrop(event, destination) {
    if (store.dragCopies == null) return;
    const copies = store.dragCopies;
    const ok = !refused(destination);
    overKey.value = null;
    store.dropTip = null;
    store.dragCopies = null;
    if (!ok) return;
    event.preventDefault();
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
