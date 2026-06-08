import { ref } from "vue";
import { defineStore } from "pinia";
import { isReadOnly } from "../utils/apiClient";

const PURGE_SNAPSHOT_WARNING_KEY = "pixlstash:hidePurgeSnapshotWarning";

function loadHidePurgeSnapshotWarning() {
  try {
    return window.localStorage?.getItem(PURGE_SNAPSHOT_WARNING_KEY) === "true";
  } catch {
    return false;
  }
}

function saveHidePurgeSnapshotWarning(val) {
  try {
    window.localStorage?.setItem(
      PURGE_SNAPSHOT_WARNING_KEY,
      val ? "true" : "false",
    );
  } catch {
    // ignore
  }
}

export const useUserPrefsStore = defineStore("userPrefs", () => {
  const dateFormat = ref("locale");
  const themeMode = ref(isReadOnly.value ? "dark" : "light");
  const showKeyboardHint = ref(true);
  const hiddenTags = ref([]);
  const applyTagFilter = ref(false);
  const penalisedTagWeights = ref({});
  const checkForUpdates = ref(null); // null = undecided, true/false = user choice
  const sidebarThumbnailSize = ref(isReadOnly.value ? 32 : 48);
  const publicUrl = ref(null);
  const embedWatermark = ref(false);
  // Per-device dismissal of the "deleted pictures still in snapshots" warning
  // shown after a purge. Stored client-side only via localStorage.
  const hidePurgeSnapshotWarning = ref(loadHidePurgeSnapshotWarning());

  function setHidePurgeSnapshotWarning(val) {
    hidePurgeSnapshotWarning.value = Boolean(val);
    saveHidePurgeSnapshotWarning(hidePurgeSnapshotWarning.value);
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
    publicUrl,
    embedWatermark,
    hidePurgeSnapshotWarning,
    setHidePurgeSnapshotWarning,
  };
});
