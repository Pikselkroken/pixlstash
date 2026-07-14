<script setup>
/**
 * Settings section for browsing, creating, renaming, and deleting snapshots,
 * and triggering a full-vault restore.
 *
 * Requires the `useSnapshotsStore` and `useSnapshotsStore.openRestoreDialog`.
 * Gated behind `isReadOnly === false` at the tab level in UserSettingsDialog.
 */
import { computed, ref, watch } from "vue";
import {
  VSwitch,
  VProgressLinear,
  VProgressCircular,
  VIcon,
  VTextField,
} from "vuetify/components";
import { useSnapshotsStore } from "../../stores/useSnapshotsStore";
import { formatUserDate } from "../../utils/utils";
import { relativeDate } from "../../utils/snapshots";
import AppInput from "../widgets/AppInput.vue";
import AppButton from "../widgets/AppButton.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsInfoCard from "./SettingsInfoCard.vue";

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
  <div class="snapshots-section">
    <!-- ── Active job banner ─────────────────────────────────────────────── -->
    <div v-if="activeJob" class="snapshot-job-banner">
      <v-progress-linear indeterminate color="accent" />
      <span class="snapshot-job-label">
        <v-icon size="16">mdi-restore</v-icon>
        {{
          activeJob.kind === "RESTORE"
            ? "Restore in progress…"
            : "Creating snapshot…"
        }}
      </span>
    </div>

    <!-- ── Toggle + create ───────────────────────────────────────────────── -->
    <SettingsSection first>
      <div class="snapshot-toggle-row">
        <v-switch
          :model-value="store.dailySnapshotsEnabled"
          label="Automatic snapshots"
          density="compact"
          hide-details
          color="accent"
          @update:model-value="handleToggleDailySnapshots($event)"
        />
      </div>
      <div v-if="dailyToggleError" class="snapshot-inline-error">
        {{ dailyToggleError }}
      </div>

      <div class="snapshot-create-row">
        <AppInput
          v-model="createLabel"
          placeholder="Label (optional)"
          :disabled="!!activeJob || creating"
          class="snapshot-label-field"
          @enter="handleCreate"
        />
        <AppButton
          variant="primary_green"
          icon-left="camera"
          :disabled="!!activeJob || creating"
          @click="handleCreate"
        >
          Create now
        </AppButton>
      </div>
      <div v-if="createError" class="snapshot-inline-error">
        {{ createError }}
      </div>
      <div v-if="createSuccess" class="snapshot-inline-success">
        {{ createSuccess }}
      </div>
    </SettingsSection>

    <!-- ── Error / Loading ───────────────────────────────────────────────── -->
    <div v-if="store.error" class="snapshot-inline-error">
      {{ store.error }}
    </div>

    <!-- ── Snapshot list ───────────────────────────────────────────────── -->
    <div v-if="isLoading && !snapshots.length" class="snapshot-loading">
      <v-progress-circular indeterminate size="20" />
      Loading snapshots…
    </div>

    <div v-else-if="!snapshots.length && !isLoading" class="snapshot-empty">
      <v-icon size="36">mdi-camera-off</v-icon>
      <p>No snapshots yet.</p>
      <p class="snapshot-empty-hint">Create your first snapshot above.</p>
    </div>

    <div v-else class="snapshot-list">
      <div
        v-for="cp in snapshots"
        :key="cp.id"
        class="snapshot-row"
        :class="{ 'snapshot-row--incompatible': !cp.is_compatible }"
      >
        <!-- Kind pill + created -->
        <div class="snapshot-row-meta">
          <span class="kind-pill" :class="`kind-pill--${cp.kind}`">
            {{ cp.kind }}
          </span>
          <span
            v-if="!cp.is_compatible"
            class="kind-pill kind-pill--incompatible"
            title="Schema version is newer than the live database; restore not supported."
          >
            incompatible
          </span>
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
          <span
            v-else
            class="snapshot-label-text"
            :class="{ 'snapshot-label-text--empty': !cp.label }"
            title="Double-click to rename"
            @dblclick="!activeJob && startEditing(cp)"
          >
            {{ cp.label || "—" }}
          </span>
        </div>

        <!-- Stats -->
        <div class="snapshot-row-stats">
          <span title="Pictures">
            <v-icon size="13">mdi-image-multiple-outline</v-icon>
            {{ cp.picture_count }}
          </span>
          <span v-if="cp.picture_set_count" title="Sets">
            <v-icon size="13">mdi-folder-multiple-outline</v-icon>
            {{ cp.picture_set_count }}
          </span>
          <span v-if="cp.project_count" title="Projects">
            <v-icon size="13">mdi-briefcase-outline</v-icon>
            {{ cp.project_count }}
          </span>
          <span v-if="cp.character_count" title="Characters">
            <v-icon size="13">mdi-account-multiple-outline</v-icon>
            {{ cp.character_count }}
          </span>
          <span title="Size">{{ humanBytes(cp.byte_size) }}</span>
        </div>

        <!-- Actions -->
        <div class="snapshot-row-actions">
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="restore"
            icon-only
            :disabled="!!activeJob || !cp.is_compatible"
            :title="
              !cp.is_compatible
                ? 'Restore not available: snapshot schema is newer than live DB'
                : 'Restore everything from this snapshot'
            "
            @click="handleRestore(cp)"
          />
          <AppButton
            variant="ghost"
            size="sm"
            icon-left="delete-outline"
            icon-only
            title="Delete"
            :disabled="!!activeJob || deletingId === cp.id"
            @click="handleDelete(cp)"
          />
        </div>
      </div>
    </div>

    <div v-if="deleteError" class="snapshot-inline-error">
      {{ deleteError }}
    </div>

    <!-- ── Retention info ────────────────────────────────────────────────── -->
    <div class="snapshot-notes">
      <SettingsInfoCard>
        GFS retention: 7 daily, 4 weekly, 12 monthly. Manual &amp; opportunistic
        snapshots are kept until deleted.
      </SettingsInfoCard>
      <SettingsInfoCard>
        Snapshots are not a backup solution. They store metadata only, on the
        same storage as the live vault. For true backups, regularly export your
        full vault and store it separately.
      </SettingsInfoCard>
    </div>
  </div>
