// Dedup resource: /dedup.
//
// Duplicate detection is a destination with a to-do count, not a sort order.
// This module is the whole contract behind it: the tier policy the controls are
// built from, the paged triage queue, the live counts the sidebar and the
// context menus read, the scan trigger, the three verdicts (stack / keep
// separate / reopen) and the bulk auto-stack of the exact tier.
//
// Two invariants shape every route here:
//
//   * **Nothing is deleted.** The only verdicts are "stack" and "keep separate".
//     A stack is a grouping row plus a cover pointer, so a verdict is reversible
//     and there is no destructive route on this surface at all.
//   * **Nothing blocks on a full pass.** The queue returns whatever has been
//     found so far together with the scan's progress, so the UI opens
//     immediately and streams the rest in.
//
// ## Contract notes (reconciled with the backend lane, 2026-07-29)
//
// This module was first written against a speculative contract while the
// backend was built in parallel. It now matches `routes/dedup.py` as shipped.
// The parts worth knowing, because they shape the callers:
//
//   1. **A group is addressed by its `signature`**, a hash of the sorted member
//      content hashes, and the signature travels in the request BODY on every
//      verdict route. It is never interpolated into a path, so no encoding
//      question arises.
//   2. **Paging is a keyset cursor, with offset as the fallback.** The queue is
//      ordered by confidence descending while a scan is still inserting rows, so
//      an offset can re-serve or skip a group as the table grows. The cursor
//      removes that hazard rather than mitigating it, so it is the primary path:
//      a response carrying `next_cursor` is paged with {@link listGroups}'s
//      `cursor` and the offset is never sent again for that queue. A server that
//      returns no `next_cursor` is paged by `offset` exactly as before, with the
//      dedupe-by-signature mitigation still in force. `useDedupStore.loadMore`
//      picks between the two per page, so both work without a feature flag.
//   3. **Counts are a POST that takes a LIST of scopes.** Read-only despite the
//      verb: a context menu asks for its own scopes and gets the global sidebar
//      badge in the same response, so the badge and the menu can never disagree.
//   4. **Tier selection is two booleans plus a threshold**, not a list of tier
//      names: `near_enabled`, then `embedding_enabled` which requires it. The
//      thresholds, the tier order, the prerequisites and the scope vocabulary
//      all come from `GET /dedup/policy`, so no bound is hardcoded twice.
//   5. **The whole-vault scope is `"global"`**, and `scope_id` is required for
//      every other scope type. `ScopeRequestModel` forbids extra fields, so a
//      caller must not send a scope label or glyph here; those are the client's
//      own presentation state and live in the URL.
//   6. **`autoStackExact` defaults to `dry_run: true`.** The dry run returns the
//      counts the consent dialog shows and writes nothing; the real run returns
//      the single `batch_id` that makes N stacks reverse with one undo.
//   7. **Keep-separate records no operation.** It changes no reversible picture
//      facet, so there is nothing for undo to restore and the backend
//      deliberately writes no operation row. Callers must not wait for a receipt
//      that will never arrive; the way back is {@link reopenGroup}.
//
// Every route is owner-scoped on the backend. Guarding a read-only session is
// the caller's job; this module is pure transport.

import { apiClient } from "../utils/apiClient";

/** The whole-vault scope. Every other scope type requires a `scope_id`. */
export const GLOBAL_SCOPE = "global";

/**
 * Build a dedup route, optionally under an explicit backend base.
 * @param {string} [path=""] - the route below `/dedup`.
 * @param {string} [baseUrl=""] - explicit backend base.
 * @returns {string}
 */
function dedupUrl(path = "", baseUrl = "") {
  return `${baseUrl}/dedup${path}`;
}

/**
 * Build a `ScopeRequestModel` body fragment.
 *
 * The model forbids extra fields, so this deliberately emits only the two the
 * server accepts: a scope label or glyph is the client's own presentation state
 * and would come back a 422.
 *
 * @param {string} [scopeType=GLOBAL_SCOPE] - `global`, `project`, `set`,
 *   `character` or `folder`, from `GET /dedup/policy` -> `bounds.scope_types`.
 * @param {number|string} [scopeId] - the collection's id, or the absolute path
 *   for `folder`. Required unless the scope is global.
 * @returns {Object}
 */
export function scopeBody(scopeType = GLOBAL_SCOPE, scopeId = null) {
  const body = { scope_type: scopeType || GLOBAL_SCOPE };
  if (
    body.scope_type !== GLOBAL_SCOPE &&
    scopeId !== undefined &&
    scopeId !== null
  ) {
    body.scope_id = String(scopeId);
  }
  return body;
}

