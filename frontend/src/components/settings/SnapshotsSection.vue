<script setup>
/**
 * Settings section for browsing, creating, renaming, and deleting snapshots,
 * and triggering a full-vault restore.
 *
 * Requires the `useSnapshotsStore` and `useSnapshotsStore.openRestoreDialog`.
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog.
 */
import { computed, ref, watch } from "vue";
import { useSnapshotsStore } from "../../stores/useSnapshotsStore";
import { formatUserDate } from "../../utils/utils";
import { kindChipColor, relativeDate } from "../../utils/snapshots";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const store = useSnapshotsStore();

// ── Local UI state ─────────────────────────────────────────────────────────
const createLabel = ref("");
const creating = ref(false);
const createError = ref("");
const createSuccess = ref("");
const dailyToggleError = ref("");

// Map of snapshot id → editing state.
const editingLabel = ref({});
const savingLabel = ref({});
const saveLabelError = ref({});

// ── Fetch on open ──────────────────────────────────────────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      store.fetchSnapshots();
      store.fetchStatus();
      store.fetchSnapshotSettings();
    }
  },
);

// ── Computed ──────────────────────────────────────────────────────────────
const snapshots = computed(() => store.snapshots);
const isLoading = computed(() => store.loading);
const activeJob = computed(() => store.activeJob);

// GFS-scheduled kinds whose most-recent snapshot is the current automatic
// restore point and must stay locked. Older snapshots of these kinds — and
// all MANUAL / OPPORTUNISTIC snapshots — can be deleted.
const GFS_KINDS = ["DAILY", "WEEKLY", "MONTHLY"];

const lockedSnapshotIds = computed(() => {
  const latestByKind = {};
  for (const cp of store.snapshots) {
    if (!GFS_KINDS.includes(cp.kind)) continue;
    const cur = latestByKind[cp.kind];
    if (!cur || new Date(cp.created_at) > new Date(cur.created_at)) {
      latestByKind[cp.kind] = cp;
    }
  }
  return new Set(Object.values(latestByKind).map((cp) => cp.id));
});

function isLocked(cp) {
  return lockedSnapshotIds.value.has(cp.id);
}

function humanBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB", "EB"];
  let v = bytes;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
}

