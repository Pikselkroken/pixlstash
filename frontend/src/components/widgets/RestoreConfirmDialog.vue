<script setup>
/**
 * Shared restore-from-snapshot confirmation dialog.
 *
 * Usage:
 *   <RestoreConfirmDialog
 *     v-model:open="open"
 *     :snapshot-id="id"      // null → show picker step first
 *     :resources="[{type,id}]" // null → full-vault restore
 *     @confirmed="onRestoreConfirmed"
 *   />
 *
 * The dialog fetches a dry-run preview from the server before confirming.
 * After the user clicks "Restore", it calls executeRestore() on the store.
 */
import { computed, ref, watch } from "vue";
import { useSnapshotsStore } from "../../stores/useSnapshotsStore";
import { formatUserDate } from "../../utils/utils";
import { kindChipColor, relativeDate } from "../../utils/snapshots";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * The snapshot to restore from.  When null, a picker step is shown so
   * the user can choose one from the store's cached list.
   */
  snapshotId: { type: Number, default: null },
  /**
   * Specific resources to restore.  When null, a full-vault restore is
   * performed.  Each item is ``{type: string, id: number}``.
   */
  resources: { type: Array, default: null },
});

const emit = defineEmits(["update:open", "confirmed"]);

const store = useSnapshotsStore();

// ── Internal state ─────────────────────────────────────────────────────────
// Which snapshot the user has chosen (may come from props or picker step).
const selectedSnapshotId = ref(null);

// Preview data fetched from the server.
const preview = ref(null);
const previewLoading = ref(false);
const previewError = ref("");

// Restore execution state.
const restoring = ref(false);
const restoreError = ref("");

// Show/hide the per-resource diff table.
const showDiffTable = ref(false);

// Full-vault restore requires an explicit acknowledgement before the
// destructive Restore button enables (`resources === null` is the
// full-vault path). For per-resource restores this is a no-op.
const fullVaultAcknowledged = ref(false);
const isFullVaultRestore = computed(() => props.resources == null);

// Missing-dependencies confirmation gate. When the server returns 409
// {code: missing_dependencies, missing: {...}}, we surface the list and
// ask the user to confirm (YES) before retrying with
// ``confirm_restore_dependencies=true``. NO leaves the live DB untouched.
const missingDependencies = ref(null);

// ── Computed ──────────────────────────────────────────────────────────────
const dialogOpen = computed({
  get: () => props.open,
  set: (val) => emit("update:open", val),
});

const isPickerStep = computed(
  () => selectedSnapshotId.value == null && props.snapshotId == null,
);

const effectiveSnapshotId = computed(
  () => selectedSnapshotId.value ?? props.snapshotId,
);

const snapshots = computed(() => store.snapshots);

function summaryLabel(key) {
  const labels = {
    pictures_to_revert: "Pictures to revert",
    pictures_to_recreate: "Pictures to recreate",
    pictures_to_delete: "Pictures to delete",
    missing_files: "Missing files",
    picture_sets_to_revert: "Sets to revert",
    projects_to_revert: "Projects to revert",
    characters_to_revert: "Characters to revert",
  };
  return labels[key] ?? key;
}

function summaryColor(key) {
  if (key === "pictures_to_delete" || key === "missing_files") return "error";
  return "on-surface";
}

// ── Lifecycle ──────────────────────────────────────────────────────────────
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      // Reset to initial state each time the dialog opens.
      selectedSnapshotId.value = null;
      preview.value = null;
      previewLoading.value = false;
      previewError.value = "";
      restoring.value = false;
      restoreError.value = "";
      showDiffTable.value = false;
      fullVaultAcknowledged.value = false;
      missingDependencies.value = null;
      // If a snapshot is already known, go straight to the preview.
      if (props.snapshotId != null) {
        fetchPreview(props.snapshotId);
      } else {
        // Load snapshot list for the picker.
        if (!store.snapshots.length) store.fetchSnapshots();
      }
    }
  },
);

