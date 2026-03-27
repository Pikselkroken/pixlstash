<script setup>
import { computed, nextTick, ref } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, required: true },
  selectedCharacterId: { type: [String, Number, null], default: null },
  allPicturesId: { type: String, default: "" },
  unassignedPicturesId: { type: String, default: "" },
});

const emit = defineEmits([
  "import-finished",
  "import-cancelled",
  "import-error",
]);

const importInProgress = ref(false);
const importProgress = ref(0);
const importTotal = ref(0);
const uploadBytesUploaded = ref(0);
const uploadBytesTotal = ref(0);
const importError = ref(null);
const importPhase = ref("");
const cancelImport = ref(false);
const currentImportController = ref(null);

let hideTimerId = null;
const TERMINAL_IMPORT_PHASES = new Set([
  "done",
  "duplicates",
  "cancelled",
  "error",
]);

const importPhaseMessage = computed(() => {
  switch (importPhase.value) {
    case "uploading":
      return "Uploading...";
    case "processing":
      return "Importing...";
    case "done":
      return "Import complete!";
    case "duplicates":
      return "All files are duplicates.";
    case "cancelled":
      return "Import cancelled.";
    case "error":
      return "Import failed.";
    default:
      return "";
  }
});

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024)
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const showCancelButton = computed(
  () =>
    importInProgress.value &&
    !["done", "duplicates", "cancelled", "error"].includes(importPhase.value),
);

function clearHideTimer() {
  if (hideTimerId !== null) {
    clearTimeout(hideTimerId);
    hideTimerId = null;
  }
}

function finalizeCancelled() {
  clearHideTimer();
  importPhase.value = "cancelled";
  importInProgress.value = false;
  importError.value = null;
  cancelImport.value = false;
  currentImportController.value = null;
  emit("import-cancelled");
}

function finalizeError(message) {
  clearHideTimer();
  importPhase.value = "error";
  importInProgress.value = false;
  importError.value = message;
  cancelImport.value = false;
  currentImportController.value = null;
  emit("import-error", { message });
}

