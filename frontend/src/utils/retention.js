// Scrapheap auto-purge retention helpers.
//
// Pure functions and constants only (no Vue, no network), so the settings
// control, the scrapheap header, and the grid tiles all speak one vocabulary.
//
// IMPORTANT - the grace math lives on the server. The backend stamps every
// scrapheap picture with an absolute `purge_at` timestamp that already accounts
// for the grace period applied when the retention window is shortened (a FLOOR
// measured from the reduction itself: after a lowering nothing is purgeable for
// one more day, however old it is),
// and leaves it `null` for pictures that will never be auto-purged (retention
// "Never", or a protected reference-folder original). The frontend therefore
// only ever formats the distance to that timestamp; it must never derive a
// purge date from the retention window itself.

/** Retention windows the UI offers, in days. `null` ("Never") is not in here. */
export const RETENTION_DAY_OPTIONS = [30, 60, 90, 120];

/**
 * Backend default when the server has never been configured: `null` ("Never"),
 * i.e. auto-empty is OFF until the user turns it on. Mirrors
 * `scrapheap_service.DEFAULT_RETENTION_DAYS`. Also the fallback for a value we
 * cannot read, which is the safe direction: showing "Never" understates what a
 * server might do, whereas showing a window we invented would promise a
 * countdown nothing is running.
 */
export const DEFAULT_RETENTION_DAYS = null;

/** The `<select>` value that stands for the `null` ("Never") retention. */
export const NEVER_SELECT_VALUE = "never";

const MS_PER_DAY = 86_400_000;

/**
 * Coerce a server-supplied retention value into `number | null`.
 *
 * `null` means "Never" and is preserved. A missing/blank/unparseable value is
 * treated as "the server did not tell us" and falls back to `fallback`.
 *
 * @param {number|string|null|undefined} value - raw `scrapheap_retention_days`.
 * @param {number|null} [fallback] - value to use when `value` is unusable.
 * @returns {number|null} a positive whole number of days, or `null` for Never.
 */
export function normalizeRetentionDays(
  value,
  fallback = DEFAULT_RETENTION_DAYS,
) {
  if (value === null) return null;
  if (value === undefined || value === "") return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.round(parsed);
}

/**
 * Human label for a retention window.
 * @param {number|null} days - retention in days, or `null` for Never.
 * @returns {string} e.g. `"30 days"`, `"1 day"`, `"Never"`.
 */
export function retentionLabel(days) {
  if (days === null || days === undefined) return "Never";
  return days === 1 ? "1 day" : `${days} days`;
}

/**
 * Map a retention value to the string a native `<select>` carries.
 * @param {number|null} days
 * @returns {string}
 */
export function retentionToSelectValue(days) {
  return days === null || days === undefined
    ? NEVER_SELECT_VALUE
    : String(days);
}

/**
 * Map a native `<select>` string back to a retention value.
 * @param {string} value
 * @returns {number|null} `null` for Never.
 */
export function selectValueToRetention(value) {
  if (value === NEVER_SELECT_VALUE || value === null || value === undefined) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed) : null;
}

/**
 * Build the `{ label, value }` option list for the retention select.
 *
 * The server declares which windows it accepts (`scrapheap_retention_choices`),
 * so that list wins over the local default when present. A server configured
 * out-of-band to some other positive value must still display truthfully rather
 * than silently snapping to a neighbour - so an unrecognised current value is
 * inserted in sorted order.
 *
 * @param {number|null} current - the currently configured retention.
 * @param {number[]} [choices] - day values the server accepts, ascending.
 * @returns {{label: string, value: string}[]}
 */
export function retentionSelectOptions(
  current,
  choices = RETENTION_DAY_OPTIONS,
) {
  const offered = (Array.isArray(choices) ? choices : [])
    .map((d) => Number(d))
    .filter((d) => Number.isFinite(d) && d > 0);
  const days = offered.length
    ? [...new Set(offered)]
    : [...RETENTION_DAY_OPTIONS];
  days.sort((a, b) => a - b);
  if (
    typeof current === "number" &&
    Number.isFinite(current) &&
    current > 0 &&
    !days.includes(current)
  ) {
    days.push(current);
    days.sort((a, b) => a - b);
  }
  return [
    ...days.map((d) => ({ label: retentionLabel(d), value: String(d) })),
    { label: "Never", value: NEVER_SELECT_VALUE },
  ];
}

