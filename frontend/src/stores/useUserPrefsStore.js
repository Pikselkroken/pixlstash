import { ref } from "vue";
import { defineStore } from "pinia";
import { apiClient, isReadOnly } from "../utils/apiClient";

export const useUserPrefsStore = defineStore("userPrefs", () => {
  const dateFormat = ref("locale");
  const themeMode = ref(isReadOnly.value ? "dark" : "light");
  const showKeyboardHint = ref(true);
  const hiddenTags = ref([]);
  const applyTagFilter = ref(false);
  const penalisedTagWeights = ref({});
  const checkForUpdates = ref(null); // null = undecided, true/false = user choice
  const sidebarThumbnailSize = ref(isReadOnly.value ? 32 : 48);
  // Expanded (non-docked) sidebar width in px. Drag-resizable, clamped 120–300.
  const sidebarWidth = ref(240);
  const publicUrl = ref(null);
  const embedWatermark = ref(false);
  // Dismissal of the "deleted pictures still in snapshots" warning shown after
  // a purge. Persisted server-side per user (hydrated from /users/me/config in
  // App.vue), so it follows the account across devices/browsers.
  const hidePurgeSnapshotWarning = ref(false);

  async function setHidePurgeSnapshotWarning(val) {
    const next = Boolean(val);
    if (hidePurgeSnapshotWarning.value === next) return;
    hidePurgeSnapshotWarning.value = next;
    // Read-only/scoped tokens cannot patch user config (and never reach the
    // purge flow that shows this dialog), so skip the request for them.
    if (isReadOnly.value) return;
    try {
      await apiClient.patch("/users/me/config", {
        hide_purge_snapshot_warning: next,
      });
    } catch (e) {
      console.error("Failed to persist hide_purge_snapshot_warning:", e);
    }
  }

  return {
    dateFormat,
    themeMode,
    showKeyboardHint,
    hiddenTags,
    applyTagFilter,
    penalisedTagWeights,
    checkForUpdates,
    sidebarThumbnailSize,
    sidebarWidth,
    publicUrl,
    embedWatermark,
    hidePurgeSnapshotWarning,
    setHidePurgeSnapshotWarning,
  };
});
