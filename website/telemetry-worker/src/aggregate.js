/**
 * Turning install rows into the three numbers the telemetry plan asks for.
 *
 * Computed in JS rather than SQL on purpose. The bit-range arithmetic for
 * "was this install active in week N of its own life" is unreadable in SQLite,
 * and at PixlStash's scale the whole table fits in one Worker invocation: even
 * 10,000 installs is well under a megabyte.
 *
 * **Compute daily, never backfill.** Life-week N is only answerable while the
 * relevant bits are still inside the 63-day window, so each day's cells are
 * computed while they exist and stored immutably. A month of missed crons is a
 * month of permanently missing cells, not a month to be reconstructed later.
 */

import { ACTIVITY_BITS, daysBetween, weekStart } from "./activity.js";

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
 * @param {{first_seen: string, last_seen: string, activity: bigint|number}} row
 * @param {number} week 0-based life week.
 * @returns {boolean|null} null when the week has aged out of the window, or has
 *   not happened yet, and therefore cannot be answered either way.
 */
export function activeInLifeWeek(row, week) {
  const bits = BigInt(row.activity) & MASK;
  const ageAtLastSeen = daysBetween(row.first_seen, row.last_seen);
  // Days-before-last_seen that correspond to this life week.
  const hi = ageAtLastSeen - week * 7;
  const lo = hi - 6;
  if (hi < 0) return null; // the week is in the future for this install
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
  const bits = BigInt(activity) & MASK;
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
 *   activity: number, is_new_install: number, install_type: string}>} rows
 * @param {string} today UTC date, YYYY-MM-DD.
 * @returns {object} JSON-safe aggregate. Contains counts and percentages only:
 *   no ids, no dates finer than a week, nothing per-install.
 */
export function buildAggregate(rows, today) {
  const active = rows.filter(
    (r) => daysBetween(r.last_seen, today) <= ACTIVE_WINDOW_DAYS,
  );

  const byType = {};
  for (const type of ["docker", "pip", "electron", "other"]) byType[type] = 0;
  for (const row of active) {
    if (byType[row.install_type] === undefined) byType[row.install_type] = 0;
    byType[row.install_type] += 1;
  }

  // Cohorts are install-weeks, and only genuinely new installs are cohorted.
  // Mixing the upgrade wave in would put users who have been around for months
  // into "week 1" and make early retention meaningless.
  const cohorts = new Map();
  for (const row of rows) {
    if (!row.is_new_install) continue;
    const key = weekStart(row.first_seen);
    if (!cohorts.has(key)) cohorts.set(key, []);
    cohorts.get(key).push(row);
  }

  const retention = {};
  for (const [cohortWeek, members] of cohorts) {
    if (members.length < MIN_COHORT) continue; // suppressed, not zero
    const cells = [];
    for (let week = 0; week < Math.ceil(ACTIVITY_BITS / 7); week++) {
      const answers = members.map((m) => activeInLifeWeek(m, week));
      const answerable = answers.filter((a) => a !== null);
      if (answerable.length < MIN_COHORT) break;
      const activeCount = answerable.filter(Boolean).length;
      cells.push(Math.round((activeCount / answerable.length) * 1000) / 10);
    }
    if (cells.length) retention[cohortWeek] = cells;
  }

  const eligible = rows.filter(
    (r) => daysBetween(r.first_seen, today) >= RESURRECTION_GAP_DAYS,
  );
  const resurrected = eligible.filter((r) => hasResurrected(r.activity));

  return {
    date: today,
    active_installs: active.length,
    active_installs_by_type: byType,
    new_installs_last_7d: rows.filter(
      (r) => r.is_new_install && daysBetween(r.first_seen, today) < 7,
    ).length,
    // Null rather than 0 when there is nothing to divide by: a rate of "0%"
    // and "we cannot say yet" are different claims and must not look alike.
    resurrection_rate: eligible.length
      ? Math.round((resurrected.length / eligible.length) * 1000) / 10
      : null,
    cohort_retention: retention,
    // Consumers need to know a suppressed cell is suppressed, not empty.
    min_cohort_size: MIN_COHORT,
    suppressed_cohorts: [...cohorts.values()].filter(
      (m) => m.length < MIN_COHORT,
    ).length,
  };
}
