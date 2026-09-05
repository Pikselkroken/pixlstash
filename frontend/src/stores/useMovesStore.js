// useMovesStore.js - the moves-made-outside-PixlStash reconciliation queue.
//
// v1.11 Phase 5. Three buckets, reclassified live by the backend on every
// GET - this store never invents or corrects a verdict itself, only holds
// what the last GET said and re-fetches on the WebSocket's "look again" nudge
// (EXTERNAL_MOVES_PENDING) or after an apply/dismiss.
//
// `off_layout` carries no decision (nothing was ever ambiguous, nothing is
// ever applied to it), so it does not count toward `hasPending`: a sidebar
// row and a badge exist to say "something needs a decision", and an
// off-layout-only queue has none. The screen still shows it for transparency
// once opened.

import { defineStore } from "pinia";
import { ref, computed, onScopeDispose } from "vue";
import * as movesApi from "../api/moves";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";

export const useMovesStore = defineStore("moves", () => {
  const unambiguous = ref([]);
  const ambiguous = ref([]);
  const offLayout = ref([]);
  const loading = ref(false);
  const error = ref(null);
  // Set once a fetch has completed, so the sidebar row can stay hidden until
  // the first real answer arrives rather than flashing empty-then-populated.
  const loaded = ref(false);

  let epoch = 0;

  const hasPending = computed(
    () => unambiguous.value.length + ambiguous.value.length > 0,
  );
  const pendingCount = computed(
    () => unambiguous.value.length + ambiguous.value.length,
  );
  // Distinct from hasPending: an off_layout-only queue has nothing to DECIDE
  // (no badge, no red dot - hasPending stays false for it), but it still has
  // to be REACHABLE, or the backend's retention window quietly expires it
  // with nobody ever having seen it. This is what gates the sidebar row's
  // and the screen's own dismiss-all action's visibility.
  const hasAnyPending = computed(
    () =>
      unambiguous.value.length + ambiguous.value.length + offLayout.value.length >
      0,
  );

  async function fetchPending() {
    loading.value = true;
    error.value = null;
    const requestEpoch = epoch;
    try {
      const summary = await movesApi.getPendingMoves();
      if (requestEpoch !== epoch) return;
      unambiguous.value = summary?.unambiguous ?? [];
      ambiguous.value = summary?.ambiguous ?? [];
      offLayout.value = summary?.off_layout ?? [];
      loaded.value = true;
    } catch (err) {
      if (requestEpoch !== epoch) return;
      error.value =
        errorDetail(err) || err?.message || "Failed to load pending moves.";
    } finally {
      if (requestEpoch === epoch) loading.value = false;
    }
  }

  /** Apply every currently-unambiguous move in one undoable batch. */
  async function applyAllUnambiguous() {
    const ids = unambiguous.value.map((item) => item.review_id);
    if (!ids.length) return { applied_picture_ids: [] };
    const result = await movesApi.applyMoves(ids);
    await fetchPending();
    return result;
  }

  /** Resolve one row - an ambiguous "Only X now", or a re-apply of any id. */
  async function applyReview(reviewId) {
    const result = await movesApi.applyMoves([reviewId]);
    await fetchPending();
    return result;
  }

  /** "Keep both" on one row, or "Leave everything as it was" on several. */
  async function dismissReviews(reviewIds) {
    const ids = Array.isArray(reviewIds) ? reviewIds : [reviewIds];
    if (!ids.length) return { dismissed_review_ids: [] };
    const result = await movesApi.dismissMoves(ids);
    await fetchPending();
    return result;
  }

  /** Dismiss the whole queue - "Leave everything as it was". */
  async function dismissAll() {
    const ids = [
      ...unambiguous.value.map((item) => item.review_id),
      ...ambiguous.value.map((item) => item.review_id),
      ...offLayout.value.map((item) => item.review_id),
    ];
    return dismissReviews(ids);
  }

  function reset() {
    epoch += 1;
    unambiguous.value = [];
    ambiguous.value = [];
    offLayout.value = [];
    loading.value = false;
    error.value = null;
    loaded.value = false;
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    unambiguous,
    ambiguous,
    offLayout,
    loading,
    error,
    loaded,
    hasPending,
    hasAnyPending,
    pendingCount,
    fetchPending,
    applyAllUnambiguous,
    applyReview,
    dismissReviews,
    dismissAll,
    reset,
  };
});
