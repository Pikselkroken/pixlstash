import { ref, computed } from "vue";

// Constants shared with ImageGrid.vue
const MIN_THUMBNAIL_SIZE = 128;
const MAX_THUMBNAIL_SIZE = 384;
const THUMBNAIL_INFO_ROW_HEIGHT = 24;
const VIEW_WINDOW = 100;

/**
 * Manages viewport geometry, row height, and scroll position for the virtual
 * scrolling image grid.
 *
 * @param {import('vue').Ref} scrollWrapper - Ref to the scrollable container element.
 * @param {import('vue').Ref} gridContainer - Ref to the inner grid element.
 * @param {object} props - Reactive props: columns, thumbnailSize, compactMode.
 * @param {import('vue').ComputedRef<number>} allGridImagesLength - Total count of grid images.
 * @param {object} [callbacks]
 * @param {Function} [callbacks.onVisibleRangeChange] - Called when the visible
 *   row range changes (triggers thumbnail fetch in the parent).
 * @param {Function} [callbacks.afterRowHeightUpdate] - Called after rowHeight is
 *   recalculated (refreshes thumbnail info text in the parent).
 */
export function useVirtualScroll(
  scrollWrapper,
  gridContainer,
  props,
  allGridImagesLength,
  { onVisibleRangeChange, afterRowHeightUpdate } = {},
) {
  // ── Render buffer ──────────────────────────────────────────────────────────
  // During the initial render the buffer is 0 so the first paint is fast;
  // once the grid has rendered once the buffer expands to a full view-window
  // worth of items on each side.
  const divisibleViewWindow = computed(() => {
    const cols = props.columns;
    return Math.ceil(VIEW_WINDOW / cols) * cols;
  });

  const initialRender = ref(true);

  const renderBuffer = computed(() =>
    initialRender.value ? 0 : divisibleViewWindow.value,
  );

  // ── Visible row tracking ───────────────────────────────────────────────────
  const visibleStart = ref(0);
  const visibleEnd = ref(0);

  // ── Row height ────────────────────────────────────────────────────────────
  const rowHeight = ref(
    Math.round(
      Math.min(
        MAX_THUMBNAIL_SIZE,
        Math.max(MIN_THUMBNAIL_SIZE, props.thumbnailSize || MIN_THUMBNAIL_SIZE),
      ) + (props.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT),
    ),
  );

  function getGridColumnWidth() {
    const cols = Math.max(1, props.columns || 1);
    const gridWidth =
      gridContainer.value?.clientWidth ??
      scrollWrapper.value?.clientWidth ??
      0;
    if (!gridWidth) {
      return Math.min(
        MAX_THUMBNAIL_SIZE,
        Math.max(
          MIN_THUMBNAIL_SIZE,
          props.thumbnailSize || MIN_THUMBNAIL_SIZE,
        ),
      );
    }
    const availableWidth = Math.max(0, gridWidth - 4);
    const rawWidth = availableWidth / cols;
    return Math.min(MAX_THUMBNAIL_SIZE, Math.max(1, rawWidth || MIN_THUMBNAIL_SIZE));
  }

  function updateRowHeightFromGrid() {
    const columnWidth = getGridColumnWidth();
    const infoHeight = props.compactMode ? 0 : THUMBNAIL_INFO_ROW_HEIGHT;
    rowHeight.value = Math.round(columnWidth + infoHeight);
    afterRowHeightUpdate?.();
  }

  // ── Render window ─────────────────────────────────────────────────────────
  const renderStart = computed(() =>
    Math.max(0, visibleStart.value - renderBuffer.value),
  );

  const renderEnd = computed(() =>
    Math.min(
      allGridImagesLength.value,
      visibleEnd.value + renderBuffer.value,
    ),
  );

  const topSpacerHeight = computed(() => {
    const cols = props.columns;
    const rowsAbove = Math.floor(renderStart.value / cols);
    return rowsAbove > 0 ? rowsAbove * rowHeight.value : 1;
  });

  const bottomSpacerHeight = computed(() => {
    const cols = props.columns;
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
      const el = scrollWrapper.value;
      if (!el) return;
      const cardHeight = rowHeight.value;
      const scrollTop = el.scrollTop;
      const cols = props.columns;
      const firstVisibleRow = scrollTop / cardHeight;
      const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;
      const newVisibleStart = Math.floor(firstVisibleRow) * cols;
      const newVisibleEnd = Math.ceil(lastVisibleRow) * cols;
      if (
        visibleStart.value !== newVisibleStart ||
        visibleEnd.value !== newVisibleEnd
      ) {
        visibleStart.value = newVisibleStart;
        visibleEnd.value = newVisibleEnd;
        onVisibleRangeChange?.();
      }
    }, 50);
  }

  // ── Immediate visible-range recalculation ──────────────────────────────────
  // Used when the layout changes (column count, compact mode) without a scroll
  // event — the debounced onGridScroll would not fire in time to fill the newly
  // visible slots.
  function recalculateVisibleRange() {
    const el = scrollWrapper.value;
    if (!el) return;
    const cardHeight = rowHeight.value;
    const scrollTop = el.scrollTop;
    const cols = props.columns;
    const firstVisibleRow = scrollTop / cardHeight;
    const lastVisibleRow = (scrollTop + el.clientHeight - 1) / cardHeight;
    const newVisibleStart = Math.floor(firstVisibleRow) * cols;
    const newVisibleEnd = Math.ceil(lastVisibleRow) * cols;
    visibleStart.value = newVisibleStart;
    visibleEnd.value = newVisibleEnd;
    onVisibleRangeChange?.();
  }

  // ── Cursor scroll-into-view ───────────────────────────────────────────────
  function scrollCursorIntoView(idx) {
    if (!scrollWrapper.value) return;
    const cols = Math.max(1, props.columns || 1);
    const row = Math.floor(idx / cols);
    const itemTop = row * rowHeight.value;
    const itemBottom = itemTop + rowHeight.value;
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
  };
}