/**
 * Build a `TierPolicyModel` body fragment from the client's gate.
 *
 * Omits every field the caller did not set, so the server's own defaults apply
 * rather than the client re-stating them. The model forbids extra fields.
 *
 * @param {Object} [policy]
 * @param {boolean} [policy.nearEnabled]
 * @param {boolean} [policy.embeddingEnabled]
 * @param {number} [policy.threshold]
 * @param {number} [policy.minGroupSize]
 * @param {number} [policy.maxGroupSize]
 * @returns {Object}
 */
export function policyBody({
  nearEnabled,
  embeddingEnabled,
  threshold,
  minGroupSize,
  maxGroupSize,
} = {}) {
  const body = {};
  if (typeof nearEnabled === "boolean") body.near_enabled = nearEnabled;
  if (typeof embeddingEnabled === "boolean") {
    body.embedding_enabled = embeddingEnabled;
  }
  if (Number.isFinite(threshold)) body.threshold = threshold;
  if (Number.isFinite(minGroupSize)) body.min_group_size = minGroupSize;
  if (Number.isFinite(maxGroupSize)) body.max_group_size = maxGroupSize;
  return body;
}

/**
 * Read the tier defaults, bounds and closed vocabularies.
 *
 * The single reason no threshold, tier id, prerequisite or scope type is
 * hardcoded in the client. Fetch this before rendering the tier controls.
 *
 * @param {Object} [options]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body: `{ defaults, bounds }`, where
 *   `bounds` carries `min_threshold`, `max_threshold`, `tiers` (strongest
 *   first), `always_on_tiers`, `tier_requires`, `scope_types`, `verdicts` and
 *   `max_page_size`.
 */
export async function getPolicy({ baseUrl = "" } = {}) {
  const res = await apiClient.get(dedupUrl("/policy", baseUrl));
  return res.data;
}

/**
 * Read one page of the queue, confidence descending.
 *
 * Returns whatever has been found so far plus this scope's scan progress, so
 * the queue can open on a partial result and stream the rest in. Groups already
 * resolved are never returned again: the verdict memory is keyed on the group
 * signature, so a rescan does not re-ask.
 *
 * @param {Object} [options]
 * @param {boolean} [options.nearEnabled=false] - include tier 2.
 * @param {boolean} [options.embeddingEnabled=false] - include tier 3. The
 *   server rejects this without `nearEnabled`.
 * @param {number} [options.threshold] - minimum similarity. Omitted means the
 *   server's default; below its floor is a 400, never a silent clamp.
 * @param {string} [options.scopeType=GLOBAL_SCOPE]
 * @param {number|string} [options.scopeId]
 * @param {string} [options.cursor] - the opaque `next_cursor` from the previous
 *   page. The primary paging path: a keyset cursor cannot re-serve or skip a
 *   group while a scan inserts rows, which an offset over the same ordering can.
 *   Supplying it suppresses `offset` entirely, since sending both would let a
 *   server pick the weaker one.
 * @param {number} [options.offset=0] - **deprecated**, and only used when no
 *   cursor is held: the first page of a queue, or a server that publishes no
 *   `next_cursor`.
 * @param {number} [options.limit=20] - clamped server-side to
 *   `bounds.max_page_size`; the response echoes the effective value.
 * @param {Array<string>} [options.verdicts=[]] - **decided page only**: list
 *   only groups whose live verdict is one of these (`bounds.verdicts`). Empty
 *   is every decision. The server refuses it without `decided`, because an
 *   open-queue group carries no verdict and the filter would silently empty
 *   the queue.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{ groups, total, offset, limit, policy, scope, verdicts, by_verdict,
 *   scan }`, plus `next_cursor` from a cursor-paging server: an opaque string
 *   while more pages remain and null (or absent) at the end. A group is
 *   `{ signature, tier, confidence, member_count, cover_picture_id, why,
 *   created_at, candidates }`. `by_verdict` is the decided page's per-verdict
 *   count, taken WITHOUT the filter in force so the filter menu can say what
 *   turning a verdict back on would add.
 */
