<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";
import { VIcon } from "vuetify/components";
import { API_BASE_URL, isReadOnly } from "../../utils/apiClient";
import { sleep } from "../../utils/utils";
import { useTasksStore } from "../../stores/useTasksStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
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
import { errorDetail } from "../../utils/apiError";

// ── Async streaming-staging import (#459) ────────────────────────────────────
// This component owns the two-phase import experience over the finalised
// streaming-staging session contract:
//   Phase A "staging / unsafe":  open a staging session, then STREAM the files
//     into it batch by batch. A calm dialog shows the upload, a beforeunload
//     guard is armed (leaving now aborts the upload), and the ONLY action is
//     Cancel (which discards the not-yet-committed session). No close/minimize.
//   Safe transition:  once every byte is staged, commit hands off to a
//     background PictureImportTask; the guard drops and the dialog auto-hides
//     itself while its count chip flies (FLIP) into the StatsSidebar Tasks-tab
//     task manager, landing as a task row.
//   Phase B "importing / safe":  the dialog is gone; the import is just another
//     task row (useTasksStore import run) counting server-side progress polled
//     BY staging id. Refresh is harmless. The grid refreshes off the backend's
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

// Dialog visibility is the Phase-A staging surface only; Phase B lives in the
// task manager, not here.
const dialogVisible = ref(false);
const dialogLeaving = ref(false);
// Whole-flow gate (Phase A + Phase B) used for re-entrancy; distinct from dialog
// visibility because Phase B keeps running after the dialog auto-hides.
const importActive = ref(false);

const importProgress = ref(0);
const importTotal = ref(0);
const uploadBytesUploaded = ref(0);
const uploadBytesTotal = ref(0);
const importError = ref(null);
const importPhase = ref(""); // uploading | processing | done | duplicates | cancelled | error
const importServerStage = ref("");
const cancelImport = ref(false);
const currentImportController = ref(null);
const uploadStallSeconds = ref(0);
const isZipImport = ref(false);

// The count chip is the FLIP flight source.
const countChipEl = ref(null);
// Unique id for the current import's task-manager row (the flight destination).
let importRunId = null;
// The current staging session id (Phase A/B backend handle), or null when idle.
let currentStagingId = null;
// True once we have crossed the safe transition (dialog hidden, Phase B in the
// task manager). Errors after this point surface on the task row, not the dialog.
let transitioned = false;

let hideTimerId = null;
let _stallTimerId = null;
let _stallLastBytes = -1;

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

function _startStallTimer() {
  _stopStallTimer();
  uploadStallSeconds.value = 0;
  _stallLastBytes = uploadBytesUploaded.value;
  _stallTimerId = setInterval(() => {
    if (uploadBytesUploaded.value !== _stallLastBytes) {
      uploadStallSeconds.value = 0;
      _stallLastBytes = uploadBytesUploaded.value;
    } else {
      uploadStallSeconds.value += 1;
    }
  }, 1000);
}

function _stopStallTimer() {
  if (_stallTimerId !== null) {
    clearInterval(_stallTimerId);
    _stallTimerId = null;
  }
  uploadStallSeconds.value = 0;
}

const TERMINAL_IMPORT_PHASES = new Set([
  "done",
  "duplicates",
  "cancelled",
  "error",
]);

const dialogTitle = computed(() =>
  importPhase.value === "error" ? "Import failed" : "Uploading pictures",
);

const chipLabel = computed(() => {
  if (isZipImport.value) return "1 archive";
  const n = importTotal.value;
  return `${n} file${n === 1 ? "" : "s"}`;
});

const uploadPct = computed(() =>
  uploadBytesTotal.value
    ? Math.min(100, (uploadBytesUploaded.value / uploadBytesTotal.value) * 100)
    : 0,
);

const uploadLabel = computed(() =>
  uploadPct.value >= 100 ? "Upload complete" : "Uploading pictures",
);

const showCancelButton = computed(
  () => dialogVisible.value && importPhase.value !== "error",
);

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function clearHideTimer() {
  if (hideTimerId !== null) {
    clearTimeout(hideTimerId);
    hideTimerId = null;
  }
}

