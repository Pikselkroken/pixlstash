<script setup>
import { nextTick, onBeforeUnmount, ref } from "vue";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import { sleep } from "../../utils/utils";
import { useTasksStore } from "../../stores/useTasksStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useImportArrivalsStore } from "../../stores/useImportArrivalsStore";
import {
  cancelStaging,
  commitStaging,
  fetchStagingStatus,
  openStagingSession,
  stageFiles,
} from "../../api/pictureImport";
// The Scrapheap restore offer below reuses the SHIPPED restore route rather than
// a second restore path, so it comes from the pictures resource module (the
// import module owns the staging session, not the whole picture domain).
import { restoreScrapheap } from "../../api/pictures";
import { errorDetail, errorMessage } from "../../utils/apiError";

// ── Async streaming-staging import (#459) ────────────────────────────────────
// This component owns the two-phase import over the finalised streaming-staging
// session contract. It renders nothing itself: what the user sees is the
// grid's arriving tiles (useImportArrivalsStore → ArrivingTiles, #2512) and,
// for anyone who navigates away, the Tasks-tab row, both live from the drop.
//   Phase A "staging / unsafe":  open a staging session, then STREAM the files
//     into it batch by batch, each with its arriving tile's key. A beforeunload
//     guard is armed (leaving now aborts the upload) and the band says so; its
//     Cancel (and the Tasks row's) discards the not-yet-committed session.
//   Safe transition:  once every byte is staged, commit hands off to a
//     background PictureImportTask; the guard drops and the tiles turn from
//     "uploading" to "checking".
//   Phase B "importing / safe":  polled BY staging id. Each file's outcome
//     (new / duplicate / scrapheaped / failed) marks its tile as it is
//     decided. Refresh is harmless. The grid refreshes off the backend's
//     CHANGED_PICTURES / PICTURE_IMPORTED WS broadcast (no results payload).
//   Scrapheap matches:  a staged file whose content matches a SOFT-DELETED
//     picture is reported in its own bucket. It is not imported again (that
//     would put a second copy of every scrapheaped picture back on disk) and not
//     restored behind the user's back either. Completion pushes ONE sticky
//     notice whose action calls the shipped POST /pictures/scrapheap/restore.
// ALL import backend calls are isolated behind ../../api/pictureImport.js; the
// restore offer reuses api/pictures.js rather than growing a second restore.
// The public API (startImport + the four emits) is unchanged so existing call
// sites keep working. Drop-target association (project / set / character) is
// passed through startImport options into openStagingSession and applied
// server-side on commit. There is no per-file results[] array - the grid
// refreshes off the WS broadcast, so import-finished carries counts but an
// empty results list. Media, .zip archives and .txt sidecars all stream.

const props = defineProps({
  backendUrl: { type: String, default: () => API_BASE_URL },
});

const emit = defineEmits([
  "import-started",
  "import-finished",
  "import-cancelled",
  "import-error",
]);

const tasksStore = useTasksStore();
const noticeStore = useNoticeStore();
const arrivals = useImportArrivalsStore();

// One coalescing key for the whole offer, so back-to-back imports replace the
// standing offer instead of stacking cards (§9.1). The newest import's ids win,
// which is what the user is looking at.
const SCRAPHEAP_OFFER_KEY = "import-scrapheap-offer";

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/**
 * The completion headline, built from the buckets it actually names.
 *
 * The old line asked "did anything import?" and answered "All files were
 * duplicates", which became a lie the moment a third outcome existed: files
 * matching a picture in the Scrapheap are not duplicates and saying so hides
 * the one thing the user can act on.
 */
function summariseImport({ imported, duplicate, scrapheaped }) {
  if (imported > 0) return `Imported ${plural(imported, "image")}`;
  if (scrapheaped > 0 && duplicate > 0)
    return "Nothing new: already in your library or Scrapheap";
  if (scrapheaped > 0)
    return `Already in your Scrapheap (${plural(scrapheaped, "file")})`;
  if (duplicate > 0) return "All files were duplicates";
  return "Nothing to import";
}

/**
 * Offer the restore. Deliberately an offer and not an automatic restore: the
 * user put those pictures in the Scrapheap on purpose, so bringing them back
 * without asking would be its own surprise. The import already refused to write
 * a second copy, which is the part that must not wait for a click.
 */