function handleCancelImport() {
  if (!importInProgress.value) return;
  cancelImport.value = true;
  if (currentImportController.value) {
    try {
      currentImportController.value.abort();
    } catch (err) {
      console.warn("Failed to abort current import", err);
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollImportStatus(taskId, importProgressAccum, importTotalAccum) {
  const maxAttempts = 600;
  const intervalMs = 1000;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    if (cancelImport.value) {
      finalizeCancelled();
      return null;
    }

    const statusRes = await apiClient.get(
      `${props.backendUrl}/pictures/import/status`,
      { params: { task_id: taskId } },
    );
    const status = statusRes?.data?.status || "in_progress";
    const processed = statusRes?.data?.processed ?? 0;
    const serverTotal = statusRes?.data?.total ?? 0;

    importPhase.value = "processing";
    if (serverTotal > 0) {
      importTotal.value = Math.max(
        importTotal.value,
        importTotalAccum + serverTotal,
      );
    }
    importProgress.value = importProgressAccum + processed;
    importTotal.value = Math.max(importTotal.value, importProgress.value);

    if (status === "completed") {
      return statusRes.data;
    }
    if (status === "failed") {
      throw new Error(statusRes?.data?.error || "Import failed");
    }

    await sleep(intervalMs);
  }

  throw new Error("Import timed out");
}

async function startImport(files, options = {}) {
  if (!files || !files.length) return;
  if (importInProgress.value) {
    // Recover from stale terminal state where the modal has not hidden yet.
    if (TERMINAL_IMPORT_PHASES.has(importPhase.value)) {
      clearHideTimer();
      importInProgress.value = false;
    } else {
      console.info(
        "Import request ignored because another import is in progress.",
      );
      return;
    }
  }

  clearHideTimer();
  cancelImport.value = false;
  importInProgress.value = true;
  importProgress.value = 0;
  importTotal.value = files.length;
  uploadBytesUploaded.value = 0;
  uploadBytesTotal.value = files.reduce((sum, f) => sum + (f.size || 0), 0);
  importError.value = null;
  importPhase.value = "uploading";
  currentImportController.value = null;

  const BATCH_SIZE = 100;
  const MAX_RETRIES = 3;
  const MIN_TIMEOUT_MS = 60000; // allow long-running server-side processing
  const TIMEOUT_PER_FILE_MS = 4000;
  const overrideTimeout =
    typeof options.timeoutMs === "number" && options.timeoutMs > 0
      ? options.timeoutMs
      : null;

  let uploadedBytesAccum = 0;
  let importProgressAccum = 0;
  let importTotalAccum = 0;
  let importedCount = 0;
  const allResults = [];

  try {
    for (let i = 0; i < files.length; i += BATCH_SIZE) {
      if (cancelImport.value) {
        finalizeCancelled();
        return;
      }

      const batch = files.slice(i, i + BATCH_SIZE);
      importPhase.value = "uploading";
      importTotal.value = Math.max(
        importTotal.value,
        importTotalAccum + batch.length,
      );
      const batchBytes = batch.reduce((sum, f) => sum + (f.size || 0), 0);
      const batchTimeoutMs =
        overrideTimeout ??
        Math.max(MIN_TIMEOUT_MS, batch.length * TIMEOUT_PER_FILE_MS);
      const formData = new FormData();
      batch.forEach((file) => {
        formData.append("file", file);
      });
      if (options.projectId != null) {
        formData.append("project_id", String(options.projectId));
      }

      let res = null;
      let lastError = null;

      for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        if (cancelImport.value) {
          finalizeCancelled();
          return;
        }

        const controller = new AbortController();
        currentImportController.value = controller;
        const timeout = setTimeout(() => controller.abort(), batchTimeoutMs);
        try {
          res = await apiClient.post(
            `${props.backendUrl}/pictures/import`,
            formData,
            {
              signal: controller.signal,
              timeout: batchTimeoutMs,
              headers: {
                "Content-Type": "multipart/form-data", // Ensure this is set correctly
              },
              onUploadProgress: (progressEvent) => {
                const loaded = progressEvent.loaded ?? 0;
                uploadBytesUploaded.value = Math.min(
                  uploadBytesTotal.value,
                  uploadedBytesAccum + loaded,
                );
              },
            },
          );
          clearTimeout(timeout);
          if (controller === currentImportController.value) {
            currentImportController.value = null;
          }
          break; // Success, exit retry loop
        } catch (err) {
          clearTimeout(timeout);
          if (controller === currentImportController.value) {
            currentImportController.value = null;
          }
          if (err.name === "AbortError" && cancelImport.value) {
            finalizeCancelled();
            return;
          }
          if (err.name === "AbortError") {
            lastError = new Error("Upload timed out");
            console.warn(
              `[IMPORT] Batch ${
                i / BATCH_SIZE + 1
              } timed out (attempt ${attempt})`,
            );
          } else {
            lastError = err;
            console.warn(
              `[IMPORT] Batch ${
                i / BATCH_SIZE + 1
              } failed (attempt ${attempt}):`,
              err,
            );
          }
        }

        if (res && res.status >= 200 && res.status < 300) {
          break;
        }
        lastError = new Error(
          res
            ? `Upload failed with status ${res.status}`
            : "No response received",
        );

        if (attempt < MAX_RETRIES) {
          await sleep(1000);
        }
      }

      if (!res || res.status < 200 || res.status >= 300) {
        const message = lastError ? lastError.message : "Upload failed.";
        finalizeError(message);
        return;
      }

      uploadedBytesAccum += batchBytes;
      uploadBytesUploaded.value = uploadedBytesAccum;
      await nextTick();

      const taskId = res?.data?.task_id;
      if (!taskId) {
        finalizeError("Missing task id from import response.");
        return;
      }

      importPhase.value = "processing";
      const statusPayload = await pollImportStatus(
        taskId,
        importProgressAccum,
        importTotalAccum,
      );
      if (!statusPayload) {
        return;
      }

      const batchResults = Array.isArray(statusPayload.results)
        ? statusPayload.results
        : [];
      allResults.push(...batchResults);
      importedCount += batchResults.filter(
        (r) => r.status === "success",
      ).length;

      const batchTotal = statusPayload.total ?? batch.length;
      importTotalAccum += batchTotal;
      importProgressAccum += batchTotal;
      importProgress.value = importProgressAccum;
      importTotal.value = Math.max(importTotal.value, importTotalAccum);
      await nextTick();
    }

    if (importedCount === 0) {
      importPhase.value = "duplicates";
      importError.value = "All files are duplicates.";
    } else {
      importPhase.value = "done";
      importError.value = `Imported ${importedCount} image${
        importedCount !== 1 ? "s" : ""
      }.`;
    }

    importTotal.value = Math.max(importTotal.value, importTotalAccum);
    importProgress.value = importTotal.value;
    uploadBytesUploaded.value = uploadBytesTotal.value;
    currentImportController.value = null;
    cancelImport.value = false;
    hideTimerId = setTimeout(() => {
      importInProgress.value = false;
      hideTimerId = null;
    }, 1500);

    emit("import-finished", {
      importedCount,
      total: allResults.length,
      phase: importPhase.value,
      results: allResults,
    });
  } catch (error) {
    const message = error?.message || String(error);
    finalizeError(message);
    window.alert("All uploads failed: " + message);
  }
}

defineExpose({ startImport });
</script>

<template>
  <div v-if="importInProgress" class="import-progress-modal">
    <div class="import-progress-content">
      <div class="import-progress-title">{{ importPhaseMessage }}</div>
      <!-- Upload progress bar -->
      <div class="import-progress-bar-section">
        <div class="import-progress-bar-label">
          Upload
          {{ formatBytes(uploadBytesUploaded) }} /
          {{ formatBytes(uploadBytesTotal) }}
        </div>
        <div class="import-progress-bar-bg">
          <div
            class="import-progress-bar upload-bar"
            :style="{
              width:
                (uploadBytesTotal
                  ? (uploadBytesUploaded / uploadBytesTotal) * 100
                  : 0) + '%',
            }"
          ></div>
        </div>
      </div>
      <!-- Import progress bar -->
      <div class="import-progress-bar-section">
        <div class="import-progress-bar-label">
          <template v-if="importTotal > 0">
            Import {{ importProgress }} / {{ importTotal }} images
          </template>
          <template v-else-if="importPhase === 'processing'">
            Waiting for server...
          </template>
          <template v-else> Pending... </template>
        </div>
        <div class="import-progress-bar-bg">
          <div
            class="import-progress-bar import-bar"
            :style="{
              width:
                (importTotal ? (importProgress / importTotal) * 100 : 0) + '%',
            }"
          ></div>
        </div>
      </div>
      <div class="import-progress-label">
        <template v-if="importPhase === 'done'"> Import complete! </template>
        <template v-else-if="importPhase === 'duplicates'">
          All files are duplicates.
        </template>
        <template v-else-if="importPhase === 'cancelled'">
          Import cancelled.
        </template>
        <template v-else-if="importPhase === 'error'">
          Import failed.
        </template>
        <span v-if="importError" class="import-progress-error">
          {{ importError }}
        </span>
      </div>
      <button
        v-if="showCancelButton"
        class="cancel-button"
        type="button"
        @click="handleCancelImport"
      >
        Cancel
      </button>
    </div>
  </div>
