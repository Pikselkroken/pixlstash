import { ref } from "vue";
import { defineStore } from "pinia";
import { isReadOnly } from "../utils/apiClient";

export const useGridStore = defineStore("grid", () => {
  const columns = ref(isReadOnly.value ? 5 : 4);
  const thumbnailSize = ref(256);
  const compactMode = ref(isReadOnly.value);
  const showStars = ref(!isReadOnly.value);
  const showFaceBboxes = ref(false);
  const showDetections = ref(false);
  const showProblemIcon = ref(true);
  const showStacks = ref(true);
  const expandedStackCount = ref(0);
  const totalStackCount = ref(0);
  const visibleRangeLabel = ref(null);
  // Total number of pictures matching the active filter/sort (the full fetched
  // set, not the virtualised window). Published by ImageGrid; read by the Filter
  // menu header to show a live "N matches" count.
  const matchCount = ref(0);
  const gridVersion = ref(0);
  const wsUpdateKey = ref(0);
  const minColumns = ref(6);
  const maxColumns = ref(12);

  function refreshGridVersion() {
    gridVersion.value++;
  }

  return {
    columns,
    thumbnailSize,
    compactMode,
    showStars,
    showFaceBboxes,
    showDetections,
    showProblemIcon,
    showStacks,
    expandedStackCount,
    totalStackCount,
    visibleRangeLabel,
    matchCount,
    gridVersion,
    wsUpdateKey,
    minColumns,
    maxColumns,
    refreshGridVersion,
  };
});
