// Server-level configuration resource - GET/PATCH /server-config/*.
//
// Server config is server-wide (persisted to server-config.json), not per-user,
// and is exposed as one small endpoint per topic rather than a single blob:
// `/server-config/snapshots`, `/server-config/watch-folders`, and - new in
// v1.8.0 - `/server-config/scrapheap-retention`.
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
 * when the reduction grace elapses - deletion starts then, not on save.
 *
 * Rejects on any transport/HTTP failure (including a 404 from a server that has
 * not shipped this endpoint yet). Callers MUST treat a rejection as "could not
 * verify" and confirm deliberately - never as "nothing would be deleted".
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
 * the tree - inside the library, inside a reference folder, cloud-synced, or on
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

/** The folder-layout topic of the server config (v1.11 Phases 4b and 4c). */
const LAYOUT_URL = "/server-config/layout";

/**
 * Read how this library's own picture root is laid out.
 *
 * @returns {Promise<Object>} the response body:
 *   - `layout`: segments as `project/person,set`, or `null` for no layout,
 *     which is every library until its owner picks one
 *   - `layout_unfiled`: the folder a picture with nothing to file it by goes to
 *   - `default_layout`: what a new library starts on
 */
export async function getLayoutSettings() {
  return unwrap(apiClient.get(LAYOUT_URL));
}

/**
 * Save the layout, the unfiled folder, or both.
 *
 * **This moves no files.** Every path already in the library is what its
 * assignments were read from, so every path is already true; what the layout
 * decides is where a *new* picture is written and where one goes when its
 * folder stops describing it. Moving an existing library onto the layout is a
 * separate, explicitly-consented action - see `getLayoutMigrationPreview`.
 *
 * A PATCH, not a PUT: a field left `undefined` keeps its stored value, so
 * sending only the unfiled folder does not turn the layout off. Pass
 * `layout: null` explicitly for that.
 *
 * Rejects with a 400 whose detail names the reason when the layout cannot be
 * read or the unfiled name is not one safe path component. Nothing is stored in
 * that case, so a malformed layout can never behave as "no layout" by accident.
 *
 * @param {{layout?: string|null, layoutUnfiled?: string|null}} patch
 * @returns {Promise<Object>} the updated settings.
 */
export async function setLayoutSettings({ layout, layoutUnfiled } = {}) {
  const body = {};
  if (layout !== undefined) body.layout = layout;
  if (layoutUnfiled !== undefined) body.layout_unfiled = layoutUnfiled;
  return unwrap(apiClient.patch(LAYOUT_URL, body));
}

/**
 * Count what moving the whole library onto its layout would do. Moves nothing.
 *
 * @returns {Promise<Object>} the response body:
 *   - `picture_count` / `folder_count`: how many pictures into how many folders
 *   - `samples` / `collisions`: up to 8 `{picture_id, from, to}` each, every
 *     path relative to the library root
 *   - `collision_count`: how many are suffixed `-2` because something already
 *     occupies the path. The file already there is never overwritten
 *   - `cross_volume_count`: how many sit across a mount point inside the
 *     library and therefore **cannot be moved at all**
 *   - `skipped_counts`: every refusal by reason
 *   - `tree`: the library's folders as this layout would draw them, one flat
 *     row per folder as `{path, name, depth, have, arriving, leaving, is_new}`.
 *     **Indent on the row's own `depth` and nothing else.** A folder is a row
 *     only when one of have/arriving/leaving is non-zero, so an intermediate
 *     folder that holds nothing and receives nothing is legitimately absent
 *     while its child is present: depth does not step by one between
 *     consecutive rows, and a parent may not be there to look up. Every
 *     folder, uncapped: the list scrolls
 *
 * @param {{sweepUnfiled?: boolean}} options - `sweepUnfiled` counts the
 *   pictures nothing files as moving into the unfiled folder too. Read with
 *   the same value the run will be sent.
 */
export async function getLayoutMigrationPreview({ sweepUnfiled = false } = {}) {
  return unwrap(
    apiClient.get(`${LAYOUT_URL}/migration`, {
      params: sweepUnfiled ? { sweep_unfiled: true } : {},
    }),
  );
}

/**
 * Move one window of the library onto its layout.
 *
 * Call again with the `next_after_id` and `batch_id` it returns until `done`.
 * That loop is the progress bar and it is also what makes the run resumable: a
 * pass that fails leaves the tree half-moved and wholly consistent, and a
 * picture already where the layout wants it plans no move, so re-running
 * finishes it rather than restarting it.
 *
 * **Echo the `batch_id` on every pass after the first.** Each pass records its
 * own operation under that one id, and a batch is a single undo unit, so one
 * undo puts every file back at the path it had. Omitting it starts a second
 * migration whose passes undo separately.
 *
 * @param {{afterId?: number, batchId?: string|null, sweepUnfiled?: boolean}}
 *   cursor - `sweepUnfiled` also moves the pictures nothing files into the
 *   unfiled folder; send the same value on every pass.
 * @returns {Promise<Object>} `{batch_id, moved_count, moved_picture_ids,
 *   examined, next_after_id, done, skipped, operation_id}`.
 */
export async function runLayoutMigrationPass({
  afterId = 0,
  batchId,
  sweepUnfiled = false,
} = {}) {
  return unwrap(
    apiClient.post(`${LAYOUT_URL}/migration`, {
      after_id: afterId,
      ...(batchId ? { batch_id: batchId } : {}),
      ...(sweepUnfiled ? { sweep_unfiled: true } : {}),
    }),
  );
}
