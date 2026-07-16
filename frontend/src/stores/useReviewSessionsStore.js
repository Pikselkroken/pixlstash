// useReviewSessionsStore.js — state for the "Review sessions" overlay.
//
// Replaces useReviewFixesStore's hidden queue with first-class review sessions:
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
// streak counters are monotonic — Undo never decrements them, and stickers are
// never clawed back.

import { ref, computed } from "vue";
import { defineStore } from "pinia";
import { apiClient } from "../utils/apiClient";
import { SET_ICONS, SET_COLORS } from "../utils/setAppearance";

const PAGE_SIZE = 200;

// localStorage keys. The heatmap key is shared with the old overlay on purpose
// so the user's evidence-region preference carries over.
const STICKERS_KEY = "pixlstash:reviewStickers";
const HEATMAP_PREF_KEY = "pixlstash:reviewHeatmap";

// A decision that contradicts a CONFIDENT prior call this session (the user has
// only ever said the opposite, at least this many times) is held for confirm.
const CONFLICT_MIN_OPPOSITE = 2;

// Floor BOTH the near-twin vote and the tagger margin must clear — and they
// must agree on the fix — for a suggestion to be auto-resolvable (same
// threshold the old overlay used for its bulk accept).
const BULK_THRESHOLD = 0.9;