</template>

<style scoped>
.import-progress-modal {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(var(--v-theme-scrim), 0.65);
  z-index: 99999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
}

.import-progress-content {
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  padding: 32px 48px;
  border-radius: 16px;
  box-shadow: 0 4px 32px rgba(var(--v-theme-shadow), 0.65);
  min-width: 380px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.import-progress-title {
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 24px;
}

.import-progress-bar-bg {
  width: 100%;
  height: 18px;
  background: rgba(var(--v-theme-on-surface), 0.2);
  border-radius: 9px;
  overflow: hidden;
}

.import-progress-bar-section {
  width: 100%;
  margin-bottom: 14px;
}

.import-progress-bar-label {
  font-size: 0.9rem;
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
  margin-bottom: 6px;
  min-height: 1.2em;
}

.import-progress-bar {
  height: 100%;
  border-radius: 9px 0 0 9px;
  transition: width 0.3s ease;
}

.upload-bar {
  background: linear-gradient(
    90deg,
    rgb(var(--v-theme-primary)) 0%,
    rgb(var(--v-theme-secondary)) 100%
  );
}

.import-bar {
  background: linear-gradient(
    90deg,
    rgb(var(--v-theme-warning)) 0%,
    rgb(var(--v-theme-accent)) 100%
  );
}

.import-progress-label {
  font-size: 1.1rem;
  margin-top: 8px;
}

.import-progress-error {
  color: rgb(var(--v-theme-error));
  margin-left: 12px;
}

.cancel-button {
  margin-top: 18px;
  padding: 8px 18px;
  border-radius: 999px;
  border: none;
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.cancel-button:hover {
  background: rgba(var(--v-theme-error), 0.85);
}

.cancel-button:focus {
  outline: 2px solid rgba(var(--v-theme-on-error), 0.5);
  outline-offset: 2px;
}

.cancel-button:disabled {
  background: rgba(var(--v-theme-on-surface), 0.4);
  cursor: not-allowed;
}
</style>
