import { describe, it, expect } from "vitest";
import {
  packJustifiedRows,
  rowAtOffset,
  rowOfIndex,
  itemCenterX,
  verticalNeighborIndex,
  normalizeAspectRatio,
  JUSTIFIED_TARGET_ROW_HEIGHT,
  JUSTIFIED_ROW_GAP,
  JUSTIFIED_MIN_ROW_HEIGHT,
} from "./useJustifiedLayout";

/** Sum of the widths (+ inter-item gaps) of row r. */
function rowPixelWidth(layout, r, gap) {
  const { rowStarts, itemScaledWidths } = layout;
  let sum = 0;
  for (let i = rowStarts[r]; i < rowStarts[r + 1]; i++) {
    sum += itemScaledWidths[i];
  }
  return sum + gap * (rowStarts[r + 1] - rowStarts[r] - 1);
}

function rowCount(layout) {
  return layout.rowStarts.length - 1;
}

describe("packJustifiedRows", () => {
  const base = {
    containerWidth: 1000,
    targetRowHeight: 240,
    gap: 4,
    rowExtraHeight: 0,
  };

  it("returns an empty layout (with sentinel) for empty input", () => {
    const layout = packJustifiedRows({ ...base, aspectRatios: [] });
    expect(layout.rowStarts).toEqual([0]);
    expect(layout.rowHeights).toEqual([]);
    expect(layout.rowOffsets).toEqual([]);
    expect(layout.itemScaledWidths).toEqual([]);
    expect(layout.totalHeight).toBe(0);
  });

  it("returns an empty layout for a zero-width container", () => {
    const layout = packJustifiedRows({
      ...base,
      containerWidth: 0,
      aspectRatios: [1, 1],
    });
    expect(layout.rowStarts).toEqual([0]);
    expect(layout.totalHeight).toBe(0);
  });

  it("forces a wide panorama that opens a row onto its own row", () => {
    // ar 8 at target 240 is 1920px wide - wider than the container alone.
    const layout = packJustifiedRows({
      ...base,
      aspectRatios: [8, 1, 1, 1],
    });
    expect(layout.rowStarts[0]).toBe(0);
    expect(layout.rowStarts[1]).toBe(1); // panorama alone in row 0
    // Scaled to exactly the container width, never beyond it.
    expect(layout.itemScaledWidths[0]).toBe(1000);
    // Exact-fill height (1000 / 8 = 125) clamps up to the minimum row height.
    expect(layout.rowHeights[0]).toBe(JUSTIFIED_MIN_ROW_HEIGHT);
  });

  it("packs a mix into multiple rows, each full row filling the width exactly", () => {
    const aspectRatios = [1.5, 1, 0.75, 1.33, 1, 1.5, 0.66, 1.2, 0.9];
    const layout = packJustifiedRows({ ...base, aspectRatios });
    const rows = rowCount(layout);
    expect(rows).toBeGreaterThan(1);
    // Every row except the last fills the container width exactly.
    for (let r = 0; r < rows - 1; r++) {
      expect(rowPixelWidth(layout, r, base.gap)).toBe(base.containerWidth);
      // Scaling down to fit means full-row heights never exceed the target.
      expect(layout.rowHeights[r]).toBeLessThanOrEqual(base.targetRowHeight);
      expect(layout.rowHeights[r]).toBeGreaterThanOrEqual(
        JUSTIFIED_MIN_ROW_HEIGHT,
      );
    }
    // Sentinel covers all items.
    expect(layout.rowStarts[rows]).toBe(aspectRatios.length);
    // Offsets are the cumulative sums of heights + gaps.
    for (let r = 1; r < rows; r++) {
      expect(layout.rowOffsets[r]).toBe(
        layout.rowOffsets[r - 1] + layout.rowHeights[r - 1] + base.gap,
      );
    }
    expect(layout.totalHeight).toBe(
      layout.rowOffsets[rows - 1] + layout.rowHeights[rows - 1],
    );
  });

  it("does not stretch the last (incomplete) row", () => {
    // Two squares can never fill 1000px at 240 target: single, final row.
    const layout = packJustifiedRows({ ...base, aspectRatios: [1, 1] });
    expect(rowCount(layout)).toBe(1);
    // Natural scale at the target height, not ballooned to fill the row.
    expect(layout.itemScaledWidths[0]).toBe(240);
    expect(layout.itemScaledWidths[1]).toBe(240);
    expect(layout.rowHeights[0]).toBe(base.targetRowHeight);
    expect(rowPixelWidth(layout, 0, base.gap)).toBeLessThan(
      base.containerWidth,
    );
  });

  it("keeps the last row unstretched after full rows too", () => {
    const aspectRatios = [1.5, 1.5, 1.5, 0.8]; // 3 wides fill a row, 1 leftover
    const layout = packJustifiedRows({ ...base, aspectRatios });
    const rows = rowCount(layout);
    const lastStart = layout.rowStarts[rows - 1];
    expect(lastStart).toBe(3);
    expect(layout.itemScaledWidths[3]).toBe(Math.floor(0.8 * 240));
    expect(layout.rowHeights[rows - 1]).toBe(base.targetRowHeight);
  });

  it("falls back to square (aspect 1) for missing or zero dimensions", () => {
    const layout = packJustifiedRows({
      ...base,
      aspectRatios: [NaN, 0, -2, Infinity, undefined],
    });
    // All five behave as squares: 240px wide each in a single last row
    // (5 * 240 + 4 * 4 = 1216 > 1000, so the first four close a full row).
    for (const w of layout.itemScaledWidths) {
      expect(w).toBeGreaterThan(0);
      expect(Number.isInteger(w)).toBe(true);
    }
    const same = packJustifiedRows({
      ...base,
      aspectRatios: [1, 1, 1, 1, 1],
    });
    expect(layout).toEqual(same);
  });

  it("includes rowExtraHeight in rowHeights and offsets", () => {
    const withInfo = packJustifiedRows({
      ...base,
      aspectRatios: [1, 1],
      rowExtraHeight: 24,
    });
    const without = packJustifiedRows({ ...base, aspectRatios: [1, 1] });
    expect(withInfo.rowHeights[0]).toBe(without.rowHeights[0] + 24);
    expect(withInfo.totalHeight).toBe(without.totalHeight + 24);
    // Widths are unaffected by the non-scaling addition.
    expect(withInfo.itemScaledWidths).toEqual(without.itemScaledWidths);
  });

  it("is deterministic: identical inputs give identical output", () => {
    const aspectRatios = Array.from(
      { length: 500 },
      (_, i) => 0.5 + ((i * 7919) % 100) / 50,
    );
    const a = packJustifiedRows({ ...base, aspectRatios });
    const b = packJustifiedRows({ ...base, aspectRatios });
    expect(a).toEqual(b);
  });

  it("handles a large input in O(N) without width drift", () => {
    const aspectRatios = Array.from(
      { length: 20000 },
      (_, i) => 0.4 + ((i * 31) % 40) / 16,
    );
    const layout = packJustifiedRows({ ...base, aspectRatios });
    const rows = rowCount(layout);
    for (let r = 0; r < rows - 1; r++) {
      expect(rowPixelWidth(layout, r, base.gap)).toBe(base.containerWidth);
    }
    expect(layout.rowStarts[rows]).toBe(aspectRatios.length);
  });

  it("uses the exported defaults when optional knobs are omitted", () => {
    const layout = packJustifiedRows({
      aspectRatios: [1],
      containerWidth: 2000,
    });
    expect(layout.itemScaledWidths[0]).toBe(JUSTIFIED_TARGET_ROW_HEIGHT);
    expect(layout.rowHeights[0]).toBe(JUSTIFIED_TARGET_ROW_HEIGHT);
    // JUSTIFIED_ROW_GAP is the default gap: verify via a two-row layout.
    const two = packJustifiedRows({
      aspectRatios: [8, 1],
      containerWidth: 1000,
    });
    expect(two.rowOffsets[1]).toBe(
      two.rowOffsets[0] + two.rowHeights[0] + JUSTIFIED_ROW_GAP,
    );
  });
});