function offerScrapheapRestore(fileCount, pictureIds) {
  const ids = Array.isArray(pictureIds)
    ? pictureIds.filter((id) => id != null)
    : [];
  if (!fileCount || !ids.length) return;
  noticeStore.push({
    level: "info",
    key: SCRAPHEAP_OFFER_KEY,
    text:
      fileCount === 1
        ? "1 file is already in your Scrapheap, so it was not imported again."
        : `${fileCount} files are already in your Scrapheap, so they were not imported again.`,
    action: {
      label: `Restore ${plural(ids.length, "picture")}`,
      handler: async () => {
        try {
          const data = await restoreScrapheap(ids);
          // The offer is not a promise: retention can sweep a match away
          // between the import and the click. Report what came back.
          const restored = Number(data?.restored_count ?? 0);
          if (restored === ids.length) {
            noticeStore.push({
              level: "success",
              text: `Restored ${plural(restored, "picture")} from the Scrapheap.`,
            });
          } else if (restored > 0) {
            noticeStore.push({
              level: "warning",
              text: `Restored ${restored} of ${ids.length}. The rest had already left the Scrapheap.`,
            });
          } else {
            noticeStore.push({
              level: "warning",
              text: "Nothing was restored. Those pictures had already left the Scrapheap.",
            });
          }
        } catch (error) {
          console.error(
            "Restoring the scrapheaped import matches failed.",
            error,
          );
          noticeStore.push({
            level: "error",
            text:
              errorDetail(error) ||
              "Could not restore those pictures. Please try again.",
          });
        }
      },
    },
  });
}

// Whole-flow gate (Phase A + Phase B) used for re-entrancy.
const importActive = ref(false);

const importProgress = ref(0);
const importTotal = ref(0);
const uploadBytesUploaded = ref(0);
const uploadBytesTotal = ref(0);
const importPhase = ref(""); // uploading | processing | done | duplicates | cancelled | error
const cancelImport = ref(false);
const currentImportController = ref(null);

// Unique id for the current import: its Tasks-tab row and its arriving group.
let importRunId = null;
// The current staging session id (Phase A/B backend handle), or null when idle.
let currentStagingId = null;
// True from the commit request on: the bytes are handed over, cancel is over.
let committing = false;
let hideTimerId = null;

// ── beforeunload guard (the hard backstop for the unsafe upload window) ───────
let guardArmed = false;
function beforeUnloadGuard(e) {
  // Standard pattern: preventDefault + assign returnValue makes the browser show
  // its native "leave site?" prompt. We only arm this WHILE uploading (Phase A).
  e.preventDefault();
  e.returnValue = "";
  return "";
}
function armGuard() {
  if (guardArmed || typeof window === "undefined") return;
  window.addEventListener("beforeunload", beforeUnloadGuard);
  guardArmed = true;
}
function disarmGuard() {
  if (!guardArmed || typeof window === "undefined") return;
  window.removeEventListener("beforeunload", beforeUnloadGuard);
  guardArmed = false;
}

const TERMINAL_IMPORT_PHASES = new Set([
  "done",
  "duplicates",
  "cancelled",
  "error",
]);

function clearHideTimer() {
  if (hideTimerId !== null) {
    clearTimeout(hideTimerId);
    hideTimerId = null;
  }
}

function finalizeCancelled() {
  clearHideTimer();
  disarmGuard();
  importPhase.value = "cancelled";
  importActive.value = false;
  cancelImport.value = false;
  currentImportController.value = null;
  if (importRunId) {
    tasksStore.clearImportRun(importRunId);
    tasksStore.unregisterImportAbort(importRunId);
    arrivals.dismiss(importRunId);
  }
  // Discard the not-yet-committed staging session (best-effort) so its streamed
  // files are cleaned up server-side.
  if (currentStagingId) {
    const stagingId = currentStagingId;
    currentStagingId = null;
    cancelStaging({ backendUrl: props.backendUrl, stagingId }).catch((err) => {
      console.warn("Failed to cancel staging session", stagingId, err);
    });
  }
  emit("import-cancelled");
}

