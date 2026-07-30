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
import { ref, computed } from "vue";
import {
  getPolicy,
  listGroups,
  getCounts,
  startScan,
  stackGroup,
  keepGroupSeparate,
  reopenGroup,
  autoStackExact,
  GLOBAL_SCOPE,
} from "../api/dedup";
import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  clampSizeLevel,
  stripHeightForSizeLevel,
} from "../utils/thumbnailSizes";
import { suggestedCoverId, candidateId } from "../utils/dedup";
import { newOperationBatchId } from "../utils/apiClient";
import { useOperationStore } from "./useOperationStore";

/** How many groups one queue page holds. */
export const QUEUE_PAGE_SIZE = 20;

/**
 * How close to the tail the focus may walk before the next page is fetched.
 * Three rows is one Enter-Enter-Enter burst of headroom, which is what a user
 * working the queue by keyboard actually consumes between frames.
 */
export const PREFETCH_MARGIN = 3;

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
 * The tier ids the server publishes, mapped to the copy the menu renders.
 *
 * Labels are the client's business: the server names its tiers and the client
 * says what they mean to a person. An id the server adds later renders under
 * its own id rather than vanishing from the menu.
 */
export const TIER_LABELS = Object.freeze({
  exact: { label: "Exact matches", hint: "identical file" },
  near: { label: "Near-identical", hint: "bursts, re-exports, resizes" },
  embedding: { label: "Same scene", hint: "cross-folder, re-framed" },
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
  status: "idle",
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
  const scanned = Number(raw.scanned_pictures) || 0;
  const total = Number(raw.total_pictures) || 0;
  const buckets = Number(raw.scanned_buckets) || 0;
  const totalBuckets = Number(raw.total_buckets) || 0;
  let percent = 100;
  if (total > 0) percent = Math.round((scanned / total) * 100);
  else if (totalBuckets > 0)
    percent = Math.round((buckets / totalBuckets) * 100);
  return {
    status: raw.status || "idle",
    scanned,
    total,
    percent: Math.max(0, Math.min(100, percent)),
    buckets,
    totalBuckets,
    groupsFound: Number(raw.groups_found) || 0,
    error: raw.error ?? null,
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

  // --- The tier gate ------------------------------------------------------
  // Tier 1 is always in and has no switch. Tier 2 is an opt-in; tier 3 requires
  // tier 2, which the server enforces and this mirrors.
  const nearEnabled = ref(false);
  const embeddingEnabled = ref(false);
  const threshold = ref(null);
  // True once openQueue has adopted the URL's (and the remembered) filter
  // selection. Until then the gate above still holds pristine DEFAULTS, and
  // the view's URL mirror must not read that transient state as "the user
  // chose the defaults" — doing so is exactly the bug that stripped the
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
   * Whether ANY duplicates exist, across every tier — the sidebar's presence
   * indicator. Deliberately not the policy-filtered count: that number moves
   * with the tier gate and the threshold, so it kept reading as churn rather
   * than information (owner call, 2026-07-29 — the badge became a dot).
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
   * The cover picture id in force for a group: the user's override when they
   * made one, otherwise the server's preselection.
   * @param {Object} group
   * @returns {number|null}
   */
  function coverIdFor(group) {
    if (!group) return null;
    const chosen = coverChoices.value[group.signature];
    if (chosen !== undefined) return chosen;
    return suggestedCoverId(group);
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
   * How many of a group's candidates the Stack button would collect.
   * @param {Object} group
   * @returns {number}
   */
  function stackSizeFor(group) {
    if (!group) return 0;
    return (
      (group.candidates?.length ?? 0) - excludedFor(group.signature).length
    );
  }

  /**
   * Choose a group's cover.
   * @param {string} signature
   * @param {number} pictureId
   */
  function setCover(signature, pictureId) {
    coverChoices.value = { ...coverChoices.value, [signature]: pictureId };
  }

  /**
   * Include or exclude one candidate.
   *
   * Two invariants ride on this, both because `X` is a one-key action with no
   * confirmation:
   *
   *   * **A stack needs two members.** The server refuses a one-member stack
   *     outright, so an exclusion that would leave a single included candidate
   *     is refused here rather than turned into a guaranteed 400 on a Stack the
   *     row still offers. The floor is {@link MIN_STACK_MEMBERS} *included*
   *     candidates, not one: a group of two therefore accepts no exclusion at
   *     all, and the way to reject one of its members is Keep separate.
   *   * Excluding the cover would leave the stack with no cover, and the server
   *     rejects a cover that is not an included member. The cover moves to the
   *     best remaining included candidate instead, using the same formula that
   *     preselected it.
   *
   * @param {Object} group
   * @param {number} pictureId
   * @returns {boolean} whether the toggle was applied. False means it was
   *   refused by the floor, which the caller narrates: a one-key action that
   *   silently does nothing is a key the user stops trusting.
   */
  function toggleExcluded(group, pictureId) {
    if (!group) return false;
    const current = excludedFor(group.signature);
    const isOut = current.includes(pictureId);
    if (!isOut && stackSizeFor(group) <= MIN_STACK_MEMBERS) return false;
    const next = isOut
      ? current.filter((id) => id !== pictureId)
      : [...current, pictureId];
    exclusions.value = { ...exclusions.value, [group.signature]: next };
    if (!isOut && coverIdFor(group) === pictureId) {
      const remaining = (group.candidates ?? []).filter(
        (c) => !next.includes(candidateId(c)),
      );
      const replacement = suggestedCoverId({ candidates: remaining });
      if (replacement !== null) setCover(group.signature, replacement);
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
    return Boolean(group) && stackSizeFor(group) <= MIN_STACK_MEMBERS;
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

  async function openQueue({
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
    // its filter params — while the real navigation from the params was still
    // in flight, so the params were dropped for good.
    filtersRestored.value = true;
    // Opening the queue IS the scan trigger (design contract: the queue opens
    // over whatever has been found while the banner streams progress). The
    // group cache only fills when a scan runs; without this the queue reads an
    // empty cache forever and no tier or threshold setting can change that.
    await triggerScan();
    await loadFirstPage();
    refreshCounts();
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
    // point at different groups — and so would a jump-to-end still chasing
    // the old list's tail, or a page request still in flight from it.
    windowEpoch += 1;
    cancelEndChase();
    clearSelection();
    try {
      const data = await listGroups({
        ...policyArgs.value,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
        decided: showingDecided.value,
        offset: 0,
        limit: QUEUE_PAGE_SIZE,
      });
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
      loading.value = false;
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

  /** The page request itself. Never called directly — go through `loadMore`. */
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
   * reaches the top. Always offset-paged and never sends a cursor — the
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

  /** The upward page itself. Never called directly — go through loadPrevious. */
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
      // (offset drift re-serving a row the client already holds — the same
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
   * cursor-paged first page — the exact inverse of the jump that left it.
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

  /** The tail request: ALWAYS offset-paged, never a cursor — the server
   * rejects the two together, and a jump is precisely the operation the
   * forward cursor chain cannot express. */
  function requestTailPage(offset) {
    return listGroups({
      ...policyArgs.value,
      scopeType: scopeType.value,
      scopeId: scopeId.value,
      decided: showingDecided.value,
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
   * two is churn). A large gap: {@link jumpToTail} — one offset request for
   * the last page, window rebased onto it, no walk through the middle. All
   * paths land the focus on the last row actually received and terminate
   * under a running scan; and all die silently the moment the user moves the
   * focus or the view cancels the jump — a stale jump that yanks the scroll
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
  // decision can be reviewed and cleared (owner request, 2026-07-29 — this
  // replaces the sticky "Kept N pictures separate" notice as the way back).

  const showingDecided = ref(false);

  async function toggleDecided() {
    showingDecided.value = !showingDecided.value;
    await loadFirstPage();
  }

  // ── Multi-select ─────────────────────────────────────────────────────────
  // Ctrl+click toggles a group in and out; Shift+click selects the range from
  // the anchor (the last toggled or focused row). A verdict given to any
  // selected group applies to the whole selection — the row buttons say so.

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
    // one — both end up selected. (Ctrl+clicking the focused row itself still
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
   * thing depending on how far the user had scrolled — 40 groups out of 300,
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
   * operation store's own guards keep it honest — it narrates only an
   * own-origin operation above its high-water mark, so a WS echo arriving
   * later cannot double-narrate.
   *
   * Called only when the response carries a `batch_id`: that is the marker
   * that an operation-log row was recorded (a stack always mints one; a
   * keep-separate does only on a backend that has made it undoable — older
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
      // One gesture, one Ctrl+Z: every selected group shares a client batch
      // id, so the operation log coalesces the verdicts into one undo step.
      const gestureId = batchId || newOperationBatchId();
      let last = null;
      for (const target of targets) {
        last = await stackOne(target, { batchId: gestureId });
        // Stop on the first failure rather than half-applying silently; the
        // failed group and the rest stay selected and in the queue.
        if (!last) return null;
      }
      clearSelection();
      // One receipt per GESTURE, not per group: the batch is one undo step.
      if (last?.batch_id) narrateVerdictOperation();
      return last;
    }
    const result = await stackOne(group, { batchId });
    if (result?.batch_id) narrateVerdictOperation();
    return result;
  }

  async function stackOne(group, { batchId } = {}) {
    if (!group || busy.value) return null;
    busy.value = true;
    try {
      const result = await stackGroup(group.signature, {
        coverPictureId: coverIdFor(group),
        excludedPictureIds: excludedFor(group.signature),
        batchId,
      });
      stackedCount.value += 1;
      removeGroup(group.signature);
      openCount.value = Math.max(0, openCount.value - 1);
      invalidateScopeCounts();
      reconcileCounts();
      return result;
    } catch (err) {
      error.value = err;
      console.warn(`[dedup] failed to stack group ${group.signature}`, err);
      return null;
    } finally {
      busy.value = false;
    }
  }

  /**
   * Keep one group separate.
   *
   * The backend deliberately records **no operation** for this: no picture row
   * changes, so there is nothing for undo to restore, and an empty operation row
   * would still consume a Ctrl+Z. The caller must therefore narrate this itself
   * and offer {@link reopen} as the way back rather than waiting for a receipt.
   *
   * @param {Object} group
   * @returns {Promise<Object|null>} the verdict response, or null on failure.
   */
  async function keepSeparate(group) {
    if (showingDecided.value) return null;
    const targets = verdictTargets(group);
    if (targets.length > 1) {
      let last = null;
      for (const target of targets) {
        last = await keepSeparateOne(target);
        if (!last) return null;
      }
      clearSelection();
      // A backend that has made keep-separate undoable mirrors the stack
      // response and carries a batch_id; an older one returns null there and
      // the gesture stays receipt-less, exactly as before.
      if (last?.batch_id) narrateVerdictOperation();
      return last;
    }
    const result = await keepSeparateOne(group);
    if (result?.batch_id) narrateVerdictOperation();
    return result;
  }

  async function keepSeparateOne(group) {
    if (!group || busy.value) return null;
    busy.value = true;
    try {
      const result = await keepGroupSeparate(group.signature);
      separatedCount.value += 1;
      removeGroup(group.signature);
      openCount.value = Math.max(0, openCount.value - 1);
      invalidateScopeCounts();
      // This verdict raises no WebSocket event at all, so this refetch is the
      // only thing that will ever correct the tick above.
      reconcileCounts();
      return result;
    } catch (err) {
      error.value = err;
      console.warn(
        `[dedup] failed to keep group ${group.signature} separate`,
        err,
      );
      return null;
    } finally {
      busy.value = false;
    }
  }

  /**
   * Return a decided group to the queue.
   *
   * The stand-in for undo on a keep-separate. The group comes back only if it
   * has been re-detected; when it has not, the response says so and the next
   * scan brings it back, which the caller must report honestly rather than
   * implying the row will reappear.
   *
   * @param {string} signature
   * @returns {Promise<Object|null>} the reopen response, or null on failure.
   */
  async function reopen(signature) {
    try {
      const result = await reopenGroup(signature);
      invalidateScopeCounts();
      await loadFirstPage();
      refreshCounts();
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
   * One reload at the end rather than per group — reopen() reloads per call,
   * which is right for one and quadratic for fifty.
   *
   * @param {string[]} signatures
   * @returns {Promise<{cleared: number, returned: number}>}
   */
  async function reopenMany(signatures) {
    let cleared = 0;
    let returned = 0;
    for (const signature of signatures) {
      try {
        const result = await reopenGroup(signature);
        cleared += 1;
        if (result?.group_returned_to_queue) returned += 1;
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
  }

  /**
   * Queue a scan for the current scope and adopt its progress.
   * @returns {Promise<void>}
   */
  async function triggerScan() {
    try {
      const data = await startScan({
        policy: policyArgs.value,
        scopeType: scopeType.value,
        scopeId: scopeId.value,
      });
      scan.value = normalizeScan(data);
      startScanPoll();
    } catch (err) {
      console.warn("[dedup] failed to start a duplicate scan", err);
    }
  }

  // --- Scan progress polling ----------------------------------------------
  // The banner and the counts are only honest while someone re-reads them:
  // tier-2 groups commit after every bucket, but nothing pushes that to the
  // client. The poll runs only while a scan is pending/running, and reloads
  // the group list only while the queue is still EMPTY — so the first finds
  // surface on their own, and a triage already in progress is never yanked
  // back to the top.
  let scanPollTimer = null;

  function stopScanPoll() {
    if (scanPollTimer) {
      clearInterval(scanPollTimer);
      scanPollTimer = null;
    }
  }

  function startScanPoll() {
    if (scanPollTimer || !isScanning.value) return;
    scanPollTimer = setInterval(async () => {
      await refreshCounts();
      if (!groups.value.length) await loadFirstPage();
      if (!isScanning.value) stopScanPoll();
    }, 2000);
  }

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
    applyUrlFilters,
    focusNext,
    focusPrev,
    removeGroup,
    // per-group choices
    coverChoices,
    exclusions,
    coverIdFor,
    excludedFor,
    stackSizeFor,
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
  };
});
