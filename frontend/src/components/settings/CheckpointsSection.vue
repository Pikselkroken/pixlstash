<script setup>
/**
 * Settings section for browsing, creating, renaming, and deleting checkpoints,
 * and triggering a full-vault restore.
 *
 * Requires the `useCheckpointsStore` and `useCheckpointsStore.openRestoreDialog`.
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog.
 */
import { computed, ref, watch } from "vue";
import { useCheckpointsStore } from "../../stores/useCheckpointsStore";
import { formatUserDate } from "../../utils/utils";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useCheckpointsStore();

// ── Local UI state ─────────────────────────────────────────────────────────
const createLabel = ref("");
const creating = ref(false);
const createError = ref("");
const createSuccess = ref("");

// Map of checkpoint id → editing state.
const editingLabel = ref({});
const savingLabel = ref({});
const saveLabelError = ref({});

// ── Fetch on open ──────────────────────────────────────────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      store.fetchCheckpoints();
      store.fetchStatus();
      store.fetchCheckpointSettings();
    }
  },
);

// ── Computed ──────────────────────────────────────────────────────────────
const checkpoints = computed(() => store.checkpoints);
const isLoading = computed(() => store.loading);
const activeJob = computed(() => store.activeJob);

function kindChipColor(kind) {
  const map = {
    MANUAL: "primary",
    DAILY: "secondary",
    WEEKLY: "info",
    MONTHLY: "success",
    OPPORTUNISTIC: "warning",
  };
  return map[kind] ?? "default";
}

function humanBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
}

