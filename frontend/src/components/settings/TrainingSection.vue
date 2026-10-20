<script setup>
/**
 * Settings section for the LoRA training feature: ai-toolkit setup, the jobs
 * list (running/queued/terminal with lifecycle actions), and the trained-LoRA
 * registry with expandable detail (loss chart, samples, download, copy path,
 * reproducibility, delete).
 *
 * Mirrors SnapshotsSection: fetch on open, poll while a job is running, per-item
 * actions with window.confirm for destructive ops. Owner-only; gated behind
 * isReadOnly === false at the tab level in UserSettingsDialog.
 */
import { computed, onUnmounted, ref, watch } from "vue";
import { API_BASE_URL, appendShareToken } from "../../utils/apiClient";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { formatUserDate } from "../../utils/utils";
import { copyText } from "../../utils/clipboard";
import LoraLossChart from "./LoraLossChart.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useTrainingStore();
const sidebarStore = useSidebarStore();
const entityNames = useEntityNamesStore();

// ── Local UI state ───────────────────────────────────────────────────────────
const expandedLoraId = ref(null);
const copiedPathId = ref(null);
const setupErrors = ref({}); // field → message
const cancellingId = ref(null);
const deletingJobId = ref(null);
const deletingLoraId = ref(null);
const actionError = ref("");

let pollTimer = null;
const POLL_INTERVAL_MS = 4000;

// ── Fetch on open + poll while running ────────────────────────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      store.fetchConfig();
      store.fetchJobs().then(maybeSchedulePoll);
      store.fetchLoras();
    } else {
      stopPolling();
    }
  },
);

