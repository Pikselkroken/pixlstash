/** Owner-safe window title. A read-only/share session never receives a name. */
export function libraryDocumentTitle(activeLibraryName, readOnly = false) {
  const name = readOnly ? "" : String(activeLibraryName ?? "").trim();
  return name ? `PixlStash - ${name}` : "PixlStash";
}

/**
 * How full the drive under a library is, for the title bar's meter (#2158).
 *
 * Green below 75 %, amber from 75 %, red from 90 %: the state names the status
 * token the bar is painted with, and `words` says the same thing in text so
 * the meter is not colour alone.
 *
 * @param {{total_bytes?: number|null, free_bytes?: number|null}} library
 * @returns {{used: number, state: string, words: string, free: number,
 *   total: number} | null} null when the drive was not measured, so the
 *   caller draws no meter rather than an empty one that reads as "full".
 */
export function driveFullness(library) {
  const total = Number(library?.total_bytes);
  const free = Number(library?.free_bytes);
  if (library?.total_bytes == null || library?.free_bytes == null) return null;
  if (!(total > 0) || !(free >= 0)) return null;
  const used = Math.min(100, Math.max(0, Math.round((1 - free / total) * 100)));
  if (used >= 90) return { used, state: "error", words: "almost full", free, total };
  if (used >= 75) {
    return { used, state: "warning", words: "getting full", free, total };
  }
  return { used, state: "success", words: "plenty of room", free, total };
}
