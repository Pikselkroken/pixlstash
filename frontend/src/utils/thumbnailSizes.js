// Canonical thumbnail-size ladder shared by the square and justified layouts.
//
// A single user-facing "size" (a level 0..6) maps to a representative column
// count for the square grid AND a target row height for the justified layout,
// so one control drives both modes. The backend persists the chosen level as
// `thumbnail_size_level` (see the 0082 migration, which backfills existing
// users from their old `columns` value). This table is the frontend source of
// truth and MUST stay in sync with the backend backfill mapping (the `columns`
// value per level).
// Column counts step DOWN gently toward the large end (…6,5,4,3) and never
// reach 1–2, where a square tile would balloon to a half- or full-width image.
// The perceptual jump between few-column layouts is large (tile width scales as
// 1/columns), so the steps shrink (2,2,2,1,1,1) rather than grow as the tiles
// get bigger. Justified row heights are a separate, smoother scale.
// `stripHeight` is the third consumer of the same ladder: the duplicate
// queue's candidate strip, where the pictures sit in a row beside the group's
// facts rather than in a grid of their own. Its numbers are a third scale
// again, and a smaller one, because a triage row is read a screenful at a
// time. The whole scale was raised 75% (owner call, 2026-07-30): every level
// drew its copies too small to judge a duplicate by, which is the one thing
// the strip exists for. The ratios between the levels are unchanged, so the
// control still steps the way it did.
export const THUMBNAIL_SIZE_STEPS = [
  { key: "tiny", label: "Tiny", columns: 12, rowHeight: 150, stripHeight: 112 },
  {
    key: "very_small",
    label: "Very Small",
    columns: 10,
    rowHeight: 180,
    stripHeight: 140,
  },
  {
    key: "small",
    label: "Small",
    columns: 8,
    rowHeight: 210,
    stripHeight: 168,
  },
  {
    key: "medium",
    label: "Medium",
    columns: 6,
    rowHeight: 245,
    stripHeight: 196,
  },
  {
    key: "large",
    label: "Large",
    columns: 5,
    rowHeight: 285,
    stripHeight: 252,
  },
  {
    key: "very_large",
    label: "Very Large",
    columns: 4,
    rowHeight: 330,
    stripHeight: 322,
  },
  { key: "huge", label: "Huge", columns: 3, rowHeight: 375, stripHeight: 406 },
];

export const DEFAULT_THUMBNAIL_SIZE_LEVEL = 3; // Medium
export const MIN_THUMBNAIL_SIZE_LEVEL = 0;
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
