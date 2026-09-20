import { describe, it, expect, vi } from "vitest";
import { ref } from "vue";
import { setActivePinia, createPinia } from "pinia";

import { useStackOrdering } from "./useStackOrdering.js";
import { useGridStore } from "../stores/useGridStore";

vi.mock("../api/stacks", () => ({
  getStack: vi.fn(),
  listStackPictures: vi.fn(() => Promise.resolve([])),
  createStack: vi.fn(),
  setStackOrder: vi.fn(),
  removeStackMembers: vi.fn(),
}));

/**
 * Six pictures, of which #2, #3 and #4 are one stack whose cover is #2. With
 * four columns the cover lands in column 2 of the first row.
 */
function fixture() {
  return [
    { id: 1, idx: 0 },
    { id: 2, idx: 1, stack_id: 7, stack_position: 0, stack_count: 3 },
    { id: 3, idx: 2, stack_id: 7, stack_position: 1, stack_count: 3 },
    { id: 4, idx: 3, stack_id: 7, stack_position: 2, stack_count: 3 },
    { id: 5, idx: 4 },
    { id: 6, idx: 5 },
  ];
}

async function build(columns) {
  setActivePinia(createPinia());
  const gridStore = useGridStore();
  // `columns` is derived from the size level and clamped down to maxColumns,
  // so this is how a test pins it.
  gridStore.sizeLevel = 0;
  gridStore.maxColumns = columns;
  const allGridImages = ref([]);
  const lastFetchedGridImages = ref(fixture());
  const stack = useStackOrdering(
    {
      allGridImages,
      lastFetchedGridImages,
      loadedRanges: ref([]),
      visibleStart: ref(0),
      visibleEnd: ref(6),
      renderBuffer: ref(0),
      divisibleViewWindow: ref(columns),
      stackReorderDrag: ref(null),
      stackReorderHoverId: ref(null),
      stackReorderHoverSide: ref(null),
      setStackReorderHoverId: () => {},
      setStackReorderHoverSide: () => {},
      selectedImageIds: ref([]),
      preserveScrollOnNextFetch: ref(false),
    },
    {},
    vi.fn(),
    {
      invalidateVisibleThumbnailRanges: () => {},
      updateVisibleThumbnails: () => {},
      debouncedFetchAllGridImages: Object.assign(() => {}, {
        cancel: () => {},
      }),
      fetchThumbnailsForRangeNow: () => {},
      maybeRefreshThumbnailsForRange: () => {},
      markVisibleFetchSuppressedForExpand: () => {},
      clearSelection: () => {},
      getPendingRanges: () => [],
      setPendingRanges: () => {},
    },
  );
  // `collapseStackImages` renders an open stack from the cached member list,
  // which the real flow fills on expand. The fixture's rows are local, so this
  // caches them without touching the API.
  await stack.ensureStackMembersLoaded("7", 3);
  return { stack, gridStore };
}

// The tray is a rectangle of whole grid rows, and it can only be one if the
// members start where the cover's ROW ends. Spliced directly after the cover
// they begin in whatever cell happens to be free, and the panel drawn around
// them is not a rectangle any more.
describe("an open stack's members start at the next row boundary", () => {
  it("leaves the cover's row to the pictures that shared it", async () => {
    const { stack } = await build(4);
    stack.expandedStackId.value = "7";
    const ids = stack.collapseStackImages(fixture()).map((img) => img.id);
    // Row 1: 1, cover 2, then the pictures that were already on it. Row 2: the
    // two other members of the stack.
    expect(ids).toEqual([1, 2, 5, 6, 3, 4]);
  });

  it("follows the column count", async () => {
    const { stack } = await build(2);
    stack.expandedStackId.value = "7";
    const ids = stack.collapseStackImages(fixture()).map((img) => img.id);
    // Two columns: the cover completes row 1 by itself.
    expect(ids).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("keeps the members behind their cover in justified mode", async () => {
    const { stack, gridStore } = await build(4);
    gridStore.thumbnailMode = "justified";
    stack.expandedStackId.value = "7";
    const ids = stack.collapseStackImages(fixture()).map((img) => img.id);
    // Justified rows are packed by aspect ratio, so there is no row boundary
    // to aim at and no tray drawn; the members follow the cover as before.
    expect(ids).toEqual([1, 2, 3, 4, 5, 6]);
  });
});

// One stack is open at a time. The state is a single id rather than a set, so
// a second open tray is unrepresentable rather than merely avoided.
describe("only one stack is open at a time", () => {
  it("reports the open stack as a one-element set, and none as empty", async () => {
    const { stack } = await build(4);
    expect([...stack.expandedStackIds.value]).toEqual([]);
    stack.expandedStackId.value = "7";
    expect([...stack.expandedStackIds.value]).toEqual(["7"]);
    stack.expandedStackId.value = "9";
    expect([...stack.expandedStackIds.value]).toEqual(["9"]);
  });

  it("drops the open stack when the fetched page no longer holds it", async () => {
    const { stack } = await build(4);
    stack.expandedStackId.value = "7";
    stack.pruneExpandedStackIfGone();
    expect(stack.expandedStackId.value).toBe("7");
    stack.expandedStackId.value = "99";
    stack.pruneExpandedStackIfGone();
    expect(stack.expandedStackId.value).toBe(null);
  });
});

// No colour is spent on the group any more: the tray carries it by containment,
// and a full-area tint over a tile is what selection is.
describe("the tray spends no colour", () => {
  it("drops the wash and the ribbon an expanded member used to wear", async () => {
    const { stack } = await build(4);
    stack.expandedStackId.value = "7";
    const member = { id: 3, stack_id: 7, stack_index: 0, stack_position: 1 };
    expect(stack.getStackCardStyle(member)).toEqual({});
    expect(stack.getStackBandStyle(member)).toBe(null);
  });

  it("keeps them in justified mode, which draws no tray", async () => {
    const { stack, gridStore } = await build(4);
    gridStore.thumbnailMode = "justified";
    stack.expandedStackId.value = "7";
    const member = { id: 3, stack_id: 7, stack_index: 0, stack_position: 1 };
    expect(stack.getStackCardStyle(member).backgroundColor).toBeTruthy();
    expect(stack.getStackBandStyle(member).borderBottom).toBeTruthy();
  });
});