// ── Create ─────────────────────────────────────────────────────────────────
let _createSuccessToken = 0;
async function handleCreate() {
  creating.value = true;
  createError.value = "";
  createSuccess.value = "";
  try {
    await store.createSnapshot(createLabel.value.trim() || null);
    createLabel.value = "";
    const token = ++_createSuccessToken;
    createSuccess.value = "Snapshot created.";
    setTimeout(() => {
      if (_createSuccessToken === token) createSuccess.value = "";
    }, 3000);
  } catch (err) {
    createError.value =
      err?.response?.data?.detail ||
      err?.message ||
      "Failed to create snapshot.";
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
  // The field is bound to both @keydown.enter and @blur. An enter-save calls
  // cancelEditing(), which removes the editing entry and unmounts the input —
  // that unmount fires @blur and re-invokes saveLabel. Bail out when there is
  // no active edit so the second call can't PATCH the just-saved label to null.
  if (editingLabel.value[id] === undefined) {
    return;
  }
  const label = (editingLabel.value[id] ?? "").trim() || null;
  savingLabel.value = { ...savingLabel.value, [id]: true };
  saveLabelError.value = { ...saveLabelError.value, [id]: "" };
  try {
    await store.renameSnapshot(id, label);
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

// ── Daily snapshot toggle ───────────────────────────────────────────────────
async function handleToggleDailySnapshots(enabled) {
  dailyToggleError.value = "";
  try {
    await store.setDailySnapshotsEnabled(enabled);
  } catch (err) {
    // The store already rolled back the optimistic value; surface why.
    dailyToggleError.value =
      err?.response?.data?.detail ||
      err?.message ||
      "Failed to update daily snapshot setting.";
  }
}

// ── Delete ─────────────────────────────────────────────────────────────────
const deletingId = ref(null);
const deleteError = ref("");

async function handleDelete(cp) {
  if (
    !window.confirm(
      `Delete snapshot "${cp.label || cp.kind + " " + relativeDate(cp.created_at)}"? This cannot be undone.`,
    )
  ) {
    return;
  }
  deletingId.value = cp.id;
  deleteError.value = "";
  try {
    await store.deleteSnapshot(cp.id);
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
  <div class="settings-section snapshots-section">
    <!-- ── Active job banner ─────────────────────────────────────────────── -->
    <div v-if="activeJob" class="snapshot-job-banner">
      <v-progress-linear indeterminate color="primary" class="mb-2" />
      <span class="snapshot-job-label">
        <v-icon size="16" class="mr-1">mdi-restore</v-icon>
        {{
          activeJob.kind === "RESTORE"
            ? "Restore in progress…"
            : "Creating snapshot…"
        }}
      </span>
    </div>

    <!-- ── Daily snapshot toggle ──────────────────────────────────────── -->
    <div class="snapshot-settings-row">
      <v-switch
        :model-value="store.dailySnapshotsEnabled"
        label="Automatic snapshots"
        density="compact"
        hide-details
        color="primary"
        @update:model-value="handleToggleDailySnapshots($event)"
      />
      <div v-if="dailyToggleError" class="snapshot-inline-error">
        {{ dailyToggleError }}
      </div>
    </div>

    <!-- ── Create snapshot ─────────────────────────────────────────────── -->
    <div class="snapshot-create-row">
      <v-text-field
        v-model="createLabel"
        label="Label (optional)"
        density="compact"
        variant="outlined"
        hide-details
        :disabled="!!activeJob || creating"
        class="snapshot-label-field"
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
    <div v-if="createError" class="snapshot-inline-error">
      {{ createError }}
    </div>
    <div v-if="createSuccess" class="snapshot-inline-success">
      {{ createSuccess }}
    </div>

    <!-- ── Retention info ────────────────────────────────────────────────── -->
    <div class="snapshot-retention-card">
      <v-icon size="14" class="mr-1">mdi-information-outline</v-icon>
      <span>
        GFS retention: 7 daily, 4 weekly, 12 monthly. Manual &amp; opportunistic
        snapshots are kept until deleted.
      </span>
    </div>

    <!-- ── Error / Loading ───────────────────────────────────────────────── -->
    <div v-if="store.error" class="snapshot-inline-error mt-2">
      {{ store.error }}
    </div>

    <!-- ── Snapshot list ───────────────────────────────────────────────── -->
    <div v-if="isLoading && !snapshots.length" class="snapshot-loading">
      <v-progress-circular indeterminate size="20" class="mr-2" />
      Loading snapshots…
    </div>

    <div v-else-if="!snapshots.length && !isLoading" class="snapshot-empty">
      <v-icon size="36" class="mb-2">mdi-camera-off</v-icon>
      <p>No snapshots yet.</p>
      <p class="snapshot-empty-hint">Create your first snapshot above.</p>
    </div>

    <template v-else>
      <div
        v-for="cp in snapshots"
        :key="cp.id"
        class="snapshot-row"
        :class="{ 'snapshot-row--incompatible': !cp.is_compatible }"
      >
        <!-- Kind chip + created -->
        <div class="snapshot-row-meta">
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
            class="snapshot-created-at"
            :title="formatUserDate(cp.created_at, 'iso')"
          >
            {{ relativeDate(cp.created_at) }}
          </span>
        </div>

        <!-- Label (inline edit or display) -->
        <div class="snapshot-row-label">
          <template v-if="editingLabel[cp.id] !== undefined">
            <v-text-field
              v-model="editingLabel[cp.id]"
              density="compact"
              variant="outlined"
              hide-details
              autofocus
              class="snapshot-edit-field"
              :loading="savingLabel[cp.id]"
              @keydown.enter="saveLabel(cp.id)"
              @keydown.esc="cancelEditing(cp.id)"
              @blur="saveLabel(cp.id)"
            />
            <div v-if="saveLabelError[cp.id]" class="snapshot-inline-error">
              {{ saveLabelError[cp.id] }}
            </div>
          </template>
          <span v-else class="snapshot-label-text">
            {{ cp.label || "—" }}
          </span>
        </div>

        <!-- Stats -->
        <div class="snapshot-row-stats">
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
        <div class="snapshot-row-actions">
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
                : 'Restore everything from this snapshot'
            "
            @click="handleRestore(cp)"
          >
            <v-icon size="14">mdi-restore</v-icon>
            Restore…
          </v-btn>

          <v-tooltip
            :text="
              isLocked(cp)
                ? `The most recent ${cp.kind} snapshot is the current automatic restore point and cannot be deleted.`
                : ''
            "
            :disabled="!isLocked(cp)"
          >
            <template #activator="{ props: tooltipProps }">
              <span v-bind="tooltipProps">
                <v-btn
                  size="x-small"
                  variant="text"
                  density="compact"
                  color="error"
                  :disabled="
                    isLocked(cp) || !!activeJob || deletingId === cp.id
                  "
                  :loading="deletingId === cp.id"
                  title="Delete this snapshot"
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

    <div v-if="deleteError" class="snapshot-inline-error mt-2">
      {{ deleteError }}
    </div>
  </div>
</template>

<style scoped>
.snapshots-section {
  padding: 12px 0;
  min-height: 200px;
}

.snapshot-job-banner {
  background: rgba(var(--v-theme-primary), 0.1);
  border: 1px solid rgba(var(--v-theme-primary), 0.3);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 12px;
}

.snapshot-job-label {
  font-size: 0.8rem;
  display: flex;
  align-items: center;
}

.snapshot-settings-row {
  margin-bottom: 4px;
}

.snapshot-create-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-bottom: 8px;
}

.snapshot-label-field {
  flex: 1;
  max-width: 280px;
}

.snapshot-retention-card {
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

.snapshot-loading,
.snapshot-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 32px 0;
  opacity: 0.6;
  font-size: 0.85rem;
  text-align: center;
}

.snapshot-empty-hint {
  font-size: 0.75rem;
  margin-top: 4px;
}

.snapshot-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 8px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  flex-wrap: wrap;
}

.snapshot-row:last-child {
  border-bottom: none;
}

.snapshot-row--incompatible {
  opacity: 0.55;
}

.snapshot-row-meta {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  min-width: 140px;
}

.snapshot-created-at {
  font-size: 0.73rem;
  opacity: 0.7;
  white-space: nowrap;
}

.snapshot-row-label {
  flex: 1;
  min-width: 80px;
  font-size: 0.8rem;
}

.snapshot-label-text {
  opacity: 0.8;
  font-style: italic;
}

.snapshot-edit-field {
  max-width: 220px;
}

.snapshot-row-stats {
  display: flex;
  gap: 10px;
  align-items: center;
  font-size: 0.72rem;
  opacity: 0.65;
  white-space: nowrap;
  flex-shrink: 0;
}

.snapshot-row-stats span {
  display: flex;
  align-items: center;
  gap: 2px;
}

.snapshot-row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.snapshot-inline-error {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-error));
  margin-top: 2px;
}

.snapshot-inline-success {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-success));
  margin-top: 2px;
}
</style>