function finalizeCancelled() {
  clearHideTimer();
  _stopStallTimer();
  disarmGuard();
  importPhase.value = "cancelled";
  importServerStage.value = "cancelled";
  dialogVisible.value = false;
  dialogLeaving.value = false;
  importActive.value = false;
  importError.value = null;
  cancelImport.value = false;
  currentImportController.value = null;
  if (importRunId) {
    tasksStore.clearImportRun(importRunId);
    tasksStore.unregisterImportAbort(importRunId);
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
  _stopStallTimer();
  disarmGuard();
  importPhase.value = "error";
  importServerStage.value = "failed";
  importActive.value = false;
  if (transitioned && importRunId) {
    // Phase B failure: the dialog is already gone. Surface it on the task row,
    // which the toolbar activity dot / Tasks-tab pulse point the user to.
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
  } else {
    // Phase A failure: keep the dialog open so the user can read the error;
    // auto-dismiss after 30 s (a new import cancels this via the stale guard).
    dialogVisible.value = true;
    dialogLeaving.value = false;
    hideTimerId = setTimeout(() => {
      dialogVisible.value = false;
      hideTimerId = null;
    }, 30000);
  }
  importError.value = message;
  cancelImport.value = false;
  currentImportController.value = null;
  if (importRunId) tasksStore.unregisterImportAbort(importRunId);
  emit("import-error", { message });
}

// The single client-side abort path, shared by the dialog's Cancel button and
// the Tasks-tab cancel affordance (via tasksStore.abortImportRun → this handler,
// registered in startImport). It stops the in-flight upload by aborting the
// active request controller; the batch loop's cancel checks then unwind cleanly
// through finalizeCancelled (no error toast). This can only stop the PRE-COMMIT
// upload - once committed, the import is a background server task the client
// cannot stop (see the abortable notes in startImport / useTasksStore).
function handleCancelImport() {
  if (!importActive.value) return;
  cancelImport.value = true;
  if (currentImportController.value) {
    try {
      currentImportController.value.abort();
    } catch (err) {
      console.warn("Failed to abort current import", err);
    }
  }
}

function dismissError() {
  clearHideTimer();
  dialogVisible.value = false;
  importPhase.value = "";
}

function logImportTrace(message, details = null) {
  if (details !== null && details !== undefined) {
    console.info(`[IMPORT TRACE] ${message}`, details);
    return;
  }
  console.info(`[IMPORT TRACE] ${message}`);
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * FLIP the count chip into the Tasks-tab import row. Resolves when the flight
 * finishes (or immediately when reduced motion is on, or the destination row
 * isn't rendered - e.g. the stats sidebar is collapsed - so the dialog just
 * fades and the toolbar activity light signals the backgrounded work).
 */
function flyChipToTaskRow(runId) {
  return new Promise((resolve) => {
    if (prefersReducedMotion()) {
      resolve();
      return;
    }
    const chip = countChipEl.value;
    const target =
      typeof document !== "undefined"
        ? document.querySelector(`[data-import-task-row="${runId}"]`)
        : null;
    if (!chip || !target || typeof chip.animate !== "function") {
      resolve();
      return;
    }
    const from = chip.getBoundingClientRect();
    const to = target.getBoundingClientRect();
    const clone = chip.cloneNode(true);
    clone.classList.add("import-fly-chip");
    clone.style.left = `${from.left}px`;
    clone.style.top = `${from.top}px`;
    clone.style.width = `${from.width}px`;
    clone.style.height = `${from.height}px`;
    document.body.appendChild(clone);
    const dx = to.left + to.width / 2 - (from.left + from.width / 2);
    const dy = to.top + to.height / 2 - (from.top + from.height / 2);
    const lift = Math.max(40, Math.abs(dx) * 0.12); // a slight arc
    const css = getComputedStyle(document.documentElement);
    const dur = parseFloat(css.getPropertyValue("--dur-4")) || 420;
    const ease = (
      css.getPropertyValue("--ease-standard") || "cubic-bezier(0.4,0,0.2,1)"
    ).trim();
    let anim;
    try {
      anim = clone.animate(
        [
          { transform: "translate(0px, 0px) scale(1)", opacity: 1, offset: 0 },
          {
            transform: `translate(${dx * 0.55}px, ${dy * 0.45 - lift}px) scale(0.8)`,
            opacity: 0.95,
            offset: 0.55,
          },
          {
            transform: `translate(${dx}px, ${dy}px) scale(0.3)`,
            opacity: 0,
            offset: 1,
          },
        ],
        { duration: dur, easing: ease, fill: "forwards" },
      );
    } catch (err) {
      console.warn("Import flight animation failed", err);
      clone.remove();
      resolve();
      return;
    }
    const cleanup = () => {
      clone.remove();
      resolve();
    };
    anim.onfinish = cleanup;
    anim.oncancel = cleanup;
  });
}

/**
 * The safe transition: register the import as a task-manager row, drop the
 * upload guard, and auto-hide the dialog while the chip flies into the row.
 * The flight IS the dismissal - there is no manual "continue in background".
 */
async function transitionToBackground(runId, total) {
  transitioned = true;
  disarmGuard();
  importPhase.value = "processing";
  tasksStore.setImportRun(runId, {
    status: "running",
    percent: 0,
    current: 0,
    total,
    message: "Queued on the server…",
    label: "Importing pictures",
  });
  // Render the destination row before measuring the flight target.
  await nextTick();
  dialogLeaving.value = true;
  await flyChipToTaskRow(runId);
  dialogVisible.value = false;
  dialogLeaving.value = false;
}

async function startImport(files, options = {}) {
  if (!files || !files.length) return;
  if (importActive.value) {
    // Recover from stale terminal state where the dialog hasn't hidden yet.
    if (TERMINAL_IMPORT_PHASES.has(importPhase.value)) {
      clearHideTimer();
      importActive.value = false;
    } else {
      console.info(
        "Import request ignored because another import is in progress.",
      );
      return;
    }
  }

  if (isReadOnly.value) {
    importActive.value = true;
    dialogVisible.value = true;
    transitioned = false;
    finalizeError("Importing is not available with a read-only token.");
    return;
  }

  clearHideTimer();
  cancelImport.value = false;
  transitioned = false;
  currentStagingId = null;
  // Drop any lingering handler from a previous run before minting a new id.
  if (importRunId) tasksStore.unregisterImportAbort(importRunId);
  importRunId = `import-${crypto?.randomUUID?.() ?? Date.now().toString(36)}`;
  // Make the (otherwise dead) import-abort subsystem live: register the real
  // client-side abort so tasksStore.abortImportRun(runId) actually stops the
  // in-flight upload. Mirrors ComfyUiRunner's registerComfyuiAbort. Unregistered
  // on every terminal path (finalizeCancelled / finalizeError / completion) and
  // on unmount.
  tasksStore.registerImportAbort(importRunId, () => handleCancelImport());
  importActive.value = true;
  dialogVisible.value = true;
  dialogLeaving.value = false;
  importProgress.value = 0;
  importTotal.value = files.length;
  isZipImport.value =
    files.length === 1 && files[0].name.toLowerCase().endsWith(".zip");
  uploadBytesUploaded.value = 0;
  uploadBytesTotal.value = files.reduce((sum, f) => sum + (f.size || 0), 0);
  importError.value = null;
  importPhase.value = "uploading";
  importServerStage.value = "uploading";
  currentImportController.value = null;
  // Arm the unsafe-window guard: leaving/refreshing now aborts the upload.
  armGuard();
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
        _startStallTimer();

        try {
          await stageFiles({
            backendUrl: props.backendUrl,
            stagingId: currentStagingId,
            files: batch,
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
            },
          });
          _stopStallTimer();
          clearInterval(noProgressTimer);
          clearTimeout(timeout);
          if (controller === currentImportController.value) {
            currentImportController.value = null;
          }
          ok = true;
          break;
        } catch (err) {
          _stopStallTimer();
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
      await nextTick();
    }

    if (cancelImport.value) {
      finalizeCancelled();
      return;
    }

    // All bytes are staged - commit the SAFE HANDOFF to the background import.
    uploadBytesUploaded.value = uploadBytesTotal.value;
    _stopStallTimer();

    let commit;
    try {
      commit = await commitStaging({
        backendUrl: props.backendUrl,
        stagingId: currentStagingId,
      });
    } catch (err) {
      // Commit is still inside the unsafe window (dialog open). Surface the
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

    // Auto-hide the dialog and fly the chip into the task manager. Phase B (the
    // server-side import) now lives entirely in the Tasks-tab row.
    await transitionToBackground(importRunId, importTotal.value);

    // ── Phase B: poll the staging session by id until the import is done. ──
    const maxAttempts = 600;
    const intervalMs = 1000;
    let attempts = 0;
    let finalStatus = null;

    while (attempts < maxAttempts) {
      const st = await fetchStagingStatus({
        backendUrl: props.backendUrl,
        stagingId,
      });

      const total = st.total || importTotal.value || files.length;
      const processed = Math.min(st.processed, total);
      importProgress.value = processed;
      importTotal.value = Math.max(importTotal.value, total);
      importServerStage.value = st.stage;

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
            ? "Importing on the server…"
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
    importServerStage.value = "completed";
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
    const message = error?.message || String(error);
    finalizeError(message);
    logImportTrace("Import failed", { message });
  }
}

onBeforeUnmount(() => {
  disarmGuard();
  clearHideTimer();
  _stopStallTimer();
  if (importRunId) tasksStore.unregisterImportAbort(importRunId);
});

defineExpose({ startImport });
</script>

<template>
  <div
    v-if="dialogVisible"
    class="dlg-scrim"
    :class="{ leaving: dialogLeaving }"
  >
    <div class="dialog" role="dialog" aria-label="Import pictures">
      <div class="dlg-head">
        <h3 class="dlg-title">{{ dialogTitle }}</h3>
        <div class="grow"></div>
        <span ref="countChipEl" class="chip">
          <VIcon class="chip-icon" size="16">
            {{ isZipImport ? "mdi-folder-zip" : "mdi-image-multiple" }}
          </VIcon>
          <span>{{ chipLabel }}</span>
        </span>
      </div>

      <!-- The unsafe-window guard, said calmly. On error it becomes the error note. -->
      <div v-if="importPhase !== 'error'" class="note-row">
        <VIcon class="note-icon" size="18">mdi-tray-arrow-up</VIcon>
        <div>
          <div class="note-title">Keep this tab open while files upload</div>
          <div class="note-sub">
            Your pictures aren't on the server yet - leaving now would stop the
            upload.
          </div>
        </div>
      </div>
      <div v-else class="note-row note-row--error">
        <VIcon class="note-icon note-icon--error" size="18"
          >mdi-alert-outline</VIcon
        >
        <div>
          <div class="note-title">Import failed</div>
          <div class="note-sub">{{ importError }}</div>
        </div>
      </div>

      <!-- Upload bar (amber accent = attention, this tab is busy) -->
      <div class="bar-section">
        <div class="bar-label">
          <span>{{ uploadLabel }}</span>
          <span>
            {{ formatBytes(uploadBytesUploaded) }} of
            {{ formatBytes(uploadBytesTotal) }}
            <span
              v-if="importPhase === 'uploading' && uploadStallSeconds >= 3"
              class="stall"
              >(stalled {{ uploadStallSeconds }}s)</span
            >
          </span>
        </div>
        <div class="bar-track">
          <div
            class="bar-fill bar-fill--upload"
            :style="{ width: uploadPct + '%' }"
          ></div>
        </div>
      </div>

      <!-- Import bar (olive primary = safe). A pending preview here - Phase B
           fills in the task manager after the dialog auto-hides. -->
      <div class="bar-section">
        <div class="bar-label">
          <span>Import pending…</span>
          <span>&nbsp;</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill bar-fill--import" style="width: 0%"></div>
        </div>
      </div>

      <!-- Phase A holds exactly one action: Cancel. No close/minimize control
           exists; the safe path auto-hides. -->
      <div class="dlg-foot">
        <button
          v-if="showCancelButton"
          class="dlg-btn dlg-btn--danger"
          type="button"
          @click="handleCancelImport"
        >
          Cancel import
        </button>
        <button
          v-if="importPhase === 'error'"
          class="dlg-btn dlg-btn--quiet"
          type="button"
          @click="dismissError"
        >
          Dismiss
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Standard surface dialog (not the old full-screen dark-surface modal): the
   non-blocking import should not read as "the app is busy". Anchored below the
   desktop title bar; sits above the grid.

   `--z-modal` is the rung: this is a modal dialog and its scrim. It no longer
   needs to out-number the title bar - it never overlapped it anyway (it starts
   at `top: var(--titlebar-h)`), and the strip now holds `--z-titlebar` above
   every modal by value. */
.dlg-scrim {
  position: fixed;
  top: var(--titlebar-h);
  left: 0;
  width: 100vw;
  height: calc(100vh - var(--titlebar-h));
  z-index: var(--z-modal);
  background: rgba(var(--v-theme-scrim), 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity var(--dur-3) var(--ease-accelerate);
}

.dlg-scrim.leaving {
  opacity: 0;
  pointer-events: none;
}

.dialog {
  width: min(440px, calc(100% - var(--space-7)));
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
  padding: var(--space-7);
  transition:
    transform var(--dur-3) var(--ease-accelerate),
    opacity var(--dur-3) var(--ease-accelerate);
}

.dlg-scrim.leaving .dialog {
  transform: scale(0.96);
  opacity: 0;
}

.dlg-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-6);
}

.dlg-head .grow {
  flex: 1;
}

.dlg-title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  margin: 0;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-accent), 0.12);
  border: 1px solid rgba(var(--v-theme-accent), 0.4);
  color: rgb(var(--v-theme-on-surface));
  font-variant-numeric: tabular-nums;
}

