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
    { id: 7, idx: 6, stack_id: 8, stack_position: 0, stack_count: 2 },
    { id: 8, idx: 7, stack_id: 8, stack_position: 1, stack_count: 2 },
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
  const loadedRanges = ref([]);
  const stack = useStackOrdering(
    {
      allGridImages,
      lastFetchedGridImages,
      loadedRanges,
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
  await stack.ensureStackMembersLoaded("8", 2);
  return { stack, allGridImages, loadedRanges, gridStore };
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
    // two other members of the stack. The second stack stays collapsed.
    expect(ids).toEqual([1, 2, 5, 6, 3, 4, 7]);
  });

  it("follows the column count", async () => {
    const { stack } = await build(3);
    stack.expandedStackId.value = "7";
    const ids = stack.collapseStackImages(fixture()).map((img) => img.id);
    // Three columns: the cover's row ends after 5, so only 5 joins it. A
    // different answer from the four-column case above, and from the naive
    // "straight after the cover" order either of them replaced.
    expect(ids).toEqual([1, 2, 5, 3, 4, 6, 7]);
  });

  it("keeps the members behind their cover in justified mode", async () => {
    const { stack, gridStore } = await build(4);
    gridStore.thumbnailMode = "justified";
    stack.expandedStackId.value = "7";
    const ids = stack.collapseStackImages(fixture()).map((img) => img.id);
    // Justified rows are packed by aspect ratio, so there is no row boundary
    // to aim at and no tray drawn; the members follow the cover as before.
    expect(ids).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });
});

// One stack is open at a time. The state is a single id rather than a set, so
// a second open tray is unrepresentable rather than merely avoided.
describe("only one stack is open at a time", () => {
  it("closes the open stack when another is opened, through the click path", async () => {
    const { stack, allGridImages } = await build(4);
    stack.rebuildGridImagesFromLastFetch();
    const cover = (id) =>
      allGridImages.value.find((img) => String(img.stack_id) === id);

    await stack.toggleStackExpand(cover("7"));
    expect(stack.expandedStackId.value).toBe("7");
    const memberIds = (id) =>
      allGridImages.value
        .filter((img) => String(img.stack_id) === id)
        .map((img) => img.id);
    expect(memberIds("7")).toEqual([2, 3, 4]);
    expect(memberIds("8")).toEqual([7]);

    // Opening the second closes the first: its members leave the grid, and
    // the state cannot hold both because it is one id.
    await stack.toggleStackExpand(cover("8"));
    expect(stack.expandedStackId.value).toBe("8");
    expect([...stack.expandedStackIds.value]).toEqual(["8"]);
    expect(memberIds("7")).toEqual([2]);
    expect(memberIds("8")).toEqual([7, 8]);

    // Clicking the open one closes it and leaves nothing open.
    await stack.toggleStackExpand(cover("8"));
    expect(stack.expandedStackId.value).toBe(null);
    expect([...stack.expandedStackIds.value]).toEqual([]);
    expect(memberIds("8")).toEqual([7]);
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

// Collapsing a tray removes its members from the grid, and the loaded-thumbnail
// window has to be shifted by where they actually WERE. Under the tray they are
// not at `cover + 1` any more, so an end still derived from the cover describes
// a different span from the start: `shiftRangesForDelta` then shifts a range it
// should have dropped, and the grid goes on believing it has thumbnails for
// indices now holding different pictures.
describe("collapsing a tray shifts the loaded window by where the members were", () => {
  it("drops a range that covered them instead of sliding it somewhere wrong", async () => {
    const { stack, allGridImages, loadedRanges } = await build(6);
    stack.rebuildGridImagesFromLastFetch();
    const cover = allGridImages.value.find(
      (img) => String(img.stack_id) === "7",
    );

    await stack.toggleStackExpand(cover);
    // Five leaders, then the two other members at the end - this stack's row
    // is the last one, so the splice clamps to the end of the list.
    expect(allGridImages.value.map((img) => img.id)).toEqual([
      1, 2, 5, 6, 7, 3, 4,
    ]);

    // [0, 2) sits entirely above the members and must survive untouched;
    // [6, 8) covers one of them and must be dropped, not slid down.
    loadedRanges.value = [
      [0, 2],
      [6, 8],
    ];
    await stack.toggleStackExpand(cover);

    expect(stack.expandedStackId.value).toBe(null);
    expect(loadedRanges.value).toEqual([[0, 2]]);
  });
});
