// Drag pictures out of the desktop window as real files (#2420).
//
// A thumbnail drag in the desktop app is an OS file drag of share copies
// (POST /pictures/share-copies), never the vault's originals, so it lands in a
// file manager, a mail or a chat the way a file manager's own drag does.
// Electron's `webContents.startDrag` replaces the HTML5 drag, so the in-app
// drop targets (sets, people, projects, stacks) recognise it here instead of
// by their JSON payload: while it is in flight, `outgoingDragIds` answers with
// the pictures being dragged.
//
// No imports on purpose: media.js reads this and stays free of the API client.

let outgoing = null;
// Every file a drag-out handed out this session, by batch and file name.
const handedOut = new Set();

function desktop() {
  return typeof window !== "undefined" ? window.pixlstashDesktop : null;
}

// The batch folder and the file name. A batch is a fresh random folder, so
// this names one copy; it also survives the root being reached through a
// symlink, where main reports the resolved path and the backend did not.
function copyKey(path) {
  return String(path || "")
    .split(/[\\/]/)
    .slice(-2)
    .join("/");
}

function droppedKeys(dataTransfer) {
  const pathOf = desktop()?.getDroppedFilePath;
  if (typeof pathOf !== "function") return [];
  return Array.from(dataTransfer?.files || []).map((file) => {
    try {
      return copyKey(pathOf(file));
    } catch (err) {
      console.warn("Could not resolve a dropped file's path", file?.name, err);
      return "";
    }
  });
}

/**
 * True where a thumbnail drag can carry files out: the desktop app, talking to
 * the backend on this machine (the staging route is loopback owner only).
 */
export function canStartShareDrag() {
  if (typeof desktop()?.startShareDrag !== "function") return false;
  const host = window.location?.hostname;
  return (
    host === "127.0.0.1" ||
    host === "localhost" ||
    host === "[::1]" ||
    host === "::1"
  );
}

/**
 * The picture ids of the drag-out this drag event belongs to, or null.
 *
 * During dragover the browser hides the files, so the drag in flight is all
 * there is to go on. On drop every file must be one this drag handed out: a
 * file from elsewhere arriving while the state is stale is an import, never a
 * reason to file the previous drag's pictures.
 */
export function outgoingDragIds(dataTransfer) {
  if (!outgoing || !dataTransfer) return null;
  const types = Array.from(dataTransfer.types || []);
  if (!types.includes("Files")) return null;
  if (!dataTransfer.files?.length) return outgoing.ids;
  const keys = droppedKeys(dataTransfer);
  return keys.length && keys.every((key) => key && outgoing.keys.has(key))
    ? outgoing.ids
    : null;
}

/** True while a drag-out is in flight, for dragover, where files are hidden. */
export function isOutgoingDragActive() {
  return outgoing != null;
}

/**
 * True when every file a drop carries is one a drag-out of this session handed
 * out. Those must never import, or the copies would come back as duplicates; a
 * drop that also holds a file of the owner's still imports.
 */
export function isOwnShareCopyDrop(dataTransfer) {
  if (!handedOut.size || !dataTransfer) return false;
  const keys = droppedKeys(dataTransfer);
  return keys.length > 0 && keys.every((key) => key && handedOut.has(key));
}

// A startDrag that returns this fast did not run the drag to its end (macOS
// returns at once; so would any platform whose Electron changes that).
const EARLY_RETURN_MS = 300;
// ponytail: the backstop for a drag that ended elsewhere with no event here.
// No OS drag lasts this long in practice; make it an event if one ever does.
const STALE_DRAG_MS = 30 * 1000;

/**
 * Run the OS drag of the staged `paths` for pictures `ids`.
 *
 * Main checks every path is a share copy. On Linux and Windows Electron's
 * startDrag runs the OS drag to its end before returning, and the drag ends
 * then. If it returned at once (macOS), the drag ends at the first sign it is
 * over: a pointer press or a move with no button held (no pointer events reach
 * the page during an OS drag), a drop on this window, or a time limit.
 *
 * @param {Array<number|string>} ids
 * @param {string[]} paths
 * @param {Object} [opts]
 * @param {ArrayBuffer} [opts.icon] - PNG bytes to show under the pointer.
 * @param {Function} [opts.onEnd] - called once when the drag is over.
 */
export async function runShareDrag(ids, paths, { icon, onEnd } = {}) {
  const drag = { ids: ids.slice(), keys: new Set(paths.map(copyKey)) };
  for (const key of drag.keys) handedOut.add(key);
  outgoing = drag;
  let ended = false;
  let timer = null;
  const endOnIdleMove = (event) => {
    if (!event.buttons) end();
  };
  // After the drop's own handlers, which need the drag to know what it is.
  const endAfterDrop = () => setTimeout(end, 0);
  function end() {
    if (ended) return;
    ended = true;
    if (outgoing === drag) outgoing = null;
    clearTimeout(timer);
    window.removeEventListener("pointerdown", end, true);
    window.removeEventListener("pointermove", endOnIdleMove, true);
    window.removeEventListener("drop", endAfterDrop, true);
    onEnd?.();
  }
  const started = Date.now();
  try {
    await desktop().startShareDrag(paths, icon);
  } catch (err) {
    end();
    throw err;
  }
  if (Date.now() - started >= EARLY_RETURN_MS) {
    end();
    return;
  }
  window.addEventListener("pointerdown", end, true);
  window.addEventListener("pointermove", endOnIdleMove, true);
  window.addEventListener("drop", endAfterDrop, true);
  timer = setTimeout(end, STALE_DRAG_MS);
}

/** For tests: forget every drag. */
export function resetShareDragForTests() {
  outgoing = null;
  handedOut.clear();
}
