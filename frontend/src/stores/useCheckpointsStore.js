import { ref } from "vue";
import { defineStore } from "pinia";
import { apiClient } from "../utils/apiClient";

export const useCheckpointsStore = defineStore("checkpoints", () => {
  // ── State ─────────────────────────────────────────────────────────────────
  const checkpoints = ref([]);
  const loading = ref(false);
  const activeJob = ref(null);
  const error = ref(null);

  // Drives the shared RestoreConfirmDialog instance hoisted in App.vue.
  const restoreDialogOpen = ref(false);
  const restoreDialogCheckpointId = ref(null);
  const restoreDialogResources = ref(null); // null → full restore

  // ── Actions ───────────────────────────────────────────────────────────────

  async function fetchCheckpoints() {
    loading.value = true;
    error.value = null;
    try {
      const res = await apiClient.get("/api/v1/checkpoints");
      checkpoints.value = res.data;
    } catch (err) {
      error.value =
        err?.response?.data?.detail || err?.message || "Failed to load checkpoints.";
    } finally {
      loading.value = false;
    }
  }

  async function fetchStatus() {
    try {
      const res = await apiClient.get("/api/v1/checkpoints/status");
      activeJob.value = res.data?.active_job ?? null;
    } catch (err) {
      // Non-fatal; silently leave activeJob as-is.
    }
  }

  async function createCheckpoint(label) {
    const body = label ? { label } : {};
    const res = await apiClient.post("/api/v1/checkpoints", body);
    const cp = res.data;
    // Prepend to local list (newest first).
    checkpoints.value = [cp, ...checkpoints.value];
    return cp;
  }

  async function renameCheckpoint(id, label) {
    // Optimistic update.
    const idx = checkpoints.value.findIndex((c) => c.id === id);
    const prev = idx >= 0 ? checkpoints.value[idx].label : undefined;
    if (idx >= 0) checkpoints.value[idx] = { ...checkpoints.value[idx], label };
    try {
      const res = await apiClient.patch(`/api/v1/checkpoints/${id}`, { label });
      if (idx >= 0) checkpoints.value[idx] = res.data;
      return res.data;
    } catch (err) {
      // Roll back optimistic update.
      if (idx >= 0) checkpoints.value[idx] = { ...checkpoints.value[idx], label: prev };
      throw err;
    }
  }

  async function deleteCheckpoint(id) {
    await apiClient.delete(`/api/v1/checkpoints/${id}`);
    checkpoints.value = checkpoints.value.filter((c) => c.id !== id);
  }

  async function previewRestore(checkpointId, resources) {
    if (resources && resources.length > 0) {
      const res = await apiClient.post(
        `/api/v1/checkpoints/${checkpointId}/restore/preview/batch`,
        { resources }
      );
      return res.data;
    }
    const res = await apiClient.get(
      `/api/v1/checkpoints/${checkpointId}/restore/preview`
    );
    return res.data;
  }

  async function previewResourceRestore(checkpointId, resourceType, resourceId) {
    const res = await apiClient.get(
      `/api/v1/checkpoints/${checkpointId}/restore/${resourceType}/${resourceId}/preview`
    );
    return res.data;
  }

  async function executeRestore(checkpointId, resources) {
    if (resources && resources.length > 0) {
      const res = await apiClient.post(
        `/api/v1/checkpoints/${checkpointId}/restore/batch`,
        { resources }
      );
      return res.data;
    }
    const res = await apiClient.post(
      `/api/v1/checkpoints/${checkpointId}/restore`,
      {}
    );
    return res.data;
  }

  /**
   * Open the shared RestoreConfirmDialog (hoisted in App.vue).
   * @param {number|null} checkpointId - null means "let user pick".
   * @param {Array|null} resources - null means full-vault restore.
   */
  function openRestoreDialog(checkpointId, resources) {
    restoreDialogCheckpointId.value = checkpointId ?? null;
    restoreDialogResources.value = resources ?? null;
    restoreDialogOpen.value = true;
  }

  // ── WebSocket event handlers (called from App.vue) ─────────────────────────

  function onCheckpointCreated(payload) {
    // Refresh the full list so ordering / counts are correct.
    fetchCheckpoints();
  }

  function onCheckpointDeleted(payload) {
    const id = payload?.id;
    if (id != null) {
      checkpoints.value = checkpoints.value.filter((c) => c.id !== id);
    }
  }

  function onRestoreStarted(payload) {
    activeJob.value = {
      kind: "RESTORE",
      checkpoint_id: payload?.checkpoint_id ?? null,
      started_at: new Date().toISOString(),
      progress: 0,
    };
  }

  function onRestoreCompleted(payload) {
    activeJob.value = null;
    // Refresh checkpoint list in case a safety OPPORTUNISTIC checkpoint was
    // created during the restore.
    fetchCheckpoints();
  }

  return {
    // state
    checkpoints,
    loading,
    activeJob,
    error,
    restoreDialogOpen,
    restoreDialogCheckpointId,
    restoreDialogResources,
    // actions
    fetchCheckpoints,
    fetchStatus,
    createCheckpoint,
    renameCheckpoint,
    deleteCheckpoint,
    previewRestore,
    previewResourceRestore,
    executeRestore,
    openRestoreDialog,
    // ws handlers
    onCheckpointCreated,
    onCheckpointDeleted,
    onRestoreStarted,
    onRestoreCompleted,
  };
});
