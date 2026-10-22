// Where a model file dropped in from outside the app lands.
//
// The window-level handler (`useWindowFileImport`) claims a model-file drop in
// the capture phase, before any component sees it, so a surface says what a drop
// on it means through a data attribute that handler reads off the drop target:
//
//   data-model-drop-folder="<model_folder.id>"
//       A shelf folder header and its rows: the file is copied into that folder.
//   data-model-drop-models="<model id | sha256> …"
//       A workflow card, or a workflow set card on the shelf grid: the models it
//       holds. The file goes to the default folder when that is where they are,
//       and the owner is asked when they are somewhere else.
//
// Anywhere else is the default folder, the server's own choice when no
// destination is named.

import { onMounted, onUnmounted, readonly, ref } from "vue";
import { listAdapters, listCheckpoints } from "../api/modelShelf";
import { basename, useModelFoldersStore } from "../stores/useModelFoldersStore";
import { isFileDrag, isInternalImageDrag } from "../utils/media.js";

const DROP_TARGET_SELECTOR =
  "[data-model-drop-folder], [data-model-drop-models]";

/** The element a drop landed on that names a destination, or null. */
export function modelDropTargetOf(target) {
  return target instanceof Element
    ? target.closest(DROP_TARGET_SELECTOR)
    : null;
}

/** A drag of files from outside the app, never one of our own payloads. */
export function isExternalFileDrag(dataTransfer) {
  return isFileDrag(dataTransfer) && !isInternalImageDrag(dataTransfer);
}

/**
 * The folders holding a present copy of any of the named models, with how many
 * of them each holds.
 *
 * @param {Array<Object>} rows - shelf rows, each with `id`, `sha256` and
 *   `locations`.
 * @param {Set<string>} tokens - model ids and sha256s, as strings.
 * @param {Set<number>} eligibleIds - the folders a file may be added to.
 * @returns {Map<number, number>} folder id to model count.
 */
export function foldersHolding(rows, tokens, eligibleIds) {
  const counts = new Map();
  for (const row of rows) {
    if (!tokens.has(String(row.id)) && !tokens.has(row.sha256)) continue;
    const folderIds = new Set(
      (row.locations ?? [])
        .filter((loc) => loc?.state === "present")
        .map((loc) => Number(loc.folder_id))
        .filter((id) => eligibleIds.has(id)),
    );
    for (const id of folderIds) counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return counts;
}

// The one question in flight: { fileLabel, options, resolve } | null.
const pending = ref(null);

/** Read-only handle for `ModelDropFolderDialog`. */
export const modelDropChoice = readonly(pending);

/**
 * Settle the open question.
 *
 * @param {number|undefined} folderId - the chosen folder, or undefined for
 *   Cancel, which drops the import.
 */
export function settleModelDropChoice(folderId) {
  pending.value?.resolve(folderId);
  pending.value = null;
}

function ask(fileLabel, options) {
  // A second drop while one is being asked about cancels the first rather than
  // queueing behind a dialog the owner may not have seen yet.
  settleModelDropChoice(undefined);
  return new Promise((resolve) => {
    pending.value = { fileLabel, options, resolve };
  });
}

/**
 * The folder a model drop should be copied into.
 *
 * @param {Element|null} el - from {@link modelDropTargetOf}.
 * @param {string} fileLabel - what is being added, for the question.
 * @returns {Promise<number|null|undefined>} a folder id, null for the default
 *   folder, undefined when the owner cancelled.
 */
export async function resolveModelDropFolder(el, fileLabel) {
  if (!el) return null;
  if (el.dataset.modelDropFolder) return Number(el.dataset.modelDropFolder);
  const tokens = new Set(
    (el.dataset.modelDropModels || "").split(/\s+/).filter(Boolean),
  );
  if (!tokens.size) return null;

  const foldersStore = useModelFoldersStore();
  if (!foldersStore.loaded) await foldersStore.refresh({ quiet: true });
  const eligible = new Map(
    foldersStore.defaultCandidates.map((folder) => [Number(folder.id), folder]),
  );
  // Read fresh rather than from the shelf store: that one holds only the blocks
  // its filters fetched, and the Workflows screen may never have opened it.
  const rows = (await Promise.all([listAdapters(), listCheckpoints()])).flat();
  const counts = foldersHolding(rows, tokens, new Set(eligible.keys()));
  const defaultFolder = foldersStore.defaultFolder;
  const defaultId = defaultFolder ? Number(defaultFolder.id) : null;
  if (!counts.size || counts.has(defaultId)) return null;

  const options = [...counts]
    .sort((a, b) => b[1] - a[1])
    .map(([id, count]) => ({
      id,
      label: `${basename(eligible.get(id).path)} - beside ${count === 1 ? "1 of its models" : `${count} of its models`}`,
    }));
  if (defaultFolder) {
    options.push({
      id: defaultId,
      label: `${basename(defaultFolder.path)} - your default folder`,
    });
  }
  return ask(fileLabel, options);
}

/**
 * A drop highlight for one surface's targets while files are dragged in from
 * outside the app.
 *
 * Cleared on the window's own `drop`, because the import handler claims a model
 * drop there and the target's `drop` never fires.
 */
export function useFileDropHighlight() {
  const dropKey = ref(null);

  function onDragOver(key, event) {
    const dataTransfer = event.dataTransfer;
    if (!isExternalFileDrag(dataTransfer)) return;
    // Only the MIME types are readable mid-drag. A drag of nothing but pictures
    // is a library import wherever it lands, so it lights no model target; a
    // `.safetensors` (or a folder) carries no image or video type.
    const items = Array.from(dataTransfer.items ?? []);
    const mayHoldModels =
      !items.length ||
      items.some((item) => !/^(image|video)\//.test(item.type));
    if (mayHoldModels) dropKey.value = key;
  }

  function onDragLeave(key, event) {
    // A leave into one of the target's own children is not a leave: it fires
    // on every row crossed, and the light would flicker row by row.
    const into = event?.relatedTarget;
    if (into instanceof Node && event.currentTarget?.contains?.(into)) return;
    if (dropKey.value === key) dropKey.value = null;
  }

  function clear() {
    dropKey.value = null;
  }

  onMounted(() => {
    window.addEventListener("drop", clear, true);
    window.addEventListener("dragend", clear, true);
  });
  onUnmounted(() => {
    window.removeEventListener("drop", clear, true);
    window.removeEventListener("dragend", clear, true);
  });

  return { dropKey, onDragOver, onDragLeave };
}
