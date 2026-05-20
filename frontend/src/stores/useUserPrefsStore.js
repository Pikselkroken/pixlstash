import { ref } from "vue";
import { defineStore } from "pinia";
import { isReadOnly } from "../utils/apiClient";

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
  };
});