function relativeDate(isoStr) {
  if (!isoStr) return "";
  const normalized =
    isoStr.includes("T") &&
    !isoStr.endsWith("Z") &&
    !/[+-]\d{2}:\d{2}$/.test(isoStr)
      ? isoStr + "Z"
      : isoStr;
  const diff = (Date.now() - new Date(normalized).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Create ─────────────────────────────────────────────────────────────────
async function handleCreate() {
  creating.value = true;
  createError.value = "";
  createSuccess.value = "";
  try {
    await store.createCheckpoint(createLabel.value.trim() || null);
    createLabel.value = "";
    createSuccess.value = "Checkpoint created.";
    setTimeout(() => {
      if (createSuccess.value === "Checkpoint created.")
        createSuccess.value = "";
    }, 3000);
  } catch (err) {
    createError.value =
      err?.response?.data?.detail ||
      err?.message ||
      "Failed to create checkpoint.";
  } finally {
    creating.value = false;
  }
}

// ── Rename (inline) ────────────────────────────────────────────────────────
function startEditing(cp) {
  editingLabel.value = { ...editingLabel.value, [cp.id]: cp.label ?? "" };
  saveLabelError.value = { ...saveLabelError.value, [cp.id]: "" };
}

function cancelEditing(id) {
  const next = { ...editingLabel.value };
  delete next[id];
  editingLabel.value = next;
}

async function saveLabel(id) {
  const label = (editingLabel.value[id] ?? "").trim() || null;
  savingLabel.value = { ...savingLabel.value, [id]: true };
  saveLabelError.value = { ...saveLabelError.value, [id]: "" };
  try {
    await store.renameCheckpoint(id, label);
    cancelEditing(id);
  } catch (err) {
    saveLabelError.value = {
      ...saveLabelError.value,
      [id]: err?.response?.data?.detail || err?.message || "Save failed.",
    };
  } finally {
    savingLabel.value = { ...savingLabel.value, [id]: false };
  }
}

// ── Delete ─────────────────────────────────────────────────────────────────
const deletingId = ref(null);
const deleteError = ref("");

async function handleDelete(cp) {
  if (
    !window.confirm(
      `Delete checkpoint "${cp.label || cp.kind + " " + relativeDate(cp.created_at)}"? This cannot be undone.`,
    )
  ) {
    return;
  }
  deletingId.value = cp.id;
  deleteError.value = "";
  try {
    await store.deleteCheckpoint(cp.id);
  } catch (err) {
    deleteError.value =
      err?.response?.data?.detail || err?.message || "Delete failed.";
  } finally {
    deletingId.value = null;
  }
}

// ── Restore ────────────────────────────────────────────────────────────────
function handleRestore(cp) {
  store.openRestoreDialog(cp.id, null);
}
</script>

<template>
  <div class="settings-section checkpoints-section">
    <!-- ── Active job banner ─────────────────────────────────────────────── -->
    <div v-if="activeJob" class="checkpoint-job-banner">
      <v-progress-linear indeterminate color="primary" class="mb-2" />
      <span class="checkpoint-job-label">
        <v-icon size="16" class="mr-1">mdi-restore</v-icon>
        {{
          activeJob.kind === "RESTORE"
            ? "Restore in progress…"
            : "Creating checkpoint…"
        }}
      </span>
    </div>

    <!-- ── Daily checkpoint toggle ──────────────────────────────────────── -->
    <div class="checkpoint-settings-row">
      <v-switch
        :model-value="store.dailyCheckpointsEnabled"
        label="Automatic daily checkpoints"
        density="compact"
        hide-details
        color="primary"
        @update:model-value="store.setDailyCheckpointsEnabled($event)"
      />
    </div>

    <!-- ── Create checkpoint ─────────────────────────────────────────────── -->
    <div class="checkpoint-create-row">
      <v-text-field
        v-model="createLabel"
        label="Label (optional)"
        density="compact"
        variant="outlined"
        hide-details
        :disabled="!!activeJob || creating"
        class="checkpoint-label-field"
        @keydown.enter="handleCreate"
      />
      <v-btn
        color="primary"
        density="compact"
        :loading="creating"
        :disabled="!!activeJob"
        @click="handleCreate"
      >
        <v-icon size="15" class="mr-1">mdi-camera</v-icon>
        Create now
      </v-btn>
    </div>
    <div v-if="createError" class="checkpoint-inline-error">
      {{ createError }}
    </div>
    <div v-if="createSuccess" class="checkpoint-inline-success">
      {{ createSuccess }}
    </div>

    <!-- ── Retention info ────────────────────────────────────────────────── -->
    <div class="checkpoint-retention-card">
      <v-icon size="14" class="mr-1">mdi-information-outline</v-icon>
      <span>
        GFS retention: 7 daily, 4 weekly, 12 monthly. Manual &amp; opportunistic
        checkpoints are kept until deleted.
      </span>
    </div>

    <!-- ── Error / Loading ───────────────────────────────────────────────── -->
    <div v-if="store.error" class="checkpoint-inline-error mt-2">
      {{ store.error }}
    </div>

    <!-- ── Checkpoint list ───────────────────────────────────────────────── -->
    <div v-if="isLoading && !checkpoints.length" class="checkpoint-loading">
      <v-progress-circular indeterminate size="20" class="mr-2" />
      Loading checkpoints…
    </div>

    <div v-else-if="!checkpoints.length && !isLoading" class="checkpoint-empty">
      <v-icon size="36" class="mb-2">mdi-camera-off</v-icon>
      <p>No checkpoints yet.</p>
      <p class="checkpoint-empty-hint">Create your first checkpoint above.</p>
    </div>

    <template v-else>
      <div
        v-for="cp in checkpoints"
        :key="cp.id"
        class="checkpoint-row"
        :class="{ 'checkpoint-row--incompatible': !cp.is_compatible }"
      >
        <!-- Kind chip + created -->
        <div class="checkpoint-row-meta">
          <v-chip
            :color="kindChipColor(cp.kind)"
            size="x-small"
            variant="tonal"
            class="mr-2"
          >
            {{ cp.kind }}
          </v-chip>
          <v-chip
            v-if="!cp.is_compatible"
            color="error"
            size="x-small"
            variant="tonal"
            title="Schema version is newer than the live database; restore not supported."
            class="mr-2"
          >
            incompatible
          </v-chip>
          <span
            class="checkpoint-created-at"
            :title="formatUserDate(cp.created_at, 'iso')"
          >
            {{ relativeDate(cp.created_at) }}
          </span>
        </div>

        <!-- Label (inline edit or display) -->
        <div class="checkpoint-row-label">
          <template v-if="editingLabel[cp.id] !== undefined">
            <v-text-field
              v-model="editingLabel[cp.id]"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              class="checkpoint-edit-field"
              :loading="savingLabel[cp.id]"
              @keydown.enter="saveLabel(cp.id)"
              @keydown.esc="cancelEditing(cp.id)"
              @blur="saveLabel(cp.id)"
            />
            <div v-if="saveLabelError[cp.id]" class="checkpoint-inline-error">
              {{ saveLabelError[cp.id] }}
            </div>
          </template>
          <span v-else class="checkpoint-label-text">
            {{ cp.label || "—" }}
          </span>
        </div>

        <!-- Stats -->
        <div class="checkpoint-row-stats">
          <span title="Pictures">
            <v-icon size="12">mdi-image-multiple-outline</v-icon>
            {{ cp.picture_count }}
          </span>
          <span v-if="cp.picture_set_count" title="Sets">
            <v-icon size="12">mdi-folder-multiple-outline</v-icon>
            {{ cp.picture_set_count }}
          </span>
          <span v-if="cp.project_count" title="Projects">
            <v-icon size="12">mdi-briefcase-outline</v-icon>
            {{ cp.project_count }}
          </span>
          <span v-if="cp.character_count" title="Characters">
            <v-icon size="12">mdi-account-multiple-outline</v-icon>
            {{ cp.character_count }}
          </span>
          <span title="Size">{{ humanBytes(cp.byte_size) }}</span>
        </div>

        <!-- Actions -->
        <div class="checkpoint-row-actions">
          <v-btn
            size="x-small"
            variant="text"
            density="compact"
            title="Rename label"
            :disabled="!!activeJob || editingLabel[cp.id] !== undefined"
            @click="startEditing(cp)"
          >
            <v-icon size="14">mdi-pencil-outline</v-icon>
          </v-btn>

          <v-btn
            size="x-small"
            variant="text"
            density="compact"
            color="primary"
            :disabled="!!activeJob || !cp.is_compatible"
            :title="
              !cp.is_compatible
                ? 'Restore not available: snapshot schema is newer than live DB'
                : 'Restore everything from this checkpoint'
            "
            @click="handleRestore(cp)"
          >
            <v-icon size="14">mdi-restore</v-icon>
            Restore…
          </v-btn>

          <v-tooltip
            :text="
              cp.kind !== 'MANUAL'
                ? `${cp.kind} checkpoints are managed by GFS retention and cannot be deleted manually.`
                : ''
            "
            :disabled="cp.kind === 'MANUAL'"
          >
            <template #activator="{ props: tooltipProps }">
              <span v-bind="tooltipProps">
                <v-btn
                  size="x-small"
                  variant="text"
                  density="compact"
                  color="error"
                  :disabled="
                    cp.kind !== 'MANUAL' || !!activeJob || deletingId === cp.id
                  "
                  :loading="deletingId === cp.id"
                  title="Delete this checkpoint"
                  @click="handleDelete(cp)"
                >
                  <v-icon size="14">mdi-delete-outline</v-icon>
                </v-btn>
              </span>
            </template>
          </v-tooltip>
        </div>
      </div>
    </template>

    <div v-if="deleteError" class="checkpoint-inline-error mt-2">
      {{ deleteError }}
    </div>
  </div>
</template>

<style scoped>
.checkpoints-section {
  padding: 12px 0;
  min-height: 200px;
}

.checkpoint-job-banner {
  background: rgba(var(--v-theme-primary), 0.1);
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 12px;
}

.checkpoint-job-label {
  font-size: 0.8rem;
  display: flex;
  align-items: center;
}

.checkpoint-settings-row {
  margin-bottom: 4px;
}

.checkpoint-create-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-bottom: 8px;
}

.checkpoint-label-field {
  flex: 1;
  max-width: 280px;
}

.checkpoint-retention-card {
  display: flex;
  align-items: center;
  font-size: 0.73rem;
  opacity: 0.65;
  background: rgba(var(--v-theme-on-surface), 0.05);
  border-radius: 4px;
  padding: 6px 10px;
  margin-bottom: 14px;
  line-height: 1.4;
}

.checkpoint-loading,
.checkpoint-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 0;
  opacity: 0.6;
  font-size: 0.85rem;
  text-align: center;
}

.checkpoint-empty-hint {
  font-size: 0.75rem;
  margin-top: 4px;
}

.checkpoint-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  flex-wrap: wrap;
}

.checkpoint-row:last-child {
  border-bottom: none;
}

.checkpoint-row--incompatible {
  opacity: 0.55;
}

.checkpoint-row-meta {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  min-width: 140px;
}

.checkpoint-created-at {
  font-size: 0.73rem;
  opacity: 0.7;
  white-space: nowrap;
}

.checkpoint-row-label {
  flex: 1;
  min-width: 80px;
  font-size: 0.8rem;
}

.checkpoint-label-text {
  opacity: 0.8;
  font-style: italic;
}

.checkpoint-edit-field {
  max-width: 220px;
}

.checkpoint-row-stats {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 0.72rem;
  opacity: 0.65;
  white-space: nowrap;
  flex-shrink: 0;
}

.checkpoint-row-stats span {
  display: flex;
  align-items: center;
  gap: 2px;
}

.checkpoint-row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.checkpoint-inline-error {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-error));
  margin-top: 2px;
}

.checkpoint-inline-success {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-success));
  margin-top: 2px;
}
</style>
