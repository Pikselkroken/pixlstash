// useImportArrivalsStore.js - the pictures an import is bringing in (#2512).
//
// A drop or an Import shows up at once as a group of "arriving" tiles at the
// top of the grid, drawn from the dropped files themselves, and each tile
// carries what is happening to its file: uploading, being checked, already in
// the library, not supported, landed. The importer (ImageImporter) writes
// here; the grid's ArrivingTiles reads it. Two importers exist (the grid's
// and the sidebar's), so the state lives in a store rather than in either.
//
// Tile states:
//   uploading   bytes still going up (the run's whole upload window)
//   checking    staged; the server has not decided this file yet
//   new         checked, not in the library - imported when the run commits
//   landed      the run committed and this file is now a picture
//   duplicate   already in the library (`pictureId` names the match if known)
//   scrapheaped matches a picture in the Scrapheap (`pictureId` names it)
//   unsupported refused by name before upload, or by the server's staging
//   failed      the server could not read or ingest it

import { defineStore } from "pinia";
import { onScopeDispose, reactive } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { isSupportedImportFile } from "../utils/media";

const IMAGE_EXT = /\.(jpe?g|png|webp|gif|bmp|avif)$/i;
const VIDEO_EXT = /\.(mp4|webm|mov|avi|mkv)$/i;
const ARCHIVE_EXT = /\.zip$/i;
const CAPTION_EXT = /\.txt$/i;

let keySeq = 0;

function tileKind(name) {
  if (IMAGE_EXT.test(name)) return "image";
  if (VIDEO_EXT.test(name)) return "video";
  if (ARCHIVE_EXT.test(name)) return "archive";
  return "other";
}

function previewUrlFor(file, kind) {
  // Only formats an <img> can draw. HEIC/TIFF and video get a named surface
  // tile; the browser would fail to decode them anyway.
  if (kind !== "image" || typeof URL?.createObjectURL !== "function") {
    return null;
  }
  try {
    return URL.createObjectURL(file);
  } catch (error) {
    console.warn("Could not preview a dropped file", file?.name, error);
    return null;
  }
}

function emptyCounts() {
  return { added: 0, duplicate: 0, scrapheaped: 0, unsupported: 0, failed: 0 };
}

