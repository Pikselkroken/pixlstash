// Hand the browser a file, from bytes the app already holds.
//
// The seventh hand-rolled copy of this was written before it became one
// function. They differed in the detail that matters: a synchronous
// `revokeObjectURL` can cancel the download before the browser has finished
// reading the blob the click just handed it, so the revoke is deferred, as the
// copies that worked already deferred it.

/** How long the blob stays alive after the click. The shipped copies' value. */
const REVOKE_DELAY_MS = 100;

/**
 * Save `blob` to the reader's disk under `filename`.
 *
 * @param {Blob} blob
 * @param {string} filename - what the browser offers to save it as.
 */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}

/**
 * Save a value as a pretty-printed `.json` file.
 *
 * @param {*} content - anything `JSON.stringify` takes.
 * @param {string} filename
 */
export function downloadJson(content, filename) {
  downloadBlob(
    new Blob([JSON.stringify(content, null, 2)], {
      type: "application/json",
    }),
    filename,
  );
}

/**
 * Ask where to save `blob`, then write it there.
 *
 * An export is a file the owner means to hand on, so they pick where it goes:
 * a plain download in the desktop app lands in Downloads without a word. The
 * desktop shell's native Save dialog (`beginMediaSaveAs`, the one the lightbox's
 * Save as uses) comes first, then the browser's own picker; a browser with
 * neither gets the plain download, which its own "ask where to save" governs.
 *
 * Throws when the write fails; the caller says so.
 *
 * @param {Blob} blob
 * @param {string} filename - the name the dialog suggests.
 * @returns {Promise<boolean>} false when the owner cancelled.
 */
export async function saveFileAs(blob, filename) {
  const desktop = window.pixlstashDesktop;
  if (desktop?.beginMediaSaveAs && desktop?.completeMediaSaveAs) {
    const choice = await desktop.beginMediaSaveAs(filename);
    if (choice?.canceled) return false;
    if (!choice?.saveId) {
      throw new Error("The desktop save dialog did not return a save request.");
    }
    try {
      const result = await desktop.completeMediaSaveAs(
        choice.saveId,
        await blob.arrayBuffer(),
      );
      if (!result?.saved) {
        throw new Error("The desktop app did not write the file.");
      }
    } catch (err) {
      await desktop.cancelMediaSaveAs?.(choice.saveId);
      throw err;
    }
    return true;
  }
  if (typeof window.showSaveFilePicker === "function") {
    let handle;
    try {
      handle = await window.showSaveFilePicker({ suggestedName: filename });
    } catch (err) {
      if (err?.name === "AbortError") return false;
      throw err;
    }
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return true;
  }
  downloadBlob(blob, filename);
  return true;
}

/**
 * {@link saveFileAs} for a value written as pretty-printed `.json`.
 *
 * @param {*} content - anything `JSON.stringify` takes.
 * @param {string} filename
 * @returns {Promise<boolean>} false when the owner cancelled.
 */
export function saveJsonAs(content, filename) {
  return saveFileAs(
    new Blob([JSON.stringify(content, null, 2)], {
      type: "application/json",
    }),
    filename,
  );
}
