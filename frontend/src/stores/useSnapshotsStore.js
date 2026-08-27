// useSnapshotsStore.js - the vault's snapshot list and the live restore job.
//
// ── Security (issue #655 item 3) ────────────────────────────────────────────
// Snapshots are an owner-only surface, so the realistic exposure is "the
// previous owner's snapshot labels linger in memory until the next login"
// rather than cross-scope leakage. It is still server-sourced state that
// outlived the credential that produced it, which is the whole class, so
// `reset()` is wired to the `onSessionReset` chokepoint in `utils/apiClient.js`
// like every other store holding server rows.

import { ref, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import * as snapshotsApi from "../api/snapshots";
import {
  getSnapshotSettings,
  setDailySnapshotsEnabled as patchDailySnapshotsEnabled,
} from "../api/serverConfig";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";

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

  // Bumped by reset(); a read still on the wire when the credential changed is
  // dropped rather than written into the next session's list.
  let epoch = 0;

  // ── Actions ───────────────────────────────────────────────────────────────

  async function fetchSnapshots() {
    loading.value = true;
    error.value = null;
    const requestEpoch = epoch;
    try {
      const rows = await snapshotsApi.listSnapshots();
      if (requestEpoch !== epoch) return;
      snapshots.value = rows;
    } catch (err) {
      if (requestEpoch !== epoch) return;
      error.value =
        errorDetail(err) || err?.message || "Failed to load snapshots.";
    } finally {
      if (requestEpoch === epoch) loading.value = false;
    }
  }

  async function fetchStatus() {
    const requestEpoch = epoch;
    try {
      const status = await snapshotsApi.getSnapshotStatus();
      if (requestEpoch !== epoch) return;
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
    const requestEpoch = epoch;
    try {
      const settings = await getSnapshotSettings();
      if (requestEpoch !== epoch) return;
      dailySnapshotsEnabled.value = settings?.daily_snapshots ?? true;
    } catch (err) {
      // Non-fatal; leave current value as-is.
      console.warn("Failed to fetch snapshot settings:", err);
    }
  }

  /**
   * Drop every trace of the previous session's snapshots.
   *
   * `dailySnapshotsEnabled` returns to its shipped default rather than being
   * left at the previous owner's choice: the toggle would otherwise assert a
   * policy for an account that has not been read yet.
   */
  function reset() {
    epoch += 1;
    snapshots.value = [];
    loading.value = false;
    activeJob.value = null;
    error.value = null;
    restoreDialogOpen.value = false;
    restoreDialogSnapshotId.value = null;
    restoreDialogResources.value = null;
    dailySnapshotsEnabled.value = true;
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

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
    reset,
    // ws handlers
    onSnapshotCreated,
    onSnapshotDeleted,
    onRestoreStarted,
    onRestoreCompleted,
    onRestoreFailed,
  };
});
