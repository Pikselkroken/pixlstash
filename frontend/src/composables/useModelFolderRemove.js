// Removing a folder in the Folders tree (#2207).
//
// The dry run always comes first. An empty folder goes at once; anything else
// opens the Remove folder dialog on the dry run's facts, and the removal sends
// back its fingerprint so the server can refuse a folder that changed since.

import { removeModelFolderDir } from "../api/modelFolders";
import { useModelFoldersStore } from "../stores/useModelFoldersStore";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorMessage } from "../utils/apiError";
import { parentPath } from "./useModelFolderMove";

/**
 * Read what removing a folder would take and break. Touches nothing.
 *
 * @param {{folderId: number, path: string}} source
 * @returns {Promise<Object>} the server's plan.
 */
export function planFolderRemove(source) {
  return removeModelFolderDir(source.folderId, {
    path: source.path,
    dryRun: true,
  });
}

/**
 * Remove a folder exactly as its plan showed it, then repaint.
 *
 * A folder open in the shelf that is gone with it gives way to the folder it
 * was in.
 *
 * @param {{folderId: number, path: string, name?: string}} source
 * @param {Object} plan - from {@link planFolderRemove}.
 * @param {boolean} permanent
 * @returns {Promise<Object>} the server's receipt.
 */
export async function removeFolder(source, plan, permanent) {
  const done = await removeModelFolderDir(source.folderId, {
    path: source.path,
    permanent,
    expect: plan.fingerprint,
  });
  const name = source.name || plan.name;
  useNoticeStore().push({
    level: "success",
    text: done.permanent
      ? `Deleted ${name}.`
      : `Moved ${name} to the ${done.trash_name}.`,
  });
  const shelf = useModelShelfStore();
  const open = shelf.folderScope;
  if (
    shelf.place === "folder" &&
    Number(open?.folderId) === Number(source.folderId) &&
    (open.path === source.path || open.path.startsWith(`${source.path}/`))
  ) {
    shelf.setFolderScope({
      folderId: source.folderId,
      path: parentPath(source.path),
    });
  }
  const folders = useModelFoldersStore();
  folders.reloadDirs();
  try {
    await Promise.all([shelf.fetchRows(), folders.refresh({ quiet: true })]);
  } catch (err) {
    // The folder is gone and that has been said; only the repaint failed.
    console.warn("[modelFolders] could not refresh after a removal:", err);
  }
  return done;
}

/**
 * The tree's *Remove folder…* and `Del`: read the folder, remove it at once
 * when it holds no file, else open the dialog on what was read.
 *
 * `permanent` applies to the empty folder only, Shift+Del's file-manager
 * meaning: with no file in it there is nothing to confirm. A folder with files
 * gets the dialog, where Shift and the typed name decide.
 *
 * @param {{folderId: number, path: string, name: string}} source
 * @param {{permanent?: boolean}} [options]
 * @returns {Promise<void>}
 */
export async function startFolderRemove(source, { permanent = false } = {}) {
  await startFoldersRemove([source], { permanent });
}

/** True when `inner` is `outer` or a folder somewhere under it. */
function isWithin(inner, outer) {
  if (Number(inner.folderId) !== Number(outer.folderId)) return false;
  return inner.path === outer.path || inner.path.startsWith(`${outer.path}/`);
}

/**
 * Remove several folders picked in the tree, one after another, each exactly
 * as {@link startFolderRemove} removes one: an empty folder goes at once, one
 * with files gets its own dialog. A folder under another picked one goes with
 * it and is not asked about twice.
 *
 * Cancelling a dialog (or leaving it for *Move the models out first…*) stops
 * the rest: the reader stopped agreeing, so nothing after it is removed. A
 * folder whose dry run fails is reported and skipped; nothing of it was
 * touched.
 *
 * @param {Array<{folderId: number, path: string, name: string}>} sources
 * @param {{permanent?: boolean}} [options]
 * @returns {Promise<void>}
 */
export async function startFoldersRemove(sources, { permanent = false } = {}) {
  const outer = sources.filter(
    (one, index) =>
      !sources.some(
        (other, at) =>
          at !== index &&
          isWithin(one, other) &&
          // Of two equal entries, the first stays.
          (other.path !== one.path || at < index),
      ),
  );
  for (const [index, source] of outer.entries()) {
    try {
      const plan = await planFolderRemove(source);
      if (Number(plan.file_count) === 0) {
        await removeFolder(source, plan, Boolean(permanent));
        continue;
      }
      const removed = await useModelShelfStore().requestFolderRemove(
        source,
        plan,
        { remaining: outer.length - index - 1 },
      );
      if (!removed) return;
    } catch (err) {
      useNoticeStore().push({
        level: "error",
        text: errorMessage(err, `Could not remove ${source.name}.`),
      });
    }
  }
}
