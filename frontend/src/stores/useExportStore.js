import { computed, ref, watch } from "vue";
import { defineStore } from "pinia";

export const useExportStore = defineStore("export", () => {
  const exportMenuOpen = ref(false);
  const exportType = ref("full");
  const exportCaptionMode = ref("description");
  const exportTagFormat = ref("spaces");
  const exportIncludeCharacterName = ref(true);
  const exportUseOriginalFileNames = ref(false);
  const exportResolution = ref("original");
  const exportSelectedCount = ref(0);
  const exportTotalCount = ref(0);

  const exportCount = computed(() =>
    exportSelectedCount.value > 0
      ? exportSelectedCount.value
      : exportTotalCount.value,
  );

  const exportTypeLocksCaptions = computed(() => exportType.value !== "full");

  const exportCaptionOptions = [
    { title: "No Captions", value: "none" },
    { title: "Description", value: "description" },
    { title: "Tags", value: "tags" },
  ];
  const exportTypeOptions = [
    { title: "Full images", value: "full" },
    { title: "Face crops", value: "face" },
  ];
  const exportResolutionOptions = [
    { title: "Original", value: "original" },
    { title: "Half Size", value: "half" },
    { title: "Quarter Size", value: "quarter" },
  ];
  const exportTagFormatOptions = [
    { title: "Spaces", value: "spaces" },
    { title: "Underscores", value: "underscores" },
  ];

  watch(
    exportType,
    (value) => {
      if (value !== "full") {
        exportCaptionMode.value = "tags";
        exportIncludeCharacterName.value = false;
      }
    },
    { immediate: true },
  );

  return {
    exportMenuOpen,
    exportType,
    exportCaptionMode,
    exportTagFormat,
    exportIncludeCharacterName,
    exportUseOriginalFileNames,
    exportResolution,
    exportSelectedCount,
    exportTotalCount,
    exportCount,
    exportTypeLocksCaptions,
    exportCaptionOptions,
    exportTypeOptions,
    exportResolutionOptions,
    exportTagFormatOptions,
  };
});
