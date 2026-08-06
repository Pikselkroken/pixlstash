/**
 * Turning install rows into the three numbers the telemetry plan asks for.
 *
 * Computed in JS rather than SQL on purpose. The bit-range arithmetic for
 * "was this install active in week N of its own life" is unreadable in SQLite,
 * and the persisted accumulator lets a bounded scheduled invocation resume at
 * the next install id instead of trying to hold or scan the whole table.
 *
 * **Compute daily, never backfill.** Life-week N is only answerable while the
 * relevant bits are still inside the 63-day window, so each day's cells are
 * computed while they exist and stored immutably. A month of missed crons is a
 * month of permanently missing cells, not a month to be reconstructed later.
 */

import {
  ACTIVITY_BITS,
  daysBetween,
  decodeActivity,
  weekStart,
} from "./activity.js";

/**
 * Smallest cohort we will publish a percentage for.
 *
 * Two jobs at once: a percentage over three installs is noise, and it caps how
 * far a poisoner can move a published cell by minting a handful of fabricated
 * ids into an otherwise empty week.
 */
export const MIN_COHORT = 20;

/** An install is "active" if it pinged inside this window. */
export const ACTIVE_WINDOW_DAYS = 28;

/** A silence of at least this many days, later broken, counts as a return. */
export const RESURRECTION_GAP_DAYS = 14;

const MASK = (1n << BigInt(ACTIVITY_BITS)) - 1n;

/**
 * Test whether any bit in the inclusive day-ago range [lo, hi] is set.
 *
 * @param {bigint} bits Bitmap relative to the install's last_seen.
 * @param {number} lo Low day-ago bound.
 * @param {number} hi High day-ago bound.
 * @returns {boolean}
 */
function anyBitInRange(bits, lo, hi) {
  const from = Math.max(0, lo);
  const to = Math.min(ACTIVITY_BITS - 1, hi);
  if (from > to) return false;
  const span = BigInt(to - from + 1);
  const window = ((1n << span) - 1n) << BigInt(from);
  return (bits & window) !== 0n;
}

/**
 * Was an install active during week N of its own life?
 *
 * Life-week N covers days first_seen+7N .. first_seen+7N+6. The bitmap is
 * relative to last_seen, so the range has to be re-expressed in days-before-
 * last_seen before it can be tested.
 *
 * @param {{first_seen: string, last_seen: string, activity: bigint|number|string}} row
 * @param {number} week 0-based life week.
 * @param {string} today UTC aggregation date, YYYY-MM-DD.
 * @returns {boolean|null} null when the week has aged out of the window, or has
 *   not fully elapsed yet, and therefore cannot be answered either way.
 */
export function activeInLifeWeek(row, week, today) {
  const bits = decodeActivity(row.activity) & MASK;
  const ageToday = daysBetween(row.first_seen, today);
  const weekEnd = week * 7 + 6;
  if (ageToday < weekEnd) return null;

  const ageAtLastSeen = daysBetween(row.first_seen, row.last_seen);
  // Days-before-last_seen that correspond to this life week.
  const hi = ageAtLastSeen - week * 7;
  const lo = hi - 6;
  // The week has elapsed, but this install stopped pinging before it began.
  // That is an inactive answer, not an unknown one; excluding it would remove
  // churned installs from the denominator and inflate retention.
  if (hi < 0) return false;
  if (lo > ACTIVITY_BITS - 1) return null; // aged out; unanswerable
  return anyBitInRange(bits, lo, hi);
}

/**
 * True if the bitmap shows a silence of RESURRECTION_GAP_DAYS or more that was
 * later broken by a ping.
 *
 * This is the pause-versus-churn answer. first_seen and last_seen alone give a
 * decay curve and cannot distinguish the two.
 *
 * @param {bigint|number} activity
 * @returns {boolean}
 */
export function hasResurrected(activity) {
  const bits = decodeActivity(activity) & MASK;
  if (bits === 0n) return false;

  // Start at the OLDEST set bit, not at the top of the window. The zero bits
  // above it are days before this install existed, or days that have aged out
  // of the bitmap. Counting them as silence would mark every young install as
  // resurrected the moment it pinged.
  let oldest = ACTIVITY_BITS - 1;
  while (oldest >= 0 && ((bits >> BigInt(oldest)) & 1n) === 0n) oldest--;

  // Walk oldest to newest: a gap only counts once a later ping closes it, so an
  // install that simply went quiet and stayed quiet is churned, not resurrected.
  let run = 0;
  for (let b = oldest - 1; b >= 0; b--) {
    if (((bits >> BigInt(b)) & 1n) === 0n) {
      run += 1;
      continue;
    }
    if (run >= RESURRECTION_GAP_DAYS) return true;
    run = 0;
  }
  return false;
}

/**
 * Build the day's publishable aggregate.
 *
 * @param {Array<{install_id: string, first_seen: string, last_seen: string,
 *   activity: bigint|number|string, has_resurrected: number,
 *   is_new_install: number, install_type: string}>} rows
 * @param {string} today UTC date, YYYY-MM-DD.
 * @returns {object} JSON-safe aggregate. Contains counts and percentages only:
 *   no ids, no dates finer than a week, nothing per-install.
 */
