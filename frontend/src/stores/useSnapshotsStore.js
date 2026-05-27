import { ref } from "vue";
import { defineStore } from "pinia";
import { apiClient } from "../utils/apiClient";

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
      const res = await apiClient.get("/api/v1/snapshots");
      snapshots.value = res.data;
    } catch (err) {
      error.value =
        err?.response?.data?.detail || err?.message || "Failed to load snapshots.";
    } finally {
      loading.value = false;
    }
  }

  async function fetchStatus() {
    try {
      const res = await apiClient.get("/api/v1/snapshots/status");
      activeJob.value = res.data?.active_job ?? null;
    } catch (err) {
      // Non-fatal; silently leave activeJob as-is.
    }
  }

  async function createSnapshot(label) {
    const body = label ? { label } : {};
    const res = await apiClient.post("/api/v1/snapshots", body);
    const cp = res.data;
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
      const res = await apiClient.patch(`/api/v1/snapshots/${id}`, { label });
      if (idx >= 0) snapshots.value[idx] = res.data;
      return res.data;
    } catch (err) {
      // Roll back optimistic update.
      if (idx >= 0) snapshots.value[idx] = { ...snapshots.value[idx], label: prev };
      throw err;
    }
  }

  async function deleteSnapshot(id) {
    await apiClient.delete(`/api/v1/snapshots/${id}`);
    snapshots.value = snapshots.value.filter((c) => c.id !== id);
  }

  async function previewRestore(snapshotId, resources) {
    if (resources && resources.length > 0) {
      const res = await apiClient.post(
        `/api/v1/snapshots/${snapshotId}/restore/preview/batch`,
        { resources }
      );
      return res.data;
    }
    const res = await apiClient.get(
      `/api/v1/snapshots/${snapshotId}/restore/preview`
    );
    return res.data;
  }

  async function previewResourceRestore(snapshotId, resourceType, resourceId) {
    const res = await apiClient.get(
      `/api/v1/snapshots/${snapshotId}/restore/${resourceType}/${resourceId}/preview`
    );
    return res.data;
  }

  async function executeRestore(snapshotId, resources) {
    if (resources && resources.length > 0) {
      const res = await apiClient.post(
        `/api/v1/snapshots/${snapshotId}/restore/batch`,
        { resources }
      );
      return res.data;
    }
    const res = await apiClient.post(
      `/api/v1/snapshots/${snapshotId}/restore`,
      {}
    );
    return res.data;
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

  function onSnapshotCreated(payload) {
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

  function onRestoreCompleted(payload) {
    activeJob.value = null;
    // Refresh snapshot list in case a safety OPPORTUNISTIC snapshot was
    // created during the restore.
    fetchSnapshots();
  }

  async function fetchSnapshotSettings() {
    try {
      const res = await apiClient.get("/api/v1/server-config/snapshots");
      dailySnapshotsEnabled.value = res.data?.daily_snapshots ?? true;
    } catch (err) {
      // Non-fatal; leave current value as-is.
    }
  }

  async function setDailySnapshotsEnabled(enabled) {
    const previous = dailySnapshotsEnabled.value;
    dailySnapshotsEnabled.value = enabled; // optimistic update
    try {
      await apiClient.patch("/api/v1/server-config/snapshots", {
        daily_snapshots: enabled,
      });
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
    previewResourceRestore,
    executeRestore,
    openRestoreDialog,
    fetchSnapshotSettings,
    setDailySnapshotsEnabled,
    // ws handlers
    onSnapshotCreated,
    onSnapshotDeleted,
    onRestoreStarted,
    onRestoreCompleted,
  };
});