async function fetchPreview(cpId) {
  previewLoading.value = true;
  previewError.value = "";
  preview.value = null;
  try {
    preview.value = await store.previewRestore(cpId, props.resources);
  } catch (err) {
    previewError.value =
      err?.response?.data?.detail || err?.message || "Failed to load preview.";
  } finally {
    previewLoading.value = false;
  }
}

// ── Picker step ────────────────────────────────────────────────────────────
function selectSnapshot(cp) {
  if (!cp.is_compatible) return;
  selectedSnapshotId.value = cp.id;
  fullVaultAcknowledged.value = false;
  missingDependencies.value = null;
  fetchPreview(cp.id);
}

function backToPicker() {
  selectedSnapshotId.value = null;
  preview.value = null;
  previewError.value = "";
  fullVaultAcknowledged.value = false;
  missingDependencies.value = null;
}

// ── Restore ────────────────────────────────────────────────────────────────
async function handleRestore(opts = {}) {
  const { confirmRestoreDependencies = false } = opts;
  restoring.value = true;
  restoreError.value = "";
  if (!confirmRestoreDependencies) {
    // Fresh attempt — clear any stale missing-deps prompt.
    missingDependencies.value = null;
  }
  try {
    const report = await store.executeRestore(
      effectiveSnapshotId.value,
      props.resources,
      { confirmRestoreDependencies },
    );
    emit("confirmed", report);
    dialogOpen.value = false;
  } catch (err) {
    const detail = err?.response?.data?.detail;
    // 409 with code=missing_dependencies — switch to the confirm prompt
    // instead of treating it as a failure.
    if (
      err?.response?.status === 409 &&
      detail &&
      typeof detail === "object" &&
      detail.code === "missing_dependencies"
    ) {
      missingDependencies.value = detail.missing || {};
      return;
    }
    restoreError.value =
      (typeof detail === "string" ? detail : null) ||
      err?.message ||
      "Restore failed.";
  } finally {
    restoring.value = false;
  }
}

function confirmRestoreDependencies() {
  handleRestore({ confirmRestoreDependencies: true });
}

function declineRestoreDependencies() {
  // User refused — leave the live DB untouched. Drop back to the
  // preview screen so they can either cancel or pick a different
  // snapshot.
  missingDependencies.value = null;
}

function humanKind(kind, count) {
  const map = {
    characters: count > 1 ? "characters" : "character",
    picture_sets: count > 1 ? "picture sets" : "picture set",
    projects: count > 1 ? "projects" : "project",
  };
  return map[kind] ?? kind;
}

const canRestore = computed(
  () =>
    !previewLoading.value &&
    !restoring.value &&
    preview.value != null &&
    preview.value?.snapshot?.is_compatible !== false &&
    // Full-vault restore is irreversible-grade destructive — require an
    // explicit acknowledgement that this overwrites the entire DB.
    (!isFullVaultRestore.value || fullVaultAcknowledged.value),
);
</script>

