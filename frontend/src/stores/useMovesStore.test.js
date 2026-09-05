// useMovesStore - the moves-made-outside-PixlStash reconciliation queue
// (v1.11 Phase 5). What the store owns: holding the last classification,
// re-fetching after every apply/dismiss (the backend recomputes fresh, this
// store never corrects a verdict itself), and the actionable-vs-informational
// split (`hasPending` excludes off_layout, which carries no decision).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/moves", () => ({
  getPendingMoves: vi.fn(),
  applyMoves: vi.fn(),
  dismissMoves: vi.fn(),
}));

import { getPendingMoves, applyMoves, dismissMoves } from "../api/moves";
import { useMovesStore } from "./useMovesStore";
import { notifySessionReset } from "../utils/apiClient";

function summary({ unambiguous = [], ambiguous = [], off_layout = [] } = {}) {
  return { unambiguous, ambiguous, off_layout };
}

beforeEach(() => {
  setActivePinia(createPinia());
  getPendingMoves.mockReset();
  applyMoves.mockReset();
  dismissMoves.mockReset();
});

describe("useMovesStore - fetching", () => {
  it("holds the three buckets from the last fetch", async () => {
    getPendingMoves.mockResolvedValue(
      summary({
        unambiguous: [{ review_id: 1, picture_id: 11 }],
        ambiguous: [{ review_id: 2, picture_id: 12 }],
        off_layout: [{ review_id: 3, picture_id: 13 }],
      }),
    );
    const store = useMovesStore();
    await store.fetchPending();

    expect(store.unambiguous).toHaveLength(1);
    expect(store.ambiguous).toHaveLength(1);
    expect(store.offLayout).toHaveLength(1);
    expect(store.loaded).toBe(true);
    expect(store.error).toBeNull();
  });

  it("off_layout does not count toward hasPending - nothing there needs a decision", async () => {
    getPendingMoves.mockResolvedValue(
      summary({ off_layout: [{ review_id: 3, picture_id: 13 }] }),
    );
    const store = useMovesStore();
    await store.fetchPending();

    expect(store.hasPending).toBe(false);
    expect(store.pendingCount).toBe(0);
    // But it must still be REACHABLE - hasAnyPending is what the sidebar row
    // gates, or an off_layout-only backlog would be invisible until its
    // backend retention window quietly expired it unseen.
    expect(store.hasAnyPending).toBe(true);
  });

  it("unambiguous and ambiguous both count toward hasPending", async () => {
    getPendingMoves.mockResolvedValue(
      summary({
        unambiguous: [{ review_id: 1 }],
        ambiguous: [{ review_id: 2 }],
      }),
    );
    const store = useMovesStore();
    await store.fetchPending();

    expect(store.hasPending).toBe(true);
    expect(store.pendingCount).toBe(2);
  });

  it("records a failure without clobbering the previous list", async () => {
    getPendingMoves
      .mockResolvedValueOnce(summary({ unambiguous: [{ review_id: 1 }] }))
      .mockRejectedValueOnce(new Error("network down"));
    const store = useMovesStore();
    await store.fetchPending();
    await store.fetchPending();

    expect(store.error).toBe("network down");
    // The last GOOD list survives a failed re-fetch - a transient error must
    // not empty a queue that was genuinely there a second ago.
    expect(store.unambiguous).toHaveLength(1);
  });
});

describe("useMovesStore - applying and dismissing", () => {
  it("applies every unambiguous review_id and refreshes", async () => {
    getPendingMoves
      .mockResolvedValueOnce(
        summary({ unambiguous: [{ review_id: 1 }, { review_id: 2 }] }),
      )
      .mockResolvedValueOnce(summary());
    applyMoves.mockResolvedValue({ applied_picture_ids: [11, 12] });
    const store = useMovesStore();
    await store.fetchPending();

    const result = await store.applyAllUnambiguous();

    expect(applyMoves).toHaveBeenCalledWith([1, 2]);
    expect(result.applied_picture_ids).toEqual([11, 12]);
    // Refetched, so a picture reconciled by someone else in the meantime is
    // read fresh rather than assumed gone.
    expect(getPendingMoves).toHaveBeenCalledTimes(2);
    expect(store.unambiguous).toEqual([]);
  });

  it("resolves one ambiguous row by its review_id - 'Only X now'", async () => {
    getPendingMoves
      .mockResolvedValueOnce(summary({ ambiguous: [{ review_id: 7 }] }))
      .mockResolvedValueOnce(summary());
    applyMoves.mockResolvedValue({ applied_picture_ids: [70] });
    const store = useMovesStore();
    await store.fetchPending();

    await store.applyReview(7);

    expect(applyMoves).toHaveBeenCalledWith([7]);
  });

  it("dismisses without ever calling apply - 'Keep both'", async () => {
    getPendingMoves
      .mockResolvedValueOnce(summary({ ambiguous: [{ review_id: 7 }] }))
      .mockResolvedValueOnce(summary());
    dismissMoves.mockResolvedValue({ dismissed_review_ids: [7] });
    const store = useMovesStore();
    await store.fetchPending();

    await store.dismissReviews(7);

    expect(dismissMoves).toHaveBeenCalledWith([7]);
    expect(applyMoves).not.toHaveBeenCalled();
  });

  it("dismissAll clears every bucket, off_layout included", async () => {
    getPendingMoves
      .mockResolvedValueOnce(
        summary({
          unambiguous: [{ review_id: 1 }],
          ambiguous: [{ review_id: 2 }],
          off_layout: [{ review_id: 3 }],
        }),
      )
      .mockResolvedValueOnce(summary());
    dismissMoves.mockResolvedValue({ dismissed_review_ids: [1, 2, 3] });
    const store = useMovesStore();
    await store.fetchPending();

    await store.dismissAll();

    expect(dismissMoves).toHaveBeenCalledWith([1, 2, 3]);
  });

  it("applyAllUnambiguous is a no-op when there is nothing unambiguous", async () => {
    const store = useMovesStore();
    const result = await store.applyAllUnambiguous();

    expect(applyMoves).not.toHaveBeenCalled();
    expect(result.applied_picture_ids).toEqual([]);
  });
});

describe("useMovesStore - session reset (issue #655)", () => {
  it("drops every row on a credential change", async () => {
    getPendingMoves.mockResolvedValue(
      summary({ unambiguous: [{ review_id: 1 }] }),
    );
    const store = useMovesStore();
    await store.fetchPending();
    expect(store.hasPending).toBe(true);

    notifySessionReset("test");

    expect(store.unambiguous).toEqual([]);
    expect(store.loaded).toBe(false);
    expect(store.hasPending).toBe(false);
  });
});
