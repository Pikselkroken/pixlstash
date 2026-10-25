<script setup>
/**
 * Settings section for the LoRA training feature: the ai-toolkit setup
 * (AiToolkitSetup) and the jobs list (running/queued/terminal with lifecycle
 * actions). A finished LoRA lands on the model shelf, marked Trained, so it
 * has no list of its own here.
 *
 * Mirrors SnapshotsSection: fetch on open, poll while a job is running, per-item
 * actions with window.confirm for destructive ops. Owner-only; gated behind
 * isReadOnly === false at the tab level in UserSettingsDialog.
 */
import { computed, onUnmounted, ref, watch } from "vue";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import Tooltip from "../widgets/Tooltip.vue";
import AiToolkitSetup from "./AiToolkitSetup.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useTrainingStore();
const sidebarStore = useSidebarStore();
const entityNames = useEntityNamesStore();

// ── Local UI state ───────────────────────────────────────────────────────────
const cancellingId = ref(null);
const deletingJobId = ref(null);
const actionError = ref("");

let pollTimer = null;
const POLL_INTERVAL_MS = 4000;

// ── Fetch on open + poll while running ────────────────────────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      store.fetchJobs().then(maybeSchedulePoll);
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
      stopPolling();
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
      `Delete job "${job.name}"? This removes its work directory. The LoRA stays on the model shelf.`,
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

</script>

<template>
  <div class="settings-section training-section">
    <!-- ── (a) ai-toolkit setup ──────────────────────────────────────────── -->
    <AiToolkitSetup :open="open" />

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
</style>