function finalizeError(message) {
  clearHideTimer();
  disarmGuard();
  importPhase.value = "error";
  importActive.value = false;
  if (importRunId) {
    // The arriving band holds the reason until it is dismissed; the task row
    // is the record for anyone who has navigated away from the grid.
    arrivals.fail(importRunId, message);
    tasksStore.setImportRun(importRunId, {
      status: "failed",
      percent: 0,
      current: importProgress.value,
      total: importTotal.value,
      message: `Import failed: ${message}`,
      label: "Import failed",
    });
    const runId = importRunId;
    hideTimerId = setTimeout(() => {
      tasksStore.clearImportRun(runId);
      hideTimerId = null;
    }, 8000);
  }
  cancelImport.value = false;
  currentImportController.value = null;
  if (importRunId) tasksStore.unregisterImportAbort(importRunId);
  emit("import-error", { message });
}

// The single client-side abort path, shared by the arriving band's Cancel and
// the Tasks-tab cancel affordance (via tasksStore.abortImportRun → this handler,
// registered in startImport). It stops the in-flight upload by aborting the
// active request controller; the batch loop's cancel checks then unwind cleanly
// through finalizeCancelled (no error toast). This can only stop the PRE-COMMIT
// upload - once committed, the import is a background server task the client
// cannot stop (see the abortable notes in startImport / useTasksStore).
function handleCancelImport() {
  // Once the commit is sent there is nothing a client abort can stop.
  if (!importActive.value || committing) return;
  cancelImport.value = true;
  if (currentImportController.value) {
    try {
      currentImportController.value.abort();
    } catch (err) {
      console.warn("Failed to abort current import", err);
    }
  }
}

function logImportTrace(message, details = null) {
  if (details !== null && details !== undefined) {
    console.info(`[IMPORT TRACE] ${message}`, details);
    return;
  }
  console.info(`[IMPORT TRACE] ${message}`);
}