describe("rowOfIndex", () => {
  const layout = packJustifiedRows({
    aspectRatios: [8, 1, 1, 1, 8, 1],
    containerWidth: 1000,
    targetRowHeight: 240,
    gap: 4,
    rowExtraHeight: 0,
  });
  // Rows: [0], [1..3 closes? 3*240+2*4 = 728 < 1000, then ar 8 closes with them]…
  // Derive expectations from the layout itself so the test tracks the packing.

  it("maps every index to the row whose slice contains it", () => {
    const rows = rowCount(layout);
    for (let r = 0; r < rows; r++) {
      for (let i = layout.rowStarts[r]; i < layout.rowStarts[r + 1]; i++) {
        expect(rowOfIndex(layout.rowStarts, i)).toBe(r);
      }
    }
  });

  it("is exact at row boundaries", () => {
    const rows = rowCount(layout);
    for (let r = 1; r < rows; r++) {
      const boundary = layout.rowStarts[r];
      expect(rowOfIndex(layout.rowStarts, boundary)).toBe(r);
      expect(rowOfIndex(layout.rowStarts, boundary - 1)).toBe(r - 1);
    }
  });

  it("clamps out-of-range indices", () => {
    const rows = rowCount(layout);
    expect(rowOfIndex(layout.rowStarts, -5)).toBe(0);
    expect(rowOfIndex(layout.rowStarts, 9999)).toBe(rows - 1);
    expect(rowOfIndex([0], 3)).toBe(0); // empty layout (sentinel only)
  });
});

