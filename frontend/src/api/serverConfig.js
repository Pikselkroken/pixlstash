// Server-level configuration resource — GET/PATCH /server-config/*.
//
// Server config is server-wide (persisted to server-config.json), not per-user,
// and is exposed as one small endpoint per topic rather than a single blob:
// `/server-config/snapshots`, `/server-config/watch-folders`, and — new in
// v1.8.0 — `/server-config/scrapheap-retention`.
//
// Per the §src/api rules the URL strings live only here, so a contract change
// is a one-line edit rather than a hunt through components and stores.

import { apiClient} from "../utils/apiClient";
import { unwrap } from "../utils/unwrap";

/** The scrapheap-retention topic of the server config (GET + PATCH). */
const SCRAPHEAP_RETENTION_URL = "/server-config/scrapheap-retention";

/** The snapshots topic of the server config (GET + PATCH). */
const SNAPSHOTS_URL = "/server-config/snapshots";

/**
 * Read the snapshot scheduling configuration.
 *
 * @returns {Promise<Object>} the response body, whose `daily_snapshots` says
 *   whether the server takes an automatic snapshot once a day.
 */
export async function getSnapshotSettings() {
  return unwrap(apiClient.get(SNAPSHOTS_URL));
}

/**
 * Turn the daily automatic snapshot on or off.
 *
 * @param {boolean} enabled
 * @returns {Promise<Object>} the updated snapshot settings (the response body).
 */
export async function setDailySnapshotsEnabled(enabled) {
  return unwrap(apiClient.patch(SNAPSHOTS_URL, {
    daily_snapshots: enabled,
  }));
}

/** Response/request key carrying the retention window. */
export const SCRAPHEAP_RETENTION_FIELD = "scrapheap_retention_days";

/** Response key listing the day values this server accepts (ascending). */
export const SCRAPHEAP_RETENTION_CHOICES_FIELD = "scrapheap_retention_choices";

/**
 * Response key: extra days granted to pictures that were already in the
 * scrapheap when the window was last lowered.
 */
export const SCRAPHEAP_RETENTION_GRACE_FIELD = "scrapheap_retention_grace_days";

/**
 * Read the scrapheap retention configuration.
 *
 * @returns {Promise<Object>} the response body:
 *   - `scrapheap_retention_days`: `30 | 60 | 90 | 120 | null` (null = "Never")
 *   - `scrapheap_retention_choices`: accepted day values, ascending
 *   - `scrapheap_retention_grace_days`: grace granted when the window is lowered
 *   - `scrapheap_retention_reduced_at`: ISO 8601 of the last reduction, or null
 */
export async function getScrapheapRetention() {
  return unwrap(apiClient.get(SCRAPHEAP_RETENTION_URL));
}

/**
 * Update the scrapheap auto-empty retention window.
 *
 * Saving never purges anything: the server applies the change on its next
 * scheduled sweep, and lowering the window grants a grace period first.
 *
 * @param {number|null} days - one of 30 / 60 / 90 / 120, or `null` for "Never".
 * @returns {Promise<Object>} the updated retention config (the response body).
 */
export async function setScrapheapRetentionDays(days) {
  return unwrap(apiClient.patch(SCRAPHEAP_RETENTION_URL, {
    [SCRAPHEAP_RETENTION_FIELD]: days,
  }));
}

/**
 * Ask what SHORTENING the window to `days` would destroy, before saving it.
 *
 * `would_purge_count` already excludes protected and locked pictures (neither is
 * ever auto-purged) and is computed with the same helpers as the sweep, so the
 * number the user confirms is the number that gets deleted. `first_purge_at` is
 * when the reduction grace elapses — deletion starts then, not on save.
 *
 * Rejects on any transport/HTTP failure (including a 404 from a server that has
 * not shipped this endpoint yet). Callers MUST treat a rejection as "could not
 * verify" and confirm deliberately — never as "nothing would be deleted".
 *
 * @param {number} days - the candidate (lower) retention window.
 * @returns {Promise<{would_purge_count: number, first_purge_at: string|null}>}
 */
export async function getScrapheapRetentionImpact(days) {
  return unwrap(apiClient.get(`${SCRAPHEAP_RETENTION_URL}/impact`, {
    params: { days },
  }));
}

/** The PixlStash Views topic of the server config (GET + PATCH). */
const VIEWS_URL = "/server-config/views";

/**
 * Read where this library publishes its PixlStash Views tree, and which kinds.
 *
 * @returns {Promise<Object>} the response body:
 *   - `views_root`: the host folder, or `null` when views are off
 *   - `kinds`: the published subset of `available_kinds`
 *   - `available_kinds`: every kind this server can publish, in display order
 */
export async function getViewsSettings() {
  return unwrap(apiClient.get(VIEWS_URL));
}

/**
 * Save the views folder and kinds, and rebuild the tree.
 *
 * Saving IS rebuilding: the tree is a full re-derive and costs a fraction of a
 * second, so sending the current values is how "Rebuild now" works and there is
 * no separate verb. Pass `root = null` to turn views off, which removes the
 * published tree and leaves the folder itself alone.
 *
 * Rejects with a 400 whose detail names the reason when the folder cannot hold
 * the tree — inside the library, inside a reference folder, cloud-synced, or on
 * a filesystem with no links. The settings are left untouched in that case, so
 * a refused folder never becomes the recorded one.
 *
 * @param {string|null} root - absolute host path, or `null` to turn views off.
 * @param {string[]} kinds - subset of `available_kinds`.
 * @returns {Promise<Object>} the updated settings, plus `last_publish` on a
 *   successful publish: `{link_mode, folders, links, skipped_missing,
 *   skipped_unlinkable}`.
 */
export async function setViewsSettings(root, kinds) {
  return unwrap(apiClient.patch(VIEWS_URL, {
    views_root: root,
    kinds,
  }));
}