export async function listGroups({
  nearEnabled = false,
  embeddingEnabled = false,
  threshold,
  scopeType = GLOBAL_SCOPE,
  scopeId = null,
  cursor = null,
  offset = 0,
  limit = 20,
  decided = false,
  verdicts = [],
  baseUrl = "",
} = {}) {
  const params = {
    near_enabled: nearEnabled,
    embedding_enabled: embeddingEnabled,
    limit,
  };
  // The decided page: resolved groups with their live verdict, so a decision
  // can be reviewed and cleared. Omitted entirely for the open queue.
  if (decided) params.decided = true;
  if (decided && verdicts.length) params.verdict = [...verdicts];
  // Never both: a cursor and an offset describe the same position two ways, and
  // a server free to choose between them could silently keep the weaker one.
  if (typeof cursor === "string" && cursor) params.cursor = cursor;
  else params.offset = offset;
  if (Number.isFinite(threshold)) params.threshold = threshold;
  Object.assign(params, scopeBody(scopeType, scopeId));
  const res = await apiClient.get(dedupUrl("/groups", baseUrl), {
    params,
    // `verdict` is a repeatable query param, which is what FastAPI's
    // `list[...]` reads. Axios' default array form is `verdict[]=`, a key the
    // server does not know, so the filter would be silently dropped.
    paramsSerializer: { indexes: null },
  });
  return res.data;
}

/**
 * Read the live counts: the sidebar badge, the per-tier split, and any number
 * of scoped counts in one request.
 *
 * The global count comes back whether or not a scope was asked for, so a
 * context menu labelling three of its entries also refreshes the badge for
 * free, and the two can never disagree. The per-tier split deliberately
 * includes tiers that are switched off, so the tier menu can show what enabling
 * one would add before the user enables it.
 *
 * @param {Object} [options]
 * @param {Object} [options.policy] - see {@link policyBody}.
 * @param {Array<{scopeType: string, scopeId: (number|string|null)}>}
 *   [options.scopes=[]] - extra scopes to count.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{ unresolved_groups, by_tier, scopes, policy, scan }`, where each entry of
 *   `scopes` is `{ scope_type, scope_id, key, unresolved_groups }`.
 */
export async function getCounts({ policy, scopes = [], baseUrl = "" } = {}) {
  const body = { scopes: scopes.map((s) => scopeBody(s.scopeType, s.scopeId)) };
  const policyFragment = policyBody(policy);
  if (Object.keys(policyFragment).length) body.policy = policyFragment;
  const res = await apiClient.post(dedupUrl("/counts", baseUrl), body);
  return res.data;
}

/**
 * Queue a scan for one scope.
 *
 * Returns the progress row immediately; the queue can be opened while it runs.
 * Cached hashes are reused, so a scoped scan only reads and compares them and
 * usually returns already-complete progress rather than starting real work.
 *
 * @param {Object} [options]
 * @param {Object} [options.policy] - see {@link policyBody}.
 * @param {string} [options.scopeType=GLOBAL_SCOPE]
 * @param {number|string} [options.scopeId]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{ status, scanned_pictures, total_pictures, scanned_buckets,
 *   total_buckets, groups_found, error }`.
 */
export async function startScan({
  policy,
  scopeType = GLOBAL_SCOPE,
  scopeId = null,
  baseUrl = "",
} = {}) {
  const body = { scope: scopeBody(scopeType, scopeId) };
  const policyFragment = policyBody(policy);
  if (Object.keys(policyFragment).length) body.policy = policyFragment;
  const res = await apiClient.post(dedupUrl("/scan", baseUrl), body);
  return res.data;
}

/**
 * Stack one group: the "yes, these are the same picture" verdict.
 *
 * Stacking unions tags, project and set membership onto every member and lifts
 * every member to the highest score, so nothing is overwritten and nothing is
 * lost. Every member file stays on disk. Answers 423 when a locked picture set
 * freezes a member, in which case the whole verdict is refused rather than
 * half-applied.
 *
 * @param {string} signature - the group signature from {@link listGroups}.
 * @param {Object} [options]
 * @param {number} [options.coverPictureId] - the cover the user chose. Omitted
 *   means the server's preselection stands.
 * @param {Array<number>} [options.excludedPictureIds=[]] - members the user
 *   left out. They are untouched, and the exclusion is recorded so a rescan
 *   does not treat it as an unfinished decision.
 * @param {string} [options.batchId] - operation-log batch to record under, so
 *   several verdicts can reverse as one undo.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{ signature, verdict, stack_id, cover_picture_id, picture_ids,
 *   excluded_picture_ids, batch_id, metadata_union }`.
 */
export async function stackGroup(
  signature,
  { coverPictureId, excludedPictureIds = [], batchId, baseUrl = "" } = {},
) {
  const body = { signature };
  if (coverPictureId !== undefined && coverPictureId !== null) {
    body.cover_picture_id = coverPictureId;
  }
  if (excludedPictureIds.length) {
    body.excluded_picture_ids = excludedPictureIds;
  }
  if (batchId) body.batch_id = batchId;
  const res = await apiClient.post(dedupUrl("/verdicts/stack", baseUrl), body);
  return res.data;
}

