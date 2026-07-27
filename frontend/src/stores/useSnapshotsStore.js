import { ref } from "vue";
import { defineStore } from "pinia";
import * as snapshotsApi from "../api/snapshots";
import {
  getSnapshotSettings,
  setDailySnapshotsEnabled as patchDailySnapshotsEnabled,
} from "../api/serverConfig";

export const useSnapshotsStore = defineStore("snapshots", () => {
  // ── State ─────────────────────────────────────────────────────────────────
  const snapshots = ref([]);
  const loading = ref(false);
  const activeJob = ref(null);
  const error = ref(null);

  // Drives the shared RestoreConfirmDialog instance hoisted in App.vue.
  const restoreDialogOpen = ref(false);
  const restoreDialogSnapshotId = ref(null);
  const restoreDialogResources = ref(null); // null → full restore

  const dailySnapshotsEnabled = ref(true);

  // ── Actions ───────────────────────────────────────────────────────────────

  async function fetchSnapshots() {
    loading.value = true;
    error.value = null;
    try {
      snapshots.value = await snapshotsApi.listSnapshots();
    } catch (err) {
      error.value =
        err?.response?.data?.detail || err?.message || "Failed to load snapshots.";
    } finally {
      loading.value = false;
    }
  }

  async function fetchStatus() {
    try {
      const status = await snapshotsApi.getSnapshotStatus();
      activeJob.value = status?.active_job ?? null;
    } catch (err) {
      // Non-fatal; leave activeJob as-is.
      console.warn("Failed to fetch snapshot status:", err);
    }
  }

  async function createSnapshot(label) {
    const cp = await snapshotsApi.createSnapshot(label);
    // Prepend to local list (newest first).
    snapshots.value = [cp, ...snapshots.value];
    return cp;
  }

  async function renameSnapshot(id, label) {
    // Optimistic update.
    const idx = snapshots.value.findIndex((c) => c.id === id);
    const prev = idx >= 0 ? snapshots.value[idx].label : undefined;
    if (idx >= 0) snapshots.value[idx] = { ...snapshots.value[idx], label };
    try {
      const updated = await snapshotsApi.renameSnapshot(id, label);
      if (idx >= 0) snapshots.value[idx] = updated;
      return updated;
    } catch (err) {
      // Roll back optimistic update.
      if (idx >= 0) snapshots.value[idx] = { ...snapshots.value[idx], label: prev };
      throw err;
    }
  }

  async function deleteSnapshot(id) {
    await snapshotsApi.deleteSnapshot(id);
    snapshots.value = snapshots.value.filter((c) => c.id !== id);
  }

  async function previewRestore(snapshotId, resources) {
    if (resources && resources.length > 0) {
      return snapshotsApi.previewRestoreBatch(snapshotId, resources);
    }
    return snapshotsApi.previewRestore(snapshotId);
  }

  async function executeRestore(
    snapshotId,
    resources,
    { confirmRestoreDependencies = false } = {},
  ) {
    if (resources && resources.length > 0) {
      return snapshotsApi.executeRestoreBatch(
        snapshotId,
        resources,
        confirmRestoreDependencies,
      );
    }
    return snapshotsApi.executeRestore(snapshotId);
  }

  /**
   * Open the shared RestoreConfirmDialog (hoisted in App.vue).
   * @param {number|null} snapshotId - null means "let user pick".
   * @param {Array|null} resources - null means full-vault restore.
   */
  function openRestoreDialog(snapshotId, resources) {
    restoreDialogSnapshotId.value = snapshotId ?? null;
    restoreDialogResources.value = resources ?? null;
    restoreDialogOpen.value = true;
  }

  // ── WebSocket event handlers (called from App.vue) ─────────────────────────

  function onSnapshotCreated() {
    // Refresh the full list so ordering / counts are correct.
    fetchSnapshots();
  }

  function onSnapshotDeleted(payload) {
    const id = payload?.id;
    if (id != null) {
      snapshots.value = snapshots.value.filter((c) => c.id !== id);
    }
  }

  function onRestoreStarted(payload) {
    activeJob.value = {
      kind: "RESTORE",
      snapshot_id: payload?.snapshot_id ?? null,
      started_at: new Date().toISOString(),
      progress: 0,
    };
  }

  function onRestoreCompleted() {
    activeJob.value = null;
    // Refresh snapshot list in case a safety OPPORTUNISTIC snapshot was
    // created during the restore.
    fetchSnapshots();
  }

  function onRestoreFailed(payload) {
    // Terminal event for a restore that started emitting STARTED but
    // hit an error. Clear activeJob so the UI buttons unlock; the
    // server's error response (404/409/412/500) already surfaced the
    // detail to the caller that triggered the restore.
    activeJob.value = null;
    if (payload?.error) {
      error.value = `Restore failed: ${payload.error}`;
    }
    // A safety snapshot may have landed before the failure; refresh.
    fetchSnapshots();
  }

  async function fetchSnapshotSettings() {
    try {
      const settings = await getSnapshotSettings();
      dailySnapshotsEnabled.value = settings?.daily_snapshots ?? true;
    } catch (err) {
      // Non-fatal; leave current value as-is.
      console.warn("Failed to fetch snapshot settings:", err);
    }
  }

  async function setDailySnapshotsEnabled(enabled) {
    const previous = dailySnapshotsEnabled.value;
    dailySnapshotsEnabled.value = enabled; // optimistic update
    try {
      await patchDailySnapshotsEnabled(enabled);
    } catch (err) {
      dailySnapshotsEnabled.value = previous; // roll back
      throw err;
    }
  }

  return {
    // state
    snapshots,
    loading,
    activeJob,
    error,
    restoreDialogOpen,
    restoreDialogSnapshotId,
    restoreDialogResources,
    dailySnapshotsEnabled,
    // actions
    fetchSnapshots,
    fetchStatus,
    createSnapshot,
    renameSnapshot,
    deleteSnapshot,
    previewRestore,
    executeRestore,
    openRestoreDialog,
    fetchSnapshotSettings,
    setDailySnapshotsEnabled,
    // ws handlers
    onSnapshotCreated,
    onSnapshotDeleted,
    onRestoreStarted,
    onRestoreCompleted,
    onRestoreFailed,
  };
});
