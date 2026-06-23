// useReviewFixesStore.js — state for the "Review tags" queue.
//
// Backs the ReviewFixesOverlay: it loads the per-tag suggestion counts, loads the
// ranked queue for one tag/direction, and applies accept/dismiss decisions through the
// /tag_suggestions endpoints. Accepting writes the label fix through to the Tag table
// (remove → deletes the wrong tag, add → creates the missing one); dismissing leaves
// the labels untouched. Resolved items are spliced out so the queue always shows the
// next pending suspect, worst-first.

import { ref, computed } from "vue";
import { defineStore } from "pinia";
import { apiClient } from "../utils/apiClient";

const PAGE_SIZE = 300;

// One picture can be the RIGHT-side twin of many cards, so the user judges the same
// picture's tag-status repeatedly and may answer inconsistently. We tally those calls
// per (tag, pictureId) and warn when a new decision contradicts a CONFIDENT prior.
// "Confident" means the user has only ever said the opposite, at least this many times.
const CONFLICT_MIN_OPPOSITE = 2;

export const useReviewFixesStore = defineStore("reviewFixes", () => {
  const overlayOpen = ref(false); // whether the review overlay is showing
  const tags = ref([]); // [{ tag, add, remove, total }], busiest first
  const activeTag = ref(null);
  const direction = ref(""); // kept for the API; the UI no longer filters by it
  const allTags = ref([]); // all vault tag values, for the tag-picker autocomplete
  // Lowercased anomaly tags (the user's smart-score "penalised" tags). Rendered red in the
  // tag picker so a reviewer can spot a flagged tag at a glance. A Set for O(1) membership.
  const anomalyTags = ref(new Set());
  const items = ref([]); // ranked pending suggestions for activeTag/direction
  const loading = ref(false);
  const error = ref(null);
  const undoStack = ref([]); // [{ item, action }] — last resolved first off the end
  const bulkThreshold = ref(0.9); // floor BOTH the near-twin vote and the tagger margin must clear — and they must agree on the fix — to auto-resolve
  const bulkCount = ref(0); // how many PENDING clear the threshold (live preview)
  const bulkBusy = ref(false);
  const lastBulk = ref(null); // { ids, count } of the most recent bulk-accept, for undo
  const bulkSample = ref([]); // a few least-confident would-be resolutions, for preview
  const previewOpen = ref(false);
  const scanning = ref(false); // a near-neighbour scan is running
  const scanError = ref(null);

  // Scope filters: narrow the suggestion queue/summary/bulk to one project / set / character.
  // characterId may be a number (a real character) or the literal "UNASSIGNED". Null means
  // "that dimension is not filtered". The overlay seeds this from the app's current selection
  // when it opens, so it lands pre-filtered to whatever view you came from.
  const scope = ref({ projectId: null, setId: null, characterId: null });
  // Option lists for the filter dropdowns, fetched in the background by fetchScopeOptions().
  const projects = ref([]);
  const sets = ref([]);
  const characters = ref([]);

  // Session vote ledger, keyed by (tag, pictureId): how many times this session the user
  // has asserted the picture HAS the active tag vs does NOT. Shaped:
  //   { [tag]: { [pid]: { has: number, not: number } } }
  // Read by component computeds (consistency chips + conflict guard), so every write
  // reassigns the ref immutably to trigger reactivity (matching the store's ref pattern).
  const tagVotes = ref({});

  // Session tally, for the progress line.
  const removedCount = ref(0);
  const addedCount = ref(0);
  const keptCount = ref(0);

  const current = computed(() => items.value[0] ?? null);
  const loadedCount = computed(() => items.value.length);
  const canUndo = computed(() => undoStack.value.length > 0);
  const reviewedCount = computed(
    () => removedCount.value + addedCount.value + keptCount.value,
  );

  // The near-neighbour link only SELECTED this pair to compare — it doesn't vote on the
  // answer. The decision comes from the tagger's confidence on the two near-identical
  // images: if they agree it IS the tag → "left" (tag both); if they agree it's NOT →
  // "right" (untag both); if they disagree it lands near 0.5 and stays for manual review.
  function decision(item) {
    if (!item) return { corner: null, confidence: 0, hasModel: false };
    // Per-image: left = the currently-tagged image, right = the untagged one.
    const leftConf =
      item.direction === "remove"
        ? item.tagger_confidence
        : item.twin_tagger_confidence;
    const rightConf =
      item.direction === "remove"
        ? item.twin_tagger_confidence
        : item.tagger_confidence;
    if (leftConf == null || rightConf == null) {
      return { corner: null, confidence: 0, hasModel: false };
    }
    const leftHas = leftConf >= 0.5;
    const rightHas = rightConf >= 0.5;
    const corner =
      leftHas && rightHas
        ? "both"
        : !leftHas && !rightHas
          ? "neither"
          : leftHas
            ? "leftonly"
            : "rightonly";
    // Decisive only when BOTH per-image calls are confident.
    const confidence = Math.min(
      Math.max(leftConf, 1 - leftConf),
      Math.max(rightConf, 1 - rightConf),
    );
    return { corner, confidence, hasModel: true };
  }

  // Review-queue grouping order. The two buckets where the near-twins AGREE come
  // first (the tagger is self-consistent, so they're the fastest, most
  // batchable, highest-trust calls); the two where the twins DISAGREE come last
  // (genuinely harder cases that deserve a closer look). Within "agree" we clear
  // wrong flags before adding missing ones; within "disagree", confirm before
  // swap. Unscored pairs (no verdict) sort to the very end.
  const BUCKET_ORDER = { neither: 0, both: 1, leftonly: 2, rightonly: 3 };
  function bucketRank(item) {
    const corner = decision(item).corner;
    return corner in BUCKET_ORDER ? BUCKET_ORDER[corner] : 99;
  }

  // --- Session vote ledger ---------------------------------------------------
  //
  // Each card is a DEFINITIVE judgement of BOTH pictures' status for the active tag.
  // LEFT is always the currently-tagged side, RIGHT the untagged side; which picture id
  // is which depends on the suggestion direction (mirrors taggedSide/untaggedSide in the
  // overlay). The four corners decide has/not for each side:
  //   leftonly  → left HAS, right NOT
  //   both      → left HAS, right HAS
  //   neither   → left NOT, right NOT
  //   rightonly → left NOT, right HAS
  const CORNER_VOTES = {
    leftonly: { left: "has", right: "not" },
    both: { left: "has", right: "has" },
    neither: { left: "not", right: "not" },
    rightonly: { left: "not", right: "has" },
  };

  // Translate (item, corner) into the per-picture votes [{ pid, side, vote }], skipping a
  // null twin (the RIGHT side may be absent). Returns [] for an unknown corner/item.
  function votesFor(item, corner) {
    const map = CORNER_VOTES[corner];
    if (!item || !map) return [];
    const leftPid =
      item.direction === "remove" ? item.picture_id : item.twin_picture_id;
    const rightPid =
      item.direction === "remove" ? item.twin_picture_id : item.picture_id;
    const out = [];
    if (leftPid != null) out.push({ pid: leftPid, side: "left", vote: map.left });
    if (rightPid != null) out.push({ pid: rightPid, side: "right", vote: map.right });
    return out;
  }

  // Increment the tally for both pictures under item.tag. Reassigns tagVotes immutably so
  // the component computeds that read it re-run.
  function recordDecisionVotes(item, corner) {
    const votes = votesFor(item, corner);
    if (!votes.length || !item?.tag) return;
    const next = { ...tagVotes.value };
    const tagBucket = { ...(next[item.tag] || {}) };
    for (const { pid, vote } of votes) {
      const prev = tagBucket[pid] || { has: 0, not: 0 };
      tagBucket[pid] = {
        has: prev.has + (vote === "has" ? 1 : 0),
        not: prev.not + (vote === "not" ? 1 : 0),
      };
    }
    next[item.tag] = tagBucket;
    tagVotes.value = next;
  }

  // The inverse of recordDecisionVotes, for undo / optimistic-failure rollback. Never lets
  // a count drop below 0 (a defensive floor — a retract should always have a matching add).
  function retractDecisionVotes(item, corner) {
    const votes = votesFor(item, corner);
    if (!votes.length || !item?.tag || !tagVotes.value[item.tag]) return;
    const next = { ...tagVotes.value };
    const tagBucket = { ...next[item.tag] };
    for (const { pid, vote } of votes) {
      const prev = tagBucket[pid];
      if (!prev) continue;
      tagBucket[pid] = {
        has: Math.max(0, prev.has - (vote === "has" ? 1 : 0)),
        not: Math.max(0, prev.not - (vote === "not" ? 1 : 0)),
      };
    }
    next[item.tag] = tagBucket;
    tagVotes.value = next;
  }

  // Prior votes recorded for a picture under the ACTIVE tag (0/0 when unseen).
  function votesForPicture(pid) {
    const bucket = tagVotes.value[activeTag.value];
    return bucket?.[pid] ? { ...bucket[pid] } : { has: 0, not: 0 };
  }

  // For the CURRENT head item, would applying `corner` contradict a confident prior?
  // A conflict is: for one of the two pictures, the user has ONLY ever asserted the
  // OPPOSITE of the new vote, at least CONFLICT_MIN_OPPOSITE times. Returns the single
  // strongest conflict (most opposite votes) when both panes conflict, else null.
  function decisionConflict(corner) {
    const item = current.value;
    const votes = votesFor(item, corner);
    if (!votes.length) return null;
    const bucket = tagVotes.value[item.tag] || {};
    let best = null;
    let bestOpposite = -1;
    for (const { pid, side, vote } of votes) {
      const prior = bucket[pid];
      if (!prior) continue;
      const oppositeCount = vote === "has" ? prior.not : prior.has;
      const sameCount = vote === "has" ? prior.has : prior.not;
      // Confident contradiction: only ever said the opposite, at least the threshold.
      if (oppositeCount >= CONFLICT_MIN_OPPOSITE && sameCount === 0) {
        if (oppositeCount > bestOpposite) {
          bestOpposite = oppositeCount;
          best = {
            pid,
            side,
            priorHas: prior.has,
            priorNot: prior.not,
            asserting: vote,
          };
        }
      }
    }
    return best;
  }

  // True number still pending for the active tag + direction, taken from the
  // summary and decremented as we resolve — NOT just the size of the loaded page
  // (the queue fetches PAGE_SIZE at a time and refills in the background).
  const remainingTotal = computed(() => {
    const entry = tags.value.find((t) => t.tag === activeTag.value);
    if (!entry) return 0;
    if (direction.value === "remove") return entry.remove;
    if (direction.value === "add") return entry.add;
    return entry.total;
  });

  function pendingForActiveTag() {
    return remainingTotal.value;
  }

  // The non-null scope filters, shaped for the API (query params or POST body). Omits any
  // dimension that is null so an unfiltered call sends nothing and behaves as before.
  function scopeParams() {
    const { projectId, setId, characterId } = scope.value;
    const p = {};
    if (projectId != null) p.project_id = projectId;
    if (setId != null) p.set_id = setId;
    // character_id is typed `str` server-side (it carries either a numeric id or the
    // literal "UNASSIGNED"). Send it as a string so the bulk-accept POST body — JSON,
    // where a raw number would fail the `str` validation — matches the query-param path.
    if (characterId != null && characterId !== "")
      p.character_id = String(characterId);
    return p;
  }

  async function fetchSummary() {
    const res = await apiClient.get("/tag_suggestions/summary", {
      params: scopeParams(),
    });
    tags.value = res.data || [];
    // Only seed a default when nothing is chosen yet. Never override an explicit pick here:
    // a deliberately selected/scanned tag with no in-scope suggestions must stay selected
    // (its empty "all caught up for the current filters" state is fine). setScope is the
    // sole caller that reconciles, because a scope change can legitimately drop the tag.
    if (!activeTag.value && tags.value.length) {
      activeTag.value = tags.value[0].tag;
    }
  }

  async function fetchQueue() {
    if (!activeTag.value) {
      items.value = [];
      return;
    }
    loading.value = true;
    error.value = null;
    try {
      const params = {
        tag: activeTag.value,
        status: "PENDING",
        limit: PAGE_SIZE,
        ...scopeParams(),
      };
      if (direction.value) params.direction = direction.value;
      const res = await apiClient.get("/tag_suggestions", { params });
      // Group by the tagger's verdict so the reviewer handles one kind of
      // decision at a time (see BUCKET_ORDER), most-decisive first within each
      // group. Unscored pairs (no model verdict) sort to the end.
      items.value = (res.data || []).sort((a, b) => {
        const rank = bucketRank(a) - bucketRank(b);
        if (rank !== 0) return rank;
        return decision(b).confidence - decision(a).confidence;
      });
    } catch (e) {
      error.value = e?.message || "Failed to load suggestions";
      items.value = [];
    } finally {
      loading.value = false;
    }
  }

  async function selectTag(tag) {
    activeTag.value = tag;
    undoStack.value = []; // undo doesn't cross tag boundaries
    lastBulk.value = null;
    await fetchQueue();
    await refreshBulkCount();
  }

  async function fetchAllTags() {
    try {
      const res = await apiClient.get("/tags");
      allTags.value = (res.data || []).map((t) => t.tag).filter(Boolean);
    } catch {
      allTags.value = [];
    }
  }

  // Load the user's smart-score "penalised" tags (the anomaly set) so the tag picker can
  // flag them in red. The config field can be an array of strings, an array of {tag,weight}
  // objects, or a {tag: weight} map (mirrors SmartScoreSection.vue) — normalise each shape
  // to lowercased tag strings. Degrades to an empty Set on error so the picker still works.
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

  // Is this tag a flagged anomaly (smart-score penalised)? Compared lowercased.
  function isAnomalyTag(tag) {
    return anomalyTags.value.has(String(tag || "").trim().toLowerCase());
  }

  // Populate the scope-filter dropdowns. Mirrors the sidebar's fetchProjects/fetchPictureSets/
  // fetchCharacters: each call is independent and degrades to an empty list on error (e.g. a
  // read-only or wrongly-scoped token can't list one of these), so a failure on one dimension
  // never blocks the others.
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
        // Drop the auto-created per-character `reference_pictures` system sets — they aren't
        // user-facing review scopes and only clutter the Set filter.
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

  // Open the queue. An optional initialScope seeds the filters (falling back to whatever
  // scope is already set) BEFORE the first summary/queue/bulk fetch, so the overlay lands
  // pre-filtered to the view it was opened from. Clears the per-session ledgers as before.
  async function load(initialScope = null) {
    undoStack.value = [];
    lastBulk.value = null;
    tagVotes.value = {}; // fresh ledger each time the overlay opens (like undoStack)
    scope.value = { ...scope.value, ...(initialScope || {}) };
    await fetchSummary();
    await fetchQueue();
    await refreshBulkCount();
    fetchAllTags(); // background; powers the tag-picker autocomplete
    fetchAnomalyTags(); // background; flags smart-score-penalised tags red in the picker
    fetchScopeOptions(); // background; powers the project/set/character filter dropdowns
  }

  // Merge new scope filters and re-run the dependent fetches. The active tag is NEVER
  // changed here: a scope filter only narrows the queue for the tag you already picked — it
  // must not swap your tag (even to empty). The summary refreshes so the picker's pending
  // counts reflect the new scope, then the queue + bulk count reload for the same tag.
  async function setScope(next) {
    scope.value = { ...scope.value, ...(next || {}) };
    await fetchSummary();
    await fetchQueue();
    await refreshBulkCount();
  }

  // Run a near-neighbour scan for a tag (rebuilds its pending queue) and switch to it.
  // Single-flight: ignored while one is already running.
  async function scanTag(tag) {
    const t = (tag || "").trim();
    if (!t || scanning.value) return null;
    scanning.value = true;
    scanError.value = null;
    try {
      const res = await apiClient.post("/tag_suggestions/scan", { tag: t });
      activeTag.value = t;
      undoStack.value = [];
      lastBulk.value = null;
      await fetchSummary();
      await fetchQueue();
      await refreshBulkCount();
      return res.data;
    } catch (e) {
      scanError.value =
        e?.response?.data?.detail || e?.message || "Scan failed";
      return null;
    } finally {
      scanning.value = false;
    }
  }

  // The tag picker's single entry point: picking a tag always re-runs the near-neighbour
  // scan so the queue reflects current data and the current twin-selection logic, rather
  // than a stale cached snapshot from a previous scan. Re-scanning is cheap (synchronous,
  // fast on a typical vault) and keeps already-reviewed rows — it only rebuilds the PENDING
  // queue — so switching tags never loses review progress. scanTag is single-flight, so a
  // pick made while a scan is already running is ignored rather than queued.
  async function selectOrScan(tag) {
    const t = (tag || "").trim();
    if (!t) return;
    await scanTag(t);
  }

  // Re-run the near-neighbour scan for the already-selected tag (rebuild its queue after the
  // underlying data changed). A thin wrapper so the overlay's "rescan" button has a verb.
  async function rescanActiveTag() {
    if (!activeTag.value) return;
    await scanTag(activeTag.value);
  }

  // Resolve the head of the queue: accept applies the fix, dismiss keeps the label.
  // `corner` (the four-corner judgement that triggered this) is optional: when given it
  // is recorded in the session vote ledger and stashed on the undo entry so undo / a
  // failed write can retract it.
  async function resolveCurrent(action, corner = null) {
    const item = current.value;
    if (!item) return;
    // Optimistic: drop it from the queue immediately so review never stalls.
    items.value = items.value.slice(1);
    if (corner) recordDecisionVotes(item, corner);
    try {
      await apiClient.post(`/tag_suggestions/${item.id}/${action}`);
      if (action === "accept") {
        if (item.direction === "remove") removedCount.value += 1;
        else addedCount.value += 1;
      } else {
        keptCount.value += 1;
      }
      // Keep the per-tag counts honest for the picker/badge.
      const entry = tags.value.find((t) => t.tag === item.tag);
      if (entry) {
        if (item.direction === "remove" && entry.remove > 0) entry.remove -= 1;
        if (item.direction === "add" && entry.add > 0) entry.add -= 1;
        if (entry.total > 0) entry.total -= 1;
      }
      undoStack.value.push({ item, action, corner });
      // Refill when the local page runs dry but the tag still has pending items.
      if (!items.value.length && pendingForActiveTag() > 0) {
        await fetchQueue();
      }
    } catch (e) {
      // Put it back at the head and surface the error so nothing is silently lost.
      // Retract the optimistic vote so a failed write leaves no phantom tally.
      if (corner) retractDecisionVotes(item, corner);
      items.value = [item, ...items.value];
      error.value = e?.message || "Failed to save your decision";
    }
  }

  function accept(corner = null) {
    return resolveCurrent("accept", corner);
  }

  function dismiss(corner = null) {
    return resolveCurrent("dismiss", corner);
  }

  // Resolve in the twin's favour: keep the suspect, flip the twin to match it.
  // (remove → tag the untagged twin; add → untag the tagged twin.)
  async function fixTwin(corner = null) {
    const item = current.value;
    if (!item || !item.twin_picture_id) return;
    items.value = items.value.slice(1);
    if (corner) recordDecisionVotes(item, corner);
    try {
      await apiClient.post(`/tag_suggestions/${item.id}/fix-twin`);
      // A label changed on the twin: remove-suggestion adds a tag, add removes one.
      if (item.direction === "remove") addedCount.value += 1;
      else removedCount.value += 1;
      const entry = tags.value.find((t) => t.tag === item.tag);
      if (entry) {
        if (item.direction === "remove" && entry.remove > 0) entry.remove -= 1;
        if (item.direction === "add" && entry.add > 0) entry.add -= 1;
        if (entry.total > 0) entry.total -= 1;
      }
      undoStack.value.push({ item, action: "fix_twin", corner });
      if (!items.value.length && pendingForActiveTag() > 0) {
        await fetchQueue();
      }
    } catch (e) {
      if (corner) retractDecisionVotes(item, corner);
      items.value = [item, ...items.value];
      error.value = e?.message || "Failed to fix the twin";
    }
  }

  // Both labels were wrong, opposite ways: the flagged (left) image is actually clean
  // and the untagged twin (right) has the tag. Clear the left, tag the right.
  async function swap(corner = null) {
    const item = current.value;
    if (!item || !item.twin_picture_id) return;
    items.value = items.value.slice(1);
    if (corner) recordDecisionVotes(item, corner);
    try {
      await apiClient.post(`/tag_suggestions/${item.id}/swap`);
      removedCount.value += 1; // the flagged (left) image was cleared
      addedCount.value += 1; // the twin (right) got the tag
      const entry = tags.value.find((t) => t.tag === item.tag);
      if (entry) {
        if (item.direction === "remove" && entry.remove > 0) entry.remove -= 1;
        if (item.direction === "add" && entry.add > 0) entry.add -= 1;
        if (entry.total > 0) entry.total -= 1;
      }
      undoStack.value.push({ item, action: "swap", corner });
      if (!items.value.length && pendingForActiveTag() > 0) {
        await fetchQueue();
      }
    } catch (e) {
      if (corner) retractDecisionVotes(item, corner);
      items.value = [item, ...items.value];
      error.value = e?.message || "Failed to swap";
    }
  }

  // Undo the most recent accept/dismiss: reverse the label change on the server and
  // put the item back at the head of the queue. (Skips aren't decisions, so not tracked.)
  async function undo() {
    const last = undoStack.value[undoStack.value.length - 1];
    if (!last) return;
    try {
      await apiClient.post(`/tag_suggestions/${last.item.id}/reopen`);
    } catch (e) {
      error.value = e?.message || "Failed to undo";
      return;
    }
    undoStack.value.pop();
    // Reverse the session vote this decision recorded (entries from before this feature,
    // or bulk/skip, carry no corner — guard for that).
    if (last.corner) retractDecisionVotes(last.item, last.corner);
    if (last.action === "accept") {
      if (last.item.direction === "remove")
        removedCount.value = Math.max(0, removedCount.value - 1);
      else addedCount.value = Math.max(0, addedCount.value - 1);
    } else if (last.action === "fix_twin") {
      if (last.item.direction === "remove")
        addedCount.value = Math.max(0, addedCount.value - 1);
      else removedCount.value = Math.max(0, removedCount.value - 1);
    } else if (last.action === "swap") {
      removedCount.value = Math.max(0, removedCount.value - 1);
      addedCount.value = Math.max(0, addedCount.value - 1);
    } else {
      keptCount.value = Math.max(0, keptCount.value - 1);
    }
    const entry = tags.value.find((t) => t.tag === last.item.tag);
    if (entry) {
      if (last.item.direction === "remove") entry.remove += 1;
      if (last.item.direction === "add") entry.add += 1;
      entry.total += 1;
    }
    items.value = [last.item, ...items.value];
  }

  // --- Bulk "resolve the confident ones" -------------------------------------
  function bulkParams(extra) {
    const p = {
      tag: activeTag.value,
      min_combined: bulkThreshold.value,
      ...scopeParams(),
      ...extra,
    };
    if (direction.value) p.direction = direction.value;
    return p;
  }

  async function refreshBulkCount() {
    if (!activeTag.value) {
      bulkCount.value = 0;
      bulkSample.value = [];
      return;
    }
    try {
      const res = await apiClient.post(
        "/tag_suggestions/bulk-accept",
        bulkParams({ dry_run: true }),
      );
      bulkCount.value = res.data?.count ?? 0;
      bulkSample.value = res.data?.sample ?? [];
    } catch {
      bulkCount.value = 0;
      bulkSample.value = [];
    }
  }

  async function setBulkThreshold(v) {
    bulkThreshold.value = v;
    await refreshBulkCount();
  }

  async function runBulk() {
    if (!activeTag.value || bulkBusy.value) return;
    bulkBusy.value = true;
    try {
      const res = await apiClient.post("/tag_suggestions/bulk-accept", bulkParams({}));
      lastBulk.value = {
        ids: res.data?.accepted_ids ?? [],
        count: res.data?.count ?? 0,
      };
      previewOpen.value = false;
      undoStack.value = []; // the batch is undone via undoBulk, not the per-item stack
      await fetchSummary();
      await fetchQueue();
      await refreshBulkCount();
    } catch (e) {
      error.value = e?.message || "Bulk resolve failed";
    } finally {
      bulkBusy.value = false;
    }
  }

  async function undoBulk() {
    const last = lastBulk.value;
    if (!last || !last.ids.length || bulkBusy.value) return;
    bulkBusy.value = true;
    try {
      await apiClient.post("/tag_suggestions/bulk-reopen", { ids: last.ids });
      lastBulk.value = null;
      await fetchSummary();
      await fetchQueue();
      await refreshBulkCount();
    } catch (e) {
      error.value = e?.message || "Undo failed";
    } finally {
      bulkBusy.value = false;
    }
  }

  function reset() {
    items.value = [];
    error.value = null;
    undoStack.value = [];
    lastBulk.value = null;
    tagVotes.value = {};
    scope.value = { projectId: null, setId: null, characterId: null };
    removedCount.value = 0;
    addedCount.value = 0;
    keptCount.value = 0;
  }

  return {
    overlayOpen,
    tags,
    activeTag,
    direction,
    items,
    loading,
    error,
    current,
    loadedCount,
    remainingTotal,
    reviewedCount,
    removedCount,
    addedCount,
    keptCount,
    canUndo,
    decision,
    bulkThreshold,
    bulkCount,
    bulkBusy,
    lastBulk,
    bulkSample,
    previewOpen,
    scanning,
    scanError,
    scanTag,
    selectOrScan,
    rescanActiveTag,
    allTags,
    anomalyTags,
    fetchAnomalyTags,
    isAnomalyTag,
    scope,
    projects,
    sets,
    characters,
    scopeParams,
    fetchScopeOptions,
    setScope,
    tagVotes,
    votesForPicture,
    decisionConflict,
    recordDecisionVotes,
    retractDecisionVotes,
    fetchSummary,
    fetchQueue,
    selectTag,
    load,
    accept,
    dismiss,
    fixTwin,
    swap,
    undo,
    setBulkThreshold,
    runBulk,
    undoBulk,
    reset,
  };
});
