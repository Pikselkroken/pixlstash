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
const importError = ref(null);
const importPhase = ref("");
const cancelImport = ref(false);
const currentImportController = ref(null);

let hideTimerId = null;

const importPhaseMessage = computed(() => {
  switch (importPhase.value) {
    case "uploading":
      return "Uploading images...";
    case "processing":
      return "Processing import...";
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

async function pollImportStatus(taskId, uploadedCount, batchCount, totalFiles) {
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
    const total = statusRes?.data?.total ?? batchCount;

    importPhase.value = "processing";
    const totalUnits = totalFiles * 2;
    importTotal.value = totalUnits;
    if (processed > 0) {
      importProgress.value = Math.min(totalUnits, uploadedCount + processed);
    } else {
      importProgress.value = Math.max(importProgress.value, uploadedCount);
    }

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
    window.alert("An import is already in progress.");
    return;
  }

  clearHideTimer();
  cancelImport.value = false;
  importInProgress.value = true;
  importProgress.value = 0;
  importTotal.value = files.length * 2;
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

  let uploadedCount = 0;
  let importedCount = 0;
  const allResults = [];

  try {
    for (let i = 0; i < files.length; i += BATCH_SIZE) {
      if (cancelImport.value) {
        finalizeCancelled();
        return;
      }

      const batch = files.slice(i, i + BATCH_SIZE);
      const batchTimeoutMs =
        overrideTimeout ??
        Math.max(MIN_TIMEOUT_MS, batch.length * TIMEOUT_PER_FILE_MS);
      const formData = new FormData();
      batch.forEach((file) => {
        formData.append("file", file);
      });

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

      uploadedCount += batch.length;
      importProgress.value = uploadedCount;
      await nextTick();

      const taskId = res?.data?.task_id;
      if (!taskId) {
        finalizeError("Missing task id from import response.");
        return;
      }

      importPhase.value = "processing";
      const statusPayload = await pollImportStatus(
        taskId,
        uploadedCount,
        batch.length,
        files.length,
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

      const processedCount = statusPayload.total ?? batch.length;
      importProgress.value = Math.min(
        importTotal.value,
        uploadedCount + processedCount,
      );
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

    importProgress.value = importTotal.value;
    currentImportController.value = null;
    cancelImport.value = false;
    hideTimerId = setTimeout(() => {
      importInProgress.value = false;
      hideTimerId = null;
    }, 1500);

    emit("import-finished", {
      importedCount,
      total: files.length,
      phase: importPhase.value,
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
      <div class="import-progress-bar-bg">
        <div
          class="import-progress-bar"
          :style="{
            width:
              (importTotal ? (importProgress / importTotal) * 100 : 0) + '%',
          }"
        ></div>
      </div>
      <div class="import-progress-label">
        <template v-if="importPhase === 'uploading'">
          Uploading {{ importProgress }} / {{ importTotal }}
        </template>
        <template v-else-if="importPhase === 'done'">
          Import complete!
        </template>
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
  background: rgba(32, 32, 32, 0.65);
  z-index: 99999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: all;
}

.import-progress-content {
  background: #222;
  color: #fff8e1;
  padding: 32px 48px;
  border-radius: 16px;
  box-shadow: 0 4px 32px rgba(0, 0, 0, 0.65);
  min-width: 320px;
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
  background: #444;
  border-radius: 9px;
  overflow: hidden;
  margin-bottom: 16px;
}

.import-progress-bar {
  height: 100%;
  background: linear-gradient(90deg, #ff9800 0%, #ffc107 100%);
  border-radius: 9px 0 0 9px;
  transition: width 0.2s;
}

.import-progress-label {
  font-size: 1.1rem;
  margin-top: 8px;
}

.import-progress-error {
  color: #ff5252;
  margin-left: 12px;
}

.cancel-button {
  margin-top: 18px;
  padding: 8px 18px;
  border-radius: 999px;
  border: none;
  background: #ff7043;
  color: #fff;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}

.cancel-button:hover {
  background: #ff5722;
}

.cancel-button:focus {
  outline: 2px solid rgba(255, 255, 255, 0.5);
  outline-offset: 2px;
}

.cancel-button:disabled {
  background: #999;
  cursor: not-allowed;
}
</style>