<template>
  <v-dialog
    v-model="dialogOpen"
    max-width="680"
    @click:outside="dialogOpen = false"
  >
    <v-card class="restore-dialog-card">
      <!-- ── Header ─────────────────────────────────────────────────────── -->
      <v-card-title class="restore-dialog-title">
        <v-icon size="20" class="mr-2">mdi-restore</v-icon>
        Restore from snapshot
        <v-btn
          icon
          size="28px"
          variant="text"
          class="ml-auto"
          @click="dialogOpen = false"
        >
          <v-icon size="18">mdi-close</v-icon>
        </v-btn>
      </v-card-title>

      <v-card-text class="restore-dialog-body">
        <!-- ── Step 1: Snapshot picker ──────────────────────────────── -->
        <template v-if="isPickerStep">
          <p class="restore-picker-hint">Select a snapshot to restore from:</p>
          <div
            v-if="store.loading && !snapshots.length"
            class="restore-loading"
          >
            <v-progress-circular indeterminate size="20" class="mr-2" />
            Loading snapshots…
          </div>
          <div v-else-if="!snapshots.length" class="restore-empty">
            No snapshots found.
          </div>
          <div
            v-for="cp in snapshots"
            :key="cp.id"
            class="restore-picker-row"
            :class="{
              'restore-picker-row--disabled': !cp.is_compatible,
            }"
            @click="selectSnapshot(cp)"
          >
            <v-chip
              :color="kindChipColor(cp.kind)"
              size="x-small"
              variant="tonal"
              class="mr-2"
            >
              {{ cp.kind }}
            </v-chip>
            <span class="restore-picker-label">
              {{ cp.label || "—" }}
            </span>
            <span
              class="restore-picker-date"
              :title="formatUserDate(cp.created_at, 'iso')"
            >
              {{ relativeDate(cp.created_at) }}
            </span>
            <v-chip
              v-if="!cp.is_compatible"
              color="error"
              size="x-small"
              variant="tonal"
              class="ml-2"
              title="Schema version newer than live DB; restore not available."
            >
              incompatible
            </v-chip>
          </div>
        </template>

        <!-- ── Step 2: Preview ────────────────────────────────────────── -->
        <template v-else>
          <!-- Back button (only when we came through the picker) -->
          <v-btn
            v-if="!props.snapshotId"
            size="x-small"
            variant="text"
            density="compact"
            class="mb-3"
            @click="backToPicker"
          >
            <v-icon size="14" class="mr-1">mdi-arrow-left</v-icon>
            Back
          </v-btn>

          <!-- Snapshot header -->
          <div v-if="preview" class="restore-preview-header">
            <v-chip
              :color="kindChipColor(preview.snapshot?.kind)"
              size="small"
              variant="tonal"
              class="mr-2"
            >
              {{ preview.snapshot?.kind }}
            </v-chip>
            <span class="restore-preview-cp-label">
              {{ preview.snapshot?.label || "—" }}
            </span>
            <span
              class="restore-preview-cp-date"
              :title="formatUserDate(preview.snapshot?.created_at, 'iso')"
            >
              {{ relativeDate(preview.snapshot?.created_at) }}
            </span>
          </div>

          <!-- Loading skeleton -->
          <div v-if="previewLoading" class="restore-loading">
            <v-progress-circular indeterminate size="20" class="mr-2" />
            Loading preview…
          </div>

          <div v-else-if="previewError" class="restore-inline-error">
            {{ previewError }}
          </div>

          <template v-else-if="preview">
            <!-- Warnings -->
            <v-alert
              v-for="(warn, i) in preview.warnings"
              :key="i"
              type="warning"
              density="compact"
              variant="tonal"
              class="mb-2"
              style="font-size: 0.78rem"
            >
              {{ warn }}
            </v-alert>

            <!-- Summary blocks -->
            <div class="restore-summary-grid">
              <template v-for="(val, key) in preview.summary" :key="key">
                <div
                  v-if="val > 0"
                  class="restore-summary-card"
                  :class="{
                    'restore-summary-card--danger':
                      key === 'pictures_to_delete' || key === 'missing_files',
                  }"
                >
                  <span class="restore-summary-value">{{ val }}</span>
                  <span class="restore-summary-label">{{
                    summaryLabel(key)
                  }}</span>
                </div>
              </template>
            </div>

            <!-- Per-resource diff table -->
            <div v-if="preview.resources?.length" class="restore-diff-section">
              <button
                class="restore-diff-toggle"
                @click="showDiffTable = !showDiffTable"
              >
                <v-icon size="14" class="mr-1">
                  {{ showDiffTable ? "mdi-chevron-up" : "mdi-chevron-down" }}
                </v-icon>
                {{ showDiffTable ? "Hide" : "Show" }} details ({{
                  preview.resources.length
                }}
                resources)
              </button>
              <div v-if="showDiffTable" class="restore-diff-table">
                <div
                  v-for="r in preview.resources"
                  :key="`${r.type}-${r.id}`"
                  class="restore-diff-row"
                  :class="{
                    'restore-diff-row--missing': !r.file_on_disk,
                    'restore-diff-row--delete': !r.exists_in_snapshot,
                  }"
                >
                  <span class="diff-type">{{ r.type }}</span>
                  <span class="diff-id">#{{ r.id }}</span>
                  <span class="diff-fields">
                    <template v-if="!r.file_on_disk">
                      <em>file missing</em>
                    </template>
                    <template v-else-if="!r.exists_in_snapshot">
                      <em>will be deleted</em>
                    </template>
                    <template v-else-if="r.changed_fields?.length">
                      {{ r.changed_fields.join(", ") }}
                    </template>
                    <template v-else>no changes</template>
                  </span>
                  <span
                    v-if="
                      r.dependent_counts &&
                      Object.keys(r.dependent_counts).length
                    "
                    class="diff-deps"
                  >
                    <span
                      v-for="(cnt, depKey) in r.dependent_counts"
                      :key="depKey"
                      >{{ depKey }}: {{ cnt }}</span
                    >
                  </span>
                </div>
              </div>
            </div>
          </template>
        </template>
      </v-card-text>

      <!-- Full-vault acknowledgement (irreversible destructive action) -->
      <div
        v-if="
          !isPickerStep &&
          isFullVaultRestore &&
          preview != null &&
          !missingDependencies
        "
        class="full-vault-ack"
      >
        <v-checkbox
          v-model="fullVaultAcknowledged"
          density="compact"
          hide-details
          color="error"
          :disabled="restoring"
        >
          <template #label>
            <span class="full-vault-ack-label">
              I understand this will <strong>overwrite the entire vault</strong>
              with the selected snapshot. All metadata changes since the
              snapshot will be lost.
            </span>
          </template>
        </v-checkbox>
      </div>

      <!-- ── Missing-dependencies prompt ──────────────────────────────────
           Server returned 409 missing_dependencies — ask the user whether
           to also restore the listed parents from the snapshot. Replaces
           the normal action footer until the user picks YES or NO. -->
      <template v-if="!isPickerStep && missingDependencies">
        <div class="missing-deps-prompt">
          <div class="missing-deps-title">
            <v-icon size="18" color="warning" class="mr-1"
              >mdi-alert-circle-outline</v-icon
            >
            This restore needs to bring back some missing parents
          </div>
          <div class="missing-deps-body">
            The snapshot references resources that have been deleted from
            your vault since it was taken:
            <ul class="missing-deps-list">
              <li
                v-for="(ids, kind) in missingDependencies"
                :key="kind"
              >
                <strong>{{ ids.length }}</strong>
                {{ humanKind(kind, ids.length) }}
                (id{{ ids.length > 1 ? "s" : "" }}: {{ ids.join(", ") }})
              </li>
            </ul>
            Restore them too, or cancel and pick a different snapshot?
          </div>
        </div>
        <v-card-actions class="restore-dialog-actions">
          <v-spacer />
          <v-btn
            variant="text"
            density="compact"
            :disabled="restoring"
            @click="declineRestoreDependencies"
          >
            No, cancel
          </v-btn>
          <v-btn
            color="error"
            density="compact"
            variant="elevated"
            :loading="restoring"
            @click="confirmRestoreDependencies"
          >
            <v-icon size="15" class="mr-1">mdi-restore</v-icon>
            Yes, restore everything
          </v-btn>
        </v-card-actions>
      </template>

      <!-- ── Footer ─────────────────────────────────────────────────────── -->
      <v-card-actions
        v-if="!isPickerStep && !missingDependencies"
        class="restore-dialog-actions"
      >
        <div v-if="restoreError" class="restore-inline-error mr-auto">
          {{ restoreError }}
        </div>
        <v-spacer v-else />
        <v-btn variant="text" density="compact" @click="dialogOpen = false">
          Cancel
        </v-btn>
        <v-btn
          color="error"
          density="compact"
          variant="elevated"
          :loading="restoring"
          :disabled="!canRestore"
          @click="handleRestore"
        >
          <v-icon size="15" class="mr-1">mdi-restore</v-icon>
          Restore
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.restore-dialog-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 10px;
  color-scheme: dark;
  max-height: 80dvh;
  display: flex;
  flex-direction: column;
}

