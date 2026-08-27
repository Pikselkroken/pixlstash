// useReviewSessionsStore.js - state for the "Review sessions" overlay.
//
// Models tag review as first-class review sessions:
// a tag-health board (landing view), a rail of open reviews (each = one tag +
// frozen scope + one scan's results), and a per-session queue of binary/pair
// cards. Decisions still write through the existing per-item /tag_suggestions
// endpoints (accept/dismiss/fix-twin/swap/reopen); the session bookkeeping
// (create/list/refresh/archive/abort) talks to the new /reviews endpoints and
// the board to /tag_health.
//
// Also owns the opt-in gamification ("Pretend this is fun"): a variable-ratio
// sticker-award schedule whose sticker vocabulary is IMPORTED from the Picture
// Set palette (setAppearance.js) so sets and stickers never drift. XP/level/
// streak counters are monotonic - Undo never decrements them, and stickers are
// never clawed back.

import { ref, computed, onScopeDispose } from "vue";
import { defineStore } from "pinia";
import { onSessionReset } from "../utils/apiClient";
import { getUserConfig } from "../api/config";
import {
  listReviews,
  getReview,
  listReviewSuggestions,
  // Aliased: the store's own action is also called createReview.
  createReview as postReview,
  refreshReview,
  archiveReview,
  abortReview,
  deleteReview,
  deleteReviewsByStatus,
} from "../api/reviews";
import {
  resolveTagSuggestion,
  skipTagSuggestion,
  reopenTagSuggestion,
  bulkReopenTagSuggestions,
} from "../api/tagSuggestions";
import { getTagHealth, rebuildTagHealth } from "../api/tagHealth";
import { getAnomalyRegion } from "../api/pictures";
import { useEntityListsStore } from "./useEntityListsStore";
import { SET_ICONS, SET_COLORS } from "../utils/setAppearance";
import { errorDetail } from "../utils/apiError";

const PAGE_SIZE = 200;

// localStorage keys. The heatmap key is shared with the old overlay on purpose
// so the user's evidence-region preference carries over.
const STICKERS_KEY = "pixlstash:reviewStickers";
const HEATMAP_PREF_KEY = "pixlstash:reviewHeatmap";
const GAMIFY_PREF_KEY = "pixlstash:reviewGamify";

// A decision that contradicts a CONFIDENT prior call this session (the user has
// only ever said the opposite, at least this many times) is held for confirm.
const CONFLICT_MIN_OPPOSITE = 2;

// Variable-ratio sticker schedule: after an award, the next one lands a uniform
// random 40–100 decisions later (mean 70). Originally 2–5 (mean 3.5), which
// rained stickers during any real review session - scaled 20× down.
export const AWARD_GAP_MIN = 40;
export const AWARD_GAP_MAX = 100;

// Sticker vocabulary: the Picture Set icon + colour palette, restyled by the
// components as die-cut stickers. Reusing the module is a hard requirement -
// the arrays are derived, never copied.
export const STICKER_ICONS = SET_ICONS.map((ic) => ({
  icon: ic.value,
  label: ic.label,
}));
const STICKER_COLORS = SET_COLORS.map((c) => c.value);

// --- Decision mapping -------------------------------------------------------
//
// The per-item endpoints keep the OLD semantics (verified against the previous
// overlay's dispatchDecision + store actions):
//   accept   → apply the suggested fix to the suspect (remove → delete the tag,
//              add → create it)
//   dismiss  → keep the labels as they are
//   fix-twin → keep the suspect, flip the TWIN to match it
//   swap     → clear the tagged side AND tag the untagged side
//
// Binary card ("Should this have the tag?" about the suspect picture):
//   remove + Yes → the tag is right, keep it            → dismiss
//   remove + No  → the tag is wrong, remove it          → accept
//   add    + Yes → the tag is missing, add it           → accept
//   add    + No  → correctly untagged, leave it         → dismiss
export function binaryAction(item, answer) {
  const yes = answer === "yes";
  if (item.direction === "remove") return yes ? "dismiss" : "accept";
  return yes ? "accept" : "dismiss";
}

// Session-tally delta for a binary answer (mirrors the old store's counters:
// removed = a wrong tag cleared, added = a missing tag applied, kept = no change).
export function binaryDelta(item, answer) {
  const yes = answer === "yes";
  if (item.direction === "remove") return yes ? { kept: 1 } : { removed: 1 };
  return yes ? { added: 1 } : { kept: 1 };
}

// Pair card (true versions of one shot; LEFT is always the tagged side, RIGHT
// the untagged side - which picture id is which depends on `direction`, exactly
// as in the old overlay). Mapping mirrors the old dispatchDecision():
//   left  (only the tagged side has it - labels already correct) → dismiss
//   both  (tag the untagged side too)  → remove: fix-twin (twin is the untagged
//          side) · add: accept (the suspect is the untagged side)
//   neither (clear the tagged side)    → remove: accept · add: fix-twin
//   right (the label is on the wrong image - move it)            → swap
export function pairAction(item, corner) {
  if (corner === "left") return "dismiss";
  if (corner === "right") return "swap";
  if (corner === "both")
    return item.direction === "remove" ? "fix-twin" : "accept";
  // neither
  return item.direction === "remove" ? "accept" : "fix-twin";
}

export function pairDelta(_item, corner) {
  if (corner === "left") return { kept: 1 };
  if (corner === "both") return { added: 1 };
  if (corner === "neither") return { removed: 1 };
  return { removed: 1, added: 1 }; // right: cleared one, tagged the other
}

// The tagged/untagged picture ids of a pair item (LEFT = tagged side).
export function pairSides(item) {
  const leftPid =
    item.direction === "remove" ? item.picture_id : item.twin_picture_id;
  const rightPid =
    item.direction === "remove" ? item.twin_picture_id : item.picture_id;
  return { leftPid, rightPid };
}

