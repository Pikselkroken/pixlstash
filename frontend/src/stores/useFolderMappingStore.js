import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import { onSessionReset } from "../utils/apiClient";

/**
 * The one folder-structure read the owner has not yet committed or dismissed.
 *
 * v1.11 Phase 3's "Cancel and organise later": the server keeps the read's
 * result in memory for the process's lifetime (integration_architecture.md
 * §20), so all the client needs to survive a reload is which task this was
 * and where it pointed - which is what makes the mapping screen reachable
 * from the sidebar afterwards. `localStorage` rather than only this store's
 * own state, because "afterwards" includes a page reload, not just a closed
 * dialog.
 *
 * Reset on session change, the same reasoning as `useModelFoldersStore`: the
 * path is a host fact about this machine and the read is owner-only, so none
 * of it may survive into a different credential's session, and any scan
 * being waited on is abandoned with it - the server thread carries on, but
 * this session no longer has standing to poll for it.
 *
 * "Add a library" saves an entry here at "Yes, build this library", with
 * `autoCommit: true`, the accepted `assignments` and `pictureCount`, right
 * before creating the library and switching to it - the switch reloads the
 * page, and the entry is what lets `FolderMappingWizard` come back on the
 * other side straight into the commit (SideBar auto-opens it). The wizard
 * re-saves the entry without `autoCommit` the moment that commit has started,
 * so a deferred or interrupted commit resumes as a plain "Finish organising…"
 * and never commits twice. `mode` defaults to `"reference"` when absent, for
 * entries saved before this field existed.
 *
 * The wizard's open state lives here too (`wizardOpen`, `wizardResume`), so
 * the one mounted instance (in SideBar) can be opened from Settings, the empty
 * library's "Choose a folder…" and the sidebar alike.
 */
const STORAGE_KEY = "pixlstash.pendingFolderMapping";

function readStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.taskId === "string" && typeof parsed.path === "string") {
      return parsed;
    }
  } catch {
    // A corrupt or blocked localStorage read is not fatal: there is simply no
    // pending mapping to resume, same as if one had never been saved.
  }
  return null;
}

export const useFolderMappingStore = defineStore("folderMapping", () => {
  const pending = ref(readStorage());
  const wizardOpen = ref(false);
  const wizardResume = ref(null);
  /**
   * How many pictures the open library's OWN folder holds on disk, as the
   * sidebar last asked the server; `null` until it has. The empty library
   * reads it to offer "Import them…" instead of "Choose a folder…", so a
   * dismissed import offer still has a way back in. `capped` means the count
   * stopped at the inspect endpoint's cap and is a floor, not a total.
   */
  const rootPictureCount = ref(null);
  const rootPictureCountCapped = ref(false);
  // A capped zero is not an empty folder: the walk stopped before a picture.
  const rootMayHoldPictures = computed(
    () => rootPictureCount.value > 0 || rootPictureCountCapped.value,
  );

  function setRootPictureCount(count, capped = false) {
    rootPictureCount.value = count;
    rootPictureCountCapped.value = capped;
  }

  /** Open the one wizard; `resume` is a saved entry, or null for a fresh add. */
  function openWizard(resume = null) {
    wizardResume.value = resume;
    wizardOpen.value = true;
  }

  function closeWizard() {
    wizardOpen.value = false;
  }

  /** @param {{taskId: string, path: string, label?: string, mode?: "reference"|"local_import", autoCommit?: boolean, assignments?: Array, captions?: Array|null, pictureCount?: number}} entry */
  function save(entry) {
    pending.value = entry;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
    } catch {
      // Best-effort: the wizard itself still works for this session even if
      // the browser refuses storage (private mode, quota).
    }
  }

  function clear() {
    pending.value = null;
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // See save() - nothing to recover from here either.
    }
  }

  // A session change forgets the entry, the count (it is the previous
  // library's) and the open wizard: Pinia outlives a logout in the same tab,
  // and the next credential must not find the previous owner's folder on screen.
  function resetForSession() {
    clear();
    wizardOpen.value = false;
    wizardResume.value = null;
    rootPictureCount.value = null;
    rootPictureCountCapped.value = false;
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(unsubscribeSessionReset);

  return {
    pending,
    save,
    clear,
    wizardOpen,
    wizardResume,
    rootPictureCount,
    rootPictureCountCapped,
    rootMayHoldPictures,
    setRootPictureCount,
    resetForSession,
    openWizard,
    closeWizard,
  };
});
