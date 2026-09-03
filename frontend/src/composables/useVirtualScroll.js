import { ref, computed, watch } from "vue";
import {
  packJustifiedRows,
  rowAtOffset,
  rowOfIndex,
  JUSTIFIED_ROW_GAP,
} from "./useJustifiedLayout";
import { rowHeightForSizeLevel } from "../utils/thumbnailSizes";
import { useGridStore } from "../stores/useGridStore";

// Constants shared with ImageGrid.vue. Cut 25% alongside the ladder in
// thumbnailSizes.js (owner call, 2026-09-03).
const MIN_THUMBNAIL_SIZE = 96;
const MAX_THUMBNAIL_SIZE = 288;
const THUMBNAIL_INFO_ROW_HEIGHT = 24;
const VIEW_WINDOW = 100;

/**
 * Manages viewport geometry, row height, and scroll position for the virtual
 * scrolling image grid.
 *
 * Two layout modes, selected by `gridStore.thumbnailMode`:
 * - `'square'` (default): the original uniform grid - `cols` items per row,
 *   every row `rowHeight` px tall, `index → row` is `floor(index / cols)`.
 * - `'justified'`: Google-Photos-style rows packed by `useJustifiedLayout`.
 *   Row heights are near-uniform but items-per-row varies, so all
 *   index↔row↔pixel arithmetic goes through the packed layout
 *   (`rowStarts` / `rowOffsets`) instead of column arithmetic. In both modes
 *   `visibleStart`/`visibleEnd` are ITEM indices - the contract the parent's
 *   thumbnail fetching relies on.
 *
 * @param {import('vue').Ref} scrollWrapper - Ref to the scrollable container element.
 * @param {import('vue').Ref} gridContainer - Ref to the inner grid element.
 * @param {object} props - Reactive props: columns, thumbnailSize, compactMode,
 *   thumbnailMode.
 * @param {import('vue').ComputedRef<number>} allGridImagesLength - Total count of grid images.
 * @param {object} [callbacks]
 * @param {Function} [callbacks.onVisibleRangeChange] - Called when the visible
 *   row range changes (triggers thumbnail fetch in the parent).
 * @param {Function} [callbacks.afterRowHeightUpdate] - Called after rowHeight is
 *   recalculated (refreshes thumbnail info text in the parent).
 * @param {Function} [callbacks.getAspectRatios] - Returns the aspect-ratio
 *   array (one entry per grid image, in grid order) used to pack justified
 *   rows. Required for `thumbnailMode === 'justified'`; unused otherwise.
 */