// Per-picture has/not votes a decision asserts, for the session consistency
// ledger (port of the old CORNER_VOTES translation).
function votesForDecision(item, kind, decision) {
  if (!item) return [];
  if (kind === "binary") {
    if (item.picture_id == null) return [];
    return [
      { pid: item.picture_id, vote: decision === "yes" ? "has" : "not" },
    ];
  }
  const map = {
    left: { left: "has", right: "not" },
    both: { left: "has", right: "has" },
    neither: { left: "not", right: "not" },
    right: { left: "not", right: "has" },
  }[decision];
  if (!map) return [];
  const { leftPid, rightPid } = pairSides(item);
  const out = [];
  if (leftPid != null) out.push({ pid: leftPid, vote: map.left });
  if (rightPid != null) out.push({ pid: rightPid, vote: map.right });
  return out;
}

// Queue ordering within a session: pair cards first (one mental frame), then
// remove-direction, then add-direction; most decisive (highest score) first
// within each group.
function queueRank(item) {
  if (item.kind === "pair") return 0;
  return item.direction === "remove" ? 1 : 2;
}

export function sortQueue(items) {
  return [...items].sort((a, b) => {
    const r = queueRank(a) - queueRank(b);
    if (r !== 0) return r;
    return (b.score ?? 0) - (a.score ?? 0);
  });
}