async function startImport(files, options = {}) {
  if (!files || !files.length) return;
  if (importActive.value) {
    // Recover from a stale terminal state left by the previous run.
    if (TERMINAL_IMPORT_PHASES.has(importPhase.value)) {
      clearHideTimer();
      importActive.value = false;
    } else {
      // Said, not swallowed: this importer runs one import at a time, and a
      // server-side import can take minutes after the upload is done.
      console.info(
        "Import request ignored because another import is in progress.",
      );
      noticeStore.warning(
        "Another import is still running here. Drop these again when it finishes.",
        { key: "import-busy" },
      );
      return;
    }
  }

  clearHideTimer();
  cancelImport.value = false;
  currentStagingId = null;
  committing = false;
  // Drop any lingering handler from a previous run before minting a new id.
  if (importRunId) tasksStore.unregisterImportAbort(importRunId);
  importRunId = `import-${crypto?.randomUUID?.() ?? Date.now().toString(36)}`;
  const totalBytes = files.reduce((sum, f) => sum + (f.size || 0), 0);
  // The acknowledgement: the arriving tiles appear on the drop itself, from
  // the files the browser already holds, before anything is sent. Keys are
  // streamed beside each file so the server can name every outcome.
  const fileKeys = arrivals.begin(importRunId, files, {
    bytesTotal: totalBytes,
  });

  if (isReadOnly.value) {
    importActive.value = true;
    finalizeError("Importing is not available with a read-only token.");
    return;
  }

  // Make the (otherwise dead) import-abort subsystem live: register the real
  // client-side abort so tasksStore.abortImportRun(runId) actually stops the
  // in-flight upload. Unregistered
  // on every terminal path (finalizeCancelled / finalizeError / completion) and
  // on unmount.
  tasksStore.registerImportAbort(importRunId, () => handleCancelImport());
  importActive.value = true;
  importProgress.value = 0;
  importTotal.value = files.length;
  uploadBytesUploaded.value = 0;
  uploadBytesTotal.value = totalBytes;
  importPhase.value = "uploading";
  currentImportController.value = null;
  // Arm the unsafe-window guard: leaving/refreshing now aborts the upload.
  armGuard();
  // The Tasks row from the drop, not from the commit: it is the record for
  // anyone who leaves the grid, and while bytes are in flight it can cancel.
  // Reported on every whole percent, so a large batch moves the row while it
  // uploads rather than jumping at its end.
  let reportedPercent = -1;
  const reportUpload = () => {
    const percent = uploadBytesTotal.value
      ? Math.min(100, (uploadBytesUploaded.value / uploadBytesTotal.value) * 100)
      : 0;
    if (Math.floor(percent) === reportedPercent) return;
    reportedPercent = Math.floor(percent);
    tasksStore.setImportRun(importRunId, {
      status: "running",
      percent,
      current: 0,
      total: importTotal.value,
      message: "Uploading - keep this tab open",
      label: "Uploading pictures",
      abortable: true,
    });
  };
  reportUpload();
  emit("import-started", {
    fileCount: files.length,
    projectId: options.projectId ?? null,
  });

  logImportTrace("Import started", {
    fileCount: files.length,
    totalUploadBytes: uploadBytesTotal.value,
    projectId: options.projectId ?? null,
  });

  const BATCH_SIZE = 100;
  const MAX_RETRIES = 3;
  const MIN_TIMEOUT_MS = 60000;
  const TIMEOUT_PER_FILE_MS = 4000;
  const TIMEOUT_PER_MB_MS = 100;
  const NO_PROGRESS_ABORT_MS = 15000;
  const overrideTimeout =
    typeof options.timeoutMs === "number" && options.timeoutMs > 0
      ? options.timeoutMs
      : null;

  let uploadedBytesAccum = 0;

  try {
    // ── Phase A: open a staging session, then STREAM every batch into it. The
    // bytes are unsafe until commit - the beforeunload guard is armed. ──
    const session = await openStagingSession({
      backendUrl: props.backendUrl,
      projectId: options.projectId ?? null,
      setId: options.setId ?? null,
      characterId: options.characterId ?? null,
      totalFiles: files.length,
    });
    if (!session.stagingId) {
      finalizeError("Could not open an import staging session.");
      return;
    }
    currentStagingId = session.stagingId;

    for (let i = 0; i < files.length; i += BATCH_SIZE) {
      if (cancelImport.value) {
        finalizeCancelled();
        return;
      }

      const batch = files.slice(i, i + BATCH_SIZE);
      const batchKeys = fileKeys.slice(i, i + BATCH_SIZE);
      const batchBytes = batch.reduce((sum, f) => sum + (f.size || 0), 0);
      const batchTimeoutMs =
        overrideTimeout ??
        Math.max(
          MIN_TIMEOUT_MS,
          batch.length * TIMEOUT_PER_FILE_MS,
          Math.ceil(batchBytes / (1024 * 1024)) * TIMEOUT_PER_MB_MS,
        );
      const batchIndex = Math.floor(i / BATCH_SIZE) + 1;

      let ok = false;
      let lastError = null;

      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (cancelImport.value) {
          finalizeCancelled();
          return;
        }

        const controller = new AbortController();
        currentImportController.value = controller;
        let firstProgressLogged = false;
        let lastProgressAt = Date.now();
        let noProgressAbortFired = false;
        const noProgressTimer = setInterval(() => {
          if (cancelImport.value) return;
          const elapsed = Date.now() - lastProgressAt;
          if (elapsed < NO_PROGRESS_ABORT_MS) return;
          noProgressAbortFired = true;
          console.warn(
            `[IMPORT] Aborting batch ${batchIndex} attempt ${attempt} due to no upload progress for ${elapsed}ms.`,
          );
          try {
            controller.abort("no-upload-progress");
          } catch {
            controller.abort();
          }
        }, 1000);
        const timeout = setTimeout(() => {
          console.warn(
            `[IMPORT] Aborting batch ${batchIndex} after ${batchTimeoutMs}ms timeout (attempt ${attempt}).`,
          );
          controller.abort();
        }, batchTimeoutMs);
        try {
          const stagedBatch = await stageFiles({
            backendUrl: props.backendUrl,
            stagingId: currentStagingId,
            files: batch,
            keys: batchKeys,
            signal: controller.signal,
            timeoutMs: batchTimeoutMs,
            onUploadProgress: (progressEvent) => {
              const loaded = progressEvent.loaded ?? 0;
              lastProgressAt = Date.now();
              if (!firstProgressLogged) firstProgressLogged = true;
              uploadBytesUploaded.value = Math.min(
                uploadBytesTotal.value,
                uploadedBytesAccum + loaded,
              );
              arrivals.setBytesSent(importRunId, uploadBytesUploaded.value);
              reportUpload();
            },
          });
          arrivals.markSkipped(
            importRunId,
            batchKeys,
            batch,
            stagedBatch.skipped,
          );
          clearInterval(noProgressTimer);
          clearTimeout(timeout);
          if (controller === currentImportController.value) {
            currentImportController.value = null;
          }
          ok = true;
          break;
        } catch (err) {
          clearInterval(noProgressTimer);
          clearTimeout(timeout);
          if (controller === currentImportController.value) {
            currentImportController.value = null;
          }
          const isAbort =
            err?.name === "AbortError" || err?.code === "ERR_CANCELED";
          const isTimeout = err?.code === "ECONNABORTED";
          if (isAbort && cancelImport.value) {
            finalizeCancelled();
            return;
          }
          if (isAbort || isTimeout) {
            lastError = new Error(
              noProgressAbortFired
                ? "Upload stalled (no progress)"
                : "Upload timed out",
            );
            console.warn(
              `[IMPORT] Batch ${batchIndex} aborted (attempt ${attempt}). reason=${
                noProgressAbortFired
                  ? "no-progress"
                  : isTimeout
                    ? "timeout"
                    : "abort"
              }`,
            );
          } else {
            lastError = err;
            console.warn(
              `[IMPORT] Batch ${batchIndex} failed (attempt ${attempt}):`,
              err,
            );
          }
          if (attempt < MAX_RETRIES) {
            await sleep(1000);
          }
        }
      }

      if (!ok) {
        finalizeError(lastError ? lastError.message : "Upload failed.");
        return;
      }

      uploadedBytesAccum += batchBytes;
      uploadBytesUploaded.value = uploadedBytesAccum;
      arrivals.setBytesSent(importRunId, uploadedBytesAccum);
      reportUpload();
      await nextTick();
    }

    if (cancelImport.value) {
      finalizeCancelled();
      return;
    }

    // All bytes are staged - commit the SAFE HANDOFF to the background import.
    // From here Cancel has nothing to stop, so neither the band nor the Tasks
    // row offers it.
    uploadBytesUploaded.value = uploadBytesTotal.value;
    committing = true;
    disarmGuard();
    arrivals.markChecking(importRunId);
    tasksStore.setImportRun(importRunId, {
      status: "running",
      percent: 0,
      current: 0,
      total: importTotal.value,
      message: "Handing over to the server…",
      label: "Importing pictures",
    });

    let commit;
    try {
      commit = await commitStaging({
        backendUrl: props.backendUrl,
        stagingId: currentStagingId,
      });
    } catch (err) {
      // Commit is still inside the unsafe window. Surface the
      // reason (400 empty / face-worker-down, 409 committed, 507 disk) and
      // discard the session. A 409 means it is already handed off, so leave it.
      const detail =
        errorDetail(err) || err?.message || "Import commit failed.";
      const status = err?.response?.status;
      if (status !== 409 && currentStagingId) {
        const stagingId = currentStagingId;
        cancelStaging({ backendUrl: props.backendUrl, stagingId }).catch((e) =>
          console.warn("Failed to cancel staging after commit error", e),
        );
      }
      currentStagingId = null;
      finalizeError(detail);
      return;
    }

    // The session is committed; it can no longer be cancelled from the client.
    const stagingId = currentStagingId;
    currentStagingId = null;
    importTotal.value = commit.stagedCount || files.length;
    importProgress.value = 0;

    // The safe transition: the bytes are on the server and leaving is harmless.
    importPhase.value = "processing";
    tasksStore.setImportRun(importRunId, {
      status: "running",
      percent: 0,
      current: 0,
      total: importTotal.value,
      message: "Queued on the server…",
      label: "Importing pictures",
    });

    // ── Phase B: poll the staging session by id until the import is done. ──
    const maxAttempts = 600;
    const intervalMs = 1000;
    let attempts = 0;
    let finalStatus = null;
    let outcomesSeen = 0;

    while (attempts < maxAttempts) {
      const st = await fetchStagingStatus({
        backendUrl: props.backendUrl,
        stagingId,
        outcomesFrom: outcomesSeen,
      });
      outcomesSeen += st.outcomes.length;
      arrivals.applyOutcomes(importRunId, st.outcomes);

      const total = st.total || importTotal.value || files.length;
      const processed = Math.min(st.processed, total);
      importProgress.value = processed;
      importTotal.value = Math.max(importTotal.value, total);
      arrivals.setProgress(importRunId, processed, total);

      const pct = importTotal.value
        ? Math.min(100, (processed / importTotal.value) * 100)
        : 0;
      tasksStore.setImportRun(importRunId, {
        status: "running",
        percent: pct,
        current: processed,
        total: importTotal.value,
        message:
          st.stage === "importing"
            ? "Checking for duplicates…"
            : "Queued on the server…",
        label: "Importing pictures",
      });

      if (st.stage === "completed") {
        finalStatus = st;
        break;
      }
      if (st.stage === "failed") {
        throw new Error(st.error || "Import failed");
      }
      if (st.stage === "cancelled") {
        // Committed imports don't cancel client-side; treat an external cancel
        // as a terminal, non-error exit.
        finalizeCancelled();
        return;
      }

      await sleep(intervalMs);
      attempts++;
    }

    if (!finalStatus) {
      throw new Error("Import timed out");
    }

    // Summarise from the terminal status. There is no per-file results[] - the
    // grid reconciles the new pictures off the backend's CHANGED_PICTURES /
    // PICTURE_IMPORTED WebSocket broadcast, which the grid already consumes.
    const importedCount = finalStatus.importedCount ?? 0;
    const duplicateCount = finalStatus.duplicateCount ?? 0;
    // The third bucket: content that matches a picture in the Scrapheap. Not
    // imported again (a re-import would put a second copy of every scrapheaped
    // picture back on disk) and not silently restored either.
    const scrapheapedCount = finalStatus.scrapheapedCount ?? 0;
    const scrapheapedPictureIds = finalStatus.scrapheapedPictureIds ?? [];

    importPhase.value = importedCount === 0 ? "duplicates" : "done";
    arrivals.land(importRunId);
    importTotal.value = Math.max(importTotal.value, files.length);
    importProgress.value = importTotal.value;
    currentImportController.value = null;
    cancelImport.value = false;
    importActive.value = false;

    // Punctuate completion on the task row, then let it leave the list.
    tasksStore.setImportRun(importRunId, {
      status: "completed",
      percent: 100,
      current: importTotal.value,
      total: importTotal.value,
      message: summariseImport({
        imported: importedCount,
        duplicate: duplicateCount,
        scrapheaped: scrapheapedCount,
      }),
      label: "Importing pictures",
    });
    offerScrapheapRestore(scrapheapedCount, scrapheapedPictureIds);
    const finishedRunId = importRunId;
    tasksStore.unregisterImportAbort(finishedRunId);
    setTimeout(() => tasksStore.clearImportRun(finishedRunId), 2600);

    // The public emit contract is unchanged, but the streaming-staging contract
    // returns no per-file results - the grid refreshes off the WS broadcast, so
    // `results` is empty. (Consumers that relied on results[].picture_id, e.g.
    // SideBar drop-to-set/character association, no longer receive ids here.)
    emit("import-finished", {
      importedCount,
      total: importTotal.value,
      phase: importPhase.value,
      results: [],
    });
    logImportTrace("Import finished", {
      importedCount,
      duplicateCount,
      scrapheapedCount,
      scrapheapedPictureIds,
      phase: importPhase.value,
    });
  } catch (error) {
    // The server's reason first: opening the session onto a locked set answers
    // 409 with why, and axios's own message says only the status code.
    const message = errorMessage(error, String(error));
    finalizeError(message);
    logImportTrace("Import failed", { message });
  }
}

onBeforeUnmount(() => {
  disarmGuard();
  clearHideTimer();
  if (importRunId) tasksStore.unregisterImportAbort(importRunId);
});

defineExpose({ startImport });

// Nothing of its own to draw: the grid's ArrivingTiles and the Tasks-tab row
// show the import (#2512).
defineOptions({ render: () => null });
</script>