/**
 * Keep one group separate: the "no, these are different pictures" verdict.
 *
 * No picture row changes, but the decision itself is undoable (owner override,
 * 2026-07-30): the backend records one `dedup.keep_separate` operation and the
 * response carries its `batch_id`, like a stack. Gate narration on `batch_id`
 * so an older backend (which returns null there) degrades to no receipt;
 * {@link reopenGroup} remains the explicit non-undo way back.
 *
 * @param {string} signature - the group signature.
 * @param {Object} [options]
 * @param {string} [options.batchId]
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body, the same `VerdictResponse`
 *   shape as {@link stackGroup} with `verdict: "keep_separate"`.
 */
export async function keepGroupSeparate(
  signature,
  { batchId, baseUrl = "" } = {},
) {
  const body = { signature };
  if (batchId) body.batch_id = batchId;
  const res = await apiClient.post(
    dedupUrl("/verdicts/keep-separate", baseUrl),
    body,
  );
  return res.data;
}

/**
 * Reopen a decided group ("Clear decision") so it is offered again.
 *
 * Clearing a `stacked` verdict whose stack still stands also dissolves that
 * stack (restoring the recorded pre-verdict stack state), because the open
 * queue only offers groups whose members span two or more stack units. That
 * unstack is one undoable `dedup.reopen` operation and the response's
 * `batch_id` is its undo handle — gate any receipt/narration on it, exactly
 * as for the other verdicts. A clear that touches no picture (keep-separate,
 * or a stack the user already dissolved by hand) records nothing and returns
 * `batch_id: null`.
 *
 * @param {string} signature - the group signature.
 * @param {Object} [options]
 * @param {string} [options.batchId] - operation-log batch to record under, so
 *   several clears can reverse as one undo.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{ signature, previous_verdict, reopened_at, group_returned_to_queue,
 *   batch_id, unstacked_picture_ids }`.
 *   `group_returned_to_queue` is false when the group has not been re-detected
 *   yet, in which case the next scan brings it back.
 */
export async function reopenGroup(signature, { batchId, baseUrl = "" } = {}) {
  const body = { signature };
  if (batchId) body.batch_id = batchId;
  const res = await apiClient.post(dedupUrl("/verdicts/reopen", baseUrl), body);
  return res.data;
}

/**
 * Auto-stack the exact tier, as a preview or for real.
 *
 * Byte-identical files are the one tier with no human judgment left in them, so
 * they get a single consent dialog instead of per-group adjudication. The dry
 * run and the real run are the same endpoint precisely so the preview cannot
 * disagree with what the confirmation then does.
 *
 * @param {Object} [options]
 * @param {boolean} [options.dryRun=true] - preview only. Defaults to the safe
 *   direction, so a caller that forgets the flag counts rather than writes.
 * @param {string} [options.scopeType=GLOBAL_SCOPE]
 * @param {number|string} [options.scopeId]
 * @param {string} [options.batchId] - omit to have the server mint one.
 * @param {number} [options.limit] - cap the groups acted on, for a paged run.
 * @param {string} [options.baseUrl=""]
 * @returns {Promise<Object>} the response body:
 *   `{ batch_id, dry_run, groups, pictures, scope, dry_run_summary, results,
 *   failures }`. `dry_run_summary` is present on a dry run only and carries
 *   `{ groups, groups_by_tier, pictures, covers_gaining_tags,
 *   covers_gaining_score, covers_gaining_metadata }`, every figure derived from
 *   the same read of the same groups so the consent dialog's rows cannot
 *   disagree with each other. `groups_by_tier` counts only what **this run**
 *   would act on (exact-only today, zero-filled for the rest), so it is not the
 *   queue's remainder; that comes from {@link getCounts}. `results` is empty for
 *   a dry run. `failures` names groups the run skipped and why: one unstackable
 *   group never aborts the run, so a partial result is reported rather than
 *   hidden.
 */
export async function autoStackExact({
  dryRun = true,
  scopeType = GLOBAL_SCOPE,
  scopeId = null,
  batchId,
  limit,
  baseUrl = "",
} = {}) {
  const body = { scope: scopeBody(scopeType, scopeId), dry_run: dryRun };
  if (batchId) body.batch_id = batchId;
  if (Number.isFinite(limit)) body.limit = limit;
  const res = await apiClient.post(dedupUrl("/auto-stack", baseUrl), body);
  return res.data;
}
