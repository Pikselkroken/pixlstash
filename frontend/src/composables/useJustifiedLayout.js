// Pure justified-row ("Google Photos" style) packing arithmetic for the image
// grid. No Vue, no DOM: identical inputs always produce identical output, so
// the layout can be recomputed on every resize/fetch without thrashing
// thumbnails, and the virtualizer's index↔row↔pixel maths can be unit-tested
// in isolation.
//
// Model: every row shares one (per-row) height; images keep their aspect ratio
// by varying in width. Rows are packed greedily and then scaled so each FULL
// row spans the container exactly - pixel-exact, so the flex-wrap rendering in
// ImageGrid.vue breaks lines at exactly the boundaries computed here and the
// virtual-scroll spacer arithmetic never drifts from what is painted.

// ── Design constants (provisional - single source for the integrator/designer
//    to retune; keep them here so JS packing and any CSS mirrors can't drift).
/** Preferred content height of a row in px, before the info-row addition. */
export const JUSTIFIED_TARGET_ROW_HEIGHT = 240;
/** Gap between adjacent items and between rows, in px. */
// Justified rows sit tighter than the square grid's 4px - packed variable-width
// thumbnails read as a wall, and 4px looked too airy there. Single source of
// truth: the packing math AND the inline column-/row-gap both read this, so they
// stay equal (the row-break invariant depends on it).
export const JUSTIFIED_ROW_GAP = 2;
/** Lower clamp for a scaled row's content height, in px. */
export const JUSTIFIED_MIN_ROW_HEIGHT = 160;
/** Upper clamp for a scaled row's content height, in px. */
const JUSTIFIED_MAX_ROW_HEIGHT = 360;

/**
 * Normalise an aspect ratio for packing. Missing / zero / negative /
 * non-finite values (unimported pictures, videos without probed dimensions)
 * fall back to 1 (square) so the packing never divides by zero.
 *
 * @param {number} ar - Raw width/height ratio.
 * @returns {number} A finite, positive aspect ratio.
 */
export function normalizeAspectRatio(ar) {
  return Number.isFinite(ar) && ar > 0 ? ar : 1;
}

/**
 * Distribute `availableWidth` px across one row's items proportionally to
 * their aspect ratios, in whole pixels that sum EXACTLY to `availableWidth`
 * (largest-remainder rounding, ties broken by lower index - deterministic).
 * Whole-pixel widths that sum exactly are what makes flex-wrap line breaking
 * agree byte-for-byte with the packed row model.
 *
 * @param {number[]} rowAspects - Normalised aspect ratios of the row's items.
 * @param {number} availableWidth - Integer px to fill (container minus gaps).
 * @returns {number[]} Integer widths (each >= 1) summing to availableWidth.
 */
function distributeRowWidths(rowAspects, availableWidth) {
  let sumAr = 0;
  for (const ar of rowAspects) sumAr += ar;
  const exact = rowAspects.map((ar) => (ar / sumAr) * availableWidth);
  const widths = exact.map(Math.floor);
  let remainder = availableWidth;
  for (const w of widths) remainder -= w;
  // Hand the leftover pixels to the largest fractional parts first.
  const order = exact
    .map((v, i) => [v - widths[i], i])
    .sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (let k = 0; k < order.length && remainder > 0; k++, remainder--) {
    widths[order[k][1]] += 1;
  }
  // Never emit a zero-width item (extreme aspect ratios). This can overfill a
  // pathological row of many sub-pixel slivers by a few px; accepted trade-off
  // for never rendering an invisible, unclickable card.
  return widths.map((w) => Math.max(1, w));
}

