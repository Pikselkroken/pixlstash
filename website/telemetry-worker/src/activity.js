/**
 * The rolling activity bitmap: one fixed-width 17-character field per install,
 * no event log.
 *
 * Bit N set means "this install pinged N days before its last_seen date". That
 * is what separates a pause from a churn, which first_seen/last_seen alone
 * cannot do: a decay curve tells you an install went quiet, not whether it came
 * back.
 *
 * The bitmap is stored **relative to last_seen and shifted lazily on write**,
 * not shifted daily by a cron sweep. Two reasons: a sweep is a full-table UPDATE
 * every day for a number that only changes when a row is touched, and a missed
 * or double-run sweep silently corrupts every row's history with no way to
 * detect it afterwards. Lazy shifting is idempotent and self-correcting.
 *
 * The established window is 63 bits: nine weeks of daily resolution covers
 * day-30 retention and the >=14-day resurrection question with room to spare.
 */

/** Usable history, in days. */
export const ACTIVITY_BITS = 63;

const MASK = (1n << BigInt(ACTIVITY_BITS)) - 1n;

/**
 * D1 cannot bind JavaScript BigInt values, and reading a 64-bit INTEGER through
 * the Workers API narrows it to a Number. Store the bitmap as a fixed-width,
 * prefixed hex string instead. The non-numeric `h` prefix also prevents an
 * older INTEGER-affinity schema from coercing an all-decimal bitmap back into
 * an imprecise INTEGER.
 */
const ENCODED_ACTIVITY = /^h([0-9a-f]{16})$/;

/** Decode either the durable string form or a legacy in-memory numeric value. */
export function decodeActivity(activity) {
  if (typeof activity === "string") {
    const match = ENCODED_ACTIVITY.exec(activity);
    if (match) return BigInt(`0x${match[1]}`) & MASK;
  }
  return BigInt(activity) & MASK;
}

/** Return the D1-safe fixed-width representation of an activity bitmap. */
export function encodeActivity(activity) {
  return `h${decodeActivity(activity).toString(16).padStart(16, "0")}`;
}

/**
 * Roll an activity bitmap forward and record a ping on the new current day.
 *
 * @param {bigint|number} activity Stored bitmap, relative to the old last_seen.
 * @param {number} daysElapsed Whole days between the old last_seen and today.
 *   Negative values (a clock-skewed or replayed ping dated before last_seen)
 *   record the ping without rolling, so history is never rewritten backwards.
 * @returns {bigint} The bitmap relative to today, with bit 0 set.
 */
export function rollActivity(activity, daysElapsed) {
  let bits = decodeActivity(activity);
  if (daysElapsed >= ACTIVITY_BITS) {
    // Everything we knew has aged out of the window.
    return 1n;
  }
  if (daysElapsed > 0) {
    bits = (bits << BigInt(daysElapsed)) & MASK;
  }
  return bits | 1n;
}

/**
 * Whole days from one UTC date string to another.
 *
 * @param {string} fromDate `YYYY-MM-DD`.
 * @param {string} toDate `YYYY-MM-DD`.
 * @returns {number} Difference in days; negative if toDate precedes fromDate.
 */
export function daysBetween(fromDate, toDate) {
  const from = Date.parse(`${fromDate}T00:00:00Z`);
  const to = Date.parse(`${toDate}T00:00:00Z`);
  return Math.round((to - from) / 86400000);
}

/**
 * True if the install pinged at least once in a given week of its bitmap.
 *
 * Weekly buckets, not daily points: only Docker installs ping every day.
 * Desktop and pip installs ping when someone runs them, so a weekend-only user
 * misses an exact day-7 check and reads as churned when they are not.
 *
 * @param {bigint|number} activity Bitmap relative to today.
 * @param {number} week 0 = the last 7 days, 1 = the 7 before that, and so on.
 * @returns {boolean}
 */
export function activeInWeek(activity, week) {
  const start = week * 7;
  if (start >= ACTIVITY_BITS) return false;
  const span = Math.min(7, ACTIVITY_BITS - start);
  const window = ((1n << BigInt(span)) - 1n) << BigInt(start);
  return (decodeActivity(activity) & window) !== 0n;
}

/**
 * The ISO week-start (Monday) a date falls in, as `YYYY-MM-DD`.
 *
 * Cohorts are install-weeks, so every install that arrived in the same week
 * shares a bucket regardless of which day it landed on.
 *
 * @param {string} date `YYYY-MM-DD`.
 * @returns {string} `YYYY-MM-DD` of that week's Monday.
 */
export function weekStart(date) {
  const ms = Date.parse(`${date}T00:00:00Z`);
  const dow = new Date(ms).getUTCDay(); // 0 = Sunday
  const backToMonday = (dow + 6) % 7;
  return new Date(ms - backToMonday * 86400000).toISOString().slice(0, 10);
}