export function useVirtualScroll(
  scrollWrapper,
  gridContainer,
  props,
  allGridImagesLength,
  { onVisibleRangeChange, afterRowHeightUpdate, getAspectRatios } = {},
) {
  const gridStore = useGridStore();
  // ── Render buffer ──────────────────────────────────────────────────────────
  // During the initial render the buffer is 0 so the first paint is fast;
  // once the grid has rendered once the buffer expands to a full view-window
  // worth of items on each side.
  const divisibleViewWindow = computed(() => {
    const cols = gridStore.columns;
    return Math.ceil(VIEW_WINDOW / cols) * cols;
  });

  const initialRender = ref(true);

  const renderBuffer = computed(() =>
    initialRender.value ? 0 : divisibleViewWindow.value,
  );

  // ── Visible row tracking ───────────────────────────────────────────────────
  const visibleStart = ref(0);
  const visibleEnd = ref(0);

  // ── Justified layout ──────────────────────────────────────────────────────
  const isJustifiedMode = computed(
    () =>
      gridStore.thumbnailMode === "justified" &&
      typeof getAspectRatios === "function",
  );

  // Measured width of the grid container, updated by updateRowHeightFromGrid
  // (mount + ResizeObserver). Floored to a whole px so packed rows can never
  // exceed the real (possibly fractional) box and wrap unexpectedly.
  const justifiedContainerWidth = ref(0);

  // The packed row model. Pure arithmetic over the aspect-ratio list - no DOM
  // reads - and deterministic, so recomputing on resize/fetch cannot thrash
  // thumbnails. Null while unmeasured/empty (the parent falls back to the
  // uniform path until the first measure lands).
  const justifiedLayout = computed(() => {
    if (!isJustifiedMode.value) return null;
    const containerWidth = justifiedContainerWidth.value;
    if (!(containerWidth > 0)) return null;
    const aspectRatios = getAspectRatios();
    if (!Array.isArray(aspectRatios) || aspectRatios.length === 0) return null;
    // Target row height comes from the shared size level so the size slider
    // resizes justified rows exactly as it resizes square columns. The min/max
    // bounds flex around the target so leftover rows can still stretch/shrink
    // to fill the container width without jumping to a fixed global size.
    const targetRowHeight = rowHeightForSizeLevel(gridStore.sizeLevel);
    return packJustifiedRows({
      aspectRatios,
      containerWidth,
      targetRowHeight,
      gap: JUSTIFIED_ROW_GAP,
      rowExtraHeight: gridStore.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT,
      minRowHeight: Math.round(targetRowHeight * 0.7),
      maxRowHeight: Math.round(targetRowHeight * 1.4),
    });
  });

  function hasJustifiedLayout() {
    return (
      isJustifiedMode.value &&
      justifiedLayout.value !== null &&
      justifiedLayout.value.rowHeights.length > 0
    );
  }

  // ── Row height ────────────────────────────────────────────────────────────
  const rowHeight = ref(
    Math.round(
      Math.min(
        MAX_THUMBNAIL_SIZE,
        Math.max(
          MIN_THUMBNAIL_SIZE,
          gridStore.thumbnailSize || MIN_THUMBNAIL_SIZE,
        ),
      ) + (gridStore.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT),
    ),
  );

  function getGridColumnWidth() {
    const cols = Math.max(1, gridStore.columns || 1);
    const gridWidth =
      gridContainer.value?.clientWidth ?? scrollWrapper.value?.clientWidth ?? 0;
    if (!gridWidth) {
      return Math.min(
        MAX_THUMBNAIL_SIZE,
        Math.max(
          MIN_THUMBNAIL_SIZE,
          gridStore.thumbnailSize || MIN_THUMBNAIL_SIZE,
        ),
      );
    }
    const availableWidth = Math.max(0, gridWidth - 4);
    const rawWidth = availableWidth / cols;
    return Math.min(
      MAX_THUMBNAIL_SIZE,
      Math.max(1, rawWidth || MIN_THUMBNAIL_SIZE),
    );
  }

  function updateRowHeightFromGrid() {
    const columnWidth = getGridColumnWidth();
    const infoHeight = gridStore.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT;
    rowHeight.value = Math.round(columnWidth + infoHeight);
    if (isJustifiedMode.value) {
      remeasureJustifiedWidth();
    }
    afterRowHeightUpdate?.();
  }

  // Re-measures the container and, when the width actually changed, re-packs
  // the row model.
  //
  // ANTI-JUMP: a repack moves every row boundary, so a naive repack would
  // leave scrollTop pointing at arbitrary different images and the viewport
  // would visibly jump on every window/sidebar resize. We therefore capture
  // the first item of the current top-visible row under the OLD layout before
  // changing the width, then restore scrollTop to that item's row offset in
  // the NEW layout - the user keeps looking at the same picture.
  function remeasureJustifiedWidth() {
    const rect = gridContainer.value?.getBoundingClientRect?.();
    const measured = Math.floor(
      rect?.width ?? scrollWrapper.value?.clientWidth ?? 0,
    );
    if (!(measured > 0) || measured === justifiedContainerWidth.value) return;

    const el = scrollWrapper.value;
    const prevLayout = hasJustifiedLayout() ? justifiedLayout.value : null;
    let anchorIdx = null;
    if (el && prevLayout && el.scrollTop > 0) {
      const topRow = rowAtOffset(prevLayout.rowOffsets, el.scrollTop);
      anchorIdx = prevLayout.rowStarts[topRow];
    }

    justifiedContainerWidth.value = measured;

    // Computeds are synchronous: reading justifiedLayout here already yields
    // the re-packed model for the new width.
    const newLayout = hasJustifiedLayout() ? justifiedLayout.value : null;
    if (el && anchorIdx !== null && newLayout) {
      const clampedIdx = Math.min(
        anchorIdx,
        newLayout.itemScaledWidths.length - 1,
      );
      const newRow = rowOfIndex(newLayout.rowStarts, Math.max(0, clampedIdx));
      el.scrollTop = newLayout.rowOffsets[newRow];
    }
    recalculateVisibleRange();
  }

  // ── Visible-range calculation (shared by scroll + immediate recalc) ───────
  // Returns ITEM indices in both modes: [start, end) is exactly the set of
  // items whose row intersects the viewport. This is the contract the parent's
  // updateVisibleThumbnails depends on - if these drifted from what is painted
  // the grid would show blank tiles.
  function computeVisibleRange() {
    const el = scrollWrapper.value;
    if (!el) return null;
    if (isJustifiedMode.value) {
      if (!hasJustifiedLayout()) return { start: 0, end: 0 };
      const layout = justifiedLayout.value;
      const firstRow = rowAtOffset(layout.rowOffsets, el.scrollTop);
      const lastRow = rowAtOffset(
        layout.rowOffsets,
        el.scrollTop + el.clientHeight - 1,
      );
      return {
        start: layout.rowStarts[firstRow],
        end: layout.rowStarts[lastRow + 1],
      };
    }
    const cardHeight = rowHeight.value;
    const scrollTop = el.scrollTop;
    const cols = gridStore.columns;
    const firstVisibleRow = scrollTop / cardHeight;
    const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;
    return {
      start: Math.floor(firstVisibleRow) * cols,
      end: Math.ceil(lastVisibleRow) * cols,
    };
  }

  // ── Render window ─────────────────────────────────────────────────────────
  // In justified mode the buffered endpoints are snapped OUTWARD to row
  // boundaries: renderStart down to the start of its row, renderEnd up to the
  // end of its row. Two invariants follow:
  //   1. every rendered row is complete - flex-wrap line breaks can only match
  //      the packed model when no row is partially rendered, and
  //   2. [visibleStart, visibleEnd) ⊆ [renderStart, renderEnd) even when the
  //      visible range is a uniform-math estimate (as right after a fetch).
  // The parent's updateVisibleThumbnails fetches exactly [renderStart,
  // renderEnd), so the fetch window always equals what is painted
  // (anti-blank-tile).
  const renderStart = computed(() => {
    const raw = Math.max(0, visibleStart.value - renderBuffer.value);
    if (hasJustifiedLayout()) {
      const layout = justifiedLayout.value;
      const n = layout.itemScaledWidths.length;
      if (raw >= n) return raw;
      // Snap down to the start of the row containing raw.
      return layout.rowStarts[rowOfIndex(layout.rowStarts, raw)];
    }
    return raw;
  });

  const renderEnd = computed(() => {
    const raw = Math.min(
      allGridImagesLength.value,
      visibleEnd.value + renderBuffer.value,
    );
    if (hasJustifiedLayout() && raw > 0) {
      const layout = justifiedLayout.value;
      const n = layout.itemScaledWidths.length;
      if (raw >= n) return raw;
      // Snap up to the end of the row containing item raw - 1 (an exclusive
      // endpoint already on a boundary is left unchanged).
      return Math.min(
        n,
        layout.rowStarts[rowOfIndex(layout.rowStarts, raw - 1) + 1],
      );
    }
    return raw;
  });

  // ── Spacers ───────────────────────────────────────────────────────────────
  // Justified mode: exact pixel sums from rowOffsets (never rows * rowHeight).
  // The grid renders as a flex-wrap container with `gap` between lines, so a
  // rendered spacer line contributes one extra inter-line gap that the spacer
  // height must not double-count.
  const topSpacerHeight = computed(() => {
    if (isJustifiedMode.value) {
      if (!hasJustifiedLayout()) return 0;
      const layout = justifiedLayout.value;
      const start = renderStart.value;
      if (start <= 0) return 0;
      const row = rowOfIndex(
        layout.rowStarts,
        Math.min(start, layout.itemScaledWidths.length - 1),
      );
      // The flex row-gap between the spacer line and the first rendered row
      // supplies JUSTIFIED_ROW_GAP px of the offset.
      return Math.max(0, layout.rowOffsets[row] - JUSTIFIED_ROW_GAP);
    }
    const cols = gridStore.columns;
    const rowsAbove = Math.floor(renderStart.value / cols);
    return rowsAbove > 0 ? rowsAbove * rowHeight.value : 1;
  });

  const bottomSpacerHeight = computed(() => {
    if (isJustifiedMode.value) {
      if (!hasJustifiedLayout()) return 0;
      const layout = justifiedLayout.value;
      const n = layout.itemScaledWidths.length;
      const end = renderEnd.value;
      if (end >= n) return 0;
      if (end <= 0) return layout.totalHeight;
      const lastRow = rowOfIndex(layout.rowStarts, end - 1);
      // rowOffsets[lastRow + 1] already includes the inter-row gap that the
      // flex layout inserts between the last rendered row and the spacer.
      const nextOffset = layout.rowOffsets[lastRow + 1] ?? layout.totalHeight;
      return Math.max(0, layout.totalHeight - nextOffset);
    }
    const cols = gridStore.columns;
    const lastRenderedRow = Math.floor((renderEnd.value - 1) / cols) + 1;
    const totalRows = Math.ceil(allGridImagesLength.value / cols);
    const rowsBelow = totalRows - lastRenderedRow;
    return rowsBelow > 0 ? rowsBelow * rowHeight.value : 0;
  });

  // ── Scroll handler ────────────────────────────────────────────────────────
  function onGridScroll() {
    if (!window._scrollDebounceTimeout) window._scrollDebounceTimeout = null;
    if (window._scrollDebounceTimeout)
      clearTimeout(window._scrollDebounceTimeout);
    window._scrollDebounceTimeout = setTimeout(() => {
      const range = computeVisibleRange();
      if (!range) return;
      if (
        visibleStart.value !== range.start ||
        visibleEnd.value !== range.end
      ) {
        visibleStart.value = range.start;
        visibleEnd.value = range.end;
        onVisibleRangeChange?.();
      }
    }, 50);
  }

  // ── Immediate visible-range recalculation ──────────────────────────────────
  // Used when the layout changes (column count, compact mode, justified
  // repack) without a scroll event - the debounced onGridScroll would not fire
  // in time to fill the newly visible slots.
  function recalculateVisibleRange() {
    const range = computeVisibleRange();
    if (!range) return;
    visibleStart.value = range.start;
    visibleEnd.value = range.end;
    onVisibleRangeChange?.();
  }

  // ── Keep the range in step with the packed model ──────────────────────────
  // justifiedLayout packs from the aspect ratios of the images that have
  // ARRIVED, so it is null until the first batch lands and then repacks on every
  // change to that list (placeholder 1:1 ratios first, real ratios as batches
  // splice in). Each repack moves every row boundary, while visibleStart/End are
  // plain refs seeded by the fetch from a UNIFORM square-grid estimate
  // (ceil(viewH / cellH) * columns) that does not describe justified geometry.
  // Nothing else recalculates them against the real model: computeVisibleRange
  // returns an empty window while the layout is null, and the only other hook,
  // remeasureJustifiedWidth, early-returns once the width stops changing and so
  // never reaches its own recalculateVisibleRange(). Without this watcher the
  // render window keeps describing a geometry that never existed, so the grid
  // paints its cards outside the viewport (commonly a zero-item window, i.e. a
  // blank grid) until the user scrolls and computeVisibleRange finally reads the
  // packed model.
  watch(justifiedLayout, (layout) => {
    if (!layout || !layout.rowHeights.length) return;
    recalculateVisibleRange();
  });

  // ── Cursor scroll-into-view ───────────────────────────────────────────────
  function scrollCursorIntoView(idx) {
    if (!scrollWrapper.value) return;
    let itemTop;
    let itemBottom;
    if (hasJustifiedLayout()) {
      const layout = justifiedLayout.value;
      const clampedIdx = Math.max(
        0,
        Math.min(idx, layout.itemScaledWidths.length - 1),
      );
      const row = rowOfIndex(layout.rowStarts, clampedIdx);
      itemTop = layout.rowOffsets[row];
      itemBottom = itemTop + layout.rowHeights[row];
    } else {
      const cols = Math.max(1, gridStore.columns || 1);
      const row = Math.floor(idx / cols);
      itemTop = row * rowHeight.value;
      itemBottom = itemTop + rowHeight.value;
    }
    const scrollTop = scrollWrapper.value.scrollTop;
    const clientHeight = scrollWrapper.value.clientHeight;
    if (itemTop < scrollTop) {
      scrollWrapper.value.scrollTop = itemTop;
    } else if (itemBottom > scrollTop + clientHeight) {
      scrollWrapper.value.scrollTop = itemBottom - clientHeight;
    }
  }

  return {
    initialRender,
    divisibleViewWindow,
    renderBuffer,
    visibleStart,
    visibleEnd,
    rowHeight,
    renderStart,
    renderEnd,
    topSpacerHeight,
    bottomSpacerHeight,
    getGridColumnWidth,
    updateRowHeightFromGrid,
    recalculateVisibleRange,
    onGridScroll,
    scrollCursorIntoView,
    // Justified-mode additions (null / false when thumbnailMode is 'square').
    isJustifiedMode,
    justifiedLayout,
  };
}