/**
 * Pack images into justified rows.
 *
 * Greedy: items accumulate into a row until their natural widths at
 * `targetRowHeight` (plus inter-item gaps) reach `containerWidth`; the row is
 * then closed INCLUDING the overflowing item and scaled down so it spans the
 * container exactly (this lowers the row's height slightly below the target -
 * the Google Photos behaviour). The scaled content height is clamped to
 * [minRowHeight, maxRowHeight]; if the clamp bites (e.g. a row dominated by an
 * extreme panorama), widths are still forced to fill the container exactly and
 * the resulting mild aspect distortion is absorbed by `object-fit: cover`.
 *
 * LAST-ROW RULE: the final, incomplete row is NOT stretched to full width -
 * its items stay at `targetRowHeight` natural scale. Stretching 1–3 leftover
 * images balloons them; leaving them ragged-right matches Google Photos.
 *
 * O(N), pure, and deterministic: no DOM reads, no randomness, no shared state.
 *
 * @param {object} options
 * @param {number[]} options.aspectRatios - width/height per image, in grid
 *   order. Invalid entries fall back to 1 (see {@link normalizeAspectRatio}).
 * @param {number} options.containerWidth - Row width in px. Floored to a whole
 *   px so packed rows never exceed the real (possibly fractional) box.
 * @param {number} [options.targetRowHeight] - Preferred content row height.
 * @param {number} [options.gap] - Px between adjacent items and between rows
 *   (rounded to a whole px).
 * @param {number} [options.rowExtraHeight] - Non-scaling per-row addition in
 *   px (the info row under each thumbnail; 0 in compact mode). Included in
 *   `rowHeights`/`rowOffsets` so spacer arithmetic is exact.
 * @param {number} [options.minRowHeight] - Lower clamp for scaled content height.
 * @param {number} [options.maxRowHeight] - Upper clamp for scaled content height.
 * @returns {{
 *   rowStarts: number[],
 *   rowHeights: number[],
 *   rowOffsets: number[],
 *   itemScaledWidths: number[],
 *   totalHeight: number,
 * }} Where:
 *   - `rowStarts[r]` is the index of the first image in row `r`;
 *     `rowStarts.length === rowCount + 1` - the sentinel
 *     `rowStarts[rowCount] === N` makes `[rowStarts[r], rowStarts[r+1])`
 *     slicing and "one past the last visible row" arithmetic branch-free.
 *   - `rowHeights[r]` is row r's total card height in whole px (scaled
 *     content height + `rowExtraHeight`).
 *   - `rowOffsets[r]` is the pixel y of row r's top:
 *     `rowOffsets[r+1] = rowOffsets[r] + rowHeights[r] + gap`. Exact spacer
 *     maths derive from these, never from `rows * rowHeight`.
 *   - `itemScaledWidths[i]` is image i's display width in whole px. Full rows
 *     sum (with gaps) to exactly `floor(containerWidth)`; the last row does not.
 *   - `totalHeight` is the full scroll height in px (no trailing gap).
 */
