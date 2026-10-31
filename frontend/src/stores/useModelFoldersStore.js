import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import {
  createModelFolder,
  createModelFolderDir,
  DOWNLOADS_KIND,
  forgetModelFolder,
  getComfyuiReads,
  listModelFolderDevices,
  listModelFolderDirs,
  listModelFolders,
  MANAGED_KIND,
  setDefaultModelFolder,
  setDownloadModelFolder,
  SOURCE_KIND,
} from "../api/modelFolders";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import { useModelShelfStore } from "./useModelShelfStore";
import { useNoticeStore } from "./useNoticeStore";

/** How often the list is re-read while a folder is being scanned. */
const POLL_MS = 3000;

/** The `scan_status` values that mean a scan is queued or walking. */
const SCAN_IN_FLIGHT = new Set(["pending", "running"]);

/** Where ComfyUI loads from, before it is read or when it cannot be. */
const NO_COMFYUI_READS = Object.freeze({ known: false, reads: [] });

/**
 * The registered model folders, and the scans running against them.
 *
 * Nobody starts a scan: the server scans a folder when it is added, whenever a
 * model file in it changes, and every ten minutes besides, and announces what
 * arrived as `models_arrived`, which the shelf listens for. This store only
 * shows it: a folder is scanning while its `scan_status` says so, and the list
 * is re-read until none is. It is a STORE rather than dialog state because the
 * notice for a folder just added must still fire when its scan lands after
 * the dialog has closed.
 *
 * The list is owner-only host data (absolute paths on this machine), so a
 * session reset drops it whole. Nothing here is persisted.
 */