.restore-dialog-title {
  font-size: 1rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  padding: 14px 16px 8px;
  flex-shrink: 0;
}

.restore-dialog-body {
  overflow-y: auto;
  flex: 1;
  padding: 8px 16px 12px;
}

.restore-dialog-actions {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  padding: 8px 16px;
  flex-shrink: 0;
}

/* Picker step */
.restore-picker-hint {
  font-size: 0.82rem;
  margin-bottom: 10px;
  opacity: 0.75;
}

.restore-picker-row {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  margin-bottom: 6px;
  transition: background 0.1s;
}

.restore-picker-row:hover {
  background: rgba(var(--v-theme-primary), 0.07);
}

.restore-picker-row--disabled {
  opacity: 0.45;
  cursor: default;
  pointer-events: none;
}

.restore-picker-label {
  flex: 1;
  font-size: 0.82rem;
  font-style: italic;
  margin-right: 8px;
}

.restore-picker-date {
  font-size: 0.72rem;
  opacity: 0.6;
  white-space: nowrap;
}

/* Preview step */
.restore-preview-header {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}

.restore-preview-cp-label {
  font-size: 0.85rem;
  font-style: italic;
  flex: 1;
}

.restore-preview-cp-date {
  font-size: 0.72rem;
  opacity: 0.6;
  margin-left: 8px;
  white-space: nowrap;
}

