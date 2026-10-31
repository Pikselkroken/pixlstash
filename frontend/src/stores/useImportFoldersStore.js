// useImportFoldersStore.js - the import folders.
//
// An import folder is configured once, in Settings › Libraries & imports, and
// what it brings in is a recent picture with no person and no set, so it shows
// in the Inbox (`useInboxStore`) like any other arrival. The Settings table,
// the Filter menu's "Imported from" and the Import panel read this one store,
// and it re-reads on the WebSocket's picture events rather than polling.

import { defineStore } from "pinia";
import { computed, onScopeDispose, ref } from "vue";
import { listImportFolders } from "../api/folders";
import { isReadOnly, onSessionReset } from "../utils/apiClient";

const REFRESH_DEBOUNCE_MS = 1500;

export const useImportFoldersStore = defineStore("importFolders", () => {
  const folders = ref([]);
  // Set once the folder list has been read, so the sidebar row does not flash.
  const loaded = ref(false);
  let epoch = 0;
  let refreshTimer = null;

  const hasFolders = computed(() => folders.value.length > 0);

  async function refresh() {
    if (isReadOnly.value) return;
    const requestEpoch = epoch;
    try {
      const folderBody = await listImportFolders();
      if (requestEpoch !== epoch) return;
      folders.value = folderBody?.folders ?? [];
      loaded.value = true;
    } catch (err) {
      console.warn("Could not read the import folders", err);
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

  function reset() {
    epoch += 1;
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = null;
    folders.value = [];
    loaded.value = false;
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    folders,
    loaded,
    hasFolders,
    refresh,
    refreshSoon,
    reset,
  };
});