describe("rowAtOffset", () => {
  const gap = 4;
  const layout = packJustifiedRows({
    aspectRatios: [8, 8, 8, 1],
    containerWidth: 1000,
    targetRowHeight: 240,
    gap,
    rowExtraHeight: 24,
  });
  // Three full panorama rows + one last row → 4 rows with known offsets.

  it("is exact at row-top boundaries", () => {
    const rows = rowCount(layout);
    for (let r = 0; r < rows; r++) {
      expect(rowAtOffset(layout.rowOffsets, layout.rowOffsets[r])).toBe(r);
      if (r > 0) {
        expect(rowAtOffset(layout.rowOffsets, layout.rowOffsets[r] - 1)).toBe(
          r - 1,
        );
      }
    }
  });

  it("maps a y inside the inter-row gap to the row above", () => {
    // Bottom edge of row 0 sits gap px above rowOffsets[1].
    const inGap = layout.rowOffsets[1] - gap + 1;
    expect(rowAtOffset(layout.rowOffsets, inGap)).toBe(0);
  });

  it("clamps y outside the layout", () => {
    const rows = rowCount(layout);
    expect(rowAtOffset(layout.rowOffsets, -10)).toBe(0);
    expect(rowAtOffset(layout.rowOffsets, layout.totalHeight + 500)).toBe(
      rows - 1,
    );
    expect(rowAtOffset([], 100)).toBe(0);
  });
});

