import { ref, computed } from "vue";
import { defineStore } from "pinia";
import { isReadOnly } from "../utils/apiClient";
import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  columnsForSizeLevel,
  nearestSizeLevelForColumns,
} from "../utils/thumbnailSizes";

export const useGridStore = defineStore("grid", () => {
  // User-facing thumbnail size as a level 0..6 (Tiny … Huge). This is the
  // single control that drives BOTH layouts: the square grid derives its
  // column count from it (see `columns` below) and the justified layout derives
  // its target row height from it. Persisted server-side as
  // `thumbnail_size_level`. Read-only starts one notch denser (~7 columns,
  // i.e. "large" on the current ladder in thumbnailSizes.js).
  const sizeLevel = ref(
    isReadOnly.value ? nearestSizeLevelForColumns(7) : DEFAULT_THUMBNAIL_SIZE_LEVEL,
  );
  const thumbnailSize = ref(192);
  // 'square' (uniform grid) or 'justified' (Google-Photos row layout). A
  // display preference persisted like `columns`; the justified path is driven
  // by useJustifiedLayout + useVirtualScroll and consumed by ImageGrid's
  // `thumbnailMode` prop.
  // Read-only starts justified: those sessions never fetch /users/me/config
  // (fetchConfig returns early), so this default is the whole setting for them,
  // and justified is how the demo is meant to look. Owner sessions keep square
  // and are overwritten by the stored preference as soon as the config lands.
  const thumbnailMode = ref(isReadOnly.value ? "justified" : "square");
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

  // Square-grid column count, derived from the size level. Clamped DOWN to
  // maxColumns (the viewport's column ceiling, so tiles never shrink below the
  // usable minimum on narrow screens) but NOT up to minColumns: the larger size
  // levels intentionally mean few columns / big tiles, even past the 384px
  // source width. Read-only - change `sizeLevel` to resize. Kept as a getter
  // named `columns` so every existing square-grid consumer works unchanged.
  const columns = computed(() => {
    const desired = columnsForSizeLevel(sizeLevel.value);
    return Math.max(1, Math.min(maxColumns.value, desired));
  });

  // Both modes render from the same stored bitmap - justified shows it whole,
  // square crops it to the stored rectangle - so a valid change applies at once
  // with no thumbnail regeneration. Rejecting the unchanged value keeps the
  // persist watcher from firing for nothing.
  function setThumbnailMode(value) {
    if (value !== "square" && value !== "justified") return;
    if (value === thumbnailMode.value) return;
    thumbnailMode.value = value;
  }

  function refreshGridVersion() {
    gridVersion.value++;
  }

  return {
    setThumbnailMode,
    columns,
    sizeLevel,
    thumbnailSize,
    thumbnailMode,
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
