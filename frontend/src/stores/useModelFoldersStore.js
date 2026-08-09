import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  createModelFolder,
  forgetModelFolder,
  listModelFolders,
  MANAGED_KIND,
  rescanModelFolder,
} from "../api/modelFolders";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import { useModelShelfStore } from "./useModelShelfStore";
import { useNoticeStore } from "./useNoticeStore";

/** How often the list is re-read while a scan is believed to be running. */
const POLL_MS = 3000;

/**
 * How long a scan may be waited on before the poll gives up.
 *
 * The scanner logs and returns without touching `last_checked` when it throws,
 * so a crashed scan is indistinguishable from a slow one on the wire. Without a
 * ceiling the poll would run for the life of the tab.
 */
const SCAN_WAIT_CEILING_MS = 10 * 60 * 1000;

/**
 * The registered model folders, and the scans running against them.
 *
 * This is a STORE rather than dialog state because a scan outlives the dialog:
 * the owner starts one on a 57 GB folder and closes the panel, and the shelf
 * still has to refresh and the notice still has to fire when it lands. The
 * dialog is a view onto this.
 *
 * The list is owner-only host data (absolute paths on this machine), so a
 * session reset drops it whole. Nothing here is persisted.
 */
export const useModelFoldersStore = defineStore("modelFolders", () => {
  const folders = ref([]);
  const loading = ref(false);
  const error = ref("");
  /** True once a read has completed, so "none registered" and "not asked" differ. */
  const loaded = ref(false);

  /** folder id → `{ startedAt, lastChecked }` for the scans being waited on. */
  const scanning = ref(new Map());

  let pollHandle = null;
  // Discards a read that was already on the wire when the credential changed,
  // the same guard `useLibrariesStore.refresh` takes.
  let epoch = 0;

  const scanningIds = computed(() => new Set(scanning.value.keys()));

  /** The one folder that is PixlStash's own storage. Always exactly one. */
  const managedFolder = computed(() =>
    folders.value.find((folder) => folder.kind === MANAGED_KIND),
  );

  /** Paths already registered, for the picker to disable rather than 409 on. */
  const registeredPaths = computed(() =>
    folders.value.map((folder) => folder.path).filter(Boolean),
  );

  /**
   * Re-read the registry.
   *
   * @param {Object} [options]
   * @param {boolean} [options.quiet=false] - true for a poll, which must not
   *   flash the list's loading state on every tick.
   */
  async function refresh({ quiet = false } = {}) {
    const startedAt = epoch;
    if (!quiet) loading.value = true;
    error.value = "";
    try {
      const next = await listModelFolders();
      if (startedAt !== epoch) return;
      folders.value = next;
      loaded.value = true;
      settleFinishedScans();
    } catch (err) {
      if (startedAt !== epoch) return;
      error.value =
        errorDetail(err) || "Could not read the registered model folders.";
    } finally {
      if (startedAt === epoch && !quiet) loading.value = false;
    }
  }

  /**
   * Retire the scans whose folder has been re-stamped, or which have waited out
   * the ceiling.
   *
   * `last_checked` advancing is the only completion signal the API offers, so a
   * scan is "finished" when the stamp differs from the one taken when it
   * started. A folder that vanished from the list (forgotten mid-scan) settles
   * too, or its entry would keep the poll alive forever.
   */
  function settleFinishedScans() {
    if (!scanning.value.size) return;
    const notices = useNoticeStore();
    const byId = new Map(folders.value.map((folder) => [folder.id, folder]));
    let changed = false;
    let landed = false;

    for (const [id, watch] of [...scanning.value]) {
      const folder = byId.get(id);
      const timedOut = Date.now() - watch.startedAt > SCAN_WAIT_CEILING_MS;
      const finished =
        folder && (folder.last_checked || null) !== watch.lastChecked;

      if (!folder || finished) {
        scanning.value.delete(id);
        changed = true;
        if (finished) {
          landed = true;
          notices.push({
            level: "success",
            text: `Scanned ${basename(folder.path)}. ${countLabel(folder.file_count)} listed.`,
          });
        }
      } else if (timedOut) {
        scanning.value.delete(id);
        changed = true;
        notices.push({
          level: "warning",
          text: `Still scanning ${basename(folder.path)}. It will appear on the shelf when it finishes.`,
        });
      }
    }

    if (changed) scanning.value = new Map(scanning.value);
    // Only a scan that actually landed changed what the shelf holds.
    if (landed) useModelShelfStore().fetchRows();
    if (!scanning.value.size) stopPolling();
  }

  function startPolling() {
    if (pollHandle !== null) return;
    pollHandle = setInterval(() => refresh({ quiet: true }), POLL_MS);
  }

  function stopPolling() {
    if (pollHandle === null) return;
    clearInterval(pollHandle);
    pollHandle = null;
  }

  /**
   * Register a folder and immediately scan it.
   *
   * Registering alone puts an empty row on screen, which reads as a failure;
   * the scan is what the owner actually asked for.
   *
   * @param {Object} options - forwarded to the API's `createModelFolder`.
   * @returns {Promise<boolean>} true when the folder was registered.
   */
  async function add(options) {
    const notices = useNoticeStore();
    try {
      const created = await createModelFolder(options);
      await refresh();
      notices.push({
        level: "success",
        text: `Added ${basename(created?.path || options.path)}. Looking for models in it now.`,
      });
      if (created?.id != null) await scan(created.id, { silent: true });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not add that folder.",
      });
      return false;
    }
  }

  /**
   * Forget a folder, offering the way back.
   *
   * No confirmation prompt: nothing on disk is touched and the models keep the
   * names, triggers and attachments the owner gave them, so this is cheap to
   * reverse. It is only cheap because the undo exists, which is why the row's
   * fields are captured BEFORE the request rather than read back after it.
   *
   * @param {Object} folder - the row as listed.
   * @returns {Promise<boolean>} true when the folder was forgotten.
   */
  async function forget(folder) {
    const notices = useNoticeStore();
    const restore = {
      path: folder.path,
      kind: folder.kind,
      hostPath: folder.host_path || undefined,
      deleteAfterImport: folder.delete_after_import ?? undefined,
    };
    try {
      const body = await forgetModelFolder(folder.id);
      await refresh();
      const gone = Number(body?.tombstoned_files || 0);
      notices.push({
        level: "success",
        text: gone
          ? `Forgot ${basename(folder.path)}. ${countLabel(gone)} left the shelf; their names and trigger words are kept.`
          : `Forgot ${basename(folder.path)}. Nothing was listed from it.`,
        action: { label: "Add it back", handler: () => add(restore) },
      });
      useModelShelfStore().fetchRows();
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not forget that folder.",
      });
      return false;
    }
  }

  /**
   * Start a scan and watch for it to land.
   *
   * @param {number} id
   * @param {Object} [options]
   * @param {boolean} [options.silent=false] - true when the caller has already
   *   said what is happening (the add flow), so this does not say it twice.
   */
  async function scan(id, { silent = false } = {}) {
    const notices = useNoticeStore();
    const folder = folders.value.find((row) => row.id === id);
    try {
      const body = await rescanModelFolder(id);
      if (body?.status === "already_running" && !silent) {
        notices.push({
          level: "info",
          text: `${basename(folder?.path)} is already being scanned.`,
        });
      }
      // `skipped` means a source folder, which carries no scan control at all,
      // so there is nothing to watch and nothing to say.
      if (body?.status === "skipped") return;
      scanning.value = new Map(scanning.value).set(id, {
        startedAt: Date.now(),
        lastChecked: folder?.last_checked || null,
      });
      startPolling();
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not start that scan.",
      });
    }
  }

  /**
   * Drop everything the previous credential could see.
   *
   * The paths are host facts about this machine and the registry is owner-only,
   * so none of it may survive into a share or read-only session that is never
   * allowed to ask for it. Any scan being waited on is abandoned with it: the
   * server thread carries on, but this session no longer has standing to poll.
   */
  function resetForSession() {
    epoch += 1;
    stopPolling();
    folders.value = [];
    scanning.value = new Map();
    loading.value = false;
    loaded.value = false;
    error.value = "";
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(() => {
    unsubscribeSessionReset();
    stopPolling();
  });

  return {
    folders,
    loading,
    loaded,
    error,
    scanningIds,
    managedFolder,
    registeredPaths,
    refresh,
    add,
    forget,
    scan,
    resetForSession,
  };
});

/** The last segment of a path, for a sentence that must not run to 60 chars. */
export function basename(path) {
  const trimmed = String(path || "").replace(/[\\/]+$/, "");
  const parts = trimmed.split(/[\\/]/);
  return parts[parts.length - 1] || trimmed || "the folder";
}

/** "1 file" / "12 files", so a sentence never reads "1 files". */
export function countLabel(n) {
  const count = Number(n) || 0;
  return `${count.toLocaleString()} ${count === 1 ? "file" : "files"}`;
}