/**
 * Parse a backend timestamp, treating a naive ISO string as UTC.
 *
 * Mirrors the quirk handled in `utils/snapshots.js`: the backend emits either a
 * naive ISO string (UTC, no suffix) or one that already carries `Z` / a numeric
 * offset. Blindly appending `Z` to the latter yields `+00:00Z`, which `Date`
 * rejects.
 *
 * @param {string|null|undefined} isoStr
 * @returns {number|null} epoch milliseconds, or `null` when unparseable.
 */
export function parseServerTimestamp(isoStr) {
  if (!isoStr || typeof isoStr !== "string") return null;
  const normalized =
    isoStr.includes("T") &&
    !isoStr.endsWith("Z") &&
    !/[+-]\d{2}:\d{2}$/.test(isoStr)
      ? `${isoStr}Z`
      : isoStr;
  const ms = new Date(normalized).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Whole days remaining until the server's `purge_at` timestamp.
 *
 * Rounds up, so a picture purging in six hours still reads "1 day left" rather
 * than "0" - and clamps at zero for a timestamp that has already passed but
 * whose sweep has not run yet.
 *
 * @param {string|null|undefined} purgeAt - the server's `purge_at` (ISO 8601).
 * @param {number} [now] - epoch ms; injectable for tests.
 * @returns {number|null} whole days remaining, or `null` when there is no
 *   scheduled purge (Never / protected / unparseable).
 */
export function daysUntilPurge(purgeAt, now = Date.now()) {
  const ms = parseServerTimestamp(purgeAt);
  if (ms === null) return null;
  return Math.max(0, Math.ceil((ms - now) / MS_PER_DAY));
}

/**
 * Countdown label for a scrapheap tile.
 * @param {number|null} days - output of {@link daysUntilPurge}.
 * @returns {string} `""` when there is nothing to count down to.
 */
export function purgeCountdownLabel(days) {
  if (days === null || days === undefined) return "";
  if (days === 0) return "Purges today";
  return days === 1 ? "1 day left" : `${days} days left`;
}

// ── Exemption reasons ───────────────────────────────────────────────────────
// A scrapheap picture can be off the auto-purge clock for two different
// reasons, and they are not interchangeable to the user: one is permanent and
// intrinsic (it is their original file on disk), the other is a state they
// control (the set is locked, and unlocking it puts the picture back on the
// clock). Labelling both "Protected" tells the second user nothing actionable.

/** Reference-folder original: the auto-purge never destroys the user's file. */
export const EXEMPT_REASON_PROTECTED = "protected";

/** Frozen by a locked picture set (including via a live stack sibling). */
export const EXEMPT_REASON_LOCKED = "locked";

/** Badge copy for a reference-folder original. */
export const PROTECTED_BADGE_LABEL = "Protected";

/** Full explanation, used as the protected badge's accessible label/tooltip. */
export const PROTECTED_BADGE_TITLE = "Protected - won't auto-delete";

/** Badge copy for a picture frozen by a locked set. */
export const LOCKED_BADGE_LABEL = "Locked set";

/**
 * Full explanation for the locked badge. Names the *cause* (the set lock) and
 * the lever (unlocking it), so the state does not read as unexplained.
 */
export const LOCKED_BADGE_TITLE =
  "In a locked set - won't auto-delete. Unlock the set to put it back on the auto-empty clock.";

// ── Lowering the window is destructive ──────────────────────────────────────
// Raising the window, or switching to Never, only ever spares pictures. LOWERING
// it makes pictures eligible for permanent, unrecoverable deletion - and the
// biggest jump (Never → 30) is the one that reads most like a harmless dropdown
// pick. So a reduction is confirmed; every other direction stays one click.
//
// Since "Never" is now the shipped default, that biggest jump is exactly what
// TURNING AUTO-EMPTY ON looks like. It is a reduction here, so switching it on
// goes through the same impact check and confirm as shortening a live window.

/**
 * Order retention windows by how much they keep. "Never" keeps everything, so
 * it ranks above every finite window.
 *
 * @param {number|null} days - retention in days, or `null` for Never.
 * @returns {number} comparable magnitude; `Infinity` for Never.
 */
export function retentionRank(days) {
  if (days === null || days === undefined) return Infinity;
  const parsed = Number(days);
  return Number.isFinite(parsed) ? parsed : Infinity;
}

/**
 * Is moving from `previous` to `next` a REDUCTION - i.e. does it expose
 * pictures to auto-deletion that were previously safe?
 *
 * @param {number|null} previous - the currently saved window.
 * @param {number|null} next - the window the user just picked.
 * @param {{previousKnown?: boolean}} [options] - `previousKnown: false` (the
 *   saved value has not loaded yet) is never treated as a reduction: we cannot
 *   claim a direction we do not know, and confirming against a guessed baseline
 *   would be its own lie.
 * @returns {boolean}
 */
export function isRetentionReduction(
  previous,
  next,
  { previousKnown = true } = {},
) {
  if (!previousKnown) return false;
  return retentionRank(next) < retentionRank(previous);
}

/** The consequence, stated the same way wherever a reduction is confirmed. */
export const RETENTION_PURGE_WARNING =
  "Files are removed from disk and cannot be restored from a snapshot.";

/**
 * Copy for the "you are about to shorten the window" confirm.
 *
 * Two shapes, because an unverified reduction must never be presented as a
 * verified one (the same fail-safe the scrapheap delete-preview follows: never
 * schedule destruction on a basis we could not check):
 *   - `verified: true`  → states the exact count and when deletion starts;
 *   - `verified: false` → says plainly that the impact could not be checked and
 *     that nothing has changed yet, leaving the user to proceed deliberately.
 *
 * @param {Object} options
 * @param {number|null} options.nextDays - the window being switched to.
 * @param {number} [options.wouldPurgeCount] - server's `would_purge_count`. A
 *   value that is absent or not a number is treated as UNVERIFIED, never as
 *   zero - see below.
 * @param {string|null} [options.firstPurgeAt] - server's `first_purge_at` (ISO).
 * @param {(iso: string) => string} [options.formatDate] - renders that instant.
 * @param {boolean} [options.verified=true] - was the impact successfully read?
 * @returns {{title: string, body: string, warning: string, confirmLabel: string}|null}
 *   `null` when a verified check found nothing would be deleted - a reduction
 *   that destroys nothing needs no confirmation.
 */
export function buildRetentionReductionMessage({
  nextDays,
  wouldPurgeCount,
  firstPurgeAt = null,
  formatDate = (iso) => iso,
  verified = true,
} = {}) {
  const target = retentionLabel(nextDays);
  const confirmLabel = `Change to ${target}`;

  // FAIL SAFE, NOT FAIL OPEN. `null` from this function means "save without
  // asking", so an unreadable count must never collapse into 0 - a 200 whose
  // body is missing or garbled would then silently schedule deletion.
  //
  // The check is on the TYPE, not on `Number(...)`: JS coercion maps `null`,
  // `""` and even `[]` to 0, which is precisely the wrong answer here. Only a
  // real number or a non-blank numeric string counts as a reading. (There is
  // also deliberately no default for `wouldPurgeCount`.)
  const isCountLike =
    typeof wouldPurgeCount === "number" ||
    (typeof wouldPurgeCount === "string" && wouldPurgeCount.trim() !== "");
  const parsedCount = isCountLike ? Number(wouldPurgeCount) : NaN;
  const countUsable = Number.isFinite(parsedCount) && parsedCount >= 0;

  if (!verified || !countUsable) {
    return {
      title: "Couldn't check what this would delete",
      body:
        `Switching to ${target} makes older scrapheap pictures eligible for ` +
        `permanent deletion, but the impact couldn't be checked just now. ` +
        `Nothing has been changed yet.`,
      warning: RETENTION_PURGE_WARNING,
      confirmLabel: "Change anyway",
    };
  }

  const count = Math.floor(parsedCount);
  // A reduction that would delete nothing is not a destructive act. Save it.
  if (count === 0) return null;

  const noun = count === 1 ? "picture" : "pictures";
  // Deletion starts when the reduction grace elapses, NOT on save. Say so - the
  // difference is the user's window to change their mind.
  const when = firstPurgeAt
    ? `, starting ${formatDate(firstPurgeAt)}`
    : " once the grace period ends";
  return {
    title: "Shorten the auto-empty window?",
    body: `${target} will permanently delete ${count} ${noun}${when}.`,
    warning: RETENTION_PURGE_WARNING,
    confirmLabel,
  };
}

/**
 * Resolve why a scrapheap picture is exempt from the auto-purge, if it is.
 *
 * DEFENSIVE ORDERING: `auto_purge_exempt_reason` is newer than
 * `auto_purge_exempt`. A server that sends the boolean but not the reason (or
 * sends a reason this build does not know) falls back to "protected" - the
 * pre-existing behaviour - so a version skew degrades to the old labelling
 * rather than dropping the badge or mislabelling an exempt picture as expiring.
 *
 * @param {Object|null|undefined} picture - a scrapheap grid picture.
 * @returns {"protected"|"locked"|null} the reason, or `null` when not exempt.
 */
export function resolveExemptReason(picture) {
  if (!picture) return null;
  const reason = picture.auto_purge_exempt_reason;
  if (reason === EXEMPT_REASON_LOCKED) return EXEMPT_REASON_LOCKED;
  if (reason === EXEMPT_REASON_PROTECTED) return EXEMPT_REASON_PROTECTED;
  // Explicitly not exempt: trust the reason over a stale/absent boolean.
  if (reason === null && !picture.auto_purge_exempt) return null;
  return picture.auto_purge_exempt ? EXEMPT_REASON_PROTECTED : null;
}

/**
 * Build the auto-purge badge descriptor for one scrapheap picture.
 *
 * Three mutually exclusive states, each carried by an icon AND text (never
 * colour alone):
 *   - exempt/protected → shield + "Protected"
 *   - exempt/locked    → lock + "Locked set"
 *   - otherwise        → countdown to the server's `purge_at`
 *
 * The purge date is never derived here: `purge_at` is authoritative and already
 * carries the server's grace period.
 *
 * @param {Object} picture - a scrapheap grid picture.
 * @param {Object} [options]
 * @param {number} [options.now] - epoch ms; injectable for tests.
 * @param {(iso: string) => string} [options.formatDate] - renders the exact
 *   purge date for the countdown title. Defaults to the raw ISO string.
 * @returns {{kind: string, icon: string, label: string, title: string}|null}
 *   `null` when the picture has no auto-purge state to show.
 */
export function buildPurgeBadge(picture, options = {}) {
  if (!picture) return null;
  const { now = Date.now(), formatDate = (iso) => iso } = options;

  const reason = resolveExemptReason(picture);
  if (reason === EXEMPT_REASON_LOCKED) {
    return {
      kind: "locked",
      icon: "mdi-lock-outline",
      label: LOCKED_BADGE_LABEL,
      title: LOCKED_BADGE_TITLE,
    };
  }
  if (reason === EXEMPT_REASON_PROTECTED) {
    return {
      kind: "protected",
      icon: "mdi-shield-check-outline",
      label: PROTECTED_BADGE_LABEL,
      title: PROTECTED_BADGE_TITLE,
    };
  }

  const days = daysUntilPurge(picture.purge_at, now);
  // No `purge_at` means nothing is scheduled (retention "Never") - no badge.
  if (days === null) return null;
  return {
    // The last day gets an emphasised variant; the label carries the meaning
    // either way (see the badge styles in ImageGrid.vue).
    kind: days <= 1 ? "countdown-urgent" : "countdown",
    icon: "mdi-delete-clock-outline",
    label: purgeCountdownLabel(days),
    title:
      days === 0
        ? "Auto-deletes today"
        : `Auto-deletes ${formatDate(picture.purge_at)}`,
  };
}
