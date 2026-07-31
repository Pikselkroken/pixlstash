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

// The grid geometry now comes from the store rather than from props, and the
// arithmetic that turns it into a render window is silent when it goes wrong:
// an undefined column count makes every bound NaN, `renderEnd` collapses, and
// the grid paints nothing at all. That is not a hypothetical - it is what the
// Phase 3 store-direct migration did to ImageGrid, with the whole unit suite
// still green, because nothing here asserted that a non-empty list produces a
// non-empty window.
describe("useVirtualScroll render window (square mode)", () => {
  function squareHarness(itemCount) {
    const gridStore = useGridStore();
    gridStore.sizeLevel = 3; // the 6-column step
    gridStore.thumbnailMode = "square";
    gridStore.thumbnailSize = 256;
    gridStore.compactMode = false;

    const scrollWrapper = ref({
      scrollTop: 0,
      clientHeight: VIEWPORT_H,
      clientWidth: CONTAINER_W,
    });
    const gridContainer = ref({
      clientWidth: CONTAINER_W,
      getBoundingClientRect: () => ({ width: CONTAINER_W }),
    });
    const length = computed(() => itemCount);
    return useVirtualScroll(
      scrollWrapper,
      gridContainer,
      reactive({}),
      length,
      {
        onVisibleRangeChange: vi.fn(),
        afterRowHeightUpdate: vi.fn(),
        getAspectRatios: () => [],
      },
    );
  }

  it("renders a non-empty window for a non-empty grid", () => {
    const vs = squareHarness(120);
    vs.visibleStart.value = 0;
    vs.visibleEnd.value = 24;

    expect(Number.isFinite(vs.renderStart.value)).toBe(true);
    expect(Number.isFinite(vs.renderEnd.value)).toBe(true);
    expect(vs.renderEnd.value).toBeGreaterThan(0);
    expect(vs.renderEnd.value).toBeGreaterThan(vs.renderStart.value);
  });

  it("keeps the spacers finite, so the scroll height is never NaN", () => {
    const vs = squareHarness(120);
    vs.visibleStart.value = 24;
    vs.visibleEnd.value = 48;

    expect(Number.isFinite(vs.topSpacerHeight.value)).toBe(true);
    expect(Number.isFinite(vs.bottomSpacerHeight.value)).toBe(true);
  });

  it("still yields an empty window for an empty grid", () => {
    const vs = squareHarness(0);
    vs.visibleStart.value = 0;
    vs.visibleEnd.value = 0;

    expect(vs.renderEnd.value).toBe(0);
  });
});
