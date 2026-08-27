// The duplicate triage queue's state.
//
// Duplicate detection is a destination with a to-do count, so its state has to
// outlive the queue view: the sidebar badge and every context menu's "Find
// duplicates in..." count read from here whether or not the queue is open.
//
// Four rules from the design shape the shape of this store:
//
//   * **Never block on a full pass.** `loadFirstPage` returns whatever has been
//     found and keeps `scan` progress alongside it, so the view can render a
//     partial queue plus a banner rather than a spinner.
//   * **Never load the queue whole.** Groups are paged by confidence descending.
//     `loadMore` is called when the focus walks close to the tail, not when the
//     user scrolls, because the keyboard is the primary way through the queue.
//     The loaded rows form a WINDOW (`groups` + `windowStart`): normally the
//     queue's head, but an End jump rebases it straight onto the tail page and
//     `loadPrevious` backfills upwards from there. All public indices are
//     absolute queue positions.
//   * **Verdicts auto-advance.** Resolving a group removes it from the list and
//     the focus lands on the next open group, so a run of Enter presses works
//     the queue without a single extra keystroke.
//   * **No bound is hardcoded twice.** The threshold, its floor, the tier order
//     and the prerequisite chain all come from `GET /dedup/policy`.
//
// Three consequences of the backend contract are load-bearing here:
//
//   * **Paging prefers a keyset cursor.** The queue is ordered by confidence
//     while a scan is still inserting rows, so an offset can re-serve a group the
//     client already holds, or skip one. When a page comes back with a
//     `next_cursor`, the next one is fetched from that cursor and the hazard is
//     gone. When it does not, `loadMore` falls back to the offset path with the
//     old mitigations intact: it dedupes by signature and drops a re-seen group
//     rather than adding it twice, because a duplicated row could be resolved
//     twice and the second verdict would 400.
//   * **The sidebar badge is reconciled from the server, not inferred.** A
//     keep-separate mutates no picture row, so it raises no WebSocket event and
//     nothing else will ever correct an optimistic decrement. Every verdict
//     therefore refetches `POST /dedup/counts` behind its own optimistic tick, so
//     a second tab and a long triage run both stay honest.
//   * **Keep-separate records no operation.** It changes no reversible picture
//     facet, so no receipt will ever arrive for it. `keepSeparate` therefore
//     returns its result for the caller to narrate, and `reopen` is the
//     documented way back.
//
// Cover overrides and exclusions are held per signature rather than on the group
// object, so a refetch that replaces the group rows cannot silently discard the
// user's `1`-`9` and `X` choices.

import { defineStore } from "pinia";
import { ref, computed, onScopeDispose } from "vue";
import {
  getPolicy,
  listGroups,
  getCounts,
  startScan,
  stackGroup,
  keepGroupSeparate,
  applyVerdictBatch,
  reopenGroup,
  autoStackExact,
  listMixedStacks,
  splitMixedStack,
  keepMixedStack,
  clearMixedStackKeep,
  GLOBAL_SCOPE,
} from "../api/dedup";
import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  clampSizeLevel,
  stripHeightForSizeLevel,
} from "../utils/thumbnailSizes";
import {
  suggestedCoverId,
  candidateId,
  groupUnits,
  unitForPictureId,
  isUnitExcluded,
  includedUnits,
  flaggedStackIdSet,
  isLockedRefusal,
  lockedSets,
  mixedStackEngineMarks,
} from "../utils/dedup";
import { newOperationBatchId, onSessionReset } from "../utils/apiClient";
import { useOperationStore } from "./useOperationStore";

/** How many groups one queue page holds. */
export const QUEUE_PAGE_SIZE = 20;

/** Rows retained ahead of an in-place undo reload's viewport anchor. */
const WINDOW_RELOAD_CONTEXT = QUEUE_PAGE_SIZE;

/**
 * How close to the tail the focus may walk before the next page is fetched.
 * Three rows is one Enter-Enter-Enter burst of headroom, which is what a user
 * working the queue by keyboard actually consumes between frames.
 */
const PREFETCH_MARGIN = 3;

/**
 * The most groups one Ctrl+A may take.
 *
 * A selection is not free: every verdict given to it is one request per group,
 * and the queue's founding rule is never to hold the whole thing in memory. A
 * ceiling keeps both bounded on a library with tens of thousands of duplicates;
 * the gesture reports when it hits one, so "all" never quietly means "some".
 */
export const SELECT_ALL_MAX = 500;

/**
 * The smallest stack the server will create.
 *
 * A stack is a grouping row over two or more pictures, so one member is not a
 * degenerate stack, it is a rejected request. The client holds the same floor so
 * `X` cannot walk a group into a state the Stack button is still offering.
 */
export const MIN_STACK_MEMBERS = 2;

/**
 * How many mixed stacks one page of that list holds.
 *
 * Larger than the queue's page on purpose. The measured list is 9 rows at the
 * 0.65 floor and 26 at the 0.90 default, so one page is normally the whole
 * thing; and because the list is ranked stranded-members-first, one page also
 * carries every stack the queue's warning chip flags. A second request to
 * answer "is this deck flagged" would be a request that is nearly always
 * empty. Clamped server-side to its own 200 ceiling.
 */
const MIXED_STACK_PAGE_SIZE = 100;

/**
 * The tier ids the server publishes, mapped to the copy the menu renders.
 *
 * Labels are the client's business: the server names its tiers and the client
 * says what they mean to a person. An id the server adds later renders under
 * its own id rather than vanishing from the menu.
 */
const TIER_LABELS = Object.freeze({
  exact: { label: "Exact matches", hint: "identical file" },
  near: { label: "Near-identical", hint: "bursts, re-exports, resizes" },
  embedding: { label: "Same scene", hint: "cross-folder, re-framed" },
});

/**
 * The verdict ids the server publishes, mapped to the copy the decided page's
 * filter renders.
 *
 * Same contract as {@link TIER_LABELS}: the server owns the vocabulary
 * (`bounds.verdicts`), the client owns the words. A verdict the server adds
 * later renders under its own id rather than vanishing from the menu.
 */
const VERDICT_LABELS = Object.freeze({
  stacked: { label: "Stacked", hint: "folded into one stack" },
  keep_separate: { label: "Kept separate", hint: "not duplicates" },
});

/**
 * Where the queue's thumbnail size is remembered.
 *
 * Deliberately NOT the grid's server-side `thumbnail_size_level`: the queue
 * reads a row of copies beside a column of facts, the grid reads a wall of
 * pictures, and a size that suits one is the wrong size for the other. Only the
 * LADDER is shared (`thumbnailSizes.js`), so the two controls speak the same
 * Tiny-to-Huge language without dragging each other around.
 */
const SIZE_LEVEL_KEY = "pixlstash:dedupSizeLevel";

/**
 * Where the queue's tier filters and threshold are remembered.
 *
 * Same tier of persistence as the queue's thumbnail size above: per-browser
 * view state, restored on the next visit. The URL still outranks it when a
 * link carries explicit filter params (a shared link must open exactly as
 * sent), and the server's policy defaults apply when neither has an opinion.
 * Promoting this to the account-level `/users/me/config` blob would need a
 * backend schema change (the PATCH endpoint rejects unknown keys), recorded
 * as a follow-up rather than half-done here.
 */
const FILTERS_KEY = "pixlstash:dedupFilters";