export const useImportArrivalsStore = defineStore("importArrivals", () => {
  /** Newest first. One entry per import still on screen. */
  const runs = reactive([]);
  /**
   * A drop is being read (a folder walk) before any import has started, and
   * the dropped files it already knows are refused by name, held for the run
   * the drop starts.
   */
  const reading = reactive({ active: false, refused: [] });

  function runById(runId) {
    return runs.find((run) => run.id === runId) || null;
  }

  function revoke(run) {
    for (const tile of run.tiles) {
      if (tile.previewUrl) URL.revokeObjectURL(tile.previewUrl);
    }
  }

  function remove(runId) {
    const index = runs.findIndex((run) => run.id === runId);
    if (index < 0) return;
    revoke(runs[index]);
    runs.splice(index, 1);
  }

  /**
   * Start a run with a tile per picture file. Returns one key per entry of
   * `files`, in order, to stream beside each file; caption sidecars get a key
   * but no tile, since they are not pictures.
   */
  function begin(runId, files, { bytesTotal = 0 } = {}) {
    remove(runId);
    const unsupported = reading.refused;
    reading.active = false;
    reading.refused = [];
    const keys = [];
    const tiles = [];
    for (const file of files) {
      const key = `${runId}:${keySeq++}`;
      keys.push(key);
      if (CAPTION_EXT.test(file.name)) continue;
      const kind = tileKind(file.name);
      tiles.push({
        key,
        name: file.name,
        kind,
        previewUrl: previewUrlFor(file, kind),
        state: "uploading",
        pictureId: null,
      });
    }
    // Refused by name before a byte was sent; shown so the drop is accounted
    // for, never streamed.
    const refused = unsupported.filter((file) => !isSupportedImportFile(file));
    for (const file of refused) {
      tiles.push({
        key: `${runId}:${keySeq++}`,
        name: file.name,
        kind: "other",
        previewUrl: null,
        state: "unsupported",
        pictureId: null,
      });
    }
    const counts = emptyCounts();
    counts.unsupported = refused.length;
    runs.unshift({
      id: runId,
      stage: "uploading",
      bytesTotal,
      bytesSent: 0,
      checked: 0,
      total: tiles.length - refused.length,
      error: null,
      counts,
      tiles,
    });
    return keys;
  }

  function setBytesSent(runId, bytes) {
    const run = runById(runId);
    if (run) run.bytesSent = bytes;
  }

  /** The server refused these files at staging (empty, unreadable, bad zip). */
  function markSkipped(runId, keys, files, skippedNames) {
    const run = runById(runId);
    if (!run || !skippedNames?.length) return;
    const pending = [...skippedNames];
    files.forEach((file, index) => {
      const at = pending.indexOf(file.name);
      if (at < 0) return;
      pending.splice(at, 1);
      const tile = run.tiles.find((t) => t.key === keys[index]);
      if (!tile || tile.state !== "uploading") return;
      tile.state = "unsupported";
      run.counts.unsupported += 1;
      run.total = Math.max(0, run.total - 1);
    });
  }

  /** Every byte is staged and handed off: what is left is being checked. */
  function markChecking(runId) {
    const run = runById(runId);
    if (!run) return;
    run.stage = "checking";
    run.bytesSent = run.bytesTotal;
    for (const tile of run.tiles) {
      if (tile.state === "uploading") tile.state = "checking";
    }
  }

  /**
   * The server's own progress. Not a count of outcomes: a zip is one tile and
   * many staged files, so only the server knows what "of how many" means.
   */
  function setProgress(runId, processed, total) {
    const run = runById(runId);
    if (!run) return;
    run.checked = processed;
    run.total = total;
  }

  /**
   * Apply the server's per-file outcomes. A zip's entries all carry the zip's
   * key, so its one tile settles on the best thing that happened inside it.
   */
  function applyOutcomes(runId, outcomes) {
    const run = runById(runId);
    if (!run || !outcomes?.length) return;
    const byKey = new Map(run.tiles.map((tile) => [tile.key, tile]));
    for (const { key, outcome, picture_id: pictureId } of outcomes) {
      if (outcome === "new") run.counts.added += 1;
      else if (outcome === "duplicate") run.counts.duplicate += 1;
      else if (outcome === "scrapheaped") run.counts.scrapheaped += 1;
      else run.counts.failed += 1;
      const tile = byKey.get(key);
      if (!tile || tile.state === "new") continue;
      tile.state = outcome === "failed" ? "failed" : outcome;
      tile.pictureId = pictureId ?? null;
    }
  }

  /** A Scrapheap match was restored: it is in the library now. */
  function markRestored(runId, tileKey) {
    const run = runById(runId);
    const tile = run?.tiles.find((t) => t.key === tileKey);
    if (!tile || tile.state !== "scrapheaped") return;
    tile.state = "duplicate";
    run.counts.scrapheaped = Math.max(0, run.counts.scrapheaped - 1);
    run.counts.duplicate += 1;
  }

  /** The run committed: what was new is now in the library. */
  function land(runId) {
    const run = runById(runId);
    if (!run) return;
    run.stage = "landed";
    for (const tile of run.tiles) {
      if (tile.state === "new") tile.state = "landed";
      // Never decided (an older backend sends no outcomes): the totals are
      // the truth, so the tile stops claiming it is still being checked.
      else if (tile.state === "checking") tile.state = "landed";
    }
  }

  function fail(runId, message) {
    const run = runById(runId);
    if (!run) return;
    run.stage = "error";
    run.error = message;
  }

  /** A drop landed; `refused` are its files already refused by name. */
  function beginReading(refused = []) {
    reading.active = true;
    reading.refused = refused;
  }

  function endReading() {
    reading.active = false;
    reading.refused = [];
  }

  /** Done, or a cancel: the group leaves and the grid keeps the pictures. */
  function dismiss(runId) {
    remove(runId);
  }

  /** The receipt stays until Done or the next navigation. */
  function dismissFinished() {
    for (const run of [...runs]) {
      if (run.stage === "landed" || run.stage === "error") remove(run.id);
    }
  }

  // A tile names the picture it matched: the next credential may not see it.
  const unsubscribeSessionReset = onSessionReset(() => {
    for (const run of [...runs]) remove(run.id);
    endReading();
  });
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    runs,
    reading,
    begin,
    setBytesSent,
    markSkipped,
    markChecking,
    setProgress,
    applyOutcomes,
    markRestored,
    land,
    fail,
    beginReading,
    endReading,
    dismiss,
    dismissFinished,
  };
});