</template>

<style scoped>
.snapshots-section {
  display: flex;
  flex-direction: column;
  min-height: 100%;
}

/* ── Active job banner ─────────────────────────────────────────────────── */
.snapshot-job-banner {
  background: rgba(var(--v-theme-accent), 0.1);
  border: 1px solid rgba(var(--v-theme-accent), 0.3);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
}

.snapshot-job-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  font-size: var(--text-sm);
}

/* ── Toggle + create ───────────────────────────────────────────────────── */
.snapshot-toggle-row {
  margin-bottom: var(--space-4);
}

.snapshot-create-row {
  display: flex;
  gap: var(--space-3);
  align-items: center;
}

.snapshot-label-field {
  flex: 1;
  min-width: 0;
}

/* ── List ──────────────────────────────────────────────────────────────── */
.snapshot-list {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding-top: var(--space-2);
  /* The list scrolls internally so the Snapshots tab itself doesn't scroll. */
  max-height: 260px;
  overflow-y: auto;
}

.snapshot-loading,
.snapshot-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-7) 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-base);
  text-align: center;
}

.snapshot-empty-hint {
  font-size: var(--text-xs);
}

.snapshot-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-1);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.snapshot-row:last-child {
  border-bottom: none;
}

.snapshot-row--incompatible {
  opacity: 0.6;
}

.snapshot-row-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  min-width: 120px;
}

/* ── Kind pill ─────────────────────────────────────────────────────────── */
/* Compact: 11px is the type-ramp floor, so the pill is shrunk via tighter
   padding rather than a smaller font, freeing row width for the label. */
.kind-pill {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.02em;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), 0.6);
  background: color-mix(in oklab, currentColor 16%, transparent);
}

.kind-pill--DAILY {
  color: rgb(var(--v-theme-tertiary));
}
.kind-pill--MANUAL {
  color: rgb(var(--v-theme-accent));
}
.kind-pill--WEEKLY {
  color: rgb(var(--v-theme-secondary));
}
.kind-pill--incompatible {
  color: rgb(var(--v-theme-error));
}

.snapshot-created-at {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.snapshot-row-label {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
}

.snapshot-label-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: italic;
  color: rgb(var(--v-theme-on-surface));
  cursor: text;
}

.snapshot-label-text--empty {
  font-style: normal;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.snapshot-edit-field {
  max-width: 220px;
}

.snapshot-row-stats {
  display: flex;
  gap: var(--space-4);
  align-items: center;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
  flex-shrink: 0;
}

.snapshot-row-stats span {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.snapshot-row-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}

/* ── Inline status ─────────────────────────────────────────────────────── */
.snapshot-inline-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin-top: var(--space-2);
}

.snapshot-inline-success {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-success));
  margin-top: var(--space-2);
}

/* ── Trailing notes ────────────────────────────────────────────────────── */
.snapshot-notes {
  margin-top: auto;
  padding-top: var(--space-5);
}
</style>
