// Canonical thumbnail-size ladder shared by the square and justified layouts.
//
// A single user-facing "size" (a level 0..6) maps to a representative column
// count for the square grid AND a target row height for the justified layout,
// so one control drives both modes. The backend persists the chosen level as
// `thumbnail_size_level` (see the 0082 migration). This table is the frontend
// source of truth for what a level MEANS; the 0082 backfill is a frozen,
// one-time translation of the legacy `columns` preference into a level and is
// deliberately NOT kept in step with later re-scalings of this ladder.
// Column counts step DOWN gently toward the large end and never reach 1–2,
// where a square tile would balloon to a half- or full-width image. The
// perceptual jump between few-column layouts is large (tile width scales as
// 1/columns), so the steps shrink rather than grow as the tiles get bigger.
// Justified row heights are a separate, smoother scale.
// Both grid scales were made STEEPER (owner call, 2026-08-05): the large end
// was right but the small end never got small enough, so `huge` is pinned where
// it was and every level below it steps down harder - a constant ratio of about
// 1.20 per notch on row height (was ~1.17) and the matching ~0.77 on square tile
// width.
// Every image-grid level (columns, rowHeight) was then cut a further 25%
// across the board (owner call, 2026-09-03): thumbnails still read too large
// once a window got wide. Columns scaled up ~1.33x, rowHeight down 0.75x, so
// `tiny`'s 19 columns is the ceiling `MAX_COLUMNS` in `useViewportLayout.js`
// allows and `MIN_THUMBNAIL_SIZE`/`MAX_THUMBNAIL_SIZE` there and in
// `useVirtualScroll.js` were cut 25% too; going denser than that means raising
// those caps further. `stripHeight` was left alone - it is the third consumer
// of the same ladder: the duplicate
// queue's candidate strip, where the pictures sit in a row beside the group's
// facts rather than in a grid of their own. Its numbers are a third scale
// again, and a smaller one, because a triage row is read a screenful at a
// time. The whole scale was raised 75% (owner call, 2026-07-30): every level
// drew its copies too small to judge a duplicate by, which is the one thing
// the strip exists for. The ratios between the levels are unchanged, so the
// control still steps the way it did.
const THUMBNAIL_SIZE_STEPS = [
  { key: "tiny", label: "Tiny", columns: 19, rowHeight: 96, stripHeight: 112 },
  {
    key: "very_small",
    label: "Very Small",
    columns: 15,
    rowHeight: 115,
    stripHeight: 140,
  },
  {
    key: "small",
    label: "Small",
    columns: 11,
    rowHeight: 137,
    stripHeight: 168,
  },
  {
    key: "medium",
    label: "Medium",
    columns: 8,
    rowHeight: 164,
    stripHeight: 196,
  },
  {
    key: "large",
    label: "Large",
    columns: 7,
    rowHeight: 197,
    stripHeight: 252,
  },
  {
    key: "very_large",
    label: "Very Large",
    columns: 5,
    rowHeight: 235,
    stripHeight: 322,
  },
  { key: "huge", label: "Huge", columns: 4, rowHeight: 281, stripHeight: 406 },
];

export const DEFAULT_THUMBNAIL_SIZE_LEVEL = 3; // Medium
const MIN_THUMBNAIL_SIZE_LEVEL = 0;
export const MAX_THUMBNAIL_SIZE_LEVEL = THUMBNAIL_SIZE_STEPS.length - 1;

/** Round and clamp an arbitrary value to a valid size level. */
export function clampSizeLevel(level) {
  const n = Math.round(Number(level));
  if (!Number.isFinite(n)) return DEFAULT_THUMBNAIL_SIZE_LEVEL;
  return Math.min(
    MAX_THUMBNAIL_SIZE_LEVEL,
    Math.max(MIN_THUMBNAIL_SIZE_LEVEL, n),
  );
}

function stepFor(level) {
  return THUMBNAIL_SIZE_STEPS[clampSizeLevel(level)];
}

/** Representative square-grid column count for a size level. */
export function columnsForSizeLevel(level) {
  return stepFor(level).columns;
}

/** Justified-layout target row height (px) for a size level. */
export function rowHeightForSizeLevel(level) {
  return stepFor(level).rowHeight;
}

/** Duplicate-queue candidate-strip thumbnail height (px) for a size level. */
export function stripHeightForSizeLevel(level) {
  return stepFor(level).stripHeight;
}

/** Human-readable label ("Tiny" … "Huge") for a size level. */
export function sizeLabelForLevel(level) {
  return stepFor(level).label;
}

/**
 * Inverse mapping used when falling back from a stored raw column count
 * (legacy configs predating the size ladder). Picks the level whose
 * representative column count is nearest; ties resolve to the larger column
 * count (the finer/smaller tile, i.e. the lower level), matching the backend
 * migration's backfill. The table is ordered by descending column count, so a
 * strict-less-than scan already keeps the larger-column entry on a tie.
 */
export function nearestSizeLevelForColumns(columns) {
  const c = Number(columns);
  if (!Number.isFinite(c)) return DEFAULT_THUMBNAIL_SIZE_LEVEL;
  let bestLevel = DEFAULT_THUMBNAIL_SIZE_LEVEL;
  let bestDist = Infinity;
  for (let i = 0; i < THUMBNAIL_SIZE_STEPS.length; i++) {
    const dist = Math.abs(THUMBNAIL_SIZE_STEPS[i].columns - c);
    if (dist < bestDist) {
      bestDist = dist;
      bestLevel = i;
    }
  }
  return bestLevel;
}
