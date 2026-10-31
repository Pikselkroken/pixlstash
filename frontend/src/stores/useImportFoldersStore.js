// useImportFoldersStore.js - import folders and the Inbox they feed.
//
// An import folder is configured once, in Settings › Libraries & imports, and what
// it brings in waits in the Inbox (sidebar › Review) until the owner has looked
// at it. The sidebar row, the Inbox header, the Settings table, the Filter
// menu's "Imported from" and the Import panel all read this one store, and it
// re-reads on the WebSocket's picture events (an arrival, a rating, Mark seen
// from another tab) rather than polling.

import { defineStore } from "pinia";
import { computed, onScopeDispose, ref } from "vue";
import { getInbox, listImportFolders, markInboxSeen } from "../api/folders";
import { isReadOnly, onSessionReset } from "../utils/apiClient";

const REFRESH_DEBOUNCE_MS = 1500;

export const useImportFoldersStore = defineStore("importFolders", () => {
  const folders = ref([]);
  const inbox = ref({ total: 0, folder_count: 0, batches: [] });
  // Set once the folder list has been read, so the sidebar row does not flash.
  const loaded = ref(false);
  let epoch = 0;
  let refreshTimer = null;

  const hasFolders = computed(() => folders.value.length > 0);
  const unseenCount = computed(() => inbox.value?.total ?? 0);

  async function refresh() {
    if (isReadOnly.value) return;
    const requestEpoch = epoch;
    try {
      const [folderBody, inboxBody] = await Promise.all([
        listImportFolders(),
        getInbox(),
      ]);
      if (requestEpoch !== epoch) return;
      folders.value = folderBody?.folders ?? [];
      inbox.value = inboxBody ?? { total: 0, folder_count: 0, batches: [] };
      loaded.value = true;
    } catch (err) {
      console.warn("Could not read the import folders and the Inbox", err);
    }
  }

  /** Coalesce a burst of picture events (one per imported file) into one read. */
  function refreshSoon() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void refresh();
    }, REFRESH_DEBOUNCE_MS);
  }

  /**
   * Mark pictures seen and re-read the Inbox.
   * @param {Object} body - see `markInboxSeen`.
   */
  async function markSeen(body) {
    const result = await markInboxSeen(body);
    await refresh();
    return result;
  }

  /** Mark one batch seen: its folder, between its first and last arrival. */
  function markBatchSeen(batch) {
    return markSeen({
      import_source_folder: batch.import_source_folder,
      since: batch.first_imported_at,
      until: batch.last_imported_at,
    });
  }

  function reset() {
    epoch += 1;
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = null;
    folders.value = [];
    inbox.value = { total: 0, folder_count: 0, batches: [] };
    loaded.value = false;
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    folders,
    inbox,
    loaded,
    hasFolders,
    unseenCount,
    refresh,
    refreshSoon,
    markSeen,
    markBatchSeen,
    reset,
  };
});