export function packJustifiedRows({
  aspectRatios,
  containerWidth,
  targetRowHeight = JUSTIFIED_TARGET_ROW_HEIGHT,
  gap = JUSTIFIED_ROW_GAP,
  rowExtraHeight = 0,
  minRowHeight = JUSTIFIED_MIN_ROW_HEIGHT,
  maxRowHeight = JUSTIFIED_MAX_ROW_HEIGHT,
}) {
  const n = Array.isArray(aspectRatios) ? aspectRatios.length : 0;
  const width = Math.floor(containerWidth || 0);
  const rowGap = Math.max(0, Math.round(gap || 0));
  const empty = {
    rowStarts: [0],
    rowHeights: [],
    rowOffsets: [],
    itemScaledWidths: [],
    totalHeight: 0,
  };
  if (n === 0 || width <= 0 || !(targetRowHeight > 0)) return empty;

  const rowStarts = [];
  const rowHeights = [];
  const rowOffsets = [];
  const itemScaledWidths = new Array(n);

  let y = 0; // Top of the row currently being filled.
  let rowStart = 0;
  let rowAspects = [];
  let naturalWidth = 0; // Sum of natural item widths at targetRowHeight.

  const closeRow = (isFullRow) => {
    const count = rowAspects.length;
    const availableWidth = Math.max(1, width - rowGap * (count - 1));
    let contentHeight;
    let widths;
    if (isFullRow) {
      // Scale the row so it spans the container exactly. naturalWidth >=
      // containerWidth here, so the exact-fill height is <= targetRowHeight.
      let sumAr = 0;
      for (const ar of rowAspects) sumAr += ar;
      contentHeight = Math.round(
        Math.min(maxRowHeight, Math.max(minRowHeight, availableWidth / sumAr)),
      );
      widths = distributeRowWidths(rowAspects, availableWidth);
    } else {
      // LAST-ROW RULE: no stretch - natural widths at the target height,
      // floored so the row can never round up past the container and wrap.
      contentHeight = Math.round(
        Math.min(maxRowHeight, Math.max(minRowHeight, targetRowHeight)),
      );
      widths = rowAspects.map((ar) =>
        Math.min(width, Math.max(1, Math.floor(ar * targetRowHeight))),
      );
    }
    for (let k = 0; k < count; k++) {
      itemScaledWidths[rowStart + k] = widths[k];
    }
    rowStarts.push(rowStart);
    rowOffsets.push(y);
    const rowHeight = contentHeight + rowExtraHeight;
    rowHeights.push(rowHeight);
    y += rowHeight + rowGap;
    rowStart += count;
    rowAspects = [];
    naturalWidth = 0;
  };

  for (let i = 0; i < n; i++) {
    const ar = normalizeAspectRatio(aspectRatios[i]);
    rowAspects.push(ar);
    naturalWidth += ar * targetRowHeight;
    const count = rowAspects.length;
    // Include inter-item gaps when testing against the container.
    if (naturalWidth + rowGap * (count - 1) >= width) {
      // Overflow: close the row at whichever break - keeping this last item or
      // leaving it for the next row - yields a full-row height closest to the
      // target. A plain greedy break (always keep the overflowing item) makes a
      // full row's height a function of its item count alone: two size levels
      // that happen to pack the same count then render identically (the reported
      // "changing size does nothing to the first row"). Biasing toward the
      // target lets the size control actually resize full rows.
      let excludeLast = false;
      if (count > 1) {
        let sumWith = 0;
        for (const a of rowAspects) sumWith += a;
        const hWith = Math.max(1, width - rowGap * (count - 1)) / sumWith;
        const hWithout =
          Math.max(1, width - rowGap * (count - 2)) / (sumWith - ar);
        excludeLast =
          Math.abs(hWithout - targetRowHeight) <
          Math.abs(hWith - targetRowHeight);
      }
      if (excludeLast) {
        // Remove the overflowing item, close the rest as a full row, then seed
        // the next row with it (a lone panorama may itself already overflow).
        rowAspects.pop();
        closeRow(true);
        rowAspects.push(ar);
        naturalWidth = ar * targetRowHeight;
        if (naturalWidth >= width) closeRow(true);
      } else {
        closeRow(true);
      }
    }
  }
  if (rowAspects.length > 0) closeRow(false);

  // Sentinel: rowStarts[rowCount] === n.
  rowStarts.push(n);

  return {
    rowStarts,
    rowHeights,
    rowOffsets,
    itemScaledWidths,
    // y carries a trailing inter-row gap after the final closeRow; drop it.
    totalHeight: rowHeights.length > 0 ? y - rowGap : 0,
  };
}

/**
 * Binary search: which row contains vertical offset `y`?
 * Returns the greatest `r` with `rowOffsets[r] <= y`, clamped to
 * `[0, rowCount - 1]` - a `y` inside the gap below row r (or past the end of
 * the layout) still maps to row r, which is the conservative choice for
 * visible-range calculations.
 *
 * @param {number[]} rowOffsets - `rowOffsets` from {@link packJustifiedRows}.
 * @param {number} y - Vertical pixel offset (e.g. scrollTop).
 * @returns {number} Row index, or 0 for an empty layout.
 */
