// useReviewFixesStore.js — state for the "Review suggested fixes" queue.
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

export const useReviewFixesStore = defineStore("reviewFixes", () => {
  const overlayOpen = ref(false); // whether the review overlay is showing
  const tags = ref([]); // [{ tag, add, remove, total }], busiest first
  const activeTag = ref(null);
  const direction = ref("remove"); // "remove" | "add" | "" (both)
  const items = ref([]); // ranked pending suggestions for activeTag/direction
  const loading = ref(false);
  const error = ref(null);
  const undoStack = ref([]); // [{ item, action }] — last resolved first off the end
  const bulkThreshold = ref(0.9); // min blended score for "resolve the confident ones"
  const bulkCount = ref(0); // how many PENDING clear the threshold (live preview)
  const bulkBusy = ref(false);
  const lastBulk = ref(null); // { ids, count } of the most recent bulk-accept, for undo
  const bulkSample = ref([]); // a few least-confident would-be resolutions, for preview
  const previewOpen = ref(false);

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

  async function fetchSummary() {
    const res = await apiClient.get("/tag_suggestions/summary");
    tags.value = res.data || [];
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
      };
      if (direction.value) params.direction = direction.value;
      const res = await apiClient.get("/tag_suggestions", { params });
      // Most decisive (highest agreement either way) first.
      items.value = (res.data || []).sort(
        (a, b) => decision(b).confidence - decision(a).confidence,
      );
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

  async function setDirection(dir) {
    direction.value = dir;
    undoStack.value = [];
    lastBulk.value = null;
    await fetchQueue();
    await refreshBulkCount();
  }

  async function load() {
    undoStack.value = [];
    lastBulk.value = null;
    await fetchSummary();
    await fetchQueue();
    await refreshBulkCount();
  }

  // Resolve the head of the queue: accept applies the fix, dismiss keeps the label.
  async function resolveCurrent(action) {
    const item = current.value;
    if (!item) return;
    // Optimistic: drop it from the queue immediately so review never stalls.
    items.value = items.value.slice(1);
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
      undoStack.value.push({ item, action });
      // Refill when the local page runs dry but the tag still has pending items.
      if (!items.value.length && pendingForActiveTag() > 0) {
        await fetchQueue();
      }
    } catch (e) {
      // Put it back at the head and surface the error so nothing is silently lost.
      items.value = [item, ...items.value];
      error.value = e?.message || "Failed to save your decision";
    }
  }

  function accept() {
    return resolveCurrent("accept");
  }

  function dismiss() {
    return resolveCurrent("dismiss");
  }

  // Resolve in the twin's favour: keep the suspect, flip the twin to match it.
  // (remove → tag the untagged twin; add → untag the tagged twin.)
  async function fixTwin() {
    const item = current.value;
    if (!item || !item.twin_picture_id) return;
    items.value = items.value.slice(1);
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
      undoStack.value.push({ item, action: "fix_twin" });
      if (!items.value.length && pendingForActiveTag() > 0) {
        await fetchQueue();
      }
    } catch (e) {
      items.value = [item, ...items.value];
      error.value = e?.message || "Failed to fix the twin";
    }
  }

  // Both labels were wrong, opposite ways: the flagged (left) image is actually clean
  // and the untagged twin (right) has the tag. Clear the left, tag the right.
  async function swap() {
    const item = current.value;
    if (!item || !item.twin_picture_id) return;
    items.value = items.value.slice(1);
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
      undoStack.value.push({ item, action: "swap" });
      if (!items.value.length && pendingForActiveTag() > 0) {
        await fetchQueue();
      }
    } catch (e) {
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
    const p = { tag: activeTag.value, min_combined: bulkThreshold.value, ...extra };
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
    fetchSummary,
    fetchQueue,
    selectTag,
    setDirection,
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