function readStickers() {
  try {
    const raw = window.localStorage.getItem(STICKERS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeStickers(list) {
  try {
    window.localStorage.setItem(STICKERS_KEY, JSON.stringify(list));
  } catch {
    // Best-effort persistence; the in-memory shelf still works this session.
  }
}

function readHeatmapPref() {
  try {
    const raw = window.localStorage.getItem(HEATMAP_PREF_KEY);
    return raw === null ? true : raw === "1";
  } catch {
    return true;
  }
}

function readGamifyPref() {
  try {
    return window.localStorage.getItem(GAMIFY_PREF_KEY) === "1";
  } catch {
    return false; // gamification defaults off
  }
}

export const useReviewSessionsStore = defineStore("reviewSessions", () => {
  const overlayOpen = ref(false);
  // What the main area shows: the board, one open session, or an archived
  // review's receipt. { type: 'board' } | { type: 'session'|'archived', id }.
  const view = ref({ type: "board" });
  const error = ref(null);
  // Set by useReviewRoute before it flips `overlayOpen`, so a `?review=<id>`
  // URL can be restored. Consumed (and cleared) by load() once the session
  // lists have landed - an id can only be resolved to an open session vs an
  // archived receipt vs nothing once both lists exist.
  const pendingRestoreViewId = ref(null);

  // Bumped by resetSession(). Every server read here belongs to exactly one
  // authentication epoch; a response that lands after the credential changed
  // is dropped rather than written into the next session's board.
  let sessionEpoch = 0;

  // --- Sessions (open + archived reviews) -----------------------------------
  const sessions = ref([]); // OPEN reviews from GET /reviews?status=OPEN
  const archived = ref([]); // ARCHIVED reviews (same endpoint, status=ARCHIVED)
  const sessionsLoading = ref(false);
  // Per-session detail (receipt stats), keyed by id, from GET /reviews/{id}.
  const details = ref({});

  // Per-session client-side state, keyed by review id.
  const queues = ref({}); // { [id]: { items: [], loading, error } }
  const tallies = ref({}); // { [id]: { removed, added, kept, skipped } }
  const undoStacks = ref({}); // { [id]: [{ item, action, delta, votes }] }
  // Open-review ids whose pre-session server receipt has already been folded
  // into `tallies` once (see seedTallyFromReceipt). Non-reactive: it only gates
  // the one-time seed so a mid-review refresh can't fold the same decisions in
  // a second time. Cleared by reset() so a reopen re-seeds from a fresh fetch.
  const seededReceipts = new Set();

  // --- Tag health board ------------------------------------------------------
  const healthRows = ref([]);
  const healthBuilding = ref(false);
  const healthProgress = ref(0); // normalised 0..1
  const healthComputedAt = ref(null);
  const healthLoading = ref(false);
  // True when a new picture, tagger run, or reviewed suggestion has landed
  // since computed_at - a rebuild is due (an auto-rebuild finder eventually
  // catches up; this just powers the persistent control's stale tint/tooltip
  // in the meantime). Always false for a scoped (live-computed) response.
  const healthStale = ref(false);
  let healthPollTimer = null;
  // Board scope (project/set/character). When any dimension is set the rows
  // are computed live server-side for that scope instead of read from the
  // vault-wide cache; the same scope prefills the New-review dialog.
  const healthScope = ref({ projectId: null, setId: null, characterId: null });
  const healthScoped = computed(() => {
    const s = healthScope.value;
    return s.projectId != null || s.setId != null || s.characterId != null;
  });

  // --- New-review creation ----------------------------------------------------
  const creating = ref(false);
  const createError = ref(null);

  // --- Scope options (for the creation dialog) --------------------------------
  // Read straight off the shared entity-list store: the same three lists the
  // sidebar and the image context menu use (§4). `reference_pictures` is the
  // system-owned set behind character references and is never a review scope.
  const entityLists = useEntityListsStore();
  const projects = computed(() => entityLists.projects);
  const sets = computed(() =>
    entityLists.pictureSets.filter((s) => s?.name !== "reference_pictures"),
  );
  const characters = computed(() => entityLists.characters);

  // Smart-score penalised ("anomaly") tags, lowercased, from the user config.
  const anomalyTags = ref(new Set());

  // --- Anomaly-region cache (same contract as the old store) ------------------
  // Value = region object, or null for "nothing to show" (404/422/503 cached
  // as a miss). Absent key = not fetched yet. heatmapEnabled is the user's
  // persisted show/hide toggle (H key), shared with the old overlay's pref.
  const heatmapEnabled = ref(readHeatmapPref());
  const anomalyRegions = ref({});
  const regionLoading = ref({});
  const regionInFlight = new Set();

  // --- Session consistency ledger ---------------------------------------------
  // { [tag]: { [pid]: { has, not } } } - how many times this session the user
  // asserted a picture HAS / does NOT have the tag. Backs the conflict guard.
  const tagVotes = ref({});

  // --- Gamification -----------------------------------------------------------
  const gamify = ref(readGamifyPref()); // persisted - survives reload/reopen
  const stickers = ref(readStickers()); // the shelf - persists across sessions
  const activeAward = ref(null); // sticker mid pop→fly animation, or null
  // NET decision count: XP/level/streak derive from it and Undo decrements it.
  // Celebrations key off decisionTick, an EXPLICIT per-decision event that
  // undo never re-fires - and stickers are never clawed back either way.
  const decisionsCount = ref(0);
  const decisionTick = ref(0); // bumps on every real decision (explicit event)
  // Variable-ratio schedule: first decision after enabling always awards, then
  // every AWARD_GAP_MIN–AWARD_GAP_MAX. `lastIcon` prevents the same sticker
  // twice in a row.
  const awardState = { since: 0, next: 1, lastIcon: -1 };
  let awardTimer = null;

  // --- Derived ----------------------------------------------------------------
  const activeSession = computed(() =>
    view.value.type === "session"
      ? (sessions.value.find((s) => s.id === view.value.id) ?? null)
      : null,
  );

  const activeQueue = computed(() => {
    const s = activeSession.value;
    return s ? (queues.value[s.id]?.items ?? []) : [];
  });

  const current = computed(() => activeQueue.value[0] ?? null);

  const EMPTY_TALLY = { removed: 0, added: 0, kept: 0, skipped: 0 };

  const activeTally = computed(() => {
    const s = activeSession.value;
    return s
      ? { ...EMPTY_TALLY, ...(tallies.value[s.id] || {}) }
      : { ...EMPTY_TALLY };
  });

  const canUndo = computed(() => {
    const s = activeSession.value;
    return !!(s && (undoStacks.value[s.id]?.length ?? 0) > 0);
  });

  const activeQueueLoading = computed(() => {
    const s = activeSession.value;
    return !!(s && queues.value[s.id]?.loading);
  });

  // Skipped count for the rail's "done/found · N skipped" line.
  function skippedCountFor(id) {
    return receiptFor(id).skipped;
  }

  // Skips made in THIS client session that can still be reopened (their ids
  // live on the undo stack).
  function reopenableSkipsFor(id) {
    return (undoStacks.value[id] || []).filter((e) => e.action === "skip")
      .length;
  }

  // How many CHANGES were made in a review (skips are not changes) - drives
  // the abort dialog's "You made N changes".
  function decidedCountFor(id) {
    const r = receiptFor(id);
    const fromReceipt = r.removed + r.added + r.kept;
    if (fromReceipt) return fromReceipt;
    // `receiptFor` derives an OPEN review's count from the local tally, which is
    // seeded from the server receipt ONLY when the session is opened this app run
    // (openSession → fetchDetail → seedTallyFromReceipt) and only once that async
    // load lands. A review aborted straight from the rail without being opened -
    // or before that seed resolves - therefore reads zero here, so
    // openAbortDialog silently skips the Keep/Undo dialog and abortSession keeps
    // changes made in an earlier sitting (the "it just applies them" bug). Fall
    // back to the server's authoritative decided-row count: `progress.done`
    // excludes skips, is present on every list row, and is bumped optimistically
    // per decision - so the dialog is never wrongly skipped.
    const s = sessions.value.find((x) => x.id === id);
    return Math.max(0, s?.progress?.done ?? 0);
  }

  // The session receipt for the completion state / abort dialog. It has ONE
  // authoritative source per review state - never server + tally summed:
  //
  //   * OPEN review  → the live local `tallies[id]`. It is seeded ONCE from the
  //     server's pre-session receipt at open (seedTallyFromReceipt) and then
  //     bumped per decision, so it counts prior-session AND this-session
  //     decisions. Crucially it NEVER re-reads the live server receipt, so a
  //     mid-review refresh (refreshSession → fetchDetail overwrites details[id]
  //     with a LIVE receipt that already counts this session's decisions) cannot
  //     double-count. Summing details + tally was exactly that double-count bug.
  //   * ARCHIVED/closed review → the backend's frozen snapshot receipt from
  //     details[id]. This app session never decided these, so there is no tally.
  function receiptFor(id) {
    const isOpen = sessions.value.some((s) => s.id === id);
    if (!isOpen) {
      const d = details.value[id];
      const r = d?.receipt || d?.stats?.receipt;
      if (r && (r.removed != null || r.added != null || r.kept != null)) {
        return {
          removed: r.removed ?? 0,
          added: r.added ?? 0,
          kept: r.kept ?? 0,
          skipped: r.skipped ?? 0,
        };
      }
    }
    return { ...EMPTY_TALLY, ...(tallies.value[id] || {}) };
  }

  function isAnomalyTag(tag) {
    return anomalyTags.value.has(String(tag || "").trim().toLowerCase());
  }

  // --- Fetches ----------------------------------------------------------------

  async function fetchSessions() {
    sessionsLoading.value = true;
    const requestEpoch = sessionEpoch;
    try {
      const rows = await listReviews("OPEN");
      if (requestEpoch !== sessionEpoch) return;
      sessions.value = Array.isArray(rows) ? rows : [];
    } catch (e) {
      if (requestEpoch !== sessionEpoch) return;
      error.value = e?.message || "Failed to load reviews";
    } finally {
      if (requestEpoch === sessionEpoch) sessionsLoading.value = false;
    }
  }

  async function fetchArchived() {
    const requestEpoch = sessionEpoch;
    try {
      const rows = await listReviews("ARCHIVED");
      if (requestEpoch !== sessionEpoch) return;
      archived.value = Array.isArray(rows) ? rows : [];
    } catch {
      if (requestEpoch !== sessionEpoch) return;
      archived.value = [];
    }
  }

  async function fetchDetail(id) {
    try {
      const data = (await getReview(id)) ?? null;
      details.value = { ...details.value, [id]: data };
      seedTallyFromReceipt(id, data);
    } catch {
      // Detail is enrichment (receipt stats); the list row carries the basics.
    }
  }

  // Fold an OPEN review's pre-session server receipt into the local tally EXACTLY
  // once, so the receipt/decided-count includes decisions made in earlier
  // sittings while still deriving from a single source (the tally). The one-shot
  // guard means a later fetchDetail (refreshSession refetches the now-live
  // receipt, which already counts this session's decisions) cannot fold those
  // same decisions in again. Archived reviews are never seeded - their receipt
  // reads the frozen server snapshot directly.
  function seedTallyFromReceipt(id, data) {
    if (seededReceipts.has(id)) return;
    if (!sessions.value.some((s) => s.id === id)) return; // OPEN reviews only
    const r = data?.receipt || data?.stats?.receipt;
    if (!r) return;
    seededReceipts.add(id);
    const base = {
      removed: r.removed ?? 0,
      added: r.added ?? 0,
      kept: r.kept ?? 0,
      skipped: r.skipped ?? 0,
    };
    if (base.removed || base.added || base.kept || base.skipped) {
      bumpTally(id, base);
    }
  }

  function queueFor(id) {
    return queues.value[id] ?? { items: [], loading: false, error: null };
  }

  function setQueue(id, patch) {
    queues.value = {
      ...queues.value,
      [id]: { ...queueFor(id), ...patch },
    };
  }

  async function fetchQueue(id, { markNewFrom = null } = {}) {
    setQueue(id, { loading: true, error: null });
    try {
      const page = await listReviewSuggestions(id, {
        status: "PENDING",
        limit: PAGE_SIZE,
      });
      let items = Array.isArray(page?.items)
        ? page.items
        : Array.isArray(page)
          ? page
          : [];
      if (markNewFrom) {
        items = items.map((it) =>
          markNewFrom.has(it.id) ? it : { ...it, _isNew: true },
        );
      }
      setQueue(id, { items: sortQueue(items), loading: false });
    } catch (e) {
      setQueue(id, {
        loading: false,
        error: e?.message || "Failed to load suggestions",
      });
    }
  }

  async function fetchHealth() {
    healthLoading.value = true;
    const requestEpoch = sessionEpoch;
    try {
      const s = healthScope.value;
      const params = {};
      if (s.projectId != null) params.project_id = s.projectId;
      if (s.setId != null) params.set_id = s.setId;
      if (s.characterId != null) params.character_id = s.characterId;
      const body = await getTagHealth(params);
      // A stale response for a scope the user has already navigated away
      // from must not clobber the current scope's rows. The epoch check is the
      // same guard one level up: a different CREDENTIAL, not just a different
      // scope, and `healthScope` is reset to the same object shape so identity
      // alone would not catch it.
      if (requestEpoch !== sessionEpoch) return;
      if (healthScope.value !== s) return;
      const data = body ?? {};
      healthRows.value = Array.isArray(data.rows) ? data.rows : [];
      healthBuilding.value = !!data.building;
      const p = Number(data.progress ?? 0);
      healthProgress.value = p > 1 ? Math.min(1, p / 100) : Math.max(0, p);
      healthComputedAt.value = data.computed_at ?? null;
      healthStale.value = !!data.stale;
      scheduleHealthPoll();
    } catch (e) {
      if (requestEpoch !== sessionEpoch) return;
      error.value = e?.message || "Failed to load tag health";
    } finally {
      if (requestEpoch === sessionEpoch) healthLoading.value = false;
    }
  }

  // Replace the board scope and refetch. Pass all-null to clear.
  function setHealthScope(scope) {
    healthScope.value = {
      projectId: scope?.projectId ?? null,
      setId: scope?.setId ?? null,
      characterId: scope?.characterId ?? null,
    };
    fetchHealth();
  }

  // Poll /tag_health only while the cache is (re)building, so the progress bar
  // advances; stop as soon as it lands.
  function scheduleHealthPoll() {
    if (healthPollTimer) {
      clearTimeout(healthPollTimer);
      healthPollTimer = null;
    }
    if (healthBuilding.value && overlayOpen.value) {
      healthPollTimer = setTimeout(() => fetchHealth(), 1500);
    }
  }

  async function rebuildHealth() {
    try {
      await rebuildTagHealth();
      healthBuilding.value = true;
      healthProgress.value = 0;
      scheduleHealthPoll();
    } catch (e) {
      error.value = e?.message || "Failed to start the health rebuild";
    }
  }

  // Load the user's smart-score "penalised" tags (mirrors the old store: the
  // config field can be an array of strings, an array of {tag,...} objects, or
  // a {tag: weight} map). Degrades to an empty Set on error.
  async function fetchAnomalyTags() {
    try {
      const cfg = await getUserConfig();
      const raw = cfg?.smart_score_penalised_tags;
      const next = new Set();
      if (Array.isArray(raw)) {
        for (const item of raw) {
          if (item == null) continue;
          const tag =
            typeof item === "object"
              ? String(item.tag || "").trim().toLowerCase()
              : String(item).trim().toLowerCase();
          if (tag) next.add(tag);
        }
      } else if (raw && typeof raw === "object") {
        for (const key of Object.keys(raw)) {
          const tag = String(key).trim().toLowerCase();
          if (tag) next.add(tag);
        }
      }
      anomalyTags.value = next;
    } catch {
      anomalyTags.value = new Set();
    }
  }

  // Populate the creation dialog's scope dropdowns. Whatever is cached renders
  // at once; this only revalidates it. Each list degrades independently - the
  // store keeps the last good one and logs the failure.
  function fetchScopeOptions() {
    return entityLists.invalidate();
  }

  // --- Anomaly-region overlay (heatmap + box) ---------------------------------

  function regionKey(pictureId, tag) {
    return `${pictureId}|${tag}`;
  }

  function anomalyRegionFor(pictureId, tag) {
    if (pictureId == null || !tag) return null;
    return anomalyRegions.value[regionKey(pictureId, tag)] ?? null;
  }

  function isRegionLoading(pictureId, tag) {
    if (pictureId == null || !tag) return false;
    return !!regionLoading.value[regionKey(pictureId, tag)];
  }

  function setHeatmapEnabled(value) {
    heatmapEnabled.value = !!value;
    try {
      window.localStorage.setItem(
        HEATMAP_PREF_KEY,
        heatmapEnabled.value ? "1" : "0",
      );
    } catch {
      // Best-effort; the in-memory toggle still works this session.
    }
  }

  async function fetchAnomalyRegion(pictureId, tag) {
    if (pictureId == null || !tag) return null;
    const key = regionKey(pictureId, tag);
    if (key in anomalyRegions.value) return anomalyRegions.value[key];
    if (regionInFlight.has(key)) return null;
    regionInFlight.add(key);
    regionLoading.value = { ...regionLoading.value, [key]: true };
    try {
      const data = (await getAnomalyRegion(pictureId, tag)) ?? null;
      anomalyRegions.value = { ...anomalyRegions.value, [key]: data };
      return data;
    } catch {
      // Tag outside the tagger vocabulary (404/422) or model unavailable (503):
      // cache the miss, show no overlay, never refetch.
      anomalyRegions.value = { ...anomalyRegions.value, [key]: null };
      return null;
    } finally {
      regionInFlight.delete(key);
      const next = { ...regionLoading.value };
      delete next[key];
      regionLoading.value = next;
    }
  }

  // --- Session consistency ledger ----------------------------------------------

  function recordVotes(tag, votes) {
    if (!tag || !votes.length) return;
    const next = { ...tagVotes.value };
    const bucket = { ...(next[tag] || {}) };
    for (const { pid, vote } of votes) {
      const prev = bucket[pid] || { has: 0, not: 0 };
      bucket[pid] = {
        has: prev.has + (vote === "has" ? 1 : 0),
        not: prev.not + (vote === "not" ? 1 : 0),
      };
    }
    next[tag] = bucket;
    tagVotes.value = next;
  }

  function retractVotes(tag, votes) {
    if (!tag || !votes.length || !tagVotes.value[tag]) return;
    const next = { ...tagVotes.value };
    const bucket = { ...next[tag] };
    for (const { pid, vote } of votes) {
      const prev = bucket[pid];
      if (!prev) continue;
      bucket[pid] = {
        has: Math.max(0, prev.has - (vote === "has" ? 1 : 0)),
        not: Math.max(0, prev.not - (vote === "not" ? 1 : 0)),
      };
    }
    next[tag] = bucket;
    tagVotes.value = next;
  }

  // Would this decision contradict a confident prior call this session?
  // Returns the strongest conflict { pid, priorHas, priorNot, asserting } or
  // null. "Confident" = only ever said the opposite, ≥ CONFLICT_MIN_OPPOSITE.
  function decisionConflict(item, kind, decision) {
    const votes = votesForDecision(item, kind, decision);
    if (!votes.length || !item?.tag) return null;
    const bucket = tagVotes.value[item.tag] || {};
    let best = null;
    let bestOpposite = -1;
    for (const { pid, vote } of votes) {
      const prior = bucket[pid];
      if (!prior) continue;
      const oppositeCount = vote === "has" ? prior.not : prior.has;
      const sameCount = vote === "has" ? prior.has : prior.not;
      if (oppositeCount >= CONFLICT_MIN_OPPOSITE && sameCount === 0) {
        if (oppositeCount > bestOpposite) {
          bestOpposite = oppositeCount;
          best = {
            pid,
            priorHas: prior.has,
            priorNot: prior.not,
            asserting: vote,
          };
        }
      }
    }
    return best;
  }

  // --- Open / navigate ----------------------------------------------------------

  // Overlay open: land on the board, load everything in parallel.
  async function load() {
    view.value = { type: "board" };
    error.value = null;
    createError.value = null;
    tagVotes.value = {}; // fresh consistency ledger each time the overlay opens
    fetchHealth();
    const archivedLoaded = fetchArchived();
    fetchAnomalyTags();
    fetchScopeOptions();
    await fetchSessions();

    // URL restore (?review=<id>). Resolved only now, against the real lists -
    // a stale/deleted/unknown id degrades to the board rather than leaving
    // `view` asserting a session that does not exist.
    const restoreId = pendingRestoreViewId.value;
    pendingRestoreViewId.value = null;
    if (restoreId == null) return;
    if (sessions.value.some((s) => s.id === restoreId)) {
      openSession(restoreId);
      return;
    }
    await archivedLoaded;
    if (archived.value.some((a) => a.id === restoreId)) openArchived(restoreId);
  }

  function showBoard() {
    view.value = { type: "board" };
  }

  function openSession(id) {
    view.value = { type: "session", id };
    if (!queues.value[id]) fetchQueue(id);
    if (!(id in details.value)) fetchDetail(id);
  }

  function openArchived(id) {
    view.value = { type: "archived", id };
    if (!(id in details.value)) fetchDetail(id);
  }

  function reset() {
    if (healthPollTimer) {
      clearTimeout(healthPollTimer);
      healthPollTimer = null;
    }
    if (awardTimer) {
      clearTimeout(awardTimer);
      awardTimer = null;
    }
    activeAward.value = null;
    view.value = { type: "board" };
    pendingRestoreViewId.value = null;
    error.value = null;
    createError.value = null;
    anomalyRegions.value = {};
    regionLoading.value = {};
    tagVotes.value = {};
    // Queues/undo stacks are per-review server state + session bookkeeping;
    // drop them so a reopen refetches fresh queues. `details` (the per-session
    // receipt snapshot) must go too - otherwise a stale open-time receipt
    // survives the reopen and re-poisons the completion/abort views. `tallies`
    // and the seed guard reset together so a reopen re-seeds the tally cleanly
    // from a fresh server receipt instead of stacking on last session's counts.
    queues.value = {};
    undoStacks.value = {};
    details.value = {};
    tallies.value = {};
    seededReceipts.clear();
  }

  /**
   * Drop everything the PREVIOUS CREDENTIAL could see (issue #655 item 3).
   *
   * Deliberately a second function rather than a widening of `reset()`.
   * `reset()` is the overlay-close path (`ReviewSessionsOverlay.vue`), and it
   * leaves `sessions` / `archived` / the health rows in place on purpose so a
   * reopen renders the board immediately while `load()` revalidates. Clearing
   * those on every close would trade this fix for a flash of empty board on a
   * far more common interaction. An auth-context change is the case where the
   * cached board must NOT survive, so it gets its own entry point.
   *
   * The epoch bump is what closes the in-flight window: `load()` fans out five
   * reads at once, and any of them can still be on the wire when the credential
   * changes.
   */
  function resetSession() {
    sessionEpoch += 1;
    reset();
    sessions.value = [];
    archived.value = [];
    sessionsLoading.value = false;
    healthRows.value = [];
    healthBuilding.value = false;
    healthProgress.value = 0;
    healthComputedAt.value = null;
    healthLoading.value = false;
    healthStale.value = false;
    healthScope.value = { projectId: null, setId: null, characterId: null };
    creating.value = false;
    anomalyTags.value = new Set();
    regionInFlight.clear();
    // Session bookkeeping, not the persisted shelf: `stickers` and `gamify`
    // are localStorage UI preferences (confirmed non-scope-sensitive in the
    // #646 review) and stay, but the running count belongs to the sitting that
    // just ended.
    decisionsCount.value = 0;
    decisionTick.value = 0;
    awardState.since = 0;
    awardState.next = 1;
    awardState.lastIcon = -1;
  }

  // Logout / login / share-token entry all funnel through the one chokepoint in
  // apiClient (`notifySessionReset`).
  const unsubscribeSessionReset = onSessionReset(resetSession);
  onScopeDispose(() => unsubscribeSessionReset());

  // --- Session lifecycle ----------------------------------------------------------

  async function createReview({
    tag,
    projectId = null,
    setId = null,
    characterId = null,
    includeReviewed = false,
  }) {
    const t = (tag || "").trim();
    if (!t || creating.value) return null;
    creating.value = true;
    createError.value = null;
    try {
      const body = { tag: t };
      if (projectId != null) body.project_id = projectId;
      if (setId != null) body.set_id = setId;
      if (characterId != null && characterId !== "")
        body.character_id = String(characterId);
      if (includeReviewed) body.include_reviewed = true;
      const review = await postReview(body);
      await fetchSessions();
      if (review?.id != null) {
        openSession(review.id);
      }
      return review;
    } catch (e) {
      createError.value =
        e?.response?.status === 409
          ? `An open review already exists for “${t}”.`
          : errorDetail(e) || e?.message || "Failed to create the review";
      return null;
    } finally {
      creating.value = false;
    }
  }

  // Refresh appends newly-found suspects - it never rebuilds or resurrects.
  // New items get a client-side _isNew badge (anything not in the queue before
  // the refresh).
  async function refreshSession(id) {
    try {
      const prevIds = new Set((queueFor(id).items || []).map((it) => it.id));
      // Decided items were popped from the queue but must not come back badged
      // "new" - reopen/refill can resurface them; count them as known too.
      for (const entry of undoStacks.value[id] || [])
        prevIds.add(entry.item.id);
      await refreshReview(id);
      await Promise.all([
        fetchSessions(),
        fetchDetail(id),
        fetchQueue(id, { markNewFrom: prevIds }),
      ]);
    } catch (e) {
      error.value = e?.message || "Refresh failed";
    }
  }

  async function archiveSession(id) {
    try {
      await archiveReview(id);
      sessions.value = sessions.value.filter((s) => s.id !== id);
      if (view.value.type === "session" && view.value.id === id) showBoard();
      fetchArchived();
      fetchSessions();
    } catch (e) {
      error.value = e?.message || "Failed to archive the review";
    }
  }

  // Abort the review; decisions already made stand (they were written through
  // on each card).
  async function abortSession(id) {
    try {
      await abortReview(id);
      sessions.value = sessions.value.filter((s) => s.id !== id);
      if (view.value.type === "session" && view.value.id === id) showBoard();
      fetchSessions();
    } catch (e) {
      error.value = e?.message || "Failed to abort the review";
    }
  }

  // Abort AND take the changes back: review-scoped bulk-reopen (the backend
  // ships a review_id param on bulk-reopen), then abort. Skipped items are not
  // changes and are never bulk-undone.
  async function undoChangesAndAbort(id) {
    try {
      await bulkReopenTagSuggestions(id);
    } catch (e) {
      error.value = e?.message || "Failed to undo the review's changes";
      return;
    }
    await abortSession(id);
  }

  // Discard one archived review's receipt. Decisions were written through on
  // each card during the review, so deleting the receipt only drops the audit
  // summary - it never reverses a change. Drop it from the local list and, if
  // its receipt is the current view, fall back to the board.
  async function deleteArchived(id) {
    try {
      await deleteReview(id);
      archived.value = archived.value.filter((a) => a.id !== id);
      if (view.value.type === "archived" && view.value.id === id) showBoard();
    } catch (e) {
      error.value = e?.message || "Failed to delete the archived review";
    }
  }

  // Bulk-clear every archived receipt. `status` is a required query param the
  // backend pins to ARCHIVED so this can never touch an open review.
  async function clearArchived() {
    try {
      await deleteReviewsByStatus("ARCHIVED");
      archived.value = [];
      if (view.value.type === "archived") showBoard();
    } catch (e) {
      error.value = e?.message || "Failed to clear the archived reviews";
    }
  }

  // --- Decisions ----------------------------------------------------------------

  function bumpTally(id, delta, sign = 1) {
    const t = { removed: 0, added: 0, kept: 0, ...(tallies.value[id] || {}) };
    for (const k of Object.keys(delta)) {
      t[k] = Math.max(0, (t[k] || 0) + sign * delta[k]);
    }
    tallies.value = { ...tallies.value, [id]: t };
  }

  // Adjust the session's progress counters. A decision moves done+1/pending-1;
  // a skip moves pending-1/skipped+1 (the item leaves the queue with no
  // decision, but the server still counts the row in its own SKIPPED bucket).
  //
  // Only the buckets a caller actually owns are recomputed - the rest of the
  // server's progress object is spread through untouched. `locked` in particular
  // is NEVER owned by a decision: it counts still-PENDING suspects frozen by a
  // locked picture set, which no accept/dismiss/skip/undo can change. Rebuilding
  // `progress` from scratch here dropped it (and `skipped`), so the "N suspects
  // frozen by a locked set" badge in ReviewSessionView vanished on the first
  // decision - the exact silent-count-drop this bucket exists to explain.
  function bumpProgress(id, { done = 0, pending = 0, skipped = 0 }) {
    sessions.value = sessions.value.map((s) => {
      if (s.id !== id) return s;
      const progress = {
        ...(s.progress || {}),
        done: Math.max(0, (s.progress?.done ?? 0) + done),
        pending: Math.max(0, (s.progress?.pending ?? 0) + pending),
        skipped: Math.max(0, (s.progress?.skipped ?? 0) + skipped),
      };
      return { ...s, progress };
    });
  }

  // Resolve the head card of the ACTIVE session with `action`, mirroring the
  // old store's resolveCurrent: optimistic head-pop, rollback + error surface
  // on failure, background refill when the page runs dry.
  async function resolveCurrent(action, delta, votes) {
    const s = activeSession.value;
    const item = current.value;
    if (!s || !item) return false;
    const id = s.id;
    // Optimistic: drop it from the queue immediately so review never stalls.
    setQueue(id, { items: queueFor(id).items.slice(1) });
    bumpTally(id, delta);
    bumpProgress(id, { done: 1, pending: -1 });
    recordVotes(item.tag, votes);
    // Gamification fires on the optimistic pop (a failed write never claws a
    // sticker back - the schedule is about the act of deciding).
    noteDecision(s.tag);
    try {
      await resolveTagSuggestion(item.id, action);
      undoStacks.value = {
        ...undoStacks.value,
        [id]: [...(undoStacks.value[id] || []), { item, action, delta, votes }],
      };
      // Refill when the local page runs dry but the review still has pending.
      const row = sessions.value.find((x) => x.id === id);
      if (!queueFor(id).items.length && (row?.progress?.pending ?? 0) > 0) {
        await fetchQueue(id);
      }
      return true;
    } catch (e) {
      // Put it back at the head and surface the error; nothing silently lost.
      setQueue(id, { items: [item, ...queueFor(id).items] });
      bumpTally(id, delta, -1);
      bumpProgress(id, { done: -1, pending: 1 });
      retractVotes(item.tag, votes);
      error.value = e?.message || "Failed to save your decision";
      return false;
    }
  }

  // Binary card: Y/N about the suspect picture.
  function answerBinary(answer) {
    const item = current.value;
    if (!item) return Promise.resolve(false);
    return resolveCurrent(
      binaryAction(item, answer),
      binaryDelta(item, answer),
      votesForDecision(item, "binary", answer),
    );
  }

  // Pair card: both / neither / left / right about the two versions.
  function answerPair(corner) {
    const item = current.value;
    if (!item) return Promise.resolve(false);
    return resolveCurrent(
      pairAction(item, corner),
      pairDelta(item, corner),
      votesForDecision(item, "pair", corner),
    );
  }

  // Skip: the reviewer can't decide, so the card leaves the queue PERMANENTLY
  // with no decision (status → SKIPPED server-side; no tag/ledger writes, no
  // award progress). Undo covers it (reopen works on SKIPPED rows).
  //
  // The skip endpoint ships in this package, so a 404 is never "not implemented
  // yet" - it means the suggestion is already gone (a dead/reopened id). In that
  // case the optimistic removal stands, but there is nothing to reopen: we must
  // NOT record a reversible skip entry, or a later undo()/reopenSkipped() would
  // POST /reopen on a dead id (another 404) and could block reopening the rest.
  async function skip() {
    const s = activeSession.value;
    const item = current.value;
    if (!s || !item) return;
    const id = s.id;
    // Optimistic: the card leaves the queue immediately.
    setQueue(id, { items: queueFor(id).items.slice(1) });
    bumpTally(id, { skipped: 1 });
    bumpProgress(id, { pending: -1, skipped: 1 });
    try {
      await skipTagSuggestion(item.id);
      undoStacks.value = {
        ...undoStacks.value,
        [id]: [
          ...(undoStacks.value[id] || []),
          { item, action: "skip", delta: { skipped: 1 }, votes: [] },
        ],
      };
    } catch (e) {
      if (e?.response?.status === 404) {
        // Already gone server-side - the card stays out of the queue, but it is
        // not reopenable, so no undo entry is recorded.
        return;
      }
      // Real failure: put the card back, nothing silently lost.
      setQueue(id, { items: [item, ...queueFor(id).items] });
      bumpTally(id, { skipped: 1 }, -1);
      bumpProgress(id, { pending: 1, skipped: -1 });
      error.value = e?.message || "Failed to skip";
    }
  }

  // Reopen every card skipped in THIS client session (their ids live on the
  // undo stack) and put them back in the queue.
  async function reopenSkipped(id) {
    const stack = undoStacks.value[id] || [];
    const skips = stack.filter((e) => e.action === "skip");
    if (!skips.length) return;
    // Settle every reopen independently - one dead/already-gone id must not
    // block reopening the rest (a single rejection in Promise.all did exactly
    // that). Fulfilled ids return to the queue; a 404 (already gone) just
    // leaves the skip stack; a hard failure keeps its entry so it can be
    // retried.
    const results = await Promise.allSettled(
      skips.map((e) => reopenTagSuggestion(e.item.id)),
    );
    const reopened = []; // fulfilled → put back in the queue
    const settled = new Set(); // reopened OR already-gone (404) → drop from stack
    let hardError = null;
    results.forEach((res, i) => {
      const entry = skips[i];
      if (res.status === "fulfilled") {
        reopened.push(entry);
        settled.add(entry);
      } else if (res.reason?.response?.status === 404) {
        settled.add(entry); // already gone - nothing to reopen, stop tracking it
      } else {
        hardError = res.reason;
      }
    });
    if (hardError) {
      error.value = hardError?.message || "Failed to reopen the skipped cards";
    }
    if (!settled.size) return;
    undoStacks.value = {
      ...undoStacks.value,
      [id]: stack.filter((e) => !settled.has(e)),
    };
    bumpTally(id, { skipped: settled.size }, -1);
    // `settled` = reopened (SKIPPED → PENDING) plus already-gone 404s (the row
    // no longer exists in any bucket). Both leave the server's `skipped` count,
    // so it drops by settled.size while `pending` only regains the reopened.
    bumpProgress(id, { pending: reopened.length, skipped: -settled.size });
    if (reopened.length) {
      setQueue(id, {
        items: sortQueue([
          ...reopened.map((e) => e.item),
          ...queueFor(id).items,
        ]),
      });
    }
  }

  // Undo the most recent decision OR skip in the active session: reopen
  // server-side, put the card back at the head, reverse the tally/progress and
  // the consistency votes. XP/level/streak are NET (a decision-undo decrements
  // them); stickers are never removed and celebrations never re-fire.
  async function undo() {
    const s = activeSession.value;
    if (!s) return;
    const stack = undoStacks.value[s.id] || [];
    const last = stack[stack.length - 1];
    if (!last) return;
    try {
      await reopenTagSuggestion(last.item.id);
    } catch (e) {
      error.value = e?.message || "Failed to undo";
      return;
    }
    undoStacks.value = { ...undoStacks.value, [s.id]: stack.slice(0, -1) };
    bumpTally(s.id, last.delta, -1);
    if (last.action === "skip") {
      bumpProgress(s.id, { pending: 1, skipped: -1 });
    } else {
      bumpProgress(s.id, { done: -1, pending: 1 });
      // Net XP: undoing a decision walks the counter back (skips never counted).
      decisionsCount.value = Math.max(0, decisionsCount.value - 1);
    }
    if (last.votes) retractVotes(last.item.tag, last.votes);
    setQueue(s.id, { items: [last.item, ...queueFor(s.id).items] });
  }

  // --- Gamification ----------------------------------------------------------------

  function setGamify(v) {
    gamify.value = !!v;
    try {
      window.localStorage.setItem(GAMIFY_PREF_KEY, gamify.value ? "1" : "0");
    } catch {
      // Best-effort; the in-memory toggle still works this session.
    }
    if (gamify.value) {
      // Instant gratification: the FIRST decision after enabling always awards.
      awardState.since = 0;
      awardState.next = 1;
      awardState.lastIcon = -1;
    }
  }

  // Called once per real decision (never for skip/undo). Bumps the monotonic
  // counters and, while gamified, advances the variable-ratio sticker schedule.
  // Celebrations key off the explicit decisionTick event, never derived state,
  // so undo can never re-trigger them.
  function noteDecision(tag) {
    decisionsCount.value += 1;
    if (!gamify.value) return null;
    decisionTick.value += 1;
    return maybeAward(tag);
  }

  // Variable-ratio schedule: award, then re-arm for AWARD_GAP_MIN..MAX
  // decisions ahead. Never the same sticker icon twice in a row.
  function maybeAward(tag) {
    awardState.since += 1;
    if (awardState.since < awardState.next) return null;
    awardState.since = 0;
    awardState.next =
      AWARD_GAP_MIN +
      Math.floor(Math.random() * (AWARD_GAP_MAX - AWARD_GAP_MIN + 1));
    let idx = Math.floor(Math.random() * STICKER_ICONS.length);
    if (idx === awardState.lastIcon) idx = (idx + 7) % STICKER_ICONS.length;
    awardState.lastIcon = idx;
    const sticker = {
      id: `${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
      icon: STICKER_ICONS[idx].icon,
      label: STICKER_ICONS[idx].label,
      color: STICKER_COLORS[Math.floor(Math.random() * STICKER_COLORS.length)],
      tag: tag ?? null,
    };
    // Pop near the rail edge, hold ~500ms, fly to the shelf - then land.
    activeAward.value = sticker;
    if (awardTimer) clearTimeout(awardTimer);
    awardTimer = setTimeout(() => {
      commitAward(sticker);
    }, 1400);
    return sticker;
  }

  function commitAward(sticker) {
    if (awardTimer) {
      clearTimeout(awardTimer);
      awardTimer = null;
    }
    if (activeAward.value?.id === sticker.id) activeAward.value = null;
    stickers.value = [...stickers.value, sticker];
    writeStickers(stickers.value);
  }

  // Empty the shelf (and its persisted copy). Also cancels an award mid-fly -
  // otherwise its pending commit would land one sticker right after the clear.
  function clearStickers() {
    if (awardTimer) {
      clearTimeout(awardTimer);
      awardTimer = null;
    }
    activeAward.value = null;
    stickers.value = [];
    writeStickers(stickers.value);
  }

  return {
    overlayOpen,
    view,
    pendingRestoreViewId,
    error,
    sessions,
    archived,
    sessionsLoading,
    details,
    queues,
    tallies,
    undoStacks,
    healthRows,
    healthBuilding,
    healthProgress,
    healthComputedAt,
    healthLoading,
    healthStale,
    healthScope,
    healthScoped,
    creating,
    createError,
    projects,
    sets,
    characters,
    anomalyTags,
    heatmapEnabled,
    anomalyRegions,
    tagVotes,
    gamify,
    stickers,
    activeAward,
    decisionsCount,
    decisionTick,
    activeSession,
    activeQueue,
    activeQueueLoading,
    current,
    activeTally,
    canUndo,
    skippedCountFor,
    reopenableSkipsFor,
    decidedCountFor,
    receiptFor,
    isAnomalyTag,
    fetchSessions,
    fetchArchived,
    fetchDetail,
    fetchQueue,
    fetchHealth,
    setHealthScope,
    rebuildHealth,
    fetchAnomalyTags,
    fetchScopeOptions,
    anomalyRegionFor,
    isRegionLoading,
    setHeatmapEnabled,
    fetchAnomalyRegion,
    recordVotes,
    retractVotes,
    decisionConflict,
    load,
    showBoard,
    openSession,
    openArchived,
    reset,
    resetSession,
    createReview,
    refreshSession,
    archiveSession,
    abortSession,
    undoChangesAndAbort,
    deleteArchived,
    clearArchived,
    answerBinary,
    answerPair,
    skip,
    reopenSkipped,
    undo,
    setGamify,
    noteDecision,
    commitAward,
    clearStickers,
  };
});
