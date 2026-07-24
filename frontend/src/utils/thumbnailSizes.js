// Canonical thumbnail-size ladder shared by the square and justified layouts.
//
// A single user-facing "size" (a level 0..6) maps to a representative column
// count for the square grid AND a target row height for the justified layout,
// so one control drives both modes. The backend persists the chosen level as
// `thumbnail_size_level` (see the 0082 migration, which backfills existing
// users from their old `columns` value). This table is the frontend source of
// truth and MUST stay in sync with the backend backfill mapping (the `columns`
// value per level).
export const THUMBNAIL_SIZE_STEPS = [
  { key: "tiny", label: "Tiny", columns: 12, rowHeight: 150 },
  { key: "very_small", label: "Very Small", columns: 9, rowHeight: 180 },
  { key: "small", label: "Small", columns: 6, rowHeight: 210 },
  { key: "medium", label: "Medium", columns: 4, rowHeight: 245 },
  { key: "large", label: "Large", columns: 3, rowHeight: 285 },
  { key: "very_large", label: "Very Large", columns: 2, rowHeight: 330 },
  { key: "huge", label: "Huge", columns: 1, rowHeight: 375 },
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
