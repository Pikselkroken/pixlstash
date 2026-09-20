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