.restore-summary-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 14px;
}

.restore-summary-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 6px;
  padding: 8px 14px;
  min-width: 80px;
}

.restore-summary-card--danger {
  background: rgba(var(--v-theme-error), 0.12);
  border: 1px solid rgba(var(--v-theme-error), 0.3);
}

.restore-summary-value {
  font-size: 1.3rem;
  font-weight: 700;
  line-height: 1.2;
}

.restore-summary-label {
  font-size: 0.68rem;
  opacity: 0.7;
  text-align: center;
  margin-top: 2px;
}

/* Diff table */
.restore-diff-section {
  margin-top: 10px;
}

.restore-diff-toggle {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.78rem;
  display: flex;
  align-items: center;
  opacity: 0.7;
  color: inherit;
  padding: 0;
  margin-bottom: 6px;
}

.restore-diff-toggle:hover {
  opacity: 1;
}

.restore-diff-table {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 6px;
  overflow: hidden;
  font-size: 0.75rem;
  max-height: 220px;
  overflow-y: auto;
}

.restore-diff-row {
  display: grid;
  grid-template-columns: 80px 60px 1fr auto;
  gap: 6px;
  padding: 5px 10px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.06);
  align-items: center;
}

.restore-diff-row:last-child {
  border-bottom: none;
}

.restore-diff-row--missing {
  opacity: 0.5;
}

.restore-diff-row--delete {
  color: rgb(var(--v-theme-error));
}

.diff-type {
  font-weight: 500;
  text-transform: capitalize;
}

.diff-id {
  opacity: 0.55;
}

.diff-fields {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.diff-deps {
  display: flex;
  gap: 8px;
  opacity: 0.55;
  flex-shrink: 0;
}

/* Shared */
.restore-loading {
  display: flex;
  align-items: center;
  padding: 16px 0;
  font-size: 0.82rem;
  opacity: 0.7;
}

.restore-empty {
  padding: 16px 0;
  font-size: 0.82rem;
  opacity: 0.6;
  text-align: center;
}

.restore-inline-error {
  font-size: 0.75rem;
  color: rgb(var(--v-theme-error));
}

.full-vault-ack {
  padding: 8px 16px 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.full-vault-ack-label {
  font-size: 0.8rem;
  line-height: 1.35;
  color: rgb(var(--v-theme-on-surface));
}

.missing-deps-prompt {
  padding: 12px 16px 4px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.missing-deps-title {
  display: flex;
  align-items: center;
  font-size: 0.85rem;
  font-weight: 600;
  color: rgb(var(--v-theme-warning));
  margin-bottom: 6px;
}

.missing-deps-body {
  font-size: 0.78rem;
  line-height: 1.4;
  color: rgb(var(--v-theme-on-surface));
}

.missing-deps-list {
  margin: 6px 0 8px;
  padding-left: 20px;
}

.missing-deps-list li {
  margin-bottom: 2px;
}
</style>