describe("itemCenterX / verticalNeighborIndex", () => {
  // Hand-built layout with known geometry (gap 4):
  //   row 0: widths [100, 300, 100] → centers 50, 254, 458
  //   row 1: widths [400, 104]      → centers 200, 456
  //   row 2: widths [100, 100, 100, 100] → centers 50, 154, 258, 362
  const gap = 4;
  const layout = {
    rowStarts: [0, 3, 5, 9],
    rowHeights: [240, 240, 240],
    rowOffsets: [0, 244, 488],
    itemScaledWidths: [100, 300, 100, 400, 104, 100, 100, 100, 100],
    totalHeight: 728,
  };

  it("computes horizontal centers with gaps included", () => {
    expect(itemCenterX(layout, gap, 0)).toBe(50);
    expect(itemCenterX(layout, gap, 1)).toBe(254);
    expect(itemCenterX(layout, gap, 2)).toBe(458);
    expect(itemCenterX(layout, gap, 3)).toBe(200);
    expect(itemCenterX(layout, gap, 4)).toBe(456);
  });

  it("moves Down to the nearest-center item across a row boundary", () => {
    // center 254 → row 1 candidates at 200 (idx 3) and 456 (idx 4)
    expect(verticalNeighborIndex(layout, gap, 1, 1)).toBe(3);
    // center 458 → nearest is 456 (idx 4)
    expect(verticalNeighborIndex(layout, gap, 2, 1)).toBe(4);
    // center 200 → row 2 candidates 50/154/258/362 → 154 (idx 6)
    expect(verticalNeighborIndex(layout, gap, 3, 1)).toBe(6);
  });

  it("moves Up to the nearest-center item across a row boundary", () => {
    // center 456 → row 0 candidates 50/254/458 → 458 (idx 2)
    expect(verticalNeighborIndex(layout, gap, 4, -1)).toBe(2);
    // center 50 (idx 5 in row 2) → row 1 candidates 200/456 → 200 (idx 3)
    expect(verticalNeighborIndex(layout, gap, 5, -1)).toBe(3);
  });

  it("clamps at the first and last row like the uniform grid", () => {
    // Up from any item of row 0 lands on item 0.
    expect(verticalNeighborIndex(layout, gap, 1, -1)).toBe(0);
    expect(verticalNeighborIndex(layout, gap, 2, -1)).toBe(0);
    // Down from any item of the last row lands on the last item.
    expect(verticalNeighborIndex(layout, gap, 5, 1)).toBe(8);
    expect(verticalNeighborIndex(layout, gap, 8, 1)).toBe(8);
  });

  it("supports multi-row deltas (visual-row paging)", () => {
    // Two rows down from idx 0 (center 50) → row 2 center 50 (idx 5).
    expect(verticalNeighborIndex(layout, gap, 0, 2)).toBe(5);
    // Two rows up is the mirror.
    expect(verticalNeighborIndex(layout, gap, 5, -2)).toBe(0);
    // Overshooting clamps to the ends.
    expect(verticalNeighborIndex(layout, gap, 4, 5)).toBe(8);
    expect(verticalNeighborIndex(layout, gap, 4, -5)).toBe(0);
  });

  it("breaks center ties toward the lower index", () => {
    // Current center 100; target row centers 50 and 150 - equidistant.
    const tie = {
      rowStarts: [0, 1, 3],
      rowHeights: [240, 240],
      rowOffsets: [0, 244],
      itemScaledWidths: [200, 100, 92],
      totalHeight: 484,
    };
    expect(itemCenterX(tie, gap, 1)).toBe(50);
    expect(itemCenterX(tie, gap, 2)).toBe(150);
    expect(verticalNeighborIndex(tie, gap, 0, 1)).toBe(1);
  });

  it("agrees with rowOfIndex on a packed layout", () => {
    const packed = packJustifiedRows({
      aspectRatios: [1.5, 1, 0.75, 1.33, 1, 1.5, 0.66, 1.2, 0.9, 1, 1, 1],
      containerWidth: 900,
      targetRowHeight: 240,
      gap,
    });
    const rows = rowCount(packed);
    expect(rows).toBeGreaterThan(1);
    for (let i = 0; i < packed.itemScaledWidths.length; i++) {
      const row = rowOfIndex(packed.rowStarts, i);
      if (row + 1 < rows) {
        const down = verticalNeighborIndex(packed, gap, i, 1);
        // Destination sits exactly one visual row below.
        expect(rowOfIndex(packed.rowStarts, down)).toBe(row + 1);
      }
    }
  });
});

describe("normalizeAspectRatio", () => {
  it("passes through valid ratios and squares invalid ones", () => {
    expect(normalizeAspectRatio(1.5)).toBe(1.5);
    expect(normalizeAspectRatio(0.2)).toBe(0.2);
    expect(normalizeAspectRatio(0)).toBe(1);
    expect(normalizeAspectRatio(-3)).toBe(1);
    expect(normalizeAspectRatio(NaN)).toBe(1);
    expect(normalizeAspectRatio(Infinity)).toBe(1);
    expect(normalizeAspectRatio(undefined)).toBe(1);
  });
});
