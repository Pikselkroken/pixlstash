// Server-level configuration resource — GET/PATCH /server-config/*.
//
// Server config is server-wide (persisted to server-config.json), not per-user,
// and is exposed as one small endpoint per topic rather than a single blob:
// `/server-config/snapshots`, `/server-config/watch-folders`, and — new in
// v1.8.0 — `/server-config/scrapheap-retention`.
//
// Per the §src/api rules the URL strings live only here, so a contract change
// is a one-line edit rather than a hunt through components and stores.

import { apiClient } from "../utils/apiClient";

/** The scrapheap-retention topic of the server config (GET + PATCH). */
const SCRAPHEAP_RETENTION_URL = "/server-config/scrapheap-retention";

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
  const res = await apiClient.get(SCRAPHEAP_RETENTION_URL);
  return res.data;
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
  const res = await apiClient.patch(SCRAPHEAP_RETENTION_URL, {
    [SCRAPHEAP_RETENTION_FIELD]: days,
  });
  return res.data;
}