export function rowAtOffset(rowOffsets, y) {
  const count = rowOffsets.length;
  if (count === 0 || y <= 0) return 0;
  let lo = 0;
  let hi = count - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (rowOffsets[mid] <= y) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/**
 * Horizontal center of item `index` within its row, in px from the row's left
 * edge (widths of the preceding row items + inter-item gaps + half the item's
 * own width). O(row length) - rows are short, so this is cheap enough for
 * keyboard navigation.
 *
 * @param {{rowStarts: number[], itemScaledWidths: number[]}} layout - A layout
 *   from {@link packJustifiedRows}.
 * @param {number} gap - Inter-item gap in px (the `gap` the layout was packed
 *   with).
 * @param {number} index - Global item index (must be within the layout).
 * @returns {number} Center x in px.
 */
export function itemCenterX(layout, gap, index) {
  const row = rowOfIndex(layout.rowStarts, index);
  const rowGap = Math.max(0, Math.round(gap || 0));
  let x = 0;
  for (let i = layout.rowStarts[row]; i < index; i++) {
    x += layout.itemScaledWidths[i] + rowGap;
  }
  return x + layout.itemScaledWidths[index] / 2;
}

/**
 * Geometry-aware vertical navigation: the item `rowDelta` visual rows away
 * from `index` whose horizontal CENTER is nearest the current item's center
 * (ties go to the lower index - deterministic). This is the justified-mode
 * replacement for the uniform grid's `index ± columns` arithmetic, which is
 * meaningless once rows hold varying item counts.
 *
 * Clamping mirrors the uniform path's `Math.max(0, …)` / `Math.min(N-1, …)`
 * semantics: a move that would leave the first row lands on item 0, and one
 * that would leave the last row lands on item N-1.
 *
 * @param {{rowStarts: number[], itemScaledWidths: number[]}} layout - A layout
 *   from {@link packJustifiedRows}.
 * @param {number} gap - Inter-item gap in px (the `gap` the layout was packed
 *   with).
 * @param {number} index - Current global item index (clamped into range).
 * @param {number} rowDelta - Visual rows to move: -1 for Up, +1 for Down,
 *   larger magnitudes for paging.
 * @returns {number} The destination global item index.
 */
export function verticalNeighborIndex(layout, gap, index, rowDelta) {
  const n = layout.itemScaledWidths.length;
  const rowCount = layout.rowStarts.length - 1;
  if (n === 0 || rowCount === 0) return 0;
  const fromIdx = Math.max(0, Math.min(index, n - 1));
  const row = rowOfIndex(layout.rowStarts, fromIdx);
  const targetRow = row + rowDelta;
  if (targetRow < 0) return 0;
  if (targetRow >= rowCount) return n - 1;
  const rowGap = Math.max(0, Math.round(gap || 0));
  const centerX = itemCenterX(layout, rowGap, fromIdx);
  let bestIdx = layout.rowStarts[targetRow];
  let bestDistance = Infinity;
  let x = 0;
  for (
    let i = layout.rowStarts[targetRow];
    i < layout.rowStarts[targetRow + 1];
    i++
  ) {
    const width = layout.itemScaledWidths[i];
    const distance = Math.abs(x + width / 2 - centerX);
    // Strict `<` keeps the lower index on ties (deterministic).
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIdx = i;
    }
    x += width + rowGap;
  }
  return bestIdx;
}

/**
 * Binary search: which row contains item `index`?
 * Returns the greatest `r` with `rowStarts[r] <= index`, clamped to
 * `[0, rowCount - 1]` (the sentinel entry is never returned).
 *
 * @param {number[]} rowStarts - `rowStarts` (with sentinel) from
 *   {@link packJustifiedRows}.
 * @param {number} index - Global item index.
 * @returns {number} Row index, or 0 for an empty layout.
 */
export function rowOfIndex(rowStarts, index) {
  // rowStarts includes the sentinel; the last real row is length - 2.
  const lastRow = rowStarts.length - 2;
  if (lastRow < 0 || index <= 0) return 0;
  let lo = 0;
  let hi = lastRow;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (rowStarts[mid] <= index) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