export function createAccumulator() {
  return {
    active: 0,
    byType: { docker: 0, pip: 0, electron: 0, other: 0 },
    newLast7d: 0,
    resurrectionEligible: 0,
    resurrected: 0,
    // cohortWeek -> { size, cells: [{answerable, active}, ...] }
    cohorts: new Map(),
  };
}

/** Encode the Map-based accumulator as checkpoint-safe JSON. */
export function serializeAccumulator(state) {
  return JSON.stringify({
    active: state.active,
    byType: state.byType,
    newLast7d: state.newLast7d,
    resurrectionEligible: state.resurrectionEligible,
    resurrected: state.resurrected,
    cohorts: [...state.cohorts.entries()],
  });
}

/** Restore an accumulator written by serializeAccumulator. */
export function deserializeAccumulator(serialized) {
  const value = JSON.parse(serialized);
  return { ...value, cohorts: new Map(value.cohorts ?? []) };
}

/**
 * Fold one install row into the accumulator.
 *
 * Memory scales with the number of COHORTS (bounded by the retention window),
 * never with the number of rows. That is what lets the ingestion ceiling be a
 * storage question rather than a "will the daily job OOM" question.
 *
 * @param {object} state From createAccumulator.
 * @param {{first_seen: string, last_seen: string, activity: bigint|number|string, has_resurrected: number, is_new_install: number, install_type: string}} row One install row.
 * @param {string} today UTC date.
 */
export function accumulateRow(state, row, today) {
  if (daysBetween(row.last_seen, today) <= ACTIVE_WINDOW_DAYS) {
    state.active += 1;
    if (state.byType[row.install_type] === undefined) {
      state.byType[row.install_type] = 0;
    }
    state.byType[row.install_type] += 1;
  }

  if (daysBetween(row.first_seen, today) >= RESURRECTION_GAP_DAYS) {
    state.resurrectionEligible += 1;
    if (row.has_resurrected) state.resurrected += 1;
  }

  if (!row.is_new_install) return;

  if (daysBetween(row.first_seen, today) < 7) state.newLast7d += 1;

  // Only genuinely new installs are cohorted. Mixing the upgrade wave in would
  // put users who have been around for months into "week 1".
  const key = weekStart(row.first_seen);
  let cohort = state.cohorts.get(key);
  if (!cohort) {
    cohort = { size: 0, cells: [] };
    state.cohorts.set(key, cohort);
  }
  cohort.size += 1;

  const maxWeek = Math.ceil(ACTIVITY_BITS / 7);
  for (let week = 0; week < maxWeek; week++) {
    const answer = activeInLifeWeek(row, week, today);
    if (answer === null) continue;
    if (!cohort.cells[week]) cohort.cells[week] = { answerable: 0, active: 0 };
    cohort.cells[week].answerable += 1;
    if (answer) cohort.cells[week].active += 1;
  }
}

/**
 * Turn an accumulator into the day's publishable aggregate.
 *
 * @param {object} state
 * @param {string} today
 * @returns {object} Counts and percentages only: no ids, no dates finer than a
 *   week, nothing per-install.
 */
export function finalizeAggregate(state, today) {
  const retention = {};
  let suppressed = 0;
  for (const [cohortWeek, cohort] of state.cohorts) {
    if (cohort.size < MIN_COHORT) {
      suppressed += 1;
      continue;
    }
    const cells = [];
    for (const cell of cohort.cells) {
      // A percentage over only the still-answerable subset is biased: rows
      // whose bitmap has aged out are not a random sample. The daily snapshots
      // preserve each cell when all cohort members can still be evaluated, so
      // omit it rather than later recomputing it from a partial denominator.
      if (!cell || cell.answerable !== cohort.size) break;
      cells.push(Math.round((cell.active / cell.answerable) * 1000) / 10);
    }
    if (cells.length) retention[cohortWeek] = cells;
  }

  return {
    date: today,
    active_installs: state.active,
    active_installs_by_type: state.byType,
    new_installs_last_7d: state.newLast7d,
    // Null rather than 0 when there is nothing to divide by: a rate of "0%"
    // and "we cannot say yet" are different claims and must not look alike.
    resurrection_rate: state.resurrectionEligible
      ? Math.round((state.resurrected / state.resurrectionEligible) * 1000) / 10
      : null,
    cohort_retention: retention,
    // Consumers need to know a suppressed cell is suppressed, not empty.
    min_cohort_size: MIN_COHORT,
    suppressed_cohorts: suppressed,
  };
}

/**
 * Convenience wrapper for callers that already hold every row.
 *
 * @param {Array<object>} rows
 * @param {string} today
 * @returns {object}
 */
export function buildAggregate(rows, today) {
  const state = createAccumulator();
  for (const row of rows) accumulateRow(state, row, today);
  return finalizeAggregate(state, today);
}