.chip-icon {
  color: rgb(var(--v-theme-accent));
}

.note-row {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-accent), 0.08);
  margin-bottom: var(--space-5);
}

.note-row--error {
  background: rgba(var(--v-theme-error), 0.08);
}

.note-icon {
  margin-top: var(--space-1);
  color: rgb(var(--v-theme-accent));
}

.note-icon--error {
  color: rgb(var(--v-theme-error));
}

.note-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
}

.note-sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin-top: var(--space-1);
}

.bar-section {
  margin-bottom: var(--space-5);
}

.bar-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  margin-bottom: var(--space-2);
  font-variant-numeric: tabular-nums;
}

.stall {
  color: rgb(var(--v-theme-warning));
}

.bar-track {
  height: var(--space-3);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.1);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  width: 0%;
  border-radius: var(--radius-pill);
  transition: width var(--dur-2) var(--ease-standard);
}

.bar-fill--upload {
  background: rgb(var(--v-theme-accent));
}

.bar-fill--import {
  background: rgb(var(--v-theme-primary));
}

.dlg-foot {
  display: flex;
  justify-content: flex-start;
  gap: var(--space-3);
  margin-top: var(--space-6);
}

.dlg-btn {
  font: inherit;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: var(--leading-snug);
  padding: var(--space-3) var(--space-5);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  transition: filter var(--dur-1) var(--ease-standard);
}

.dlg-btn:hover {
  filter: brightness(1.05);
}

.dlg-btn--danger {
  background: rgba(var(--v-theme-error), 0.1);
  border-color: rgba(var(--v-theme-error), 0.55);
  color: rgb(var(--v-theme-error));
}

.dlg-btn--quiet {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
}
</style>

<!-- The FLIP flight clone is appended to <body>, so its style is global (not
     scoped to this component's rendered tree). -->
<style>
.import-fly-chip {
  position: fixed;
  /* Same rung as the import dialog it flies out of (`--z-modal`). This clone is
     appended to <body>, so unlike the dialog it lives in the ROOT stacking
     context, where `.app-viewport` (z-index: 0) is its only in-app competitor:
     any positive value already clears the whole app shell, including the title
     bar and the notice host. It is a 420ms pointer-events:none flight clone, so
     that is intended and matches what it did at 99999. */
  z-index: var(--z-modal);
  margin: 0;
  pointer-events: none;
  will-change: transform, opacity;
}
</style>
