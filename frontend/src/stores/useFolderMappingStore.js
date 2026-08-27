import { onScopeDispose, ref } from "vue";
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
 * `taskId` can be the empty string: "Add a library" saves an entry here
 * *before* any read has started, for a "pictures" verdict's switch-then-reload
 * - `mode: "local_import"` and an empty `taskId` mean "start scanning this
 * known path fresh", which `FolderMappingWizard`'s `resume` handling already
 * treats correctly (an empty `resumeTaskId` is falsy, so the scan step starts
 * a new read rather than reattaching to one). `mode` defaults to `"reference"`
 * when absent, for entries saved before this field existed.
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

  /** @param {{taskId: string, path: string, label?: string, mode?: "reference"|"local_import"}} entry */
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

  const unsubscribeSessionReset = onSessionReset(clear);
  onScopeDispose(unsubscribeSessionReset);

  return { pending, save, clear };
});