export const useModelFoldersStore = defineStore("modelFolders", () => {
  const folders = ref([]);
  /** One entry per DRIVE the folders sit on, for the shelf's bands. */
  const devices = ref([]);
  const loading = ref(false);
  const error = ref("");
  /** True once a read has completed, so "none registered" and "not asked" differ. */
  const loaded = ref(false);

  /** Folders added this session whose first scan has not been announced yet. */
  const announcing = new Set();

  let pollHandle = null;
  // Discards a read that was already on the wire when the credential changed,
  // the same guard `useLibrariesStore.refresh` takes.
  let epoch = 0;

  const scanningIds = computed(
    () =>
      new Set(
        folders.value
          .filter((folder) => SCAN_IN_FLIGHT.has(folder.scan_status))
          .map((folder) => folder.id),
      ),
  );

  /**
   * folder id → the drive it sits on.
   *
   * The shelf bands by this rather than by path prefix: a bind mount and a
   * symlinked folder look like different drives by path and are one, and two
   * folders under one root can be different drives when a mount sits between
   * them. The server measured it; the client must not re-derive it.
   */
  const deviceByFolderId = computed(() => {
    const byId = new Map();
    for (const device of devices.value) {
      for (const folderId of device.folder_ids || []) {
        byId.set(Number(folderId), device);
      }
    }
    return byId;
  });

  /** folder id → its row name, told apart from its namesakes. */
  const labelByFolderId = computed(() => folderLabels(folders.value));

  /** The one folder that is PixlStash's own storage. Always exactly one. */
  const managedFolder = computed(() =>
    folders.value.find((folder) => folder.kind === MANAGED_KIND),
  );

  /**
   * The registered ai-toolkit output root, or `undefined` while none is set.
   *
   * Singular on purpose. The server permits several `source` folders, but
   * ai-toolkit writes every run under one output root, so a second would be a
   * setting nobody asked for: the offer to set one disappears once this exists,
   * and the training-runs view reads exactly this folder. `find`, so a registry
   * that somehow holds two still resolves rather than reporting an error the
   * owner has no control to fix.
   */
  const sourceFolder = computed(() =>
    folders.value.find((folder) => folder.kind === SOURCE_KIND),
  );

  /** The watched Downloads folder, or `undefined` while none is set. */
  const downloadsFolder = computed(() =>
    folders.value.find((folder) => folder.kind === DOWNLOADS_KIND),
  );

  /** Where a new model goes when nothing else is named. */
  const defaultFolder = computed(() =>
    folders.value.find((folder) => folder.is_default),
  );

  /** Where a model PixlStash downloads goes; the default folder until chosen. */
  const downloadFolder = computed(() =>
    folders.value.find((folder) => folder.is_download_target),
  );

  /**
   * The folders that can be the default: the ones the shelf catalogues and
   * writes into. Never a taken-from folder, never PixlStash's own `foreign`.
   */
  const defaultCandidates = computed(() =>
    folders.value.filter(
      (folder) => folder.kind === "user" || folder.kind === MANAGED_KIND,
    ),
  );

  /** Paths already registered, for the picker to disable rather than 409 on. */
  const registeredPaths = computed(() =>
    folders.value.map((folder) => folder.path).filter(Boolean),
  );

  /**
   * One level of subfolders per `"<folder id>:<path>"`: `{loading, listing,
   * error}`. Shared by the Folders tree and the folder view, so the folder
   * that is open is read once. Replaced wholesale so templates react.
   */
  const dirListings = ref(new Map());
  const dirsInFlight = new Map();
  /** Per key, the newest request: only it may write its answer. */
  const dirsLatest = new Map();

  /**
   * The sidebar tree's open rows, kept here so they outlive the sidebar: a
   * trip to another view and back finds the tree as it was left. Expanded
   * folder keys, and the drives closed (drives start open).
   */
  const treeExpanded = ref(new Set());
  const treeClosedDrives = ref(new Set());

  /** The cache key for one listing. */
  function dirsKey(id, path = "") {
    return `${Number(id)}:${path}`;
  }

  /**
   * Read one level of a registered folder, once unless `force`.
   *
   * @param {number} id - the registered folder.
   * @param {string} [path=""] - POSIX, relative to it.
   * @param {{force?: boolean}} [options]
   * @returns {Promise<Object|null>} the listing, or null when it could not be
   *   read (a remote owner is refused the route; the tree then offers no
   *   subfolders rather than an error per row).
   */
  async function loadDirs(id, path = "", { force = false } = {}) {
    const key = dirsKey(id, path);
    const known = dirListings.value.get(key);
    if (!force && known?.listing) return known.listing;
    // A forced read supersedes one in flight: that one may have left before
    // the scan or move that asked for this.
    if (!force && dirsInFlight.has(key)) return dirsInFlight.get(key);
    const startedAt = epoch;
    const ticket = {};
    dirsLatest.set(key, ticket);
    const put = (entry) => {
      if (startedAt !== epoch || dirsLatest.get(key) !== ticket) return;
      dirListings.value = new Map(dirListings.value).set(key, entry);
    };
    put({ loading: true, listing: known?.listing ?? null, error: "" });
    const request = listModelFolderDirs(id, path)
      .then((listing) => {
        put({ loading: false, listing, error: "" });
        return listing;
      })
      .catch((err) => {
        console.warn(`Could not list model folder ${id} at "${path}"`, err);
        put({
          loading: false,
          listing: null,
          error: errorDetail(err) || "Could not read this folder.",
        });
        return null;
      })
      .finally(() => {
        if (dirsInFlight.get(key) === request) dirsInFlight.delete(key);
      });
    dirsInFlight.set(key, request);
    return request;
  }

  /**
   * Where ComfyUI loads each kind from (#2205): `{known, reads}`. Unknown
   * until read, and unknown when the read fails (a remote owner is refused
   * it), so no folder is marked and no model called misplaced on a guess.
   */
  const comfyuiReads = ref(NO_COMFYUI_READS);

  /** Ask once per shelf visit; ComfyUI's folders rarely move under it. */
  async function loadComfyuiReads() {
    const startedAt = epoch;
    let answer = NO_COMFYUI_READS;
    try {
      const body = await getComfyuiReads();
      if (body?.known && Array.isArray(body.reads)) answer = body;
    } catch (err) {
      console.warn("Could not read where ComfyUI loads models from", err);
    }
    if (startedAt === epoch) comfyuiReads.value = answer;
  }

  /** Forget every listing, so the next look reads the disk again. */
  function clearDirs() {
    dirListings.value = new Map();
  }

  /**
   * Re-read every listing already shown, in place: a scan, a move or an added
   * file changes counts and folders under rows that are open. Clearing instead
   * would fold every open row shut.
   */
  function reloadDirs() {
    for (const key of dirListings.value.keys()) {
      const at = key.indexOf(":");
      loadDirs(Number(key.slice(0, at)), key.slice(at + 1), { force: true });
    }
  }

  /**
   * Re-read the registry.
   *
   * @param {Object} [options]
   * @param {boolean} [options.quiet=false] - true for a poll, which must not
   *   flash the list's loading state on every tick.
   */
  /**
   * Re-read the drive capacities.
   *
   * Its own call, and its own failure. The route stats the filesystem, so an
   * offline mount can make it slow or make it fail, and neither may take the
   * folder list down with it: without capacities the shelf still draws its
   * bands, it just cannot draw a meter in them.
   */
  async function refreshDevices() {
    const startedAt = epoch;
    try {
      const next = await listModelFolderDevices();
      if (startedAt !== epoch) return;
      devices.value = next;
    } catch (err) {
      if (startedAt !== epoch) return;
      // Not surfaced as `error`: that field drives the folder dialog's alert,
      // and a drive we could not measure is not a reason to tell the owner
      // their folders could not be read.
      devices.value = [];
      console.warn("Could not read the model drives' capacity", err);
    }
  }

  async function refresh({ quiet = false } = {}) {
    const startedAt = epoch;
    if (!quiet) loading.value = true;
    error.value = "";
    try {
      const next = await listModelFolders();
      if (startedAt !== epoch) return;
      folders.value = next;
      loaded.value = true;
      announceFinishedScans();
      if (scanningIds.value.size) startPolling();
      else stopPolling();
    } catch (err) {
      if (startedAt !== epoch) return;
      error.value =
        errorDetail(err) || "Could not read the registered model folders.";
    } finally {
      if (startedAt === epoch && !quiet) loading.value = false;
    }
  }

  /**
   * Say how the first scan of a folder added here went, once it has finished.
   *
   * Only those: every other scan is the server keeping up by itself, and a
   * notice each time would be noise. What it found reaches the shelf as
   * `models_arrived`. A folder that vanished from the list (forgotten
   * mid-scan) is dropped silently.
   */
  function announceFinishedScans() {
    if (!announcing.size) return;
    const notices = useNoticeStore();
    const byId = new Map(folders.value.map((folder) => [folder.id, folder]));
    for (const id of [...announcing]) {
      const folder = byId.get(id);
      if (folder && SCAN_IN_FLIGHT.has(folder.scan_status)) continue;
      announcing.delete(id);
      if (folder?.scan_status === "completed") {
        notices.push({
          level: "success",
          text: `Scanned ${basename(folder.path)}. ${countLabel(folder.file_count)} listed.`,
        });
      } else if (folder?.scan_status === "failed") {
        notices.push({
          level: "error",
          text: `Could not scan ${basename(folder.path)}${folder.scan_error ? `: ${folder.scan_error}` : "."}`,
        });
      }
    }
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
   * Register a folder. The server queues its first scan itself, so the row
   * comes back already `pending` rather than reading as an empty failure.
   *
   * A `source` or Downloads folder is never scanned (it is taken from, not
   * catalogued), so its notice promises no scan and none is waited for.
   *
   * @param {Object} options - forwarded to the API's `createModelFolder`.
   * @returns {Promise<boolean>} true when the folder was registered.
   */
  async function add(options) {
    const notices = useNoticeStore();
    const takenFrom =
      options?.kind === SOURCE_KIND || options?.kind === DOWNLOADS_KIND;
    try {
      const created = await createModelFolder(options);
      await refresh();
      const name = basename(created?.path || options.path);
      const text =
        options?.kind === DOWNLOADS_KIND
          ? `Watching ${name}. New models saved there move to your default folder.`
          : takenFrom
            ? `Added ${name}. Its training runs are ready to import.`
            : `Added ${name}. Looking for models in it now.`;
      notices.push({ level: "success", text });
      if (!takenFrom && created?.id != null) {
        announcing.add(created.id);
        announceFinishedScans();
      }
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
   * Make a folder the default destination for a new model.
   *
   * Choosing the managed store clears the owner's mark rather than setting
   * one: with nothing marked the managed store is the default, so the two say
   * the same thing and only one of them is stored.
   *
   * @param {Object} folder - a row from `defaultCandidates`.
   * @returns {Promise<boolean>} true when the default changed.
   */
  async function setDefault(folder) {
    const notices = useNoticeStore();
    try {
      await setDefaultModelFolder(folder.id, true);
      await refresh({ quiet: true });
      notices.push({
        level: "success",
        text: `New models now go to ${basename(folder.path)}.`,
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not change the default folder.",
      });
      return false;
    }
  }

  /**
   * Send the models PixlStash downloads to *folder*. Unlike the default the
   * mark is always stored, so a later change of default does not move it.
   *
   * @param {Object} folder - a row from `defaultCandidates`.
   * @returns {Promise<boolean>} true when it changed.
   */
  async function setDownloadTarget(folder) {
    const notices = useNoticeStore();
    try {
      await setDownloadModelFolder(folder.id, true);
      await refresh({ quiet: true });
      notices.push({
        level: "success",
        text: `Downloaded models now go to ${basename(folder.path)}.`,
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not change the download folder.",
      });
      return false;
    }
  }

  /**
   * Make one directory inside a registered folder (#2206).
   *
   * Refusals throw, with the server's `detail` in the response, so the field
   * that asked can show them under itself and stay open. Only the parent's
   * listing is re-read here; the caller reveals the new row in the tree it
   * owns, since the tree's open state is its own.
   *
   * @param {number} folderId
   * @param {string} parentPath - POSIX, `""` for the folder's root.
   * @param {string} name - one segment.
   * @returns {Promise<{folder_id: number, path: string}>} the new directory.
   */
  async function createDir(folderId, parentPath, name) {
    const created = await createModelFolderDir(folderId, {
      path: parentPath,
      name,
    });
    await loadDirs(folderId, parentPath, { force: true });
    useNoticeStore().push({ level: "success", text: `Created ${name}.` });
    return created;
  }

  /**
   * Drop everything the previous credential could see.
   *
   * The paths are host facts about this machine and the registry is owner-only,
   * so none of it may survive into a share or read-only session that is never
   * allowed to ask for it. Any scan being waited on is abandoned with it: the
   * server carries on, but this session no longer has standing to poll.
   */
  function resetForSession() {
    epoch += 1;
    stopPolling();
    folders.value = [];
    devices.value = [];
    announcing.clear();
    dirListings.value = new Map();
    treeExpanded.value = new Set();
    treeClosedDrives.value = new Set();
    comfyuiReads.value = NO_COMFYUI_READS;
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
    devices,
    deviceByFolderId,
    labelByFolderId,
    loading,
    loaded,
    error,
    scanningIds,
    managedFolder,
    sourceFolder,
    downloadsFolder,
    defaultFolder,
    downloadFolder,
    defaultCandidates,
    registeredPaths,
    dirListings,
    treeExpanded,
    treeClosedDrives,
    dirsKey,
    comfyuiReads,
    loadComfyuiReads,
    loadDirs,
    clearDirs,
    reloadDirs,
    refresh,
    refreshDevices,
    add,
    forget,
    setDefault,
    setDownloadTarget,
    createDir,
    resetForSession,
  };
});

/** The last segment of a path, for a sentence that must not run to 60 chars. */
export function basename(path) {
  const trimmed = String(path || "").replace(/[\\/]+$/, "");
  const parts = trimmed.split(/[\\/]/);
  return parts[parts.length - 1] || trimmed || "the folder";
}

/**
 * folder id → the name a folder row goes by.
 *
 * A folder is named for its tool, else its last segment. Folders that would
 * share a name (every StabilityMatrix folder reads "StabilityMatrix") are told
 * apart by the shortest tail of their paths no other in the group shares, with
 * the tool after it: "Lora (StabilityMatrix)", "Models/Lora", "Other/Lora".
 *
 * @param {Array<Object>} folders - the registry rows.
 * @returns {Map<number, string>}
 */
export function folderLabels(folders) {
  const nameOf = (folder) => folder.tool || basename(folder.path);
  const groups = new Map();
  for (const folder of folders) {
    const name = nameOf(folder);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(folder);
  }
  const labels = new Map();
  for (const [name, group] of groups) {
    if (group.length === 1) {
      labels.set(Number(group[0].id), name);
      continue;
    }
    const segments = group.map((folder) =>
      String(folder.path || "")
        .split(/[\\/]/)
        .filter(Boolean),
    );
    group.forEach((folder, i) => {
      const own = segments[i];
      let tail = own.join("/") || name;
      for (let n = 1; n <= own.length; n += 1) {
        const candidate = own.slice(-n).join("/");
        const shared = segments.some(
          (other, j) => j !== i && other.slice(-n).join("/") === candidate,
        );
        if (!shared) {
          tail = candidate;
          break;
        }
      }
      labels.set(Number(folder.id), folder.tool ? `${tail} (${folder.tool})` : tail);
    });
  }
  return labels;
}

/** "1 file" / "12 files", so a sentence never reads "1 files". */
export function countLabel(n) {
  const count = Number(n) || 0;
  return `${count.toLocaleString()} ${count === 1 ? "file" : "files"}`;
}