/** Read the remembered filter selection, or null when there is none. */
function storedFilters() {
  try {
    const raw = window.localStorage?.getItem(FILTERS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const filters = {};
    if (typeof parsed.near === "boolean") filters.near = parsed.near;
    if (typeof parsed.embedding === "boolean") {
      filters.embedding = parsed.embedding;
    }
    if (Number.isFinite(parsed.threshold)) filters.threshold = parsed.threshold;
    return Object.keys(filters).length ? filters : null;
  } catch (err) {
    // Private mode, or a corrupt blob. The server defaults are a fine
    // outcome; a thrown getter that takes the queue with it is not.
    console.warn(
      "[dedup] could not read the remembered filter selection; using defaults",
      err,
    );
    return null;
  }
}

/** Read the remembered size level, falling back to the ladder's default. */
function storedSizeLevel() {
  try {
    const raw = window.localStorage?.getItem(SIZE_LEVEL_KEY);
    if (raw === null || raw === undefined) return DEFAULT_THUMBNAIL_SIZE_LEVEL;
    return clampSizeLevel(raw);
  } catch (err) {
    // Private mode, or storage disabled by policy. A default size is a fine
    // outcome; a thrown getter that takes the whole queue with it is not.
    console.warn(
      "[dedup] could not read the remembered thumbnail size; using the default",
      err,
    );
    return DEFAULT_THUMBNAIL_SIZE_LEVEL;
  }
}

/** The empty scan record, so consumers never branch on `null`. */
const IDLE_SCAN = Object.freeze({
  scanId: null,
  scopeKey: null,
  status: "idle",
  tiers: [],
  threshold: null,
  scanned: 0,
  total: 0,
  percent: 100,
  buckets: 0,
  totalBuckets: 0,
  groupsFound: 0,
  error: null,
});

/** Scan statuses that mean work is still happening. */
const RUNNING_STATUSES = new Set(["pending", "running"]);

/**
 * Cache key for a scope's count.
 *
 * Mirrors the `key` the server returns on each scope row, so a cached entry and
 * a served one land in the same slot.
 *
 * @param {string} type
 * @param {number|string} [id]
 * @returns {string}
 */
export function scopeKey(type, id) {
  return id === undefined || id === null ? String(type) : `${type}:${id}`;
}

/**
 * Normalise a `ScanProgressModel` into what the banner renders.
 *
 * The server reports pictures and buckets but no percentage and no estimate, so
 * the percentage is derived here. Tier 2 streams its groups in per bucket, so a
 * scope whose picture total is not yet known still shows honest progress from
 * the bucket counters rather than sitting at zero.
 *
 * @param {Object} [raw] - a `ScanProgressModel`.
 * @returns {Object}
 */
function normalizeScan(raw) {
  if (!raw) return { ...IDLE_SCAN };
  const status = raw.status || "idle";
  const scanned = Number(raw.scanned_pictures) || 0;
  const total = Number(raw.total_pictures) || 0;
  const buckets = Number(raw.scanned_buckets) || 0;
  const totalBuckets = Number(raw.total_buckets) || 0;
  // Status is the terminal truth. A fresh pending/running scan has no totals
  // yet, which means "unknown", not "100%". Near scans can finish their
  // picture enumeration before their candidate batches, so once the backend
  // publishes bucket counters those are the authoritative progress slice.
  let percent = RUNNING_STATUSES.has(status) ? 0 : 100;
  if (totalBuckets > 0)
    percent = Math.round((buckets / totalBuckets) * 100);
  else if (total > 0) percent = Math.round((scanned / total) * 100);
  return {
    scanId: raw.scan_id ?? null,
    scopeKey: raw.scope_key ?? null,
    status,
    tiers: Array.isArray(raw.tiers) ? [...raw.tiers] : [],
    threshold: Number.isFinite(Number(raw.threshold))
      ? Number(raw.threshold)
      : null,
    scanned,
    total,
    percent: Math.max(0, Math.min(100, percent)),
    buckets,
    totalBuckets,
    groupsFound: Number(raw.groups_found) || 0,
    error: raw.error ?? null,
  };
}

/**
 * Rewrite one loaded group as if the given pictures had never been in it.
 *
 * The queue's rows are a SNAPSHOT of a server read, and a soft-delete elsewhere
 * (the grid, the lightbox, another tab) makes that snapshot wrong in three
 * places at once, all of which this repairs:
 *
 *   * `candidates`, a tile whose thumbnail now 404s, which is the "empty
 *     placeholder" in the report;
 *   * `member_count`, the group's own size, which the server reports live;
 *   * `stacks[id]`, a deck's DEPTH. A deck stands for the whole existing
 *     stack, so a scrapheaped stack member leaves a hole in it even when the
 *     group never named that member. Only the ids this group can attribute to a
 *     stack (its matched members and the stack's leader) are subtracted; a
 *     sibling outside the group is not knowable from the payload and its depth
 *     corrects itself on the next page load.
 *
 * Returns a NEW group object when anything changed and `null` when the group is
 * untouched, so the caller can leave unaffected rows at their existing object
 * identity and not re-render them.
 *
 * @param {Object} group - a loaded queue group.
 * @param {Set<number|string>} removed - the picture ids that went away.
 * @returns {Object|null}
 */
function groupWithoutPictures(group, removed) {
  const candidates = group?.candidates ?? [];
  const kept = candidates.filter((c) => !removed.has(candidateId(c)));
  const stacks = group?.stacks ?? {};
  let stacksChanged = false;
  const nextStacks = {};
  for (const [key, entry] of Object.entries(stacks)) {
    const matched = Array.isArray(entry?.matched_picture_ids)
      ? entry.matched_picture_ids
      : [];
    const keptMatched = matched.filter((id) => !removed.has(id));
    const leaderId = entry?.leader_picture_id;
    const leaderGone =
      leaderId !== null && leaderId !== undefined && removed.has(leaderId);
    if (keptMatched.length === matched.length && !leaderGone) {
      nextStacks[key] = entry;
      continue;
    }
    stacksChanged = true;
    // The leader counts once: it is only an EXTRA loss when it was not already
    // one of the matched members this group named.
    const lost =
      matched.length -
      keptMatched.length +
      (leaderGone && !matched.includes(leaderId) ? 1 : 0);
    const served = Number(entry?.member_count);
    nextStacks[key] = {
      ...entry,
      member_count: Number.isFinite(served)
        ? Math.max(keptMatched.length, served - lost)
        : keptMatched.length,
      matched_picture_ids: keptMatched,
      // Which member the stack promotes next is a fact only the server holds,
      // the canonical order can promote one this group never named so the
      // face falls back to the first surviving matched candidate. That is the
      // degradation `finaliseDeck` already applies to a payload with no
      // `stacks` block, and the true leader returns with the next page load.
      ...(leaderGone
        ? { leader_picture_id: null, leader_thumbnail_version: "" }
        : {}),
    };
  }
  if (kept.length === candidates.length && !stacksChanged) return null;
  return {
    ...group,
    candidates: kept,
    stacks: nextStacks,
    member_count: kept.length,
  };
}

export const useDedupStore = defineStore("dedup", () => {
  // --- The server's policy, bounds and vocabularies ------------------------
  const policyDefaults = ref(null);
  const bounds = ref(null);
  const policyLoaded = ref(false);

  // --- The sidebar's live count -------------------------------------------
  const openCount = ref(0);
  const byTier = ref({});
  const scan = ref({ ...IDLE_SCAN });
  const countsLoaded = ref(false);

  // --- Per-scope counts, for the context menus ----------------------------
  const scopeCounts = ref({});
  const scopeCountsInFlight = new Map();
  // Concurrent mounts/router syncs for the same destination share the whole
  // open operation. Without this, both could observe the same completed scan
  // and enqueue two fresh passes before either response reached the store.
  const openQueueInFlight = new Map();
  // Explicit tier/threshold gestures can race a route open too. The backend is
  // also idempotent for equivalent active scans; this client layer avoids the
  // duplicate round trip in the first place.
  const scanRequestInFlight = new Map();

  // --- The queue ----------------------------------------------------------
  const scopeType = ref(GLOBAL_SCOPE);
  const scopeId = ref(null);
  const scopeLabel = ref("");
  const scopeIcon = ref("");
  // `groups` is a contiguous WINDOW of the queue, not necessarily its head:
  // `windowStart` is the absolute queue index of groups[0]. Every public index
  // (focusIndex, the view's row indices) is ABSOLUTE; groups[i] is the group
  // at absolute index windowStart + i. The window starts at 0 and stays there
  // through normal top-down paging; an End jump rebases it onto the tail
  // (see focusEnd), and paging then runs by offset in both directions.
  const groups = ref([]);
  const windowStart = ref(0);
  // Bumped every time the window is REPLACED (first page, End jump): a page
  // request still in flight from before the rebase must discard its result
  // rather than append rows from one window into another.
  let windowEpoch = 0;
  const total = ref(0);
  const nextOffset = ref(0);
  // The opaque keyset cursor for the next page, when the server publishes one.
  // Non-null is the only signal that this queue is cursor-paged; it is reset on
  // every first page, so a policy or scope change never carries a stale one.
  const nextCursor = ref(null);
  const hasMore = ref(false);
  const focusIndex = ref(0);
  const loading = ref(false);
  // The page request currently on the wire, so a second caller joins it instead
  // of being dropped by the busy guard. Not a ref: nothing renders from it.
  let pageInFlight = null;
  // The upward (backfill) page in flight, same joining contract.
  let prevInFlight = null;
  const loadingMore = ref(false);
  const error = ref(null);
  const busy = ref(false);
  const stackedCount = ref(0);
  const separatedCount = ref(0);

  // --- The decided page's verdict gate -------------------------------------
  // The tier gate says nothing on the decided page - a decision was made under
  // whatever policy was live then, and the server ignores the gate there - so
  // the filter menu offers the two VERDICTS instead (owner call, 2026-07-30).
  // Held as the verdicts switched OFF rather than on, so a verdict the server
  // adds later is included by default instead of silently filtered out.
  const hiddenVerdicts = ref(new Set());
  // The per-verdict counts the menu shows, from the decided page's response.
  // Taken WITHOUT the filter in force, so a row says what turning it back on
  // would add rather than the zero its own exclusion produced.
  const decidedByVerdict = ref({});

  // --- The tier gate ------------------------------------------------------
  // Tier 1 is always in and has no switch. Tier 2 is an opt-in; tier 3 requires
  // tier 2, which the server enforces and this mirrors.
  const nearEnabled = ref(false);
  const embeddingEnabled = ref(false);
  const threshold = ref(null);
  // True once openQueue has adopted the URL's (and the remembered) filter
  // selection. Until then the gate above still holds pristine DEFAULTS, and
  // the view's URL mirror must not read that transient state as "the user
  // chose the defaults" - doing so is exactly the bug that stripped the
  // filter params off the URL on every full reload (see openQueue).
  const filtersRestored = ref(false);

  // --- How big the queue draws its candidates ------------------------------
  // A view preference, so it lives on the client and survives a reload.
  const sizeLevel = ref(storedSizeLevel());
  const thumbHeight = computed(() => stripHeightForSizeLevel(sizeLevel.value));

  /**
   * Move the size, clamped to the ladder, and remember it.
   *
   * @param {number} level
   */
  function setSizeLevel(level) {
    const next = clampSizeLevel(level);
    if (next === sizeLevel.value) return;
    sizeLevel.value = next;
    try {
      window.localStorage?.setItem(SIZE_LEVEL_KEY, String(next));
    } catch (err) {
      // The size still applies for this session; only the memory of it is lost.
      console.warn(
        "[dedup] could not remember the thumbnail size for next time",
        err,
      );
    }
  }

  // --- Per-group user choices, keyed by signature --------------------------
  const coverChoices = ref({});
  const exclusions = ref({});

  const isScoped = computed(() => scopeType.value !== GLOBAL_SCOPE);
  const isScanning = computed(() => RUNNING_STATUSES.has(scan.value.status));

  /**
   * Whether ANY duplicates exist, across every tier - the sidebar's presence
   * indicator. Deliberately not the policy-filtered count: that number moves
   * with the tier gate and the threshold, so it kept reading as churn rather
   * than information (owner call, 2026-07-29 - the badge became a dot).
   */
  const hasDuplicates = computed(() => {
    if (openCount.value > 0) return true;
    return Object.values(byTier.value || {}).some((n) => Number(n) > 0);
  });
  const hasGroups = computed(() => groups.value.length > 0);
  // focusIndex is ABSOLUTE; the group it names lives at the window offset.
  const focusedGroup = computed(
    () => groups.value[focusIndex.value - windowStart.value] ?? null,
  );
  const doneCount = computed(() => stackedCount.value + separatedCount.value);

  /** Unresolved exact groups: what the auto-stack button offers to clear. */
  const exactCount = computed(() => Number(byTier.value.exact) || 0);

  /** Unresolved groups in every tier the queue does not stack in bulk. */
  const queueOnlyCount = computed(() =>
    Object.entries(byTier.value).reduce(
      (sum, [tier, count]) =>
        tier === "exact" ? sum : sum + (Number(count) || 0),
      0,
    ),
  );

  /** The policy fragment every request travels with, so counts match the queue. */
  const policyArgs = computed(() => {
    const args = {
      nearEnabled: nearEnabled.value,
      embeddingEnabled: embeddingEnabled.value,
    };
    if (Number.isFinite(threshold.value)) args.threshold = threshold.value;
    return args;
  });

  /**
   * The tier rows the menu renders: the server's ids and prerequisites, this
   * client's copy, and the live per-tier counts.
   */
  const tierRows = computed(() => {
    const ids = bounds.value?.tiers ?? [];
    const alwaysOn = new Set(bounds.value?.always_on_tiers ?? []);
    const requires = bounds.value?.tier_requires ?? {};
    return ids.map((id) => ({
      id,
      label: TIER_LABELS[id]?.label ?? id,
      hint: TIER_LABELS[id]?.hint ?? "",
      count: Number(byTier.value[id]) || 0,
      locked: alwaysOn.has(id),
      requires: requires[id] ?? null,
      enabled: isTierEnabled(id),
    }));
  });

  /** Every verdict the server publishes, in the order it publishes them. */
  const verdictIds = computed(() => bounds.value?.verdicts ?? []);

  /**
   * The verdict rows the decided page's filter renders: the server's ids, this
   * client's copy, and the live per-verdict counts.
   */
  const verdictRows = computed(() =>
    verdictIds.value.map((id) => ({
      id,
      label: VERDICT_LABELS[id]?.label ?? id,
      hint: VERDICT_LABELS[id]?.hint ?? "",
      count: Number(decidedByVerdict.value[id]) || 0,
      enabled: !hiddenVerdicts.value.has(id),
    })),
  );

  /** The verdicts currently listed, in the server's order. */
  const enabledVerdicts = computed(() =>
    verdictIds.value.filter((id) => !hiddenVerdicts.value.has(id)),
  );

  /**
   * The `verdicts` argument every decided-page request travels with.
   *
   * Empty when nothing is filtered out, so "no filter" is expressed by absence
   * rather than by re-listing the whole vocabulary - the server treats a full
   * list as no filter too, but only absence also keeps the verdict-less tail
   * (a resolved group whose live verdict row is gone) reachable.
   */
  const verdictArgs = computed(() =>
    enabledVerdicts.value.length === verdictIds.value.length
      ? []
      : enabledVerdicts.value,
  );

  /**
   * Show or hide one verdict on the decided page, then reload.
   *
   * Turning the LAST one off is refused: an empty gate can only ever render an
   * empty page, and a filter that can hide everything reads as a broken queue
   * rather than as a choice the user made. The menu disables that row for the
   * same reason, so this is the floor rather than the message.
   *
   * @param {string} id - a verdict id from `bounds.verdicts`.
   * @param {boolean} on
   * @returns {Promise<boolean>} whether the change was applied.
   */
  async function setVerdictEnabled(id, on) {
    if (!verdictIds.value.includes(id)) return false;
    if (on === !hiddenVerdicts.value.has(id)) return false;
    if (!on && enabledVerdicts.value.length <= 1) return false;
    const next = new Set(hiddenVerdicts.value);
    if (on) next.delete(id);
    else next.add(id);
    hiddenVerdicts.value = next;
    // Only the decided page reads this gate; on the open queue it is state
    // waiting for the flip, and reloading there would be a pointless request.
    if (showingDecided.value) await loadFirstPage();
    return true;
  }

  /**
   * Whether a tier currently feeds the queue.
   * @param {string} id
   * @returns {boolean}
   */
  function isTierEnabled(id) {
    if (id === "near") return nearEnabled.value;
    if (id === "embedding") return embeddingEnabled.value;
    // Tier 1 and anything the server adds later are on unless it says otherwise.
    return true;
  }

  /**
   * A group's units: the things a stack verdict can move independently.
   *
   * Recomputed per call rather than cached, because the group objects are
   * replaced wholesale by every refetch and a cache keyed on a stale object
   * identity is how the row starts disagreeing with the request.
   *
   * @param {Object} group
   * @returns {Array<Object>}
   */
  function unitsFor(group) {
    return group ? groupUnits(group) : [];
  }

  /**
   * The best cover among the units that would end up in the stack.
   *
   * **A deck wins over the server's smart-score pick.** Otherwise the default
   * action silently re-curates a stack the user already made: the verdict folds
   * the whole stack in, so a loose picture as cover demotes that stack's chosen
   * leader without anyone asking. The deepest deck wins a tie between two,
   * because merging into the larger stack re-curates the fewest pictures.
   *
   * @param {Object} group
   * @param {Array<Object>} units
   * @param {Array<number|string>} excluded - the user's exclusions in force.
   * @returns {number|string|null}
   */
  function pickCoverForUnits(group, units, excluded) {
    const live = includedUnits(units, excluded);
    const decks = live.filter((unit) => unit.kind === "deck");
    if (decks.length) {
      return decks.reduce((best, unit) =>
        unit.depth > best.depth ? unit : best,
      ).coverPictureId;
    }
    const candidates = live.flatMap((unit) => unit.candidates);
    if (!candidates.length) return suggestedCoverId(group);
    // The server's preselection stands while it is still in the running; the
    // local formula is the fallback that keeps the label truthful when it is
    // not (`suggestedCoverId` would otherwise hand back an excluded picture).
    const served = group?.cover_picture_id;
    if (
      served !== undefined &&
      served !== null &&
      candidates.some((candidate) => candidateId(candidate) === served)
    ) {
      return served;
    }
    return suggestedCoverId({ candidates });
  }

  /**
   * The cover picture id in force for a group: the user's override when they
   * made one, otherwise the preselection.
   *
   * **Two gestures arrive on this one channel, and the chosen ID is what tells
   * them apart.** Both must work:
   *
   *   * *Choosing a UNIT.* Every deck-level gesture in the app, the row's
   *     tile, the digits `1`-`9`, Compare's card and its zoom, and the
   *     automatic move when the cover's unit is excluded: passes that unit's
   *     `coverPictureId`, i.e. the stack's LEADER. It resolves to the leader,
   *     because that is the picture the tile shows and the only one the server
   *     can lead the resulting stack with.
   *   * *Promoting a MEMBER.* Compare's expansion band passes a specific
   *     picture of a deck, which is never the leader (the strip refuses a
   *     click on the current cover). It is honoured verbatim: the whole point
   *     of that band's two-step confirmation is that this stack's cover
   *     changes across the library, and normalising it back to the leader made
   *     the promotion a silent no-op for every member the group named; it
   *     only ever "worked" for members the group did not name, which fell
   *     through the unit lookup by accident.
   *
   * So a non-leader picture of a deck means the member, and anything else
   * means the unit. A future gesture that means "this deck" must therefore
   * keep passing `unit.coverPictureId`, never one of its matched members.
   *
   * @param {Object} group
   * @returns {number|null}
   */
  function coverIdFor(group) {
    if (!group) return null;
    const units = unitsFor(group);
    const chosen = coverChoices.value[group.signature];
    if (chosen !== undefined) {
      const unit = unitForPictureId(units, chosen);
      // A locked-out unit cannot lead a stack it is not in. The server would
      // move the cover for us and report where it landed, but doing it here
      // keeps the row's "Cover" label truthful before Stack is ever pressed.
      if (unit?.stackable) {
        return unit.kind === "deck" && chosen !== unit.coverPictureId
          ? chosen
          : unit.coverPictureId;
      }
      if (!unit) return chosen;
    }
    return pickCoverForUnits(group, units, []);
  }

  /**
   * The ids this group cannot stack, whatever the user asked for: the user's own
   * exclusions plus every picture in every unit a locked set freezes.
   *
   * This is what the request sends as `excluded_picture_ids`, so the server never
   * has to refuse a member the queue already knew was frozen. It is unit-level
   * because a locked set freezes a whole stack: one frozen member takes its
   * deck's whole visible membership out with it.
   *
   * @param {Object} group
   * @returns {Array<number>}
   */
  function effectiveExcludedFor(group) {
    if (!group) return [];
    const merged = new Set(excludedFor(group.signature));
    for (const unit of unitsFor(group)) {
      if (unit.stackable) continue;
      for (const id of unit.pictureIds) merged.add(id);
    }
    return [...merged];
  }

  /**
   * How many UNITS the Stack button would collect.
   *
   * The floor and the button's own count are both this number, not a picture
   * count: the server folds a stack as one thing, so two units is the smallest
   * group with a decision left in it even when they are 4 and 6 pictures deep.
   *
   * @param {Object} group
   * @returns {number}
   */
  function includedUnitCountFor(group) {
    if (!group) return 0;
    return includedUnits(unitsFor(group), excludedFor(group.signature)).length;
  }

  /**
   * The picture ids the user excluded from a group's stack.
   * @param {string} signature
   * @returns {Array<number>}
   */
  function excludedFor(signature) {
    return exclusions.value[signature] ?? [];
  }

  /**
   * How many of a group's CANDIDATES the Stack button would collect.
   *
   * A picture count over the group's own members, which the announcements use.
   * It is deliberately not the button's number (that is
   * {@link includedUnitCountFor}) and it under-reports whenever a deck folds in
   * a stack member the group never named: which is why the receipt prefers the
   * server's returned `picture_ids` over this estimate.
   *
   * @param {Object} group
   * @returns {number}
   */
  function stackSizeFor(group) {
    if (!group) return 0;
    return (group.candidates?.length ?? 0) - effectiveExcludedFor(group).length;
  }

  /**
   * Choose a group's cover.
   *
   * The id carries the gesture: a unit's `coverPictureId` chooses that unit, a
   * deck's non-leader member promotes that member. See {@link coverIdFor},
   * which is where the two are told apart.
   *
   * @param {string} signature
   * @param {number} pictureId
   */
  function setCover(signature, pictureId) {
    coverChoices.value = { ...coverChoices.value, [signature]: pictureId };
  }

  /**
   * Include or exclude the UNIT one picture belongs to.
   *
   * **The gesture is unit-level, not picture-level.** Excluding one member of an
   * existing stack was a silent no-op: the backend's `_stack_members` folds in
   * every member of any stack the group touches, so the rest of that stack
   * dragged the excluded picture straight back in. A deck therefore goes out
   * whole, and any of its pictures, matched member or leader, addresses it.
   *
   * Two invariants ride on this, both because `X` is a one-key action with no
   * confirmation:
   *
   *   * **A stack needs two units.** The server refuses a one-member stack
   *     outright, so an exclusion that would leave a single included unit is
   *     refused here rather than turned into a guaranteed 400 on a Stack the
   *     row still offers. The floor is {@link MIN_STACK_MEMBERS} *included*
   *     units, not pictures: a group of two units therefore accepts no
   *     exclusion at all, and the way to reject one of them is Keep separate.
   *   * Excluding the cover would leave the stack with no cover, and the server
   *     rejects a cover that is not in the resulting stack. The cover moves to
   *     the best remaining included unit instead, by the same rule that
   *     preselected it, so it lands on a surviving deck's leader rather than
   *     re-curating that stack.
   *
   * @param {Object} group
   * @param {number} pictureId - any picture of the unit to toggle.
   * @returns {boolean|string} `true` when the toggle was applied, `false` when
   *   the floor refused it, and `"locked"` when the unit is the server's
   *   exclusion rather than the user's. Each refusal is narrated by the caller:
   *   a one-key action that silently does nothing is a key the user stops
   *   trusting, and the two refusals need different sentences.
   */
  function toggleExcluded(group, pictureId) {
    if (!group) return false;
    const units = unitsFor(group);
    const unit = unitForPictureId(units, pictureId);
    if (!unit) return false;
    // A locked-out unit is the server's exclusion, not the user's, so `X`
    // cannot walk it back. Refused here rather than optimistically re-included
    // and then dropped again by the request the row would go on to send.
    if (!unit.stackable) return "locked";
    const current = excludedFor(group.signature);
    const isOut = isUnitExcluded(unit, current);
    if (!isOut && includedUnits(units, current).length <= MIN_STACK_MEMBERS) {
      return false;
    }
    const coverBefore = coverIdFor(group);
    const ids = new Set(current);
    for (const id of unit.pictureIds) {
      if (isOut) ids.delete(id);
      else ids.add(id);
    }
    const next = [...ids];
    exclusions.value = { ...exclusions.value, [group.signature]: next };
    if (!isOut && unitForPictureId(units, coverBefore) === unit) {
      const replacement = pickCoverForUnits(group, units, next);
      if (replacement !== null && replacement !== undefined) {
        setCover(group.signature, replacement);
      }
    }
    return true;
  }

  /**
   * Whether one more exclusion would drop this group below the stack floor.
   *
   * The row and the key handler both read it, so the tooltip that explains the
   * refusal and the refusal itself can never disagree.
   *
   * @param {Object} group
   * @returns {boolean}
   */
  function isAtStackFloor(group) {
    return Boolean(group) && includedUnitCountFor(group) <= MIN_STACK_MEMBERS;
  }

  /**
   * Read the tier defaults, bounds and closed vocabularies, once.
   *
   * Everything the tier menu renders comes from here, so a threshold or a
   * prerequisite is never stated twice in two places that can drift apart.
   *
   * @param {Object} [options]
   * @param {boolean} [options.force=false]
   * @returns {Promise<void>}
   */
  async function loadPolicy({ force = false } = {}) {
    if (policyLoaded.value && !force) return;
    try {
      const data = await getPolicy();
      policyDefaults.value = data?.defaults ?? null;
      bounds.value = data?.bounds ?? null;
      if (!Number.isFinite(threshold.value)) {
        const served = Number(data?.defaults?.threshold);
        if (Number.isFinite(served)) threshold.value = served;
      }
      policyLoaded.value = true;
    } catch (err) {
      console.warn(
        "[dedup] failed to read the duplicate detection policy",
        err,
      );
    }
  }

  /**
   * Refresh the live counts, optionally alongside extra scopes.
   *
   * The global badge comes back whether or not a scope was asked for, so this
   * one call feeds the sidebar, the tier menu's per-tier split and the scan
   * banner, and the three can never disagree.
   *
   * @param {Array<{scopeType: string, scopeId: (number|string|null)}>}
   *   [extraScopes=[]]
   * @returns {Promise<Object|null>} the response body, or null on failure.
   */
  async function refreshCounts(extraScopes = []) {
    try {
      const data = await getCounts({
        policy: policyArgs.value,
        scopes: extraScopes,
      });
      openCount.value = Number(data?.unresolved_groups) || 0;
      byTier.value = data?.by_tier ?? {};
      scan.value = normalizeScan(data?.scan);
      for (const row of data?.scopes ?? []) {
        const key = row.key ?? scopeKey(row.scope_type, row.scope_id);
        scopeCounts.value = {
          ...scopeCounts.value,
          [key]: Number(row.unresolved_groups) || 0,
        };
      }
      countsLoaded.value = true;
      return data;
    } catch (err) {
      console.warn("[dedup] failed to read the duplicate counts", err);
      return null;
    }
  }

  /**
   * Read one scope's duplicate count, for a context menu.
   *
   * Cached, and de-duplicated while a request is in flight, because opening a
   * context menu on the same set twice in a row is the common case and a second
   * round trip there shows a flicker rather than a number. The same request
   * refreshes the sidebar badge, since the server returns it either way.
   *
   * @param {string} type
   * @param {number|string} id
   * @param {Object} [options]
   * @param {boolean} [options.force=false] - bypass the cache.
   * @returns {Promise<number|null>} the count, or null when it could not be read.
   */
  async function fetchScopeCount(type, id, { force = false } = {}) {
    const key = scopeKey(type, id);
    if (!force && scopeCounts.value[key] !== undefined) {
      return scopeCounts.value[key];
    }
    if (scopeCountsInFlight.has(key)) return scopeCountsInFlight.get(key);
    const request = refreshCounts([{ scopeType: type, scopeId: id }])
      .then((data) => {
        if (!data) return null;
        const value = scopeCounts.value[key];
        return value === undefined ? 0 : value;
      })
      .finally(() => {
        scopeCountsInFlight.delete(key);
      });
    scopeCountsInFlight.set(key, request);
    return request;
  }

  /** Drop the cached per-scope counts after anything that could move them. */
  function invalidateScopeCounts() {
    scopeCounts.value = {};
  }

  /**
   * Point the queue at a scope and load its first page.
   *
   * @param {Object} [scope]
   * @param {string} [scope.type=GLOBAL_SCOPE] - `global`, `project`, `set`,
   *   `character` or `folder`.
   * @param {number|string} [scope.id=null]
   * @param {string} [scope.label=""] - what the scope pill reads.
   * @param {string} [scope.icon=""] - the pill's mdi glyph.
   * @returns {Promise<void>}
   */
  /**
   * Apply a URL-restored filter selection, under the same rules the tier menu
   * enforces: embedding requires near, and the threshold is clamped to the
   * server's published bounds (loadPolicy has run by the time this is called).
   *
   * @param {Object} filters - `{near?, embedding?, threshold?, decided?}`.
   */
  function applyUrlFilters(filters) {
    if (typeof filters.near === "boolean") nearEnabled.value = filters.near;
    if (typeof filters.embedding === "boolean") {
      embeddingEnabled.value = filters.embedding;
      if (filters.embedding) nearEnabled.value = true;
    }
    if (!nearEnabled.value) embeddingEnabled.value = false;
    if (Number.isFinite(filters.threshold)) {
      const min = Number(bounds.value?.min_threshold);
      const max = Number(bounds.value?.max_threshold);
      let next = filters.threshold;
      if (Number.isFinite(max)) next = Math.min(max, next);
      if (Number.isFinite(min)) next = Math.max(min, next);
      threshold.value = next;
    }
    if (typeof filters.decided === "boolean") {
      showingDecided.value = filters.decided;
    }
    if (Array.isArray(filters.verdicts)) {
      // The URL names what to SHOW; the store holds what to hide. An id the
      // server does not publish is ignored rather than trusted, and a selection
      // that would leave nothing on screen falls back to showing everything -
      // a link must not open onto a page that can only be empty.
      const shown = filters.verdicts.filter((id) =>
        verdictIds.value.includes(id),
      );
      hiddenVerdicts.value = shown.length
        ? new Set(verdictIds.value.filter((id) => !shown.includes(id)))
        : new Set();
    }
  }

  /**
   * Remember the current filter selection for the next visit.
   *
   * Called on every deliberate filter change, so a full page refresh (or a
   * later session) reopens the queue the way the user left it. The Decided
   * flip is deliberately NOT remembered: it is a place the user visits, not
   * a lens they set.
   */
  function rememberFilters() {
    // A remembered selection is by definition a deliberate one: from here on
    // the gate's state is authoritative and the URL mirror may write it.
    filtersRestored.value = true;
    try {
      const remembered = {
        near: nearEnabled.value,
        embedding: embeddingEnabled.value,
      };
      if (Number.isFinite(threshold.value)) {
        remembered.threshold = threshold.value;
      }
      window.localStorage?.setItem(FILTERS_KEY, JSON.stringify(remembered));
    } catch (err) {
      // The selection still applies this session; only the memory is lost.
      console.warn(
        "[dedup] could not remember the filter selection for next time",
        err,
      );
    }
  }

  async function openQueueOnce({
    type = GLOBAL_SCOPE,
    id = null,
    label = "",
    icon = "",
    filters = null,
  } = {}) {
    // "library" was this lane's own name for the unscoped case before the
    // backend named it; accept it so an old bookmark still opens the queue.
    scopeType.value = !type || type === "library" ? GLOBAL_SCOPE : type;
    scopeId.value = id;
    scopeLabel.value = label;
    scopeIcon.value = icon;
    stackedCount.value = 0;
    separatedCount.value = 0;
    showingDecided.value = false;
    // The third page is a place the user visits, like Decided: arriving at the
    // destination starts on the queue. Its potentially cold all-stack list is
    // left unloaded until the page is actually opened.
    showingMixed.value = false;
    mixedFocusStackId.value = null;
    mixedLoaded.value = false;
    // The decided page is a place the user visits, not a lens they set, and so
    // is its verdict gate: arriving at the queue starts from every decision.
    // A link that carries one restores it below, through applyUrlFilters.
    hiddenVerdicts.value = new Set();
    decidedByVerdict.value = {};
    await loadPolicy();
    // Last visit's selection first (per-browser memory, same tier as the
    // thumbnail size), then the URL's explicit filters on top: a shared or
    // refreshed link opens exactly as sent, and a bare /duplicates reopens
    // the way the user left it rather than on the server defaults. Both run
    // through applyUrlFilters, so the tier chain and the threshold clamp
    // hold whatever the source.
    const remembered = storedFilters();
    if (remembered) applyUrlFilters(remembered);
    if (filters) applyUrlFilters(filters);
    if (remembered || filters) rememberFilters();
    // Only NOW may the URL mirror trust the gate: between the policy landing
    // and this line the store held plain defaults, and a mirror that ran in
    // that window concluded "default selection" and replaced the URL without
    // its filter params - while the real navigation from the params was still
    // in flight, so the params were dropped for good.
    filtersRestored.value = true;
    // A poll belongs to the old scope/policy. Stop it before adopting this
    // destination; the active scan discovered below starts the right one.
    stopScanPoll();
    // Read the durable queue and its scan progress before requesting work, so
    // rows already found render before a potentially long pass begins.
    await loadFirstPage();
    const request = currentScanRequest();
    if (RUNNING_STATUSES.has(scan.value.status) && scanHasPolicy(scan.value)) {
      if (!scanMatchesRequest(scan.value, request)) {
        // The backend refuses a different policy with 409 rather than mutating
        // active work. Waiting here avoids that known-busy request; completion
        // gets exactly one retry for the policy the user actually asked for.
        deferScanUntilCurrentCompletes(request);
      }
      startScanPoll();
    } else {
      // `complete` is progress, not a freshness proof: pictures may have been
      // imported since it finished. Ask the backend, which joins equivalent
      // active work and starts completed/failed rows anew.
      await triggerScan(request);
    }
    refreshCounts();
    // Do not prefetch the Mixed stacks page here. The first startup after a
    // cohesion-cache migration is necessarily cold, and that endpoint ranks
    // every live stack before paging. Launching it from the ordinary queue made
    // an optional page occupy the serial database worker during startup. The
    // page loads on showMixedStacks(); until then its warning chips are absent.
  }

  /**
   * Point the queue at one destination, sharing concurrent equivalent opens.
   *
   * @param {Object} [options]
   * @returns {Promise<void>}
   */
  function openQueue(options = {}) {
    const normalized = {
      type:
        !options.type || options.type === "library"
          ? GLOBAL_SCOPE
          : options.type,
      id: options.id ?? null,
      label: options.label ?? "",
      icon: options.icon ?? "",
      filters: options.filters ?? null,
    };
    const key = JSON.stringify({
      type: normalized.type,
      id: normalized.id,
      filters: normalized.filters
        ? {
            near: normalized.filters.near ?? null,
            embedding: normalized.filters.embedding ?? null,
            threshold: normalized.filters.threshold ?? null,
            decided: normalized.filters.decided ?? null,
            verdicts: normalized.filters.verdicts ?? null,
          }
        : null,
    });
    const joined = openQueueInFlight.get(key);
    if (joined) return joined;
    const request = openQueueOnce(normalized).finally(() => {
      if (openQueueInFlight.get(key) === request) openQueueInFlight.delete(key);
    });
    openQueueInFlight.set(key, request);
    return request;
  }

  /**
   * Widen a scoped queue back to the whole vault.
   *
   * The focus goes back to the top, exactly as it does when a tier is toggled.
   * The global queue is ordered by confidence across everything, so position 3
   * in a set's queue and position 3 in the global one are unrelated groups:
   * carrying the index over would silently drop the cursor three rows into a
   * list the user has not seen, with the row treatment insisting that is where
   * the keyboard acts.
   *
   * @returns {Promise<void>}
   */
  async function clearScope() {
    if (!isScoped.value) return;
    scopeType.value = GLOBAL_SCOPE;
    scopeId.value = null;
    scopeLabel.value = "";
    scopeIcon.value = "";
    await loadFirstPage();
    refreshCounts();
  }

  /**
   * Read a page's `next_cursor`, normalised to null when the server has none.
   *
   * Absent and null mean the same thing here and must: absent is an offset-only
   * server, null is a cursor server saying this was the last page, and both end
   * the cursor path.
   *
   * @param {Object} [data] - a queue response.
   * @returns {string|null}
   */
  function cursorFrom(data) {
    const cursor = data?.next_cursor;
    return typeof cursor === "string" && cursor ? cursor : null;
  }

  /**
   * Adopt the decided page's per-verdict counts from a queue response.
   *
   * Only the decided page carries them, and only the FIRST page is read for
   * them: the counts describe the whole scope, so re-reading them off every
   * appended page would be the same number three times. A server that predates
   * the field leaves the menu's counts at zero rather than at a stale figure.
   *
   * @param {Object} [data] - a queue response.
   */
  function adoptVerdictCounts(data) {
    if (!showingDecided.value) {
      decidedByVerdict.value = {};
      return;
    }
    const counts = data?.by_verdict;
    decidedByVerdict.value =
      counts && typeof counts === "object" ? { ...counts } : {};
  }

  /**
   * Load the first page of the queue, replacing whatever was there.
   *
   * Always an offset-0 request: a cursor is a position inside one ordering, so
   * the only honest way to start a queue whose policy or scope may just have
   * changed is from the top. The response decides which path pages it.
   *
   * @returns {Promise<void>}
   */
  async function loadFirstPage() {
    loading.value = true;
    error.value = null;
    // The window is being rebuilt (scope change, tier change, rescan, Home
    // after an End jump), so a selection over the old rows would silently
    // point at different groups - and so would a jump-to-end still chasing
    // the old list's tail, or a page request still in flight from it.
    windowEpoch += 1;
    // This rebuild's own claim on the window. Two first-page loads CAN be in
    // flight at once - the scan poll reloads an empty list on its own clock,
    // and the user can flip to Decided (or change scope/tier) meanwhile -
    // and only the NEWEST rebuild may write. Without this check the poll's
    // stale open-queue response landed after the Decided flip's rows and
    // wrote its empty page over them: "Still scanning…", then nothing.
    const epoch = windowEpoch;
    cancelEndChase();
    clearSelection();
    try {
      const data = await listGroups({
        ...policyArgs.value,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
        decided: showingDecided.value,
        verdicts: verdictArgs.value,
        offset: 0,
        limit: QUEUE_PAGE_SIZE,
      });
      if (epoch !== windowEpoch) return;
      adoptVerdictCounts(data);
      groups.value = Array.isArray(data?.groups) ? data.groups : [];
      windowStart.value = 0;
      total.value = Number(data?.total) || groups.value.length;
      nextOffset.value = groups.value.length;
      nextCursor.value = cursorFrom(data);
      // A cursor is the server's own answer to "is there more", and it outranks
      // the offset arithmetic: `total` is a live count under a running scan.
      hasMore.value =
        nextCursor.value !== null || nextOffset.value < total.value;
      scan.value = normalizeScan(data?.scan);
      focusIndex.value = groups.value.length ? 0 : -1;
    } catch (err) {
      // A stale rebuild's failure must not blank the fresh rebuild's rows
      // either - logged (never swallowed), then discarded like its success.
      if (epoch !== windowEpoch) {
        console.warn(
          "[dedup] discarding a superseded first-page load's failure",
          err,
        );
        return;
      }
      error.value = err;
      groups.value = [];
      windowStart.value = 0;
      total.value = 0;
      nextOffset.value = 0;
      nextCursor.value = null;
      hasMore.value = false;
      focusIndex.value = -1;
      console.warn("[dedup] failed to load the duplicate queue", err);
    } finally {
      // Only the CURRENT rebuild owns the loading flag: a superseded one
      // finishing late must not clear (or appear to clear) the fresh one's.
      if (epoch === windowEpoch) loading.value = false;
    }
  }

  /**
   * Re-read a bounded window around an absolute queue index without sending a
   * reviewer back to the head of the queue.
   *
   * This is deliberately narrower than {@link loadFirstPage}: it is the
   * reconciliation path for a successful undo that inserted a resolved group
   * back into the ordered queue. The view owns the pixel/signature viewport
   * anchor; this store owns replacing the server window while retaining the
   * focused group and any still-present multi-selection.
   *
   * @param {number} anchorIndex - absolute row currently at the viewport edge.
   * @param {Object} [options]
   * @param {string|null} [options.focusSignature] - focused group before reload.
   * @returns {Promise<void>}
   */
  async function reloadWindowAround(
    anchorIndex,
    { focusSignature = null } = {},
  ) {
    loading.value = true;
    error.value = null;
    windowEpoch += 1;
    const epoch = windowEpoch;
    cancelEndChase();

    // Use the server's largest legal page. A bulk verdict can restore many
    // adjacent groups ahead of the old anchor; the wide read keeps both that
    // restored run and the group that held keyboard focus in one coherent
    // window, while the component still mounts only its small virtual slice.
    const pageSize = Math.max(
      1,
      Number(bounds.value?.max_page_size) || QUEUE_PAGE_SIZE,
    );
    const before = Math.min(WINDOW_RELOAD_CONTEXT, pageSize - 1);
    const requestedIndex = Math.max(0, Math.floor(Number(anchorIndex) || 0));
    const offset = Math.max(0, requestedIndex - before);
    const selectedBefore = new Set(selectedSignatures.value);

    try {
      const data = await listGroups({
        ...policyArgs.value,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
        decided: showingDecided.value,
        verdicts: verdictArgs.value,
        offset,
        limit: pageSize,
      });
      if (epoch !== windowEpoch) return;

      adoptVerdictCounts(data);
      groups.value = Array.isArray(data?.groups) ? data.groups : [];
      windowStart.value = offset;
      total.value = Number(data?.total) || groups.value.length;
      nextOffset.value = offset + groups.value.length;
      nextCursor.value = cursorFrom(data);
      hasMore.value =
        nextCursor.value !== null || nextOffset.value < total.value;
      scan.value = normalizeScan(data?.scan);

      const heldSignatures = new Set(groups.value.map((g) => g.signature));
      selectedSignatures.value = new Set(
        [...selectedBefore].filter((signature) =>
          heldSignatures.has(signature),
        ),
      );
      selectionAnchor = null;

      const focusedLocal = focusSignature
        ? groups.value.findIndex((g) => g.signature === focusSignature)
        : -1;
      if (focusedLocal >= 0) {
        focusIndex.value = offset + focusedLocal;
      } else if (groups.value.length) {
        focusIndex.value = Math.max(
          offset,
          Math.min(offset + groups.value.length - 1, requestedIndex),
        );
      } else {
        focusIndex.value = -1;
      }
    } catch (err) {
      // The undo itself has already succeeded. A failed reconciliation must
      // leave the still-usable local window, focus and selection in place;
      // clearing them would turn a transient read failure into a second UI
      // failure and lose the reviewer's context anyway.
      if (epoch !== windowEpoch) {
        console.warn(
          "[dedup] discarding a superseded anchored reload's failure",
          err,
        );
        return;
      }
      error.value = err;
      console.warn("[dedup] failed to reload the duplicate queue in place", err);
    } finally {
      if (epoch === windowEpoch) loading.value = false;
    }
  }

  /**
   * Append the next page, if there is one.
   *
   * Cursor first: a keyset cursor over `(confidence DESC, signature)` cannot
   * re-serve or skip a group while a scan inserts rows, so a server that
   * publishes one is paged from it and the offset is never sent again.
   *
   * Without a cursor the offset path stands, mitigations and all: offset paging
   * over a table a scan is still inserting into can re-serve a group the client
   * already holds, a duplicated row would be resolvable twice and the second
   * verdict would fail, so a re-seen signature is dropped. The offset still
   * advances by the page's full length, because the server counted those rows
   * even though this client discarded some. The dedupe runs on both paths: it
   * costs a Set per page and it is the thing that keeps a mid-flight switch
   * between the two seamless.
   *
   * A caller that arrives while a page is already in flight JOINS it rather
   * than being dropped: `selectAll` pages in a loop and has to know when each
   * page has actually landed, and the view's scroll handler can fire in the
   * middle of that loop.
   *
   * @param {Object} [options]
   * @param {number} [options.limit] - page size, defaulting to the queue's.
   *   Clamped to the server's published maximum.
   * @returns {Promise<void>}
   */
  function loadMore(options = {}) {
    if (!hasMore.value || loading.value) return Promise.resolve();
    if (pageInFlight) return pageInFlight;
    pageInFlight = fetchNextPage(options).finally(() => {
      pageInFlight = null;
    });
    return pageInFlight;
  }

  /** The page request itself. Never called directly - go through `loadMore`. */
  async function fetchNextPage({ limit } = {}) {
    loadingMore.value = true;
    const epoch = windowEpoch;
    try {
      const cursor = nextCursor.value;
      const ceiling = Number(bounds.value?.max_page_size) || QUEUE_PAGE_SIZE;
      const pageSize = Math.min(
        Math.max(Number(limit) || QUEUE_PAGE_SIZE, 1),
        ceiling,
      );
      const data = await listGroups({
        ...policyArgs.value,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
        decided: showingDecided.value,
        verdicts: verdictArgs.value,
        ...(cursor === null ? { offset: nextOffset.value } : { cursor }),
        limit: pageSize,
      });
      // The window was replaced under this request (a reload, or an End jump
      // rebased onto the tail): these rows belong to the OLD window and
      // appending them would splice the middle of the queue onto its end.
      if (epoch !== windowEpoch) return;
      const page = Array.isArray(data?.groups) ? data.groups : [];
      const seen = new Set(groups.value.map((g) => g.signature));
      groups.value = [
        ...groups.value,
        ...page.filter((g) => !seen.has(g.signature)),
      ];
      nextOffset.value += page.length;
      nextCursor.value = cursorFrom(data);
      total.value = Number(data?.total) || total.value;
      // An empty page is the end whatever the total or the cursor says: a total
      // that shrank under a concurrent verdict would otherwise leave this
      // looping, and so would a server that keeps minting cursors past the end.
      hasMore.value =
        page.length > 0 &&
        (nextCursor.value !== null || nextOffset.value < total.value);
      scan.value = normalizeScan(data?.scan);
      // A page that lands into an emptied queue has to be given the cursor, or
      // the rows arrive with nothing focused and the keyboard model is dead
      // until the user clicks.
      if (focusIndex.value < 0 && groups.value.length) {
        focusIndex.value = windowStart.value;
      }
    } catch (err) {
      console.warn("[dedup] failed to page the duplicate queue", err);
    } finally {
      loadingMore.value = false;
    }
  }

  /**
   * Page the queue UPWARDS, prepending the rows just above the window.
   *
   * Only meaningful after an End jump has rebased the window off the top
   * (`windowStart > 0`): scrolling or stepping up from the jumped tail
   * backfills the rows above it, one offset page at a time, until the window
   * reaches the top. Always offset-paged and never sends a cursor - the
   * cursor chain names positions in a forward walk and is broken the moment
   * an offset jump happens; the two must never travel in one request.
   *
   * @param {Object} [options]
   * @param {number} [options.limit] - page size, defaulting to the queue's.
   * @returns {Promise<void>}
   */
  function loadPrevious(options = {}) {
    if (windowStart.value <= 0 || loading.value) return Promise.resolve();
    if (prevInFlight) return prevInFlight;
    prevInFlight = fetchPreviousPage(options).finally(() => {
      prevInFlight = null;
    });
    return prevInFlight;
  }

  /** The upward page itself. Never called directly - go through loadPrevious. */
  async function fetchPreviousPage({ limit } = {}) {
    const epoch = windowEpoch;
    try {
      const ceiling = Number(bounds.value?.max_page_size) || QUEUE_PAGE_SIZE;
      const pageSize = Math.min(
        Math.max(Number(limit) || QUEUE_PAGE_SIZE, 1),
        ceiling,
      );
      const prevOffset = Math.max(0, windowStart.value - pageSize);
      const data = await listGroups({
        ...policyArgs.value,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
        decided: showingDecided.value,
        verdicts: verdictArgs.value,
        offset: prevOffset,
        limit: windowStart.value - prevOffset,
      });
      if (epoch !== windowEpoch) return;
      const page = Array.isArray(data?.groups) ? data.groups : [];
      // Nothing served for a range that should exist: scan drift. Leave the
      // window alone; the next scroll tick retries from the same place.
      if (!page.length) return;
      const before = windowStart.value;
      const held = new Set(groups.value.map((g) => g.signature));
      const kept = page.filter((g) => !held.has(g.signature));
      groups.value = [...kept, ...groups.value];
      windowStart.value = prevOffset;
      // Under a running scan the page can be short, or overlap the window
      // (offset drift re-serving a row the client already holds - the same
      // hazard the downward fallback de-dupes). The pre-existing rows'
      // absolute indices then shift; keep the focused GROUP under the cursor.
      const shift = prevOffset + kept.length - before;
      if (shift !== 0 && focusIndex.value >= before) focusIndex.value += shift;
      if (focusIndex.value < 0 && groups.value.length) {
        // A tail window emptied by verdicts refills from above; the last row
        // is the nearest one to where the user was working.
        focusIndex.value = windowStart.value + groups.value.length - 1;
      }
      total.value = Number(data?.total) || total.value;
      scan.value = normalizeScan(data?.scan);
    } catch (err) {
      console.warn("[dedup] failed to page the duplicate queue upwards", err);
    }
  }

  /**
   * Move the focus (an ABSOLUTE queue index), clamped to the held window,
   * fetching ahead near either edge of it.
   * @param {number} index
   */
  function setFocus(index) {
    // Any focus move that is not the chase's own completion is the user (or a
    // verdict's auto-advance) acting: their position outranks a jump-to-end
    // still paging behind the scenes.
    cancelEndChase();
    if (!groups.value.length) {
      focusIndex.value = -1;
      return;
    }
    const first = windowStart.value;
    const last = windowStart.value + groups.value.length - 1;
    const clamped = Math.max(first, Math.min(last, index));
    focusIndex.value = clamped;
    if (clamped >= last + 1 - PREFETCH_MARGIN) loadMore();
    if (first > 0 && clamped < first + PREFETCH_MARGIN) loadPrevious();
  }

  /**
   * Jump to the FIRST group of the queue.
   *
   * On a top-anchored window this is just a focus move. After an End jump the
   * window no longer contains the top, so Home is a reset to the normal
   * cursor-paged first page - the exact inverse of the jump that left it.
   *
   * @returns {Promise<void>}
   */
  async function focusStart() {
    if (windowStart.value > 0) {
      await loadFirstPage();
      return;
    }
    setFocus(0);
  }

  // ── The End-key jump to the true end ─────────────────────────────────────
  // The queue's total is known a priori, so End does not have to walk there:
  // over a large gap it fetches the LAST page directly by offset and REBASES
  // the window onto it (windowStart moves), landing the focus on the true
  // last group off one request. Over a small gap (or none) rebasing would be
  // churn, so it keeps the old behaviour: focus the last held row, chasing
  // the couple of missing pages in sequence. The token invalidates either
  // path the moment anything else moves the focus; the ref lets the view pin
  // its scroll to the track's bottom (already sized from the server total)
  // while the work runs, and cancel it when the user scrolls away.

  /** Gaps at most this many browsing pages are chased, not jumped. */
  const END_JUMP_GAP_PAGES = 2;

  const endChaseActive = ref(false);
  let endChaseToken = 0;

  /** Stop a running jump-to-end, leaving focus and scroll where they are. */
  function cancelEndChase() {
    if (!endChaseActive.value) return;
    endChaseToken += 1;
    endChaseActive.value = false;
  }

  /** The tail request: ALWAYS offset-paged, never a cursor - the server
   * rejects the two together, and a jump is precisely the operation the
   * forward cursor chain cannot express. */
  function requestTailPage(offset) {
    return listGroups({
      ...policyArgs.value,
      scopeType: scopeType.value,
      scopeId: scopeId.value,
      decided: showingDecided.value,
      verdicts: verdictArgs.value,
      offset,
      limit: QUEUE_PAGE_SIZE,
    });
  }

  /**
   * Fetch the queue's last page and rebase the window onto it.
   *
   * Offset paging under a running scan can skip or re-serve a row; for a jump
   * that is acceptable and bounded (one page seam). If the aimed-at tail no
   * longer exists (the served total came back below the requested offset),
   * one re-aim from the served total is made; a still-empty page gives up and
   * leaves the window untouched, so the caller lands on the last row actually
   * held. Terminates in at most two requests by construction.
   *
   * @param {number} token - the chase token this jump runs under.
   * @returns {Promise<void>}
   */
  async function jumpToTail(token) {
    let tailOffset = Math.max(0, total.value - QUEUE_PAGE_SIZE);
    let data = await requestTailPage(tailOffset);
    if (token !== endChaseToken) return;
    let page = Array.isArray(data?.groups) ? data.groups : [];
    const servedTotal = Number(data?.total) || 0;
    if (!page.length && servedTotal > 0) {
      const retryOffset = Math.max(0, servedTotal - QUEUE_PAGE_SIZE);
      if (retryOffset < tailOffset) {
        data = await requestTailPage(retryOffset);
        if (token !== endChaseToken) return;
        page = Array.isArray(data?.groups) ? data.groups : [];
        tailOffset = retryOffset;
      }
    }
    if (Number(data?.total)) total.value = Number(data.total);
    scan.value = normalizeScan(data?.scan);
    if (!page.length) return;
    // REBASE. The old window's rows are dropped, so a selection over them
    // cannot survive (same rationale as loadFirstPage: a verdict must never
    // silently act on rows the client no longer holds). The epoch bump makes
    // any normal page still in flight discard itself on landing.
    windowEpoch += 1;
    clearSelection();
    const seen = new Set();
    groups.value = page.filter(
      (g) => !seen.has(g.signature) && seen.add(g.signature),
    );
    windowStart.value = tailOffset;
    nextCursor.value = null;
    nextOffset.value = tailOffset + groups.value.length;
    hasMore.value = nextOffset.value < total.value;
  }

  /**
   * Focus the TRUE last group of the queue in one gesture.
   *
   * Everything loaded: focus the last row, synchronously, exactly as before.
   * A small gap: chase the missing pages in sequence (rebasing for a page or
   * two is churn). A large gap: {@link jumpToTail} - one offset request for
   * the last page, window rebased onto it, no walk through the middle. All
   * paths land the focus on the last row actually received and terminate
   * under a running scan; and all die silently the moment the user moves the
   * focus or the view cancels the jump - a stale jump that yanks the scroll
   * later is worse than the bug it fixes.
   *
   * @returns {Promise<void>}
   */
  async function focusEnd() {
    if (!groups.value.length) return;
    if (!hasMore.value) {
      setFocus(windowStart.value + groups.value.length - 1);
      return;
    }
    const token = ++endChaseToken;
    endChaseActive.value = true;
    try {
      const windowEnd = windowStart.value + groups.value.length;
      const gap = Math.max(0, total.value - windowEnd);
      if (gap > END_JUMP_GAP_PAGES * QUEUE_PAGE_SIZE) {
        await jumpToTail(token);
      } else {
        const pageSize = Number(bounds.value?.max_page_size) || QUEUE_PAGE_SIZE;
        while (hasMore.value) {
          const before = groups.value.length;
          await loadMore({ limit: pageSize });
          // Someone moved the focus or reloaded the list: their position wins.
          if (token !== endChaseToken) return;
          // A page that added nothing is the end whatever `hasMore` claims,
          // exactly as selectAll treats it: without this a failed request or
          // a total that leads a running scan would spin the loop forever.
          if (groups.value.length === before) break;
        }
      }
    } catch (err) {
      // loadMore and listGroups failures are already logged; this keeps a
      // programming error from becoming an unhandled rejection on a keypress.
      console.warn("[dedup] the jump to the end of the queue failed", err);
    } finally {
      if (token === endChaseToken) endChaseActive.value = false;
    }
    if (token !== endChaseToken) return;
    // The chase is already over, so setFocus's cancelEndChase is a no-op here
    // rather than a self-cancellation. After a successful jump this is the
    // last row of the tail page; after a failed one, the last row still held.
    if (groups.value.length) {
      setFocus(windowStart.value + groups.value.length - 1);
    }
  }

  // ── The decided page ─────────────────────────────────────────────────────
  // The queue's flip side: resolved groups with their live verdict, so a
  // decision can be reviewed and cleared (owner request, 2026-07-29 - this
  // replaces the sticky "Kept N pictures separate" notice as the way back).

  const showingDecided = ref(false);

  async function toggleDecided() {
    showingDecided.value = !showingDecided.value;
    // The three pages are one at a time. Unlike the Decided flip this costs
    // nothing to leave: the mixed list stays loaded behind it, because the
    // queue's chip reads the same flags.
    showingMixed.value = false;
    mixedFocusStackId.value = null;
    await loadFirstPage();
  }

  // ── Multi-select ─────────────────────────────────────────────────────────
  // Ctrl+click toggles a group in and out; Shift+click selects the range from
  // the anchor (the last toggled or focused row). A verdict given to any
  // selected group applies to the whole selection - the row buttons say so.

  const selectedSignatures = ref(new Set());
  let selectionAnchor = null;

  const selectionCount = computed(() => selectedSignatures.value.size);

  /** @param {string} signature @returns {boolean} */
  function isSelected(signature) {
    return selectedSignatures.value.has(signature);
  }

  function clearSelection() {
    selectionAnchor = null;
    if (selectedSignatures.value.size) selectedSignatures.value = new Set();
  }

  /** Ctrl+click: toggle one group, move the focus and the range anchor there.
   * `index` is absolute, like every public index. */
  function toggleSelected(index) {
    const sig = groups.value[index - windowStart.value]?.signature;
    if (!sig) return;
    const next = new Set(selectedSignatures.value);
    // Grid parity: the FIRST Ctrl+click starts a multi-selection from the row
    // the user is on, so it must not trade the focused row for the clicked
    // one - both end up selected. (Ctrl+clicking the focused row itself still
    // just toggles it.)
    if (!next.size && focusIndex.value >= 0 && focusIndex.value !== index) {
      const focusedSig =
        groups.value[focusIndex.value - windowStart.value]?.signature;
      if (focusedSig) next.add(focusedSig);
    }
    if (next.has(sig)) next.delete(sig);
    else next.add(sig);
    selectedSignatures.value = next;
    selectionAnchor = index;
    setFocus(index);
  }

  /**
   * Ctrl+A: every group in the queue, not just the pages already fetched.
   *
   * Selecting "what happens to be loaded" made the gesture mean a different
   * thing depending on how far the user had scrolled - 40 groups out of 300,
   * with nothing on screen saying so. So this pages the rest in first, at the
   * server's maximum page size rather than the queue's browsing page size.
   *
   * It stops at {@link SELECT_ALL_MAX}, because the selection is not free: a
   * verdict on it is one request per group, and the queue's own rule is never
   * to hold the whole thing in memory. Hitting the ceiling is reported rather
   * than hidden, so "all" never silently means "some".
   *
   * @returns {Promise<{selected: number, total: number, truncated: boolean}>}
   */
  async function selectAll() {
    if (!groups.value.length) {
      return { selected: 0, total: 0, truncated: false };
    }
    const pageSize = Number(bounds.value?.max_page_size) || QUEUE_PAGE_SIZE;
    // After an End jump the window hangs off the top: "all" still means the
    // whole queue, so the rows ABOVE the window page back in first.
    while (windowStart.value > 0 && groups.value.length < SELECT_ALL_MAX) {
      const before = windowStart.value;
      await loadPrevious({ limit: pageSize });
      // An upward page that moved nothing (drift, a failed request) must not
      // spin the loop; the truncation flag below reports the shortfall.
      if (windowStart.value === before) break;
    }
    while (hasMore.value && groups.value.length < SELECT_ALL_MAX) {
      const before = groups.value.length;
      await loadMore({ limit: pageSize });
      // A page that added nothing is the end of the queue, whatever `hasMore`
      // still claims. Without this the loop would spin on a failed request.
      if (groups.value.length === before) break;
    }
    selectedSignatures.value = new Set(groups.value.map((g) => g.signature));
    selectionAnchor = null;
    return {
      selected: selectedSignatures.value.size,
      total: Math.max(total.value, selectedSignatures.value.size),
      truncated: hasMore.value || windowStart.value > 0,
    };
  }

  /** Shift+click: select the whole run from the anchor to `index` (absolute). */
  function selectRange(index) {
    if (!groups.value.length) return;
    const from =
      selectionAnchor ??
      (focusIndex.value < 0 ? windowStart.value : focusIndex.value);
    const [lo, hi] = from <= index ? [from, index] : [index, from];
    const next = new Set();
    for (let i = lo; i <= hi; i += 1) {
      const sig = groups.value[i - windowStart.value]?.signature;
      if (sig) next.add(sig);
    }
    selectedSignatures.value = next;
    setFocus(index);
  }

  /**
   * The groups a verdict on `group` applies to: the whole multi-selection when
   * the acted-on group is part of it, else just that group. Queue order, so
   * the narration and the auto-advance read top to bottom.
   *
   * @param {Object} group
   * @returns {Object[]}
   */
  function verdictTargets(group) {
    if (
      group &&
      selectedSignatures.value.size > 1 &&
      selectedSignatures.value.has(group.signature)
    ) {
      return groups.value.filter((g) =>
        selectedSignatures.value.has(g.signature),
      );
    }
    return group ? [group] : [];
  }

  /** Move the focus one group down. */
  function focusNext() {
    setFocus(focusIndex.value + 1);
  }

  /** Move the focus one group up. */
  function focusPrev() {
    setFocus(focusIndex.value - 1);
  }

  /**
   * Drop a resolved group and land the focus on the next open one.
   *
   * Auto-advance keeps the index where it is, because removing the row at that
   * index means the next group has already slid into it. Only a verdict on the
   * last row walks the focus backwards.
   *
   * @param {string} signature
   */
  function removeGroup(signature) {
    const local = groups.value.findIndex((g) => g.signature === signature);
    if (local < 0) return;
    const absolute = windowStart.value + local;
    groups.value = groups.value.filter((g) => g.signature !== signature);
    if (selectedSignatures.value.has(signature)) {
      const next = new Set(selectedSignatures.value);
      next.delete(signature);
      selectedSignatures.value = next;
    }
    const { [signature]: _cover, ...restCovers } = coverChoices.value;
    coverChoices.value = restCovers;
    const { [signature]: _out, ...restExclusions } = exclusions.value;
    exclusions.value = restExclusions;
    // The row left the client's list and the server's unresolved set at once,
    // so the offset the next page starts from moves with it. A keyset cursor
    // needs no such correction: it names a position in the ordering rather than
    // a count of rows before it, so resolving a group cannot shift it.
    if (nextCursor.value === null) {
      nextOffset.value = Math.max(0, nextOffset.value - 1);
    }
    total.value = Math.max(0, total.value - 1);
    if (!groups.value.length) {
      focusIndex.value = -1;
      // A page can be emptied faster than the read-ahead refills it. Without
      // this the queue shows its done state while the server still holds
      // thousands of groups, which is the one lie a to-do count cannot afford.
      // A jumped tail window that empties refills from ABOVE itself: rows
      // still exist there even when nothing is left below.
      if (hasMore.value) loadMore();
      else if (windowStart.value > 0) loadPrevious();
      return;
    }
    setFocus(Math.min(absolute, windowStart.value + groups.value.length - 1));
  }

  // --- Live picture events -------------------------------------------------
  //
  // The queue rows are a snapshot of a server read, and nothing about a
  // scrapheap move goes through this store: the user deletes from the grid, the
  // lightbox or another tab, and the loaded row keeps drawing a candidate whose
  // thumbnail now 404s: the owner's "empty placeholder", and a group of one
  // still offering a Stack the server would refuse.
  //
  // The COUNTS were never the gap: `pictures_changed` already reaches
  // `useSidebarRefresh`, which refreshes the badge on every removal and every
  // restore. What was missing is the LIST, and it is repaired here rather than
  // in the view so it works whichever route is mounted.
  //
  // The decision table below is deliberately narrow, and asymmetric on purpose:
  //
  //   * **A removal is applied surgically.** The event names exactly which
  //     pictures went, so the affected rows can be rewritten and the ones that
  //     drop below two units removed: the same rule `live_groups_filter`
  //     applies server-side. `loadFirstPage` would be the easy answer and it is
  //     the wrong one: it rebases the window on the queue's head, which throws
  //     a user 250 rows into a triage back to row 1 for a change that touched
  //     one row.
  //   * **A restore is NOT inserted.** The group returns to the server's
  //     unresolved set at a position in the confidence ordering the client
  //     cannot compute, and there is no per-signature read to fetch it with.
  //     The queue has never been a live insert surface (a scan's new groups
  //     arrive by paging too), so the badge carries the change and the row
  //     comes back with the next page: UNLESS the window is empty, where
  //     "nothing left to review" would be an outright lie and there is nothing
  //     on screen to disturb by rebuilding it.
  //
  // No coalescing window: a bulk scrapheap move broadcasts ONE event carrying
  // every id, and the work is a map over the ≤ one page of rows that are
  // actually loaded. This is the undo/redo subscription's counterpart, not a
  // replacement: that one handles a dedup verdict coming back, which no
  // picture event announces.

  /**
   * Rebuild the queue only when there is nothing on screen to disturb.
   *
   * @param {string} reason - for the returned descriptor.
   * @returns {{action: string, reason: string}}
   */
  function refillIfEmpty(reason) {
    if (showingDecided.value) {
      // The decided page lists resolved groups whatever happened to their
      // members, so nothing returns to it and nothing leaves it.
      return { action: "ignored", reason: `${reason}-decided-page` };
    }
    if (groups.value.length || loading.value) {
      return { action: "ignored", reason: `${reason}-mid-triage` };
    }
    // loadFirstPage owns its own error handling and never rejects.
    loadFirstPage();
    return { action: "reload", reason };
  }

  /**
   * Take the given pictures out of every loaded group.
   *
   * @param {Array<number|string>} pictureIds
   * @returns {{action: string, reason: string, dropped: Array<string>}}
   */
  function dropPictures(pictureIds) {
    const removed = new Set(pictureIds);
    const doomed = [];
    let touched = 0;
    groups.value = groups.value.map((group) => {
      const rewritten = groupWithoutPictures(group, removed);
      if (!rewritten) return group;
      touched += 1;
      // A group that no longer spans two stack UNITS poses no decision, the
      // second HAVING clause of the server's live_groups_filter, applied to the
      // rows already on screen. The decided page keeps its thinned rows for the
      // same reason the server does: the verdict happened, and "clear this
      // decision" is the only way back to it.
      if (
        !showingDecided.value &&
        groupUnits(rewritten).length < MIN_STACK_MEMBERS
      ) {
        doomed.push(rewritten.signature);
      }
      return rewritten;
    });
    // Per-group choices can now name a picture that is gone: an exclusion would
    // be sent to the server as an excluded id it cannot resolve, and a cover
    // choice would survive `coverIdFor`'s unit lookup as a raw deleted id.
    for (const group of groups.value) {
      const signature = group?.signature;
      if (signature === undefined || signature === null) continue;
      const chosen = coverChoices.value[signature];
      if (chosen !== undefined && removed.has(chosen)) {
        const { [signature]: _gone, ...rest } = coverChoices.value;
        coverChoices.value = rest;
      }
      const excluded = exclusions.value[signature];
      if (!excluded) continue;
      const kept = excluded.filter((id) => !removed.has(id));
      if (kept.length !== excluded.length) {
        exclusions.value = { ...exclusions.value, [signature]: kept };
      }
    }
    // removeGroup is what already knows how to take a row out: it walks the
    // focus, drops the selection, forgets the per-group choices, corrects the
    // offset and refills an emptied window.
    for (const signature of doomed) removeGroup(signature);
    if (doomed.length) {
      openCount.value = Math.max(0, openCount.value - doomed.length);
      invalidateScopeCounts();
    }
    if (!touched) return { action: "ignored", reason: "removed-untouched" };
    return { action: "targeted", reason: "removed", dropped: doomed };
  }

  /**
   * Apply one live picture-mutation event to the loaded queue.
   *
   * Called for every `/ws/updates` picture event regardless of origin: unlike
   * the grid, this store never applies a scrapheap move optimistically, so its
   * own tab's echo is as new to it as another tab's.
   *
   * @param {Object} payload - a `/ws/updates` message.
   * @returns {{action: string, reason: string}} the DECISION, for tests and
   *   logging; callers ignore it.
   */
  function applyPictureEvent(payload) {
    if (payload?.type !== "pictures_changed") {
      return { action: "ignored", reason: "not-a-picture-change" };
    }
    const changeKind = payload?.change_kind;
    const pictureIds = Array.isArray(payload?.picture_ids)
      ? payload.picture_ids
      : [];
    if (changeKind === "removed") {
      // A removal with no ids (a bulk purge that could not name them) is
      // untargetable: there is no way to tell which rows it thinned, so the
      // window is rebuilt only if it is empty, exactly like a restore.
      if (!pictureIds.length) {
        return refillIfEmpty("removed-untargetable-empty-ids");
      }
      return dropPictures(pictureIds);
    }
    if (changeKind === "restored") return refillIfEmpty("restored");
    return { action: "ignored", reason: `change-kind-${changeKind ?? "none"}` };
  }

  /**
   * Raise the standard action receipt for a verdict that recorded an
   * operation.
   *
   * Everywhere else the receipt rides the mutation's own-origin WebSocket
   * echo: the backend emits `pictures_changed`, App.vue hands it to
   * `useOperationStore.onPictureEvent`, and the debounced refresh narrates
   * the newest own operation. The dedup verdict service emits NO WebSocket
   * event (backend gap, reported), so that pipeline never fires and a stack
   * verdict produced no pill. The verdict RESPONSE is the trigger instead:
   * the same `refresh({ narrate: true })` → `narrateNewest` → receipt path,
   * just started by the response that proves the operation exists. The
   * operation store's own guards keep it honest - it narrates only an
   * own-origin operation above its high-water mark, so a WS echo arriving
   * later cannot double-narrate.
   *
   * Called only when the response carries a `batch_id`: that is the marker
   * that an operation-log row was recorded (a stack always mints one; a
   * keep-separate does only on a backend that has made it undoable - older
   * backends return null there and this degrades silently to no receipt).
   */
  function narrateVerdictOperation() {
    try {
      useOperationStore().refresh({ narrate: true });
    } catch (err) {
      // The verdict itself landed; only its narration is lost. Logged so the
      // silent pill does not become an unexplained mystery.
      console.warn(
        "[dedup] could not refresh the operation log for the verdict receipt",
        err,
      );
    }
  }

  /**
   * Reconcile the badge with the server after a verdict.
   *
   * The optimistic decrement in {@link stack} and {@link keepSeparate} is what
   * makes the badge feel instant, but nothing else will ever correct it: a
   * keep-separate mutates no picture row, so it raises no WebSocket event and
   * `App.refreshSidebar` never runs for it. Left alone the badge is wrong in a
   * second tab from the first verdict and drifts further with every one after.
   * One scope, one cheap COUNT, fired behind the optimistic tick rather than
   * awaited, so auto-advance is not held up by it.
   */
  function reconcileCounts() {
    refreshCounts().catch((err) => {
      // refreshCounts already swallows and logs its own failures; this only
      // catches a programming error in it, which must not become an unhandled
      // rejection on a keypress.
      console.warn("[dedup] could not reconcile the duplicate counts", err);
    });
  }

  /**
   * Stack one group.
   *
   * Records one operation, so the shared receipt narrates it and Ctrl+Z reverses
   * it without this store doing anything undo-specific.
   *
   * @param {Object} group
   * @param {Object} [options]
   * @param {string} [options.batchId]
   * @returns {Promise<Object|null>} the verdict response, or null on failure.
   */
  async function stack(group, { batchId } = {}) {
    // The decided page reviews verdicts; it never gives them. Enter on a
    // decided row must be inert, not a silent re-stack.
    if (showingDecided.value) return null;
    const targets = verdictTargets(group);
    if (targets.length > 1) {
      const gestureId = batchId || newOperationBatchId();
      busy.value = true;
      try {
        const result = await applyVerdictBatch(
          targets.map((target) => ({
            verdict: "stacked",
            signature: target.signature,
            coverPictureId: coverIdFor(target),
            excludedPictureIds: effectiveExcludedFor(target),
          })),
          { batchId: gestureId },
        );
        commitStackedGroups(targets);
        clearSelection();
        if (result?.batch_id) narrateVerdictOperation();
        return {
          ...result,
          gesture_skipped: (result?.results ?? []).flatMap(
            (item) => item.skipped ?? [],
          ),
        };
      } catch (err) {
        error.value = err;
        console.warn("[dedup] failed to apply the bulk stack gesture", err);
        if (isAmbiguousMutationError(err)) {
          await reconcileAfterUncertainMutation(err);
        }
        return {
          failed: true,
          uncertain: isAmbiguousMutationError(err),
          completed: 0,
          requested: targets.length,
        };
      } finally {
        busy.value = false;
      }
    }
    const result = await stackOne(group, { batchId });
    if (result?.batch_id) narrateVerdictOperation();
    return result
      ? { ...result, gesture_skipped: result.skipped ?? [] }
      : result;
  }

  async function stackOne(group, { batchId } = {}) {
    if (!group || busy.value) return null;
    busy.value = true;
    try {
      const result = await stackGroup(group.signature, {
        coverPictureId: coverIdFor(group),
        // Locked candidates ride along with the user's own exclusions: the
        // server would skip them anyway and report them in `skipped`, and
        // sending them would make every frozen group a partial success rather
        // than the clean stack the queue already knows it is.
        excludedPictureIds: effectiveExcludedFor(group),
        batchId,
      });
      commitStackedGroups([group]);
      return result;
    } catch (err) {
      error.value = err;
      console.warn(`[dedup] failed to stack group ${group.signature}`, err);
      if (isAmbiguousMutationError(err)) {
        await reconcileAfterUncertainMutation(err);
      }
      return null;
    } finally {
      busy.value = false;
    }
  }

  /**
   * Apply one settled stack gesture to the visible queue in one assignment.
   *
   * A bulk gesture commits atomically on the server and is reflected here in
   * one assignment only after that response lands, so selected rows never jump
   * while their shared transaction is in flight.
   *
   * @param {Object[]} stackedGroups
   */
  function commitStackedGroups(stackedGroups) {
    const signatures = new Set(
      stackedGroups.map((group) => group?.signature).filter(Boolean),
    );
    if (!signatures.size) return;
    const firstLocal = groups.value.findIndex((group) =>
      signatures.has(group.signature),
    );
    const removed = groups.value.filter((group) =>
      signatures.has(group.signature),
    ).length;
    if (!removed) return;

    groups.value = groups.value.filter(
      (group) => !signatures.has(group.signature),
    );
    selectedSignatures.value = new Set(
      [...selectedSignatures.value].filter(
        (signature) => !signatures.has(signature),
      ),
    );
    coverChoices.value = Object.fromEntries(
      Object.entries(coverChoices.value).filter(
        ([signature]) => !signatures.has(signature),
      ),
    );
    exclusions.value = Object.fromEntries(
      Object.entries(exclusions.value).filter(
        ([signature]) => !signatures.has(signature),
      ),
    );
    if (nextCursor.value === null) {
      nextOffset.value = Math.max(0, nextOffset.value - removed);
    }
    total.value = Math.max(0, total.value - removed);
    stackedCount.value += removed;
    openCount.value = Math.max(0, openCount.value - removed);
    invalidateScopeCounts();
    reconcileCounts();

    if (!groups.value.length) {
      focusIndex.value = -1;
      if (hasMore.value) loadMore();
      else if (windowStart.value > 0) loadPrevious();
      return;
    }
    const absolute = windowStart.value + Math.max(0, firstLocal);
    setFocus(Math.min(absolute, windowStart.value + groups.value.length - 1));
  }

  /**
   * Keep one group separate.
   *
   * No picture row changes, but the decision itself records one undoable
   * operation (owner override, 2026-07-30) whose `batch_id` rides the
   * response; narration is gated on it so an older backend (null there)
   * degrades to no receipt. {@link reopen} remains the explicit non-undo way
   * back.
   *
   * @param {Object} group
   * @returns {Promise<Object|null>} the verdict response, or null on failure.
   */
  async function keepSeparate(group, { batchId } = {}) {
    if (showingDecided.value) return null;
    const targets = verdictTargets(group);
    if (targets.length > 1) {
      const gestureId = batchId || newOperationBatchId();
      busy.value = true;
      try {
        const result = await applyVerdictBatch(
          targets.map((target) => ({
            verdict: "keep_separate",
            signature: target.signature,
          })),
          { batchId: gestureId },
        );
        commitSeparatedGroups(targets);
        clearSelection();
        if (result?.batch_id) narrateVerdictOperation();
        return {
          ...result,
          completed: targets.length,
          requested: targets.length,
        };
      } catch (err) {
        error.value = err;
        console.warn(
          "[dedup] failed to apply the bulk keep-separate gesture",
          err,
        );
        if (isAmbiguousMutationError(err)) {
          await reconcileAfterUncertainMutation(err);
        }
        return {
          failed: true,
          uncertain: isAmbiguousMutationError(err),
          completed: 0,
          requested: targets.length,
        };
      } finally {
        busy.value = false;
      }
    }
    const result = await keepSeparateOne(group, { batchId });
    if (result?.batch_id) narrateVerdictOperation();
    return result;
  }

  async function keepSeparateOne(group, { batchId } = {}) {
    if (!group || busy.value) return null;
    busy.value = true;
    try {
      const result = await keepGroupSeparate(group.signature, { batchId });
      commitSeparatedGroups([group]);
      return result;
    } catch (err) {
      error.value = err;
      console.warn(
        `[dedup] failed to keep group ${group.signature} separate`,
        err,
      );
      if (isAmbiguousMutationError(err)) {
        await reconcileAfterUncertainMutation(err);
      }
      return null;
    } finally {
      busy.value = false;
    }
  }

  function commitSeparatedGroups(separatedGroups) {
    const signatures = new Set(
      separatedGroups.map((group) => group?.signature).filter(Boolean),
    );
    if (!signatures.size) return;
    const removed = groups.value.filter((group) =>
      signatures.has(group.signature),
    ).length;
    groups.value = groups.value.filter(
      (group) => !signatures.has(group.signature),
    );
    selectedSignatures.value = new Set(
      [...selectedSignatures.value].filter(
        (signature) => !signatures.has(signature),
      ),
    );
    separatedCount.value += signatures.size;
    if (removed) {
      if (nextCursor.value === null) {
        nextOffset.value = Math.max(0, nextOffset.value - removed);
      }
      total.value = Math.max(0, total.value - removed);
      openCount.value = Math.max(0, openCount.value - removed);
    }
    invalidateScopeCounts();
    reconcileCounts();
    if (!groups.value.length) {
      focusIndex.value = -1;
      if (hasMore.value) loadMore();
      else if (windowStart.value > 0) loadPrevious();
    } else if (focusIndex.value >= windowStart.value + groups.value.length) {
      setFocus(windowStart.value + groups.value.length - 1);
    }
  }

  function isAmbiguousMutationError(err) {
    return Number(err?.response?.status) >= 500;
  }

  async function reconcileAfterUncertainMutation(mutationError) {
    // A 5xx can arrive after the transaction committed. Re-read before a retry
    // so the user never submits the same verdict against a stale row.
    await loadFirstPage();
    await refreshCounts();
    error.value = mutationError;
  }

  /**
   * Return a decided group to the queue ("Clear decision").
   *
   * Clearing a stacked verdict dissolves the stack that verdict created, so
   * the group genuinely returns to review instead of only leaving the Decided
   * page. When the clear unstacked pictures the response carries a `batch_id`
   * (one undoable `dedup.reopen` operation) and the shared receipt narrates
   * it, exactly like the verdict paths; a picture-neutral clear returns null
   * there and stays receipt-less. The group comes back only if it has been
   * re-detected; when it has not, the response says so and the next scan
   * brings it back, which the caller must report honestly rather than
   * implying the row will reappear.
   *
   * @param {string} signature
   * @param {Object} [options]
   * @param {string} [options.batchId] - client gesture id grouping several
   *   clears into one undo step.
   * @returns {Promise<Object|null>} the reopen response, or null on failure.
   */
  async function reopen(signature, { batchId } = {}) {
    try {
      const result = await reopenGroup(signature, { batchId });
      invalidateScopeCounts();
      await loadFirstPage();
      refreshCounts();
      if (result?.batch_id) narrateVerdictOperation();
      return result;
    } catch (err) {
      error.value = err;
      console.warn(`[dedup] failed to reopen group ${signature}`, err);
      return null;
    }
  }

  /**
   * Clear several decisions in one gesture (the Decided page's bulk path).
   *
   * One reload at the end rather than per group - reopen() reloads per call,
   * which is right for one and quadratic for fifty. Every clear shares one
   * client gesture id, so the clears that recorded an operation (the ones
   * that unstacked pictures) reverse as ONE undo step; a single receipt
   * narrates the gesture when any of them did.
   *
   * @param {string[]} signatures
   * @returns {Promise<{cleared: number, returned: number}>}
   */
  async function reopenMany(signatures) {
    let cleared = 0;
    let returned = 0;
    let recorded = false;
    const gestureId = newOperationBatchId();
    for (const signature of signatures) {
      try {
        const result = await reopenGroup(signature, { batchId: gestureId });
        cleared += 1;
        if (result?.group_returned_to_queue) returned += 1;
        if (result?.batch_id) recorded = true;
      } catch (err) {
        error.value = err;
        console.warn(`[dedup] failed to reopen group ${signature}`, err);
        break;
      }
    }
    if (cleared) {
      invalidateScopeCounts();
      await loadFirstPage();
      refreshCounts();
      // One receipt per GESTURE, mirroring stack(): the batch is one undo step.
      if (recorded) narrateVerdictOperation();
    }
    return { cleared, returned };
  }

  /**
   * Turn a tier on or off.
   *
   * Enabling a tier requires the tier above it and disabling one drops every
   * looser tier with it, so a user cannot land on "same scene" suggestions
   * without having deliberately walked down to them. The server enforces the
   * same rule; this mirrors it so the UI never sends a request it knows is a
   * 400.
   *
   * @param {string} id - a tier id from `bounds.tiers`.
   * @param {boolean} on
   * @returns {Promise<void>}
   */
  async function setTierEnabled(id, on) {
    const before = [nearEnabled.value, embeddingEnabled.value];
    if (id === "near") {
      nearEnabled.value = on;
      if (!on) embeddingEnabled.value = false;
    } else if (id === "embedding") {
      embeddingEnabled.value = on;
      if (on) nearEnabled.value = true;
    } else {
      return;
    }
    if (
      before[0] === nearEnabled.value &&
      before[1] === embeddingEnabled.value
    ) {
      return;
    }
    rememberFilters();
    // Enabling a tier loosens detection, and looser groups only exist in the
    // cache once a scan has looked for them. Disabling narrows a query over
    // the existing superset, so no rescan is needed there.
    if (on) await triggerScan();
    await loadFirstPage();
    refreshCounts();
  }

  /**
   * Move the similarity threshold and reload.
   *
   * Clamped to the server's published bounds rather than to a number repeated
   * here: below the floor is a 400, deliberately, because a low threshold
   * produces confident-looking garbage and destroys trust in the count.
   *
   * @param {number} value
   * @returns {Promise<void>}
   */
  async function setThreshold(value) {
    const next = Number(value);
    if (!Number.isFinite(next)) return;
    const min = Number(bounds.value?.min_threshold);
    const max = Number(bounds.value?.max_threshold);
    const clamped = Math.max(
      Number.isFinite(min) ? min : next,
      Math.min(Number.isFinite(max) ? max : next, next),
    );
    if (clamped === threshold.value) return;
    const loosened = clamped < threshold.value;
    threshold.value = clamped;
    rememberFilters();
    // Lowering the threshold asks for groups a stricter scan never wrote to
    // the cache; raising it just narrows the query over what is already there.
    if (loosened) await triggerScan();
    await loadFirstPage();
    refreshCounts();
    // The mixed list IS a function of the threshold, but it is an optional page.
    // Reload it only after it has actually been opened; otherwise a threshold
    // change on the ordinary queue would reintroduce the cold all-stack scan.
    if (mixedLoaded.value || showingMixed.value) await loadMixedStacks();
  }

  /** Snapshot the scope and policy a scan request must retain across a retry. */
  function currentScanRequest() {
    return {
      policy: { ...policyArgs.value },
      scopeType: scopeType.value,
      scopeId: scopeId.value,
    };
  }

  function scanRequestKey(request) {
    return JSON.stringify({
      scopeType: request.scopeType,
      scopeId: request.scopeId ?? null,
      nearEnabled: Boolean(request.policy?.nearEnabled),
      embeddingEnabled: Boolean(request.policy?.embeddingEnabled),
      threshold: Number(request.policy?.threshold),
    });
  }

  function requestedTiers(request) {
    const tiers = ["exact"];
    if (request.policy?.nearEnabled) tiers.push("near");
    if (request.policy?.embeddingEnabled) tiers.push("embedding");
    return tiers;
  }

  function scanHasPolicy(active) {
    return (
      Array.isArray(active?.tiers) &&
      active.tiers.length > 0 &&
      Number.isFinite(active?.threshold)
    );
  }

  function scanMatchesRequest(active, request) {
    const wanted = requestedTiers(request);
    return (
      active.tiers.length === wanted.length &&
      active.tiers.every((tier, index) => tier === wanted[index]) &&
      Number(active.threshold) === Number(request.policy?.threshold)
    );
  }

  let deferredScanRequest = null;

  function deferScanUntilCurrentCompletes(request) {
    deferredScanRequest = { request, retriesRemaining: 1 };
  }

  function busyScanFrom(err) {
    const detail = err?.response?.data?.detail;
    return detail?.code === "dedup_scan_busy" ? detail.active_scan : null;
  }

  /**
   * Queue a scan for one captured scope/policy and adopt its progress.
   * @returns {Promise<Object|null>}
   */
  async function triggerScan(
    request = currentScanRequest(),
    { deferOnBusy = true } = {},
  ) {
    const key = scanRequestKey(request);
    const joined = scanRequestInFlight.get(key);
    if (joined) return joined;
    const pending = (async () => {
      try {
        const data = await startScan(request);
        error.value = null;
        scan.value = normalizeScan(data);
        startScanPoll();
        return data;
      } catch (err) {
        const active = busyScanFrom(err);
        if (active) {
          scan.value = normalizeScan(active);
          if (deferOnBusy) deferScanUntilCurrentCompletes(request);
          startScanPoll();
          return active;
        }
        error.value = err;
        console.warn("[dedup] failed to start a duplicate scan", err);
        return null;
      }
    })().finally(() => {
      if (scanRequestInFlight.get(key) === pending) {
        scanRequestInFlight.delete(key);
      }
    });
    scanRequestInFlight.set(key, pending);
    return pending;
  }

  // --- Scan progress polling ----------------------------------------------
  // The banner and the counts are only honest while someone re-reads them:
  // tier-2 groups commit after every bucket, but nothing pushes that to the
  // client. The poll runs only while a scan is pending/running, and reloads
  // the group list only while the queue is still EMPTY - so the first finds
  // surface on their own, and a triage already in progress is never yanked
  // back to the top.
  let scanPollTimer = null;
  let scanPollGeneration = 0;
  let scanPollTickGeneration = null;

  function stopScanPoll({ discardDeferred = true } = {}) {
    scanPollGeneration += 1;
    if (scanPollTimer) {
      clearInterval(scanPollTimer);
      scanPollTimer = null;
    }
    if (discardDeferred) deferredScanRequest = null;
  }

  function startScanPoll() {
    if (scanPollTimer || !isScanning.value) return;
    const generation = ++scanPollGeneration;
    scanPollTimer = setInterval(async () => {
      if (scanPollTickGeneration === generation) return;
      scanPollTickGeneration = generation;
      try {
        const wasScanning = isScanning.value;
        await refreshCounts();
        if (generation !== scanPollGeneration) return;
        if (!groups.value.length || (wasScanning && !isScanning.value)) {
          await loadFirstPage();
        }
        if (generation !== scanPollGeneration) return;
        if (!isScanning.value) {
          const deferred = deferredScanRequest;
          deferredScanRequest = null;
          stopScanPoll({ discardDeferred: false });
          if (deferred?.retriesRemaining > 0) {
            await triggerScan(deferred.request, { deferOnBusy: false });
          }
        }
      } finally {
        if (scanPollTickGeneration === generation) {
          scanPollTickGeneration = null;
        }
      }
    }, 2000);
  }

  onScopeDispose(stopScanPoll);

  /**
   * Preview the bulk auto-stack of the exact tier.
   * @returns {Promise<Object|null>} the dry-run report.
   */
  async function previewAutoStack() {
    try {
      return await autoStackExact({
        dryRun: true,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
      });
    } catch (err) {
      console.warn("[dedup] failed to preview the auto-stack", err);
      return null;
    }
  }

  /**
   * Run the bulk auto-stack for real.
   *
   * The whole run coalesces into one operation batch, so the receipt it raises
   * reverses every stack it created with a single undo.
   *
   * @returns {Promise<Object|null>} the run report, carrying `batch_id` and any
   *   `failures`.
   */
  async function runAutoStack() {
    busy.value = true;
    try {
      const result = await autoStackExact({
        dryRun: false,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
      });
      invalidateScopeCounts();
      // The whole run is one operation batch; the same response-driven
      // narration as a single verdict raises its one receipt.
      if (result?.batch_id) narrateVerdictOperation();
      await loadFirstPage();
      await refreshCounts();
      return result;
    } catch (err) {
      error.value = err;
      console.warn("[dedup] failed to run the auto-stack", err);
      return null;
    } finally {
      busy.value = false;
    }
  }

  // ── Mixed stacks (design D5) ─────────────────────────────────────────────
  // The third page of the Duplicates destination: live stacks whose members do
  // not form one connected cluster at the CURRENT threshold. Not a sidebar
  // destination (only a to-do count earns a row, and 9-26 items is not one)
  // and not a grid filter value.
  //
  // Two rules shape the state below:
  //
  //   * **The list is bound to the threshold slider, never to a constant.** The
  //     same stack is mixed at 0.90 and one clean cluster at 0.65, so
  //     `setThreshold` reloads it and the page always states what it was
  //     computed at.
  //   * **The page is a PAGE, not a route.** Flipping to it leaves the queue's
  //     window, focus and per-group choices exactly where they were, which is
  //     what lets the two-way shortcut offer a return that restores them.

  const showingMixed = ref(false);
  const mixedStacks = ref([]);
  const mixedTotal = ref(0);
  const mixedKeptTotal = ref(0);
  const mixedLiveStackCount = ref(0);
  const mixedThreshold = ref(null);
  const mixedNextOffset = ref(null);
  const mixedLoading = ref(false);
  const mixedLoaded = ref(false);
  const mixedError = ref(null);
  const mixedBusyStackId = ref(null);
  /** The row the two-way shortcut arrived at, so the page can reveal it. */
  const mixedFocusStackId = ref(null);

  /** More rows exist than are held. */
  const hasMoreMixed = computed(() => mixedNextOffset.value !== null);

  /**
   * The stack ids the queue's deck badges flag: the STRONG case only, a member
   * joined to nothing else in its stack.
   *
   * Derived from the loaded page rather than from a second request, which the
   * list's stranded-first ranking makes honest: every strong case is at the
   * head of the list, so a page that holds the head holds all of them.
   */
  const flaggedStackIds = computed(() => flaggedStackIdSet(mixedStacks.value));

  /**
   * Whether one stack carries the queue's warning chip.
   * @param {number|string} stackId
   * @returns {boolean}
   */
  function isStackFlagged(stackId) {
    if (stackId === null || stackId === undefined) return false;
    return flaggedStackIds.value.has(String(stackId));
  }

  /**
   * Load the mixed stacks at the current threshold, replacing what was there.
   *
   * Called when the page is shown, and on later threshold changes after that
   * first load. A failure leaves the list empty and records the error: an empty
   * list that claims "no mixed stacks" after a failed read would be a lie, so
   * the view branches on `mixedError` before it renders the empty state.
   *
   * @returns {Promise<void>}
   */
  async function loadMixedStacks() {
    mixedLoading.value = true;
    mixedError.value = null;
    try {
      const data = await listMixedStacks({
        threshold: Number.isFinite(threshold.value)
          ? threshold.value
          : undefined,
        offset: 0,
        limit: MIXED_STACK_PAGE_SIZE,
      });
      mixedStacks.value = Array.isArray(data?.stacks) ? data.stacks : [];
      mixedTotal.value = Number(data?.total) || mixedStacks.value.length;
      mixedKeptTotal.value = Number(data?.kept_total) || 0;
      mixedLiveStackCount.value = Number(data?.live_stack_count) || 0;
      // The server echoes what it computed at, so the page can state the
      // threshold rather than assume the slider and the list agree.
      mixedThreshold.value = Number.isFinite(Number(data?.threshold))
        ? Number(data.threshold)
        : threshold.value;
      mixedNextOffset.value = normalisedNextOffset(data);
      mixedLoaded.value = true;
    } catch (err) {
      mixedError.value = err;
      mixedStacks.value = [];
      mixedTotal.value = 0;
      mixedKeptTotal.value = 0;
      mixedNextOffset.value = null;
      mixedLoaded.value = false;
      console.warn("[dedup] failed to load the mixed stacks", err);
    } finally {
      mixedLoading.value = false;
    }
  }

  /**
   * A page's `next_offset`, normalised to null at the end of the list.
   * @param {Object} [data]
   * @returns {number|null}
   */
  function normalisedNextOffset(data) {
    const raw = data?.next_offset;
    // `Number(null)` is 0, which is finite: reading it as an offset would page
    // the first page again, forever, on every server that says "no more" the
    // way this one does.
    if (raw === null || raw === undefined) return null;
    const next = Number(raw);
    return Number.isFinite(next) ? next : null;
  }

  /**
   * Append the next page of mixed stacks.
   *
   * Plain offset paging, matching the route: the list is tens of rows and is
   * not being decided out from under the client the way the queue is.
   *
   * @returns {Promise<void>}
   */
  async function loadMoreMixedStacks() {
    if (mixedLoading.value || mixedNextOffset.value === null) return;
    mixedLoading.value = true;
    try {
      const data = await listMixedStacks({
        threshold: Number.isFinite(threshold.value)
          ? threshold.value
          : undefined,
        offset: mixedNextOffset.value,
        limit: MIXED_STACK_PAGE_SIZE,
      });
      const page = Array.isArray(data?.stacks) ? data.stacks : [];
      // De-duped by stack id: an offset over a list that a split just shortened
      // can re-serve a row the client already holds.
      const held = new Set(mixedStacks.value.map((s) => String(s.stack_id)));
      mixedStacks.value = [
        ...mixedStacks.value,
        ...page.filter((s) => !held.has(String(s.stack_id))),
      ];
      mixedTotal.value = Number(data?.total) || mixedTotal.value;
      mixedNextOffset.value = page.length ? normalisedNextOffset(data) : null;
    } catch (err) {
      mixedError.value = err;
      console.warn("[dedup] failed to load more mixed stacks", err);
    } finally {
      mixedLoading.value = false;
    }
  }

  /**
   * Show the Mixed stacks page, optionally revealing one stack's row.
   *
   * The queue's window, focus and per-group choices are untouched: this is a
   * page of the same destination, not a route away, which is what lets the
   * page offer a return that restores exactly what the user left.
   *
   * @param {number|string} [stackId] - the row to reveal.
   * @returns {Promise<void>}
   */
  async function showMixedStacks(stackId = null) {
    showingMixed.value = true;
    mixedFocusStackId.value = stackId === null ? null : String(stackId);
    if (!mixedLoaded.value && !mixedLoading.value) await loadMixedStacks();
  }

  /**
   * Return to the review queue with its focus intact.
   * @returns {void}
   */
  function hideMixedStacks() {
    showingMixed.value = false;
    mixedFocusStackId.value = null;
  }

  /**
   * The absolute queue index of the first LOADED group that holds one stack,
   * or -1.
   *
   * Deliberately over the loaded window only. The queue is paged and the
   * client cannot know where an unloaded group sits, and a shortcut that
   * scrolled to the wrong row would be worse than one that is not offered:
   * the view hides the control when this answers -1.
   *
   * @param {number|string} stackId
   * @returns {number}
   */
  function groupIndexForStack(stackId) {
    if (stackId === null || stackId === undefined) return -1;
    const wanted = String(stackId);
    const local = groups.value.findIndex((group) =>
      Object.keys(group?.stacks ?? {}).some((key) => String(key) === wanted),
    );
    return local < 0 ? -1 : local + windowStart.value;
  }

  /**
   * Jump from a mixed-stack row back to a duplicate group the stack appears in.
   *
   * @param {number|string} stackId
   * @returns {boolean} false when no loaded group holds it, so the caller can
   *   decline rather than move the cursor somewhere arbitrary.
   */
  function showQueueForStack(stackId) {
    const index = groupIndexForStack(stackId);
    if (index < 0) return false;
    hideMixedStacks();
    setFocus(index);
    return true;
  }

  /**
   * Drop one row from the list and keep the totals honest.
   * @param {number|string} stackId
   * @param {Object} [options]
   * @param {boolean} [options.kept=false] - the row left because it was kept.
   */
  function removeMixedStack(stackId, { kept = false } = {}) {
    const wanted = String(stackId);
    const before = mixedStacks.value.length;
    mixedStacks.value = mixedStacks.value.filter(
      (stack) => String(stack.stack_id) !== wanted,
    );
    if (mixedStacks.value.length === before) return;
    mixedTotal.value = Math.max(0, mixedTotal.value - 1);
    if (kept) mixedKeptTotal.value += 1;
    if (mixedFocusStackId.value === wanted) mixedFocusStackId.value = null;
  }

  /**
   * Record on the held row that a locked set freezes this stack.
   *
   * A 423 is fresher truth about the row than the page it was read from: the
   * lock landed after the list was served, so the row is stale and its primary
   * button is still offering an outcome the server will refuse again. Patching
   * `stackable` / `blocked_by_sets` from the refusal locks the button and names
   * the set in the same words a freshly-read page would have, with no reload
   * and no second vocabulary for the same fact.
   *
   * The row is replaced rather than mutated so a row object shared with a
   * previous render cannot be edited underneath it.
   *
   * @param {number|string} stackId
   * @param {Array<Object>} sets - `[{id, name}]` from the refusal.
   */
  function markMixedStackLocked(stackId, sets) {
    const wanted = String(stackId);
    mixedStacks.value = mixedStacks.value.map((stack) =>
      String(stack.stack_id) === wanted
        ? { ...stack, stackable: false, blocked_by_sets: sets }
        : stack,
    );
  }

  /**
   * The threshold the mixed LIST was computed at.
   *
   * Not the slider's live value. The two differ for exactly as long as a
   * reload is in flight, and that is the window in which a write built from
   * the rows on screen would be bounded by a threshold those rows were never
   * computed at. The server echoes what it used; that echo is the answer.
   *
   * @returns {number|undefined} undefined when neither is known, which lets the
   *   server pick its own default.
   */
  function listThreshold() {
    if (Number.isFinite(mixedThreshold.value)) return mixedThreshold.value;
    return Number.isFinite(threshold.value) ? threshold.value : undefined;
  }

  /**
   * Run the row's primary action: take the marked members out of the stack.
   *
   * **One call for both outcomes.** The split route takes any live member of
   * the stack, so an unstack is simply "every member leaves": the server
   * dissolves a stack that would be left with fewer than two members either
   * way, and reports which happened in `stack_dissolved`. Routing between two
   * endpoints on a client-side prediction would only create a case where the
   * prediction and the request disagree.
   *
   * The ids the ROW showed are sent, never recomputed server-side, so the split
   * matches what the user marked even if the stack has changed since. One
   * operation, so the standard receipt and a single Ctrl+Z cover it, narrated
   * through the shared operation store exactly as a verdict is, gated on the
   * response's `batch_id`.
   *
   * @param {Object} stack - the row.
   * @param {Array<number>} [pictureIds] - the marks in force. Defaults to the
   *   engine's own marks, which is what the row opens with.
   * @returns {Promise<Object|null>} the action response, or null on failure.
   */
  async function resolveMixedStack(stack, pictureIds) {
    const stackId = stack?.stack_id;
    if (stackId === null || stackId === undefined) return null;
    const ids =
      Array.isArray(pictureIds) && pictureIds.length
        ? [...pictureIds]
        : mixedStackEngineMarks(stack);
    if (!ids.length) return null;
    mixedBusyStackId.value = stackId;
    error.value = null;
    try {
      const result = await splitMixedStack(stackId, {
        pictureIds: ids,
        threshold: listThreshold(),
        batchId: newOperationBatchId(),
      });
      removeMixedStack(stackId);
      if (result?.batch_id) narrateVerdictOperation();
      return result;
    } catch (err) {
      error.value = err;
      // A locked picture set refuses the WHOLE stack with 423 and writes
      // nothing, so the row stays exactly where it is (`removeMixedStack` is
      // the success path's alone) and is marked with what the server named.
      // Without the mark the button keeps offering an outcome that cannot
      // happen, and the second press is refused for a reason nothing on screen
      // states.
      if (isLockedRefusal(err)) {
        const sets = lockedSets(err);
        markMixedStackLocked(stackId, sets);
        console.warn(
          "[dedup] mixed stack %s is frozen by %d locked set(s); nothing was changed",
          stackId,
          sets.length,
          err,
        );
        return null;
      }
      // A 400 means the request no longer describes the stack: a marked member
      // has left it, or been scrapheaped, since the list was read. The row is
      // therefore STALE, and leaving it on screen unchanged is what made this
      // present as a dead button. Re-read the list so the row either comes back
      // correct or leaves, and let the caller say the stack changed. The reload
      // is awaited so the caller narrates over the truth, not over the row that
      // just failed.
      if (Number(err?.response?.status) === 400) {
        console.warn(
          "[dedup] mixed stack %s no longer matches the row it was read from; re-reading the list",
          stackId,
          err,
        );
        await loadMixedStacks();
        return null;
      }
      console.warn("[dedup] failed to resolve mixed stack %s", stackId, err);
      return null;
    } finally {
      mixedBusyStackId.value = null;
    }
  }

  /**
   * Keep one stack as it is, so it stops being listed.
   *
   * Keep is what makes the list drainable: without it the legitimate-but-odd
   * stacks sit there forever and the page becomes ignorable. It changes no
   * picture, so it records no operation and is NOT undoable; `unkeepMixedStack`
   * is the way back, and the caller offers it.
   *
   * @param {Object} stack
   * @returns {Promise<Object|null>}
   */
  async function keepMixed(stack) {
    const stackId = stack?.stack_id;
    if (stackId === null || stackId === undefined) return null;
    mixedBusyStackId.value = stackId;
    error.value = null;
    try {
      const result = await keepMixedStack(stackId);
      removeMixedStack(stackId, { kept: true });
      return result;
    } catch (err) {
      error.value = err;
      console.warn("[dedup] failed to keep mixed stack %s", stackId, err);
      return null;
    } finally {
      mixedBusyStackId.value = null;
    }
  }

  /**
   * Clear every Keep on one stack, so it is listed again if it is still mixed.
   *
   * The list is reloaded rather than the row re-inserted from memory: the row
   * has to come back at its ranked position, and only the server knows whether
   * the stack is still mixed at the current threshold at all.
   *
   * @param {number|string} stackId
   * @returns {Promise<Object|null>}
   */
  async function unkeepMixedStack(stackId) {
    if (stackId === null || stackId === undefined) return null;
    mixedBusyStackId.value = stackId;
    try {
      const result = await clearMixedStackKeep(stackId);
      await loadMixedStacks();
      return result;
    } catch (err) {
      error.value = err;
      console.warn(
        "[dedup] failed to clear the Keep on stack %s",
        stackId,
        err,
      );
      return null;
    } finally {
      mixedBusyStackId.value = null;
    }
  }

  /**
   * Drop every trace of the previous credential's dedup state (issue #655).
   *
   * Dedup is an owner-only surface, so this is hygiene rather than a
   * cross-scope leak - but the queue holds picture ids, thumbnails and group
   * signatures read under one credential, and none of it survived a logout by
   * design. It survived only because nothing cleared it.
   *
   * Three things have to go together, in this order:
   *
   *   1. The scan poll and the end-chase, so no TIMER re-enters the store and
   *      repopulates it a second after the clear. `stopScanPoll` already owns
   *      the interval and the deferred request, so it is reused rather than
   *      re-implemented.
   *   2. The in-flight bookkeeping, so a caller cannot JOIN a request that
   *      belongs to the previous session.
   *   3. The state itself.
   *
   * `windowEpoch` is bumped for the same reason it is bumped on a window
   * rebase: a page request still on the wire must discard its result instead
   * of appending the previous session's rows into an empty window.
   *
   * `sizeLevel` deliberately stays. It is a persisted VIEW preference backed by
   * localStorage (like the review overlay's sticker shelf), not server data.
   */
  function reset() {
    stopScanPoll();
    cancelEndChase();
    windowEpoch += 1;
    pageInFlight = null;
    prevInFlight = null;
    scopeCountsInFlight.clear();
    openQueueInFlight.clear();
    scanRequestInFlight.clear();

    policyDefaults.value = null;
    bounds.value = null;
    policyLoaded.value = false;

    openCount.value = 0;
    byTier.value = {};
    scan.value = { ...IDLE_SCAN };
    countsLoaded.value = false;
    scopeCounts.value = {};

    scopeType.value = GLOBAL_SCOPE;
    scopeId.value = null;
    scopeLabel.value = "";
    scopeIcon.value = "";

    groups.value = [];
    windowStart.value = 0;
    total.value = 0;
    nextOffset.value = 0;
    nextCursor.value = null;
    hasMore.value = false;
    focusIndex.value = 0;
    loading.value = false;
    loadingMore.value = false;
    error.value = null;
    busy.value = false;
    stackedCount.value = 0;
    separatedCount.value = 0;

    hiddenVerdicts.value = new Set();
    decidedByVerdict.value = {};
    showingDecided.value = false;

    nearEnabled.value = false;
    embeddingEnabled.value = false;
    threshold.value = null;
    filtersRestored.value = false;

    coverChoices.value = {};
    exclusions.value = {};

    selectedSignatures.value = new Set();
    selectionAnchor = null;

    showingMixed.value = false;
    mixedStacks.value = [];
    mixedTotal.value = 0;
    mixedKeptTotal.value = 0;
    mixedLiveStackCount.value = 0;
    mixedThreshold.value = null;
    mixedNextOffset.value = null;
    mixedLoading.value = false;
    mixedLoaded.value = false;
    mixedError.value = null;
    mixedBusyStackId.value = null;
    mixedFocusStackId.value = null;
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    // policy
    policyDefaults,
    bounds,
    policyLoaded,
    loadPolicy,
    tierRows,
    nearEnabled,
    embeddingEnabled,
    threshold,
    filtersRestored,
    setTierEnabled,
    setThreshold,
    // counts
    openCount,
    byTier,
    exactCount,
    queueOnlyCount,
    scan,
    countsLoaded,
    isScanning,
    scopeCounts,
    refreshCounts,
    fetchScopeCount,
    invalidateScopeCounts,
    // queue
    scopeType,
    scopeId,
    scopeLabel,
    scopeIcon,
    isScoped,
    groups,
    total,
    nextCursor,
    hasMore,
    hasGroups,
    focusIndex,
    focusedGroup,
    loading,
    loadingMore,
    error,
    busy,
    stackedCount,
    separatedCount,
    doneCount,
    // size
    sizeLevel,
    thumbHeight,
    setSizeLevel,
    openQueue,
    clearScope,
    loadFirstPage,
    reloadWindowAround,
    loadMore,
    loadPrevious,
    windowStart,
    setFocus,
    focusStart,
    focusEnd,
    cancelEndChase,
    endChaseActive,
    selectionCount,
    isSelected,
    clearSelection,
    toggleSelected,
    selectAll,
    selectRange,
    verdictTargets,
    reopenMany,
    hasDuplicates,
    showingDecided,
    toggleDecided,
    // the decided page's verdict gate
    verdictRows,
    enabledVerdicts,
    verdictArgs,
    decidedByVerdict,
    setVerdictEnabled,
    applyUrlFilters,
    focusNext,
    focusPrev,
    removeGroup,
    applyPictureEvent,
    // per-group choices
    coverChoices,
    exclusions,
    coverIdFor,
    excludedFor,
    effectiveExcludedFor,
    stackSizeFor,
    unitsFor,
    includedUnitCountFor,
    isAtStackFloor,
    setCover,
    toggleExcluded,
    // verdicts and bulk
    stack,
    keepSeparate,
    reopen,
    triggerScan,
    stopScanPoll,
    previewAutoStack,
    runAutoStack,
    // mixed stacks (the third page)
    showingMixed,
    mixedStacks,
    mixedTotal,
    mixedKeptTotal,
    mixedLiveStackCount,
    mixedThreshold,
    mixedLoading,
    mixedLoaded,
    mixedError,
    mixedBusyStackId,
    mixedFocusStackId,
    hasMoreMixed,
    flaggedStackIds,
    isStackFlagged,
    loadMixedStacks,
    loadMoreMixedStacks,
    showMixedStacks,
    hideMixedStacks,
    groupIndexForStack,
    showQueueForStack,
    resolveMixedStack,
    keepMixed,
    unkeepMixedStack,
    reset,
  };
});
