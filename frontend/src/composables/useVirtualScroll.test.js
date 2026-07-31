import { describe, it, expect, vi, beforeEach } from "vitest";
import { ref, reactive, computed, nextTick } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { useGridStore } from "../stores/useGridStore.js";
import { useVirtualScroll } from "./useVirtualScroll.js";

// The composable reads the grid geometry (columns, size level, thumbnail mode
// and size, compact mode) from the grid store.
beforeEach(() => {
  setActivePinia(createPinia());
});

// Justified mode packs its rows from the aspect ratios of the images that have
// actually arrived, so `justifiedLayout` is null before the first batch and
// repacks every time the ratio list changes. `visibleStart`/`visibleEnd` are
// plain refs seeded by the fetch from a UNIFORM square-grid estimate, so once
// the real packed model exists the range has to be recomputed against it;
// otherwise the render window describes a geometry that never existed and the
// grid paints its cards outside the viewport until a scroll recomputes it.

const VIEWPORT_H = 600;
const CONTAINER_W = 1000;

function makeHarness() {
  const scrollWrapper = ref({
    scrollTop: 0,
    clientHeight: VIEWPORT_H,
    clientWidth: CONTAINER_W,
  });
  const gridContainer = ref({
    clientWidth: CONTAINER_W,
    getBoundingClientRect: () => ({ width: CONTAINER_W }),
  });
  // Grid geometry lives in the store. Size level 3 is the 6-column step, which
  // is the geometry these layout cases are written against.
  const gridStore = useGridStore();
  gridStore.sizeLevel = 3;
  gridStore.thumbnailSize = 256;
  gridStore.compactMode = false;
  gridStore.thumbnailMode = "justified";
  const props = reactive({});
  // The image list starts empty, exactly as it is when ImageGrid mounts.
  const aspectRatios = ref([]);
  const allGridImagesLength = computed(() => aspectRatios.value.length);
  const onVisibleRangeChange = vi.fn();

  const vs = useVirtualScroll(
    scrollWrapper,
    gridContainer,
    props,
    allGridImagesLength,
    {
      onVisibleRangeChange,
      getAspectRatios: () => aspectRatios.value,
    },
  );
  return { vs, aspectRatios, onVisibleRangeChange, scrollWrapper };
}

describe("useVirtualScroll justified layout", () => {
  it("recalculates the visible range when the layout packs for the first time", async () => {
    const { vs, aspectRatios, onVisibleRangeChange } = makeHarness();

    // Mount-time measure: width is known, but no images have arrived, so the
    // layout cannot pack and the range collapses to an empty window.
    vs.updateRowHeightFromGrid();
    expect(vs.visibleEnd.value).toBe(0);

    // The first batch lands: real aspect ratios, so the model packs. No scroll
    // happens, because the user has not touched the grid.
    aspectRatios.value = Array.from({ length: 120 }, () => 1.5);
    await nextTick();

    const autoEnd = vs.visibleEnd.value;

    // Ground truth: what the range is when computed against the packed model.
    onVisibleRangeChange.mockClear();
    vs.recalculateVisibleRange();
    const trueEnd = vs.visibleEnd.value;

    expect(trueEnd).toBeGreaterThan(0);
    expect(
      autoEnd,
      "visible range is stale after the justified layout packed: the grid " +
        "renders a window computed for a geometry that never existed, and only " +
        "a scroll repairs it",
    ).toBe(trueEnd);
  });

  it("recalculates the visible range when a repack moves every row boundary", async () => {
    const { vs, aspectRatios } = makeHarness();

    vs.updateRowHeightFromGrid();
    // Placeholder rows: ImageGrid seeds allGridImages with id-less entries whose
    // aspect ratio defaults to 1, so the first pack is all squares.
    aspectRatios.value = Array.from({ length: 120 }, () => 1);
    await nextTick();
    const squareEnd = vs.visibleEnd.value;
    expect(squareEnd).toBeGreaterThan(0);

    // Real ratios splice in: much wider images pack fewer rows into the same
    // viewport, so the visible item count must change with them.
    aspectRatios.value = Array.from({ length: 120 }, () => 3.2);
    await nextTick();
    const autoEnd = vs.visibleEnd.value;

    vs.recalculateVisibleRange();
    expect(autoEnd, "visible range is stale after a repack").toBe(
      vs.visibleEnd.value,
    );
    expect(autoEnd).not.toBe(squareEnd);
  });
});
