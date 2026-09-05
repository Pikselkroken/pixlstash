import { onMounted, onUnmounted, watch } from "vue";
import { useGridStore } from "../stores/useGridStore";
import { useSidebarStore } from "../stores/useSidebarStore";

// Cut 25% from the pre-2026-09-03 96/384/14 (see thumbnailSizes.js) so the
// column ceiling keeps pace with the smaller grid ladder.
const MIN_THUMBNAIL_SIZE = 72;
const MAX_THUMBNAIL_SIZE = 288;
const MIN_COLUMNS = 2;
const MAX_COLUMNS = 19;
const SIDEBAR_HIDE_BREAKPOINT = 790;
const STATS_HIDE_BREAKPOINT = 1280;

/**
 * Viewport-driven layout: which rails the window is wide enough to show, and
 * how many grid columns fit in what is left.
 *
 * The column ceiling is a viewport fact, not a preference - it stops tiles
 * shrinking below a usable width on a narrow window - so it is recomputed on
 * every resize and whenever the space around the grid changes.
 *
 * @param {object} deps
 * @param {import("vue").Ref} deps.mainAreaRef - the element the grid lives
 *   in, measured for the column ceiling.
 */
export function useViewportLayout({ mainAreaRef }) {
  const gridStore = useGridStore();
  const sidebarStore = useSidebarStore();

  function updateSidebarBreakpoints() {
    if (typeof window !== "undefined") {
      sidebarStore.sidebarForcedHidden =
        window.innerWidth < SIDEBAR_HIDE_BREAKPOINT;
      sidebarStore.statsForcedHidden =
        window.innerWidth < STATS_HIDE_BREAKPOINT;
    }
  }

  function updateIsMobile() {
    updateSidebarBreakpoints();
    updateMaxColumns();
  }

  function updateMaxColumns() {
    // Maintain the responsive column bounds. gridStore.columns is derived from
    // the size level and clamps itself to these bounds, so there is nothing to
    // write back here - updating the bounds re-evaluates the derived count.
    const width = mainAreaRef.value?.clientWidth ?? window.innerWidth ?? 0;
    if (!width) {
      gridStore.minColumns = MIN_COLUMNS;
      gridStore.maxColumns = MAX_COLUMNS;
      return;
    }
    const availableWidth = Math.max(0, width - 8);
    const computedMin = Math.max(
      1,
      Math.ceil(availableWidth / MAX_THUMBNAIL_SIZE),
    );
    const computedMax = Math.max(
      computedMin,
      Math.floor(availableWidth / MIN_THUMBNAIL_SIZE),
    );
    gridStore.minColumns = Math.max(MIN_COLUMNS, computedMin);
    gridStore.maxColumns = Math.min(MAX_COLUMNS, computedMax);
  }

  function closeSidebarIfMobile() {
    if (sidebarStore.sidebarForcedHidden) {
      sidebarStore.hideAutoSidebar();
    }
  }

  // Opening or closing the stats rail changes the width the grid has to work
  // with, so the column ceiling is recomputed with it.
  watch(
    () => sidebarStore.statsOpen,
    () => {
      updateIsMobile();
    },
  );

  onMounted(() => window.addEventListener("resize", updateIsMobile));
  onUnmounted(() => window.removeEventListener("resize", updateIsMobile));

  return {
    updateSidebarBreakpoints,
    updateIsMobile,
    updateMaxColumns,
    closeSidebarIfMobile,
  };
}