function maybeSchedulePoll() {
  if (store.hasRunningJob && props.open) {
    startPolling();
  } else {
    stopPolling();
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    await store.fetchJobs();
    if (!store.hasRunningJob) {
      // A run just finished: refresh the registry so a new LoRA shows up.
      stopPolling();
      store.fetchLoras();
    }
  }, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onUnmounted(stopPolling);

// ── Derived ────────────────────────────────────────────────────────────────────
const jobs = computed(() => store.jobs);
const loras = computed(() => store.loras);

function humanBytes(bytes) {
  if (!bytes) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
}

function relativeDate(dateStr) {
  if (!dateStr) return "";
  const then = new Date(dateStr).getTime();
  if (!Number.isFinite(then)) return "";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// Status chip: text + icon (never colour alone, WCAG 1.4.1).
const STATUS_META = {
  queued: { icon: "mdi-clock-outline", color: "grey", label: "queued" },
  running: { icon: "mdi-play-circle-outline", color: "primary", label: "running" },
  stopping: { icon: "mdi-stop-circle-outline", color: "warning", label: "stopping" },
  stopped: { icon: "mdi-cancel", color: "warning", label: "cancelled" },
  completed: { icon: "mdi-check-circle-outline", color: "success", label: "completed" },
  error: { icon: "mdi-alert-circle-outline", color: "error", label: "failed" },
};

function statusMeta(status) {
  return (
    STATUS_META[status] || {
      icon: "mdi-help-circle-outline",
      color: "grey",
      label: status || "unknown",
    }
  );
}

function scopeLabel(row) {
  if (row.character_id != null) {
    const name = entityNames.characterNames[row.character_id];
    return name ? `★ ${name}` : `Character ${row.character_id}`;
  }
  if (row.set_id != null) {
    const name = entityNames.setNames[row.set_id];
    return name ? `Set "${name}"` : `Set ${row.set_id}`;
  }
  return "-";
}

// ── ai-toolkit setup (optimistic PATCH + revert) ────────────────────────────────
async function saveSetupField(field) {
  setupErrors.value = { ...setupErrors.value, [field]: "" };
  const valueMap = {
    ai_toolkit_repo_path: store.aiToolkitRepoPath,
    ai_toolkit_python: store.aiToolkitPython,
  };
  try {
    await store.patchConfigField(field, valueMap[field]);
  } catch (err) {
    setupErrors.value = {
      ...setupErrors.value,
      [field]:
        err?.response?.data?.detail || err?.message || "Failed to save.",
    };
  }
}

// ── Jobs lifecycle ─────────────────────────────────────────────────────────────
function viewInTasks() {
  sidebarStore.showTasksTab();
}

async function cancelJob(job) {
  if (
    !window.confirm(
      `Cancel training job "${job.name}"? The run will stop and cannot be resumed.`,
    )
  ) {
    return;
  }
  cancellingId.value = job.id;
  actionError.value = "";
  try {
    await store.cancelJob(job.id);
  } catch (err) {
    actionError.value =
      err?.response?.data?.detail || err?.message || "Cancel failed.";
  } finally {
    cancellingId.value = null;
  }
}

async function deleteJob(job) {
  if (
    !window.confirm(
      `Delete job "${job.name}"? This removes its work directory and any LoRAs it produced. This cannot be undone.`,
    )
  ) {
    return;
  }
  deletingJobId.value = job.id;
  actionError.value = "";
  try {
    await store.deleteJob(job.id);
  } catch (err) {
    actionError.value =
      err?.response?.data?.detail || err?.message || "Delete failed.";
  } finally {
    deletingJobId.value = null;
  }
}

// ── Registry: expand + actions ───────────────────────────────────────────────────
async function toggleLora(lora) {
  if (expandedLoraId.value === lora.id) {
    expandedLoraId.value = null;
    return;
  }
  expandedLoraId.value = lora.id;
  if (!store.loraDetails[lora.id]) {
    try {
      await store.fetchLoraDetail(lora.id);
    } catch (err) {
      actionError.value =
        err?.response?.data?.detail ||
        err?.message ||
        "Failed to load LoRA detail.";
    }
  }
}

function detailFor(id) {
  return store.loraDetails[id] || null;
}

function downloadLora(lora) {
  // Direct browser GET (bypasses axios); preserve share token like other media.
  // API_BASE_URL already includes the /api/v1 prefix.
  const url = appendShareToken(`${API_BASE_URL}/loras/${lora.id}/download`);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${lora.name}.safetensors`;
  a.click();
}

async function copyFilePath(lora) {
  actionError.value = "";
  try {
    const path = await store.fetchLoraFilePath(lora.id);
    if (path && (await copyText(path))) {
      copiedPathId.value = lora.id;
      setTimeout(() => {
        if (copiedPathId.value === lora.id) copiedPathId.value = null;
      }, 2000);
    }
  } catch (err) {
    actionError.value =
      err?.response?.data?.detail ||
      err?.message ||
      "Could not read the file path.";
  }
}

async function deleteLora(lora) {
  if (
    !window.confirm(
      `Delete LoRA "${lora.name}"? This permanently removes the .safetensors file from disk. This cannot be undone.`,
    )
  ) {
    return;
  }
  deletingLoraId.value = lora.id;
  actionError.value = "";
  try {
    await store.deleteLora(lora.id);
    if (expandedLoraId.value === lora.id) expandedLoraId.value = null;
  } catch (err) {
    actionError.value =
      err?.response?.data?.detail || err?.message || "Delete failed.";
  } finally {
    deletingLoraId.value = null;
  }
}

// Reproducibility disclosure state (per-lora).
const reproOpen = ref({});
function toggleRepro(id) {
  reproOpen.value = { ...reproOpen.value, [id]: !reproOpen.value[id] };
}
</script>

<template>
  <div class="settings-section training-section">
    <!-- ── (a) ai-toolkit setup ──────────────────────────────────────────── -->
    <section class="training-block" aria-labelledby="training-setup-heading">
      <h3 id="training-setup-heading" class="training-block-heading">
        ai-toolkit setup
      </h3>
      <p class="training-block-hint">
        Point PixlStash at your local
        <a
          href="https://github.com/ostris/ai-toolkit"
          target="_blank"
          rel="noopener noreferrer"
          >ai-toolkit</a
        >
        checkout so it can run training jobs.
      </p>

      <v-text-field
        :model-value="store.aiToolkitRepoPath"
        label="ai-toolkit repo path"
        placeholder="/home/you/ai-toolkit"
        density="compact"
        variant="outlined"
        hide-details="auto"
        class="mb-2"
        :error-messages="setupErrors.ai_toolkit_repo_path || []"
        @update:model-value="store.aiToolkitRepoPath = $event"
        @blur="saveSetupField('ai_toolkit_repo_path')"
      />
      <v-text-field
        :model-value="store.aiToolkitPython"
        label="Python interpreter"
        placeholder="/home/you/ai-toolkit/.venv/bin/python"
        density="compact"
        variant="outlined"
        hide-details="auto"
        class="mb-2"
        :error-messages="setupErrors.ai_toolkit_python || []"
        @update:model-value="store.aiToolkitPython = $event"
        @blur="saveSetupField('ai_toolkit_python')"
      />

      <div
        v-if="store.configLoaded && !store.aiToolkitConfigured"
        class="training-inline-warn"
      >
        <v-icon size="14" class="mr-1">mdi-alert-outline</v-icon>
        Set the repo path and Python interpreter to enable training.
      </div>
    </section>

    <div v-if="actionError" class="training-inline-error">{{ actionError }}</div>

    <!-- ── (b) Jobs ──────────────────────────────────────────────────────── -->
    <section class="training-block" aria-labelledby="training-jobs-heading">
      <h3 id="training-jobs-heading" class="training-block-heading">Jobs</h3>

      <div v-if="store.jobsError" class="training-inline-error">
        {{ store.jobsError }}
      </div>

      <div
        v-if="store.jobsLoading && !jobs.length"
        class="training-loading"
        aria-live="polite"
      >
        <v-skeleton-loader type="list-item-two-line" class="training-skel" />
        <v-skeleton-loader type="list-item-two-line" class="training-skel" />
      </div>

      <p v-else-if="!jobs.length" class="training-empty-line">
        No training jobs yet.
      </p>

      <ul v-else class="training-list">
        <li v-for="job in jobs" :key="job.id" class="training-row">
          <div class="training-row-main">
            <v-chip
              :color="statusMeta(job.status).color"
              size="x-small"
              variant="tonal"
              class="training-status-chip"
            >
              <v-icon start size="13">{{ statusMeta(job.status).icon }}</v-icon>
              {{ statusMeta(job.status).label }}
            </v-chip>
            <span class="training-row-name" :title="job.name">{{
              job.name
            }}</span>
            <span class="training-row-scope">{{ scopeLabel(job) }}</span>
          </div>
          <div class="training-row-sub">
            <span
              v-if="!store.isTerminalStatus(job.status) && job.total_steps"
              class="training-row-progress"
            >
              step {{ job.step }}/{{ job.total_steps }}
            </span>
            <span
              v-if="job.status === 'error' && job.error"
              class="training-row-err"
              :title="job.error"
              >{{ job.error }}</span
            >
          </div>
          <div class="training-row-actions">
            <v-btn
              v-if="!store.isTerminalStatus(job.status)"
              size="x-small"
              variant="text"
              density="compact"
              color="primary"
              @click="viewInTasks"
            >
              View in Tasks
              <v-icon end size="13">mdi-open-in-new</v-icon>
            </v-btn>
            <v-btn
              v-if="!store.isTerminalStatus(job.status)"
              size="x-small"
              variant="text"
              density="compact"
              color="warning"
              :loading="cancellingId === job.id"
              @click="cancelJob(job)"
            >
              Cancel
            </v-btn>
            <v-btn
              v-else
              size="x-small"
              variant="text"
              density="compact"
              color="error"
              :loading="deletingJobId === job.id"
              aria-label="Delete job"
              @click="deleteJob(job)"
            >
              <v-icon size="14">mdi-delete-outline</v-icon>
              <Tooltip
                text="Delete job and its work directory"
                activator="parent"
              />
            </v-btn>
          </div>
        </li>
      </ul>
    </section>

    <!-- ── (c) Trained LoRAs registry ────────────────────────────────────── -->
    <section class="training-block" aria-labelledby="training-loras-heading">
      <h3 id="training-loras-heading" class="training-block-heading">
        Trained LoRAs
      </h3>

      <div v-if="store.lorasError" class="training-inline-error">
        {{ store.lorasError }}
      </div>

      <div
        v-if="store.lorasLoading && !loras.length"
        class="training-loading"
        aria-live="polite"
      >
        <v-skeleton-loader type="list-item-two-line" class="training-skel" />
        <v-skeleton-loader type="list-item-two-line" class="training-skel" />
      </div>

      <div v-else-if="!loras.length" class="training-empty">
        <v-icon size="36" class="mb-2">mdi-creation-outline</v-icon>
        <p>No trained LoRAs yet.</p>
        <p class="training-empty-hint">
          Open a set or character and choose Train LoRA to create one. A LoRA
          teaches an image model your subject so you can generate new pictures
          of it.
        </p>
      </div>

      <ul v-else class="training-list">
        <li v-for="lora in loras" :key="lora.id" class="training-lora">
          <button
            type="button"
            class="training-lora-header"
            :aria-expanded="expandedLoraId === lora.id ? 'true' : 'false'"
            @click="toggleLora(lora)"
          >
            <v-icon size="16" class="training-lora-chevron">{{
              expandedLoraId === lora.id
                ? "mdi-chevron-down"
                : "mdi-chevron-right"
            }}</v-icon>
            <span class="training-lora-name">{{ lora.name }}</span>
            <span class="training-lora-meta">
              {{ scopeLabel(lora) }}
              <template v-if="lora.step != null"
                >· {{ lora.step }} steps</template
              >
              · {{ humanBytes(lora.file_size_bytes) }} ·
              <span
                >{{ relativeDate(lora.created_at)
                }}<Tooltip
                  :text="formatUserDate(lora.created_at, 'iso')"
                  activator="parent"
                /></span
              >
            </span>
          </button>

          <div v-if="expandedLoraId === lora.id" class="training-lora-detail">
            <div
              v-if="store.loraDetailLoading[lora.id]"
              class="training-loading"
            >
              <v-progress-circular indeterminate size="18" class="mr-2" />
              Loading detail…
            </div>

            <template v-else-if="detailFor(lora.id)">
              <!-- Base model line (from the producing job) -->
              <p class="training-detail-line">
                <strong>Base model:</strong>
                {{ detailFor(lora.id).lora.job_base_model || "-" }}
                <span v-if="detailFor(lora.id).lora.job_trigger_word">
                  · <strong>Trigger:</strong>
                  {{ detailFor(lora.id).lora.job_trigger_word }}
                </span>
              </p>

              <!-- Loss chart (with text alternative) -->
              <LoraLossChart
                v-if="detailFor(lora.id).job"
                :curve="detailFor(lora.id).job.loss_curve || []"
                class="mb-2"
              />

              <!-- Sample previews. The vault has no path-based image-serving
                   endpoint, so we list the relative vault paths rather than
                   inline thumbnails (the files sit next to the LoRA on disk). -->
              <div
                v-if="
                  detailFor(lora.id).job &&
                  (detailFor(lora.id).job.sample_image_paths || []).length
                "
                class="training-samples"
              >
                <div class="training-samples-label">
                  Sample previews ({{
                    detailFor(lora.id).job.sample_image_paths.length
                  }})
                  <span class="training-samples-note"
                    >(stored in the vault)</span
                  >
                </div>
                <ul class="training-samples-list">
                  <li
                    v-for="(p, i) in detailFor(lora.id).job.sample_image_paths"
                    :key="i"
                    class="training-sample-path"
                  >
                    {{ p }}
                  </li>
                </ul>
              </div>

              <!-- Primary actions -->
              <div class="training-detail-actions">
                <v-btn
                  size="small"
                  variant="tonal"
                  color="primary"
                  @click="downloadLora(lora)"
                >
                  <v-icon start size="15">mdi-download</v-icon>
                  Download .safetensors
                </v-btn>
                <v-btn
                  size="small"
                  variant="text"
                  @click="copyFilePath(lora)"
                >
                  <v-icon start size="15">{{
                    copiedPathId === lora.id
                      ? "mdi-check"
                      : "mdi-content-copy"
                  }}</v-icon>
                  {{ copiedPathId === lora.id ? "Copied" : "Copy file path" }}
                </v-btn>
                <v-btn
                  size="small"
                  variant="text"
                  color="error"
                  :loading="deletingLoraId === lora.id"
                  @click="deleteLora(lora)"
                >
                  <v-icon start size="15">mdi-delete-outline</v-icon>
                  Delete
                </v-btn>
              </div>
              <p class="training-detail-hint">
                Copy the file path into a ComfyUI LoRA loader node to use this
                model.
              </p>

              <!-- Reproducibility disclosure -->
              <button
                type="button"
                class="training-repro-toggle"
                :aria-expanded="reproOpen[lora.id] ? 'true' : 'false'"
                @click="toggleRepro(lora.id)"
              >
                <v-icon size="13">{{
                  reproOpen[lora.id]
                    ? "mdi-chevron-down"
                    : "mdi-chevron-right"
                }}</v-icon>
                Reproducibility
              </button>
              <div v-if="reproOpen[lora.id]" class="training-repro">
                <p class="training-detail-line">
                  <strong>ai-toolkit commit:</strong>
                  {{
                    detailFor(lora.id).lora.job_ai_toolkit_commit || "-"
                  }}
                </p>
                <p
                  v-if="detailFor(lora.id).job"
                  class="training-detail-line"
                >
                  <strong>Dataset pictures:</strong>
                  {{
                    (detailFor(lora.id).job.dataset_picture_ids || []).length
                  }}
                </p>
                <details
                  v-if="detailFor(lora.id).job?.rendered_config"
                  class="training-config-details"
                >
                  <summary>Rendered config</summary>
                  <pre class="training-config-pre">{{
                    JSON.stringify(
                      detailFor(lora.id).job.rendered_config,
                      null,
                      2,
                    )
                  }}</pre>
                </details>
              </div>
            </template>

            <p v-else class="training-empty-line">
              Could not load detail for this LoRA.
            </p>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.training-section {
  padding: var(--space-4) 0;
  min-height: 200px;
}

.training-block {
  margin-bottom: var(--space-5);
}

.training-block-heading {
  font-size: var(--text-base);
  font-weight: 600;
  margin: 0 0 var(--space-2);
}

.training-block-hint {
  font-size: var(--text-xs);
  opacity: 0.75;
  margin: 0 0 var(--space-3);
}

.training-block-hint a {
  color: rgb(var(--v-theme-primary));
}

.training-inline-warn {
  display: flex;
  align-items: center;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-warning));
  margin-top: var(--space-3);
}

.training-inline-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin: var(--space-2) 0;
}

.training-loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) 0;
  font-size: var(--text-sm);
  opacity: 0.7;
}

.training-skel {
  background: transparent;
}

.training-empty-line {
  font-size: var(--text-sm);
  opacity: 0.6;
  padding: var(--space-2) 0;
  margin: 0;
}

.training-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--space-6) var(--space-4);
  opacity: 0.75;
}

.training-empty p {
  margin: 0;
  font-size: var(--text-sm);
}

.training-empty-hint {
  font-size: var(--text-xs) !important;
  opacity: 0.8;
  margin-top: var(--space-2) !important;
  max-width: 360px;
}

.training-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.training-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-3) var(--space-2);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.training-row-main {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  flex: 1;
}

.training-status-chip {
  flex-shrink: 0;
}

.training-row-name {
  font-size: var(--text-sm);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.training-row-scope {
  font-size: var(--text-xs);
  opacity: 0.65;
  white-space: nowrap;
}

.training-row-sub {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-xs);
  opacity: 0.75;
}

.training-row-err {
  color: rgb(var(--v-theme-error));
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.training-row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
}

/* ── Registry ── */
.training-lora {
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.training-lora-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-2);
  background: none;
  border: none;
  text-align: left;
  cursor: pointer;
  min-height: 44px;
}

.training-lora-header:focus-visible {
  outline: var(--focus-width) solid rgb(var(--v-theme-primary));
  outline-offset: calc(-1 * var(--focus-width));
  border-radius: var(--radius-sm);
}

.training-lora-chevron {
  flex-shrink: 0;
  opacity: 0.7;
}

.training-lora-name {
  font-size: var(--text-sm);
  font-weight: 600;
  flex-shrink: 0;
}

.training-lora-meta {
  font-size: var(--text-xs);
  opacity: 0.65;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.training-lora-detail {
  padding: var(--space-2) var(--space-3) var(--space-4) var(--space-6);
}

.training-detail-line {
  font-size: var(--text-xs);
  margin: var(--space-1) 0;
}

.training-detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.training-detail-hint {
  font-size: var(--text-xs);
  opacity: 0.6;
  margin: var(--space-2) 0 var(--space-1);
}

.training-samples {
  margin: var(--space-3) 0;
}

.training-samples-label {
  font-size: var(--text-xs);
  font-weight: 500;
  opacity: 0.8;
}

.training-samples-note {
  font-weight: 400;
  opacity: 0.7;
}

.training-samples-list {
  list-style: none;
  margin: var(--space-2) 0 0;
  padding: 0;
}

.training-sample-path {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  opacity: 0.75;
  word-break: break-all;
}

.training-repro-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: none;
  padding: var(--space-2) 0 var(--space-1);
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.training-repro-toggle:focus-visible {
  outline: var(--focus-width) solid rgb(var(--v-theme-primary));
  outline-offset: var(--focus-offset);
  border-radius: var(--radius-sm);
}

.training-repro {
  padding-top: var(--space-2);
}

.training-config-details {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
}

.training-config-details summary {
  cursor: pointer;
  opacity: 0.8;
}

.training-config-pre {
  max-height: 220px;
  overflow: auto;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  font-size: var(--text-2xs);
  margin-top: var(--space-2);
}
</style>