// Sticker vocabulary: the Picture Set icon + colour palette, restyled by the
// components as die-cut stickers. Reusing the module is a hard requirement —
// the arrays are derived, never copied.
export const STICKER_ICONS = SET_ICONS.map((ic) => ({
  icon: ic.value,
  label: ic.label,
}));
export const STICKER_COLORS = SET_COLORS.map((c) => c.value);

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
// the untagged side — which picture id is which depends on `direction`, exactly
// as in the old overlay). Mapping mirrors the old dispatchDecision():
//   left  (only the tagged side has it — labels already correct) → dismiss
//   both  (tag the untagged side too)  → remove: fix-twin (twin is the untagged
//          side) · add: accept (the suspect is the untagged side)
//   neither (clear the tagged side)    → remove: accept · add: fix-twin
//   right (the label is on the wrong image — move it)            → swap
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
export function votesForDecision(item, kind, decision) {
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

export const useReviewSessionsStore = defineStore("reviewSessions", () => {
  const overlayOpen = ref(false);
  // What the main area shows: the board, one open session, or an archived
  // review's receipt. { type: 'board' } | { type: 'session'|'archived', id }.
  const view = ref({ type: "board" });
  const error = ref(null);

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
  // Skip calls that 404ed (interim testing before the backend ships
  // POST /tag_suggestions/{id}/skip): the client-side removal stands and the
  // call is retried opportunistically.
  const pendingSkips = [];

  // --- Tag health board ------------------------------------------------------
  const healthRows = ref([]);
  const healthBuilding = ref(false);
  const healthProgress = ref(0); // normalised 0..1
  const healthComputedAt = ref(null);
  const healthLoading = ref(false);
  let healthPollTimer = null;

  // --- New-review creation ----------------------------------------------------
  const creating = ref(false);
  const createError = ref(null);

  // --- Scope options (for the creation dialog) --------------------------------
  const projects = ref([]);
  const sets = ref([]);
  const characters = ref([]);

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
  // { [tag]: { [pid]: { has, not } } } — how many times this session the user
  // asserted a picture HAS / does NOT have the tag. Backs the conflict guard.
  const tagVotes = ref({});

  // --- Bulk auto-resolve (for the active session's receipt line) ---------------
  const bulkCount = ref(0);
  const bulkSample = ref([]);
  const bulkBusy = ref(false);
  const lastBulk = ref(null); // { ids, count } of the most recent bulk-accept

  // --- Gamification -----------------------------------------------------------
  const gamify = ref(false);
  const stickers = ref(readStickers()); // the shelf — persists across sessions
  const activeAward = ref(null); // sticker mid pop→fly animation, or null
  // NET decision count: XP/level/streak derive from it and Undo decrements it.
  // Celebrations key off decisionTick, an EXPLICIT per-decision event that
  // undo never re-fires — and stickers are never clawed back either way.
  const decisionsCount = ref(0);
  const decisionTick = ref(0); // bumps on every real decision (explicit event)
  // Variable-ratio schedule: first decision after enabling always awards, then
  // every 2–5. `lastIcon` prevents the same sticker twice in a row.
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

  // How many CHANGES were made in a review (skips are not changes) — drives
  // the abort dialog's "You made N changes".
  function decidedCountFor(id) {
    const r = receiptFor(id);
    return r.removed + r.added + r.kept;
  }

  // The session receipt for the completion state: prefer the server's receipt
  // (covers decisions made in earlier app sessions), fall back to this
  // client's tally.
  function receiptFor(id) {
    const d = details.value[id];
    const r = d?.receipt || d?.stats?.receipt;
    if (r && (r.removed != null || r.added != null || r.kept != null)) {
      return {
        removed: r.removed ?? 0,
        added: r.added ?? 0,
        kept: r.kept ?? 0,
        skipped: r.skipped ?? tallies.value[id]?.skipped ?? 0,
      };
    }
    return { ...EMPTY_TALLY, ...(tallies.value[id] || {}) };
  }

  function isAnomalyTag(tag) {
    return anomalyTags.value.has(String(tag || "").trim().toLowerCase());
  }

  // --- Fetches ----------------------------------------------------------------

  async function fetchSessions() {
    sessionsLoading.value = true;
    try {
      const res = await apiClient.get("/reviews", {
        params: { status: "OPEN" },
      });
      sessions.value = Array.isArray(res.data) ? res.data : [];
    } catch (e) {
      error.value = e?.message || "Failed to load reviews";
    } finally {
      sessionsLoading.value = false;
    }
  }

  async function fetchArchived() {
    try {
      const res = await apiClient.get("/reviews", {
        params: { status: "ARCHIVED" },
      });
      archived.value = Array.isArray(res.data) ? res.data : [];
    } catch {
      archived.value = [];
    }
  }

  async function fetchDetail(id) {
    try {
      const res = await apiClient.get(`/reviews/${id}`);
      details.value = { ...details.value, [id]: res.data ?? null };
    } catch {
      // Detail is enrichment (receipt stats); the list row carries the basics.
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
      const res = await apiClient.get(`/reviews/${id}/suggestions`, {
        params: { status: "PENDING", limit: PAGE_SIZE },
      });
      let items = Array.isArray(res.data?.items)
        ? res.data.items
        : Array.isArray(res.data)
          ? res.data
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
    try {
      const res = await apiClient.get("/tag_health");
      const data = res.data ?? {};
      healthRows.value = Array.isArray(data.rows) ? data.rows : [];
      healthBuilding.value = !!data.building;
      const p = Number(data.progress ?? 0);
      healthProgress.value = p > 1 ? Math.min(1, p / 100) : Math.max(0, p);
      healthComputedAt.value = data.computed_at ?? null;
      scheduleHealthPoll();
    } catch (e) {
      error.value = e?.message || "Failed to load tag health";
    } finally {
      healthLoading.value = false;
    }
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
      await apiClient.post("/tag_health/rebuild");
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
      const res = await apiClient.get("/users/me/config");
      const raw = res.data?.smart_score_penalised_tags;
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

  // Populate the creation dialog's scope dropdowns (mirrors the old store: each
  // call independent, degrades to an empty list on error).
  async function fetchScopeOptions() {
    apiClient
      .get("/projects")
      .then((res) => {
        projects.value = Array.isArray(res.data) ? res.data : [];
      })
      .catch(() => {
        projects.value = [];
      });
    apiClient
      .get("/picture_sets")
      .then((res) => {
        sets.value = Array.isArray(res.data)
          ? res.data.filter((s) => s?.name !== "reference_pictures")
          : [];
      })
      .catch(() => {
        sets.value = [];
      });
    apiClient
      .get("/characters")
      .then((res) => {
        characters.value = Array.isArray(res.data) ? res.data : [];
      })
      .catch(() => {
        characters.value = [];
      });
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
      const res = await apiClient.get(`/pictures/${pictureId}/anomaly_region`, {
        params: { tag },
      });
      const data = res.data ?? null;
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
    fetchArchived();
    fetchAnomalyTags();
    fetchScopeOptions();
    await fetchSessions();
  }

  function showBoard() {
    view.value = { type: "board" };
  }

  function openSession(id) {
    view.value = { type: "session", id };
    if (!queues.value[id]) fetchQueue(id);
    if (!(id in details.value)) fetchDetail(id);
    refreshBulk(id);
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
    error.value = null;
    createError.value = null;
    anomalyRegions.value = {};
    regionLoading.value = {};
    tagVotes.value = {};
    lastBulk.value = null;
    // Queues/undo stacks are per-review server state + session bookkeeping;
    // drop them so a reopen refetches fresh queues.
    queues.value = {};
    undoStacks.value = {};
  }

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
      const res = await apiClient.post("/reviews", body);
      const review = res.data;
      await fetchSessions();
      if (review?.id != null) {
        openSession(review.id);
      }
      return review;
    } catch (e) {
      createError.value =
        e?.response?.status === 409
          ? `An open review already exists for “${t}”.`
          : e?.response?.data?.detail ||
            e?.message ||
            "Failed to create the review";
      return null;
    } finally {
      creating.value = false;
    }
  }

  // Refresh appends newly-found suspects — it never rebuilds or resurrects.
  // New items get a client-side _isNew badge (anything not in the queue before
  // the refresh).
  async function refreshSession(id) {
    try {
      const prevIds = new Set((queueFor(id).items || []).map((it) => it.id));
      // Decided items were popped from the queue but must not come back badged
      // "new" — reopen/refill can resurface them; count them as known too.
      for (const entry of undoStacks.value[id] || [])
        prevIds.add(entry.item.id);
      await apiClient.post(`/reviews/${id}/refresh`);
      await Promise.all([
        fetchSessions(),
        fetchDetail(id),
        fetchQueue(id, { markNewFrom: prevIds }),
      ]);
      refreshBulk(id);
    } catch (e) {
      error.value = e?.message || "Refresh failed";
    }
  }

  async function archiveSession(id) {
    try {
      await apiClient.post(`/reviews/${id}/archive`);
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
      await apiClient.post(`/reviews/${id}/abort`);
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
      await apiClient.post("/tag_suggestions/bulk-reopen", { review_id: id });
    } catch (e) {
      error.value = e?.message || "Failed to undo the review's changes";
      return;
    }
    await abortSession(id);
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
  // a skip only drains pending (the item leaves the queue with no decision).
  function bumpProgress(id, { done = 0, pending = 0 }) {
    sessions.value = sessions.value.map((s) => {
      if (s.id !== id) return s;
      const progress = {
        done: Math.max(0, (s.progress?.done ?? 0) + done),
        pending: Math.max(0, (s.progress?.pending ?? 0) + pending),
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
    // sticker back — the schedule is about the act of deciding).
    noteDecision(s.tag);
    try {
      await apiClient.post(`/tag_suggestions/${item.id}/${action}`);
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
  // award progress). Undo covers it (reopen works on SKIPPED rows). While the
  // backend endpoint hasn't landed, a 404 degrades gracefully: the client-side
  // removal stands and the call is queued for a later retry.
  async function skip() {
    const s = activeSession.value;
    const item = current.value;
    if (!s || !item) return;
    const id = s.id;
    // Optimistic: the card leaves the queue immediately.
    setQueue(id, { items: queueFor(id).items.slice(1) });
    bumpTally(id, { skipped: 1 });
    bumpProgress(id, { pending: -1 });
    flushPendingSkips();
    try {
      await apiClient.post(`/tag_suggestions/${item.id}/skip`);
      undoStacks.value = {
        ...undoStacks.value,
        [id]: [
          ...(undoStacks.value[id] || []),
          { item, action: "skip", delta: { skipped: 1 }, votes: [] },
        ],
      };
    } catch (e) {
      if (e?.response?.status === 404) {
        // Interim: endpoint not shipped yet. Keep the removal, retry later,
        // and still allow undo (reopen no-ops harmlessly on a PENDING row).
        pendingSkips.push(item.id);
        undoStacks.value = {
          ...undoStacks.value,
          [id]: [
            ...(undoStacks.value[id] || []),
            { item, action: "skip", delta: { skipped: 1 }, votes: [] },
          ],
        };
        return;
      }
      // Real failure: put the card back, nothing silently lost.
      setQueue(id, { items: [item, ...queueFor(id).items] });
      bumpTally(id, { skipped: 1 }, -1);
      bumpProgress(id, { pending: 1 });
      error.value = e?.message || "Failed to skip";
    }
  }

  // Retry skip calls that 404ed earlier (fires opportunistically).
  function flushPendingSkips() {
    while (pendingSkips.length) {
      const sid = pendingSkips.pop();
      apiClient.post(`/tag_suggestions/${sid}/skip`).catch(() => {
        // Still not there — give up quietly; the backend scan state will
        // reconcile once the endpoint lands.
      });
    }
  }

  // Reopen every card skipped in THIS client session (their ids live on the
  // undo stack) and put them back in the queue.
  async function reopenSkipped(id) {
    const stack = undoStacks.value[id] || [];
    const skips = stack.filter((e) => e.action === "skip");
    if (!skips.length) return;
    try {
      await Promise.all(
        skips.map((e) => apiClient.post(`/tag_suggestions/${e.item.id}/reopen`)),
      );
    } catch (e) {
      error.value = e?.message || "Failed to reopen the skipped cards";
      return;
    }
    undoStacks.value = {
      ...undoStacks.value,
      [id]: stack.filter((e) => e.action !== "skip"),
    };
    bumpTally(id, { skipped: skips.length }, -1);
    bumpProgress(id, { pending: skips.length });
    setQueue(id, {
      items: sortQueue([...skips.map((e) => e.item), ...queueFor(id).items]),
    });
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
      await apiClient.post(`/tag_suggestions/${last.item.id}/reopen`);
    } catch (e) {
      error.value = e?.message || "Failed to undo";
      return;
    }
    undoStacks.value = { ...undoStacks.value, [s.id]: stack.slice(0, -1) };
    bumpTally(s.id, last.delta, -1);
    if (last.action === "skip") {
      bumpProgress(s.id, { pending: 1 });
    } else {
      bumpProgress(s.id, { done: -1, pending: 1 });
      // Net XP: undoing a decision walks the counter back (skips never counted).
      decisionsCount.value = Math.max(0, decisionsCount.value - 1);
    }
    if (last.votes) retractVotes(last.item.tag, last.votes);
    setQueue(s.id, { items: [last.item, ...queueFor(s.id).items] });
  }

  // --- Bulk auto-resolve ("N obvious suspects — auto-resolve?") ------------------
  //
  // Ports the old overlay's bulk accept against the session's frozen scope:
  // dry-run for the receipt count + least-confident sample, real run + undo.

  function bulkParams(session, extra = {}) {
    const scope = session?.scope || {};
    const p = {
      tag: session.tag,
      min_combined: BULK_THRESHOLD,
      ...extra,
    };
    if (scope.project_id != null) p.project_id = scope.project_id;
    if (scope.set_id != null) p.set_id = scope.set_id;
    if (scope.character_id != null && scope.character_id !== "")
      p.character_id = String(scope.character_id);
    return p;
  }

  async function refreshBulk(id) {
    const session = sessions.value.find((x) => x.id === id);
    if (!session) {
      bulkCount.value = 0;
      bulkSample.value = [];
      return;
    }
    // Prefer the server's receipt count when the detail carries one.
    const d = details.value[id];
    try {
      const res = await apiClient.post(
        "/tag_suggestions/bulk-accept",
        bulkParams(session, { dry_run: true }),
      );
      bulkCount.value = res.data?.count ?? d?.stats?.auto_resolvable ?? 0;
      bulkSample.value = res.data?.sample ?? [];
    } catch {
      bulkCount.value = d?.stats?.auto_resolvable ?? 0;
      bulkSample.value = [];
    }
  }

  async function runBulk(id) {
    const session = sessions.value.find((x) => x.id === id);
    if (!session || bulkBusy.value) return;
    bulkBusy.value = true;
    try {
      const res = await apiClient.post(
        "/tag_suggestions/bulk-accept",
        bulkParams(session),
      );
      lastBulk.value = {
        ids: res.data?.accepted_ids ?? [],
        count: res.data?.count ?? 0,
      };
      await Promise.all([fetchSessions(), fetchDetail(id), fetchQueue(id)]);
      bulkCount.value = 0;
      bulkSample.value = [];
    } catch (e) {
      error.value = e?.message || "Auto-resolve failed";
    } finally {
      bulkBusy.value = false;
    }
  }

  async function undoBulk(id) {
    const last = lastBulk.value;
    if (!last || !last.ids.length || bulkBusy.value) return;
    bulkBusy.value = true;
    try {
      await apiClient.post("/tag_suggestions/bulk-reopen", { ids: last.ids });
      lastBulk.value = null;
      await Promise.all([fetchSessions(), fetchDetail(id), fetchQueue(id)]);
      refreshBulk(id);
    } catch (e) {
      error.value = e?.message || "Undo failed";
    } finally {
      bulkBusy.value = false;
    }
  }

  // --- Gamification ----------------------------------------------------------------

  function setGamify(v) {
    gamify.value = !!v;
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

  // Variable-ratio schedule: award, then re-arm for 2–5 decisions ahead. Never
  // the same sticker icon twice in a row.
  function maybeAward(tag) {
    awardState.since += 1;
    if (awardState.since < awardState.next) return null;
    awardState.since = 0;
    awardState.next = 2 + Math.floor(Math.random() * 4); // 2..5
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
    // Pop near the rail edge, hold ~500ms, fly to the shelf — then land.
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

  return {
    overlayOpen,
    view,
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
    creating,
    createError,
    projects,
    sets,
    characters,
    anomalyTags,
    heatmapEnabled,
    anomalyRegions,
    tagVotes,
    bulkCount,
    bulkSample,
    bulkBusy,
    lastBulk,
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
    createReview,
    refreshSession,
    archiveSession,
    abortSession,
    undoChangesAndAbort,
    answerBinary,
    answerPair,
    skip,
    reopenSkipped,
    undo,
    refreshBulk,
    runBulk,
    undoBulk,
    setGamify,
    noteDecision,
    commitAward,
  };
});
