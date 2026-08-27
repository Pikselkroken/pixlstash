import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

// The store imports a singleton apiClient; mock the module so no real HTTP
// happens and we can assert which per-item endpoints a decision hits.
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  isReadOnly: { value: false },
}));

import { apiClient } from "../utils/apiClient";
import {
  useReviewSessionsStore,
  binaryAction,
  binaryDelta,
  pairAction,
  pairDelta,
  sortQueue,
  STICKER_ICONS,
  AWARD_GAP_MIN,
  AWARD_GAP_MAX,
} from "./useReviewSessionsStore";
import { SET_ICONS, SET_COLORS } from "../utils/setAppearance";

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.delete.mockReset();
  apiClient.get.mockResolvedValue({ data: [] });
  apiClient.post.mockResolvedValue({ data: {} });
  apiClient.delete.mockResolvedValue({ data: {} });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

// --- Decision mapping (verified against the OLD overlay's dispatchDecision) ---

describe("binary decision mapping", () => {
  it("remove + Yes keeps the tag (dismiss)", () => {
    expect(binaryAction({ direction: "remove" }, "yes")).toBe("dismiss");
    expect(binaryDelta({ direction: "remove" }, "yes")).toEqual({ kept: 1 });
  });

  it("remove + No clears the wrong tag (accept)", () => {
    expect(binaryAction({ direction: "remove" }, "no")).toBe("accept");
    expect(binaryDelta({ direction: "remove" }, "no")).toEqual({ removed: 1 });
  });

  it("add + Yes applies the missing tag (accept)", () => {
    expect(binaryAction({ direction: "add" }, "yes")).toBe("accept");
    expect(binaryDelta({ direction: "add" }, "yes")).toEqual({ added: 1 });
  });

  it("add + No leaves it untagged (dismiss)", () => {
    expect(binaryAction({ direction: "add" }, "no")).toBe("dismiss");
    expect(binaryDelta({ direction: "add" }, "no")).toEqual({ kept: 1 });
  });
});

describe("pair decision mapping", () => {
  it("left-only keeps the labels as they are (dismiss), either direction", () => {
    expect(pairAction({ direction: "remove" }, "left")).toBe("dismiss");
    expect(pairAction({ direction: "add" }, "left")).toBe("dismiss");
    expect(pairDelta({}, "left")).toEqual({ kept: 1 });
  });

  it("right-only moves the tag (swap), either direction", () => {
    expect(pairAction({ direction: "remove" }, "right")).toBe("swap");
    expect(pairAction({ direction: "add" }, "right")).toBe("swap");
    expect(pairDelta({}, "right")).toEqual({ removed: 1, added: 1 });
  });

  it("both tags the untagged side: fix-twin on remove, accept on add", () => {
    expect(pairAction({ direction: "remove" }, "both")).toBe("fix-twin");
    expect(pairAction({ direction: "add" }, "both")).toBe("accept");
    expect(pairDelta({}, "both")).toEqual({ added: 1 });
  });

  it("neither clears the tagged side: accept on remove, fix-twin on add", () => {
    expect(pairAction({ direction: "remove" }, "neither")).toBe("accept");
    expect(pairAction({ direction: "add" }, "neither")).toBe("fix-twin");
    expect(pairDelta({}, "neither")).toEqual({ removed: 1 });
  });
});

describe("queue ordering", () => {
  it("sorts pair cards first, then remove-direction, then add-direction", () => {
    const items = [
      { id: 1, kind: "binary", direction: "add", score: 0.9 },
      { id: 2, kind: "binary", direction: "remove", score: 0.5 },
      { id: 3, kind: "pair", direction: "remove", score: 0.1 },
      { id: 4, kind: "binary", direction: "remove", score: 0.8 },
    ];
    expect(sortQueue(items).map((i) => i.id)).toEqual([3, 4, 2, 1]);
  });
});

// --- Decisions write through the existing per-item endpoints -------------------

// `progress` mirrors the server's four-bucket shape from `_progress_map`
// (done / pending / skipped / locked). `progress` overrides let a test seed a
// session that already has frozen suspects or earlier skips.
function seedSession(store, item, progress = {}) {
  store.sessions = [
    {
      id: 1,
      tag: item.tag,
      stats: { scanned: 100, found: 2, prev_reviewed: 0 },
      progress: { done: 0, pending: 2, skipped: 0, locked: 0, ...progress },
      stale: false,
    },
  ];
  store.view = { type: "session", id: 1 };
  store.queues = { 1: { items: [item], loading: false, error: null } };
}

describe("resolveCurrent (via answerBinary)", () => {
  const item = {
    id: 77,
    picture_id: 900,
    tag: "cat",
    direction: "remove",
    kind: "binary",
  };

  it("POSTs the mapped action, pops the head, and records the tally + undo", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);

    await store.answerBinary("no");

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/77/accept");
    expect(store.queues[1].items).toHaveLength(0);
    expect(store.tallies[1]).toEqual({ removed: 1, added: 0, kept: 0 });
    expect(store.undoStacks[1]).toHaveLength(1);
    expect(store.sessions[0].progress).toEqual({
      done: 1,
      pending: 1,
      skipped: 0,
      locked: 0,
    });
  });

  it("rolls back the head, tally, and progress when the write fails", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.post.mockRejectedValueOnce(new Error("boom"));

    await store.answerBinary("no");

    expect(store.queues[1].items[0]).toEqual(item);
    expect(store.tallies[1]).toEqual({ removed: 0, added: 0, kept: 0 });
    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 0,
      locked: 0,
    });
    expect(store.error).toBeTruthy();
  });
});

// --- bumpProgress preserves the buckets no decision owns ----------------------
//
// REGRESSION GUARD: bumpProgress used to rebuild `progress` as a fresh
// {done, pending} object, dropping `locked` and `skipped`. ReviewSessionView
// renders its "N suspects frozen by a locked set" badge from
// `session.progress.locked`, so the badge silently vanished on the reviewer's
// first decision - the count dropped with no explanation, which is precisely
// what the locked bucket was added to explain.

describe("bumpProgress bucket preservation", () => {
  const item = {
    id: 77,
    picture_id: 900,
    tag: "cat",
    direction: "remove",
    kind: "binary",
  };

  it("keeps locked and skipped intact through a decision", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });

    await store.answerBinary("no");

    expect(store.sessions[0].progress).toEqual({
      done: 1,
      pending: 1,
      skipped: 4, // a decision never touches the skipped bucket
      locked: 3, // nor the frozen-suspect bucket
    });
  });

  it("keeps locked intact when a decision write fails and rolls back", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });
    apiClient.post.mockRejectedValueOnce(new Error("boom"));

    await store.answerBinary("no");

    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 4,
      locked: 3,
    });
  });

  it("moves pending into skipped on a skip, leaving locked alone", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });

    await store.skip();

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/77/skip");
    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 1,
      skipped: 5,
      locked: 3,
    });
  });

  it("rolls a failed skip back without disturbing locked", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });
    apiClient.post.mockRejectedValueOnce(new Error("boom"));

    await store.skip();

    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 4,
      locked: 3,
    });
    expect(store.error).toBeTruthy();
  });

  it("undoes a decision back to the seeded buckets", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });

    await store.answerBinary("no");
    await store.undo();

    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 4,
      locked: 3,
    });
  });

  it("undoes a skip back to the seeded buckets", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });

    await store.skip();
    await store.undo();

    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 4,
      locked: 3,
    });
  });

  it("returns skipped cards to pending on reopenSkipped, locked untouched", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });

    await store.skip();
    await store.reopenSkipped(1);

    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 4,
      locked: 3,
    });
    expect(store.queues[1].items).toHaveLength(1);
  });

  it("drops an already-gone (404) skip from skipped without crediting pending", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 3, skipped: 4 });

    await store.skip(); // skipped 4 → 5, pending 2 → 1
    const gone = new Error("gone");
    gone.response = { status: 404 };
    apiClient.post.mockRejectedValueOnce(gone);
    await store.reopenSkipped(1);

    // The row no longer exists server-side: it leaves `skipped` but never
    // rejoins `pending`.
    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 1,
      skipped: 4,
      locked: 3,
    });
    expect(store.queues[1].items).toHaveLength(0);
  });

  it("preserves an unknown future bucket the server may add", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item, { locked: 2, deferred: 9 });

    await store.answerBinary("no");

    expect(store.sessions[0].progress.deferred).toBe(9);
    expect(store.sessions[0].progress.locked).toBe(2);
  });
});

// --- Sticker vocabulary comes from setAppearance.js (hard requirement) --------

describe("sticker vocabulary", () => {
  it("derives every sticker icon from the Picture Set palette", () => {
    expect(STICKER_ICONS.map((s) => s.icon)).toEqual(
      SET_ICONS.map((ic) => ic.value),
    );
    expect(STICKER_ICONS.length).toBeGreaterThan(10);
    expect(SET_COLORS.length).toBeGreaterThan(10);
  });
});

// --- Award scheduling (variable-ratio) -----------------------------------------

describe("award scheduling", () => {
  it("always awards on the first decision after enabling", () => {
    const store = useReviewSessionsStore();
    store.setGamify(true);
    const sticker = store.noteDecision("cat");
    expect(sticker).not.toBeNull();
    expect(store.activeAward).toEqual(sticker);
  });

  it("never awards while gamify is off, but the net counter still moves", () => {
    const store = useReviewSessionsStore();
    expect(store.noteDecision("cat")).toBeNull();
    expect(store.decisionsCount).toBe(1);
    expect(store.decisionTick).toBe(0);
  });

  it("undo decrements the net XP counter but never re-fires a celebration", async () => {
    const store = useReviewSessionsStore();
    store.setGamify(true);
    seedSession(store, {
      id: 5,
      picture_id: 1,
      tag: "cat",
      direction: "remove",
      kind: "binary",
    });
    await store.answerBinary("no");
    expect(store.decisionsCount).toBe(1);
    const tickAfterDecision = store.decisionTick;

    await store.undo();
    expect(store.decisionsCount).toBe(0); // net: undo walks it back
    expect(store.decisionTick).toBe(tickAfterDecision); // no celebration on undo
  });

  it("then awards again after AWARD_GAP_MIN..MAX decisions (variable ratio)", () => {
    const store = useReviewSessionsStore();
    // Deterministic randomness: next gap = AWARD_GAP_MIN + floor(0 * range).
    vi.spyOn(Math, "random").mockReturnValue(0);
    store.setGamify(true);
    expect(store.noteDecision("cat")).not.toBeNull(); // first always awards
    for (let i = 1; i < AWARD_GAP_MIN; i += 1) {
      expect(store.noteDecision("cat")).toBeNull(); // i of AWARD_GAP_MIN
    }
    expect(store.noteDecision("cat")).not.toBeNull(); // gap reached → award
  });

  it("caps the gap at AWARD_GAP_MAX for any random value", () => {
    const store = useReviewSessionsStore();
    vi.spyOn(Math, "random").mockReturnValue(0.999999);
    store.setGamify(true);
    store.noteDecision("cat"); // first award; re-arms with the max gap
    let gap = 0;
    let sticker = null;
    while (sticker === null && gap < AWARD_GAP_MAX * 2) {
      sticker = store.noteDecision("cat");
      gap += 1;
    }
    expect(gap).toBe(AWARD_GAP_MAX);
  });

  it("never hands out the same sticker icon twice in a row", () => {
    const store = useReviewSessionsStore();
    // random 0 → minimum gap, icon idx 0 every time; the schedule must bump a
    // repeat to a different icon.
    vi.spyOn(Math, "random").mockReturnValue(0);
    store.setGamify(true);
    const first = store.noteDecision("cat");
    let second = null;
    for (let i = 0; second === null && i < AWARD_GAP_MAX * 2; i += 1) {
      second = store.noteDecision("cat");
    }
    expect(first.icon).toBe(STICKER_ICONS[0].icon);
    expect(second.icon).toBe(STICKER_ICONS[7].icon);
    expect(second.icon).not.toBe(first.icon);
  });

  it("lands the award in the shelf after the fly animation, and undo never removes it", async () => {
    vi.useFakeTimers();
    const store = useReviewSessionsStore();
    store.setGamify(true);
    const sticker = store.noteDecision("cat");
    expect(store.stickers).toHaveLength(0);
    vi.advanceTimersByTime(1500);
    expect(store.stickers).toHaveLength(1);
    expect(store.stickers[0].id).toBe(sticker.id);
    expect(store.activeAward).toBeNull();

    // Undo is a store no-op for stickers: nothing in undo() touches them.
    seedSession(store, {
      id: 5,
      picture_id: 1,
      tag: "cat",
      direction: "remove",
      kind: "binary",
    });
    store.undoStacks = {
      1: [{ item: { id: 5, tag: "cat" }, action: "accept", delta: { removed: 1 } }],
    };
    await store.undo();
    expect(store.stickers).toHaveLength(1);
  });

  it("clearStickers empties the shelf, its persisted copy, and an award mid-fly", () => {
    vi.useFakeTimers();
    const store = useReviewSessionsStore();
    store.setGamify(true);
    // One landed sticker...
    store.noteDecision("cat");
    vi.advanceTimersByTime(1500);
    expect(store.stickers).toHaveLength(1);
    // ...and one mid-fly (commit still pending).
    store.commitAward({ id: "landed-2", icon: "mdi-star", label: "Star" });
    expect(store.stickers).toHaveLength(2);
    store.activeAward = { id: "flying", icon: "mdi-star", label: "Star" };

    store.clearStickers();
    expect(store.stickers).toHaveLength(0);
    expect(store.activeAward).toBeNull();
    expect(
      JSON.parse(window.localStorage.getItem("pixlstash:reviewStickers")),
    ).toEqual([]);
    // The cancelled fly must not land a sticker after the clear.
    vi.advanceTimersByTime(5000);
    expect(store.stickers).toHaveLength(0);
  });

  it("re-enabling resets the schedule so the next decision awards again", () => {
    const store = useReviewSessionsStore();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    store.setGamify(true);
    store.noteDecision("cat"); // awards, re-arms
    store.setGamify(false);
    store.setGamify(true);
    expect(store.noteDecision("cat")).not.toBeNull();
  });

  it("skip does not advance the award counter", async () => {
    const store = useReviewSessionsStore();
    vi.spyOn(Math, "random").mockReturnValue(0);
    store.setGamify(true);
    store.noteDecision("cat"); // award; gap re-armed to AWARD_GAP_MIN
    seedSession(store, {
      id: 9,
      picture_id: 2,
      tag: "cat",
      direction: "remove",
      kind: "binary",
    });
    await store.skip(); // leaves the queue undecided; NOT an award step
    // Had skip advanced the counter, the award would land one decision early.
    for (let i = 1; i < AWARD_GAP_MIN; i += 1) {
      expect(store.noteDecision("cat")).toBeNull(); // i of AWARD_GAP_MIN
    }
    expect(store.noteDecision("cat")).not.toBeNull(); // gap reached → award
  });
});

// --- Skip: a permanent, undoable, no-decision exit -----------------------------

describe("skip", () => {
  const item = {
    id: 41,
    picture_id: 10,
    tag: "cat",
    direction: "remove",
    kind: "binary",
  };

  it("POSTs the skip endpoint, pops the head, and only drains pending", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);

    await store.skip();

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/41/skip");
    expect(store.queues[1].items).toHaveLength(0);
    expect(store.tallies[1].skipped).toBe(1);
    expect(store.skippedCountFor(1)).toBe(1);
    // A skip is not a decision: done unchanged, pending drained into skipped.
    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 1,
      skipped: 1,
      locked: 0,
    });
    expect(store.undoStacks[1]).toHaveLength(1);
    expect(store.undoStacks[1][0].action).toBe("skip");
  });

  it("treats a 404 as already-gone: drops the card, no reversible skip entry", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.post.mockRejectedValueOnce({ response: { status: 404 } });

    await store.skip();

    // The card stays out of the queue and counts as skipped, but a 404 means the
    // suggestion is already gone - so NO undo entry is recorded (a bogus entry
    // would later POST /reopen on a dead id and 404 again).
    expect(store.queues[1].items).toHaveLength(0);
    expect(store.tallies[1].skipped).toBe(1);
    expect(store.error).toBeNull();
    expect(store.undoStacks[1] || []).toHaveLength(0);
    expect(store.reopenableSkipsFor(1)).toBe(0);
  });

  it("rolls back on a real failure", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.post.mockRejectedValueOnce(new Error("boom"));

    await store.skip();

    expect(store.queues[1].items[0]).toEqual(item);
    expect(store.tallies[1].skipped).toBe(0);
    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 0,
      locked: 0,
    });
    expect(store.error).toBeTruthy();
  });

  it("undo reopens a skip and restores the card without touching net XP", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    await store.skip();
    apiClient.post.mockClear();

    await store.undo();

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/41/reopen");
    expect(store.queues[1].items[0]).toEqual(item);
    expect(store.tallies[1].skipped).toBe(0);
    expect(store.sessions[0].progress).toEqual({
      done: 0,
      pending: 2,
      skipped: 0,
      locked: 0,
    });
    expect(store.decisionsCount).toBe(0); // skips never counted as decisions
  });

  it("reopenSkipped puts every session-skipped card back in the queue", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    await store.skip();
    expect(store.reopenableSkipsFor(1)).toBe(1);
    apiClient.post.mockClear();

    await store.reopenSkipped(1);

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/41/reopen");
    expect(store.queues[1].items).toHaveLength(1);
    expect(store.tallies[1].skipped).toBe(0);
    expect(store.reopenableSkipsFor(1)).toBe(0);
  });

  it("reopenSkipped reopens the good ids even when one id 404s", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    // Two skipped cards on the undo stack; the queue is otherwise empty and
    // pending starts drained (both cards already left as skips).
    store.sessions[0].progress = { done: 0, pending: 0 };
    store.queues = { 1: { items: [], loading: false, error: null } };
    store.undoStacks = {
      1: [
        {
          item: { id: 41, tag: "cat", kind: "binary" },
          action: "skip",
          delta: { skipped: 1 },
          votes: [],
        },
        {
          item: { id: 42, tag: "cat", kind: "binary" },
          action: "skip",
          delta: { skipped: 1 },
          votes: [],
        },
      ],
    };
    store.tallies = { 1: { removed: 0, added: 0, kept: 0, skipped: 2 } };
    // 41 reopens fine; 42 is already gone (404). One dead id must not block the
    // other from being reopened (the old Promise.all aborted the whole batch).
    apiClient.post.mockImplementation((url) =>
      url === "/tag_suggestions/42/reopen"
        ? Promise.reject({ response: { status: 404 } })
        : Promise.resolve({ data: {} }),
    );

    await store.reopenSkipped(1);

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/41/reopen");
    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/42/reopen");
    // The good id is back in the queue; the dead id is dropped, not re-queued.
    expect(store.queues[1].items.map((i) => i.id)).toEqual([41]);
    // Both left the skip stack; skipped tally drained for both.
    expect(store.reopenableSkipsFor(1)).toBe(0);
    expect(store.tallies[1].skipped).toBe(0);
    // Only the reopened card adds back to pending.
    expect(store.sessions[0].progress.pending).toBe(1);
  });
});

// --- Abort dialog plumbing -------------------------------------------------------

describe("undoChangesAndAbort", () => {
  it("bulk-reopens the review's changes (review-scoped) and then aborts", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, {
      id: 7,
      picture_id: 3,
      tag: "cat",
      direction: "remove",
      kind: "binary",
    });

    await store.undoChangesAndAbort(1);

    expect(apiClient.post).toHaveBeenCalledWith("/tag_suggestions/bulk-reopen", {
      review_id: 1,
    });
    expect(apiClient.post).toHaveBeenCalledWith("/reviews/1/abort");
    expect(store.sessions).toHaveLength(0);
  });

  it("counts only decisions as changes - skips are not changes", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, {
      id: 8,
      picture_id: 4,
      tag: "cat",
      direction: "remove",
      kind: "binary",
    });
    await store.skip();
    expect(store.decidedCountFor(1)).toBe(0);
  });

  it("falls back to server progress.done when the tally is unseeded (rail abort of a review not opened this run)", () => {
    // The review carries prior-sitting changes on the server (progress.done),
    // but it was never opened this app run, so fetchDetail never seeded the
    // local tally. decidedCountFor must still report the changes so the
    // Keep/Undo abort dialog is shown instead of silently keeping them.
    const store = useReviewSessionsStore();
    seedSession(
      store,
      { id: 9, picture_id: 5, tag: "cat", direction: "remove", kind: "binary" },
      { done: 3, pending: 0 },
    );
    // No fetchDetail / decision this run → the tally is empty.
    expect(store.tallies[1]).toBeUndefined();
    expect(store.decidedCountFor(1)).toBe(3);
  });

  it("does not count skipped-only rows as changes via the progress fallback", () => {
    // progress.done excludes skips, so a review with only skips reads zero and
    // aborts straight through (no Keep/Undo dialog for nothing to undo).
    const store = useReviewSessionsStore();
    seedSession(
      store,
      { id: 10, picture_id: 6, tag: "cat", direction: "remove", kind: "binary" },
      { done: 0, skipped: 2 },
    );
    expect(store.decidedCountFor(1)).toBe(0);
  });
});

// --- Archived-receipt deletion ---------------------------------------------------

describe("deleteArchived", () => {
  it("DELETEs the review and drops it from the archived list", async () => {
    const store = useReviewSessionsStore();
    store.archived = [
      { id: 3, tag: "cat" },
      { id: 4, tag: "dog" },
    ];

    await store.deleteArchived(3);

    expect(apiClient.delete).toHaveBeenCalledWith("/reviews/3");
    expect(store.archived.map((a) => a.id)).toEqual([4]);
  });

  it("falls back to the board when the deleted receipt is the active view", async () => {
    const store = useReviewSessionsStore();
    store.archived = [{ id: 3, tag: "cat" }];
    store.openArchived(3);
    expect(store.view).toEqual({ type: "archived", id: 3 });

    await store.deleteArchived(3);

    expect(store.view).toEqual({ type: "board" });
  });

  it("leaves the view alone when a different archived receipt is active", async () => {
    const store = useReviewSessionsStore();
    store.archived = [
      { id: 3, tag: "cat" },
      { id: 4, tag: "dog" },
    ];
    store.openArchived(4);

    await store.deleteArchived(3);

    expect(store.view).toEqual({ type: "archived", id: 4 });
  });
});

describe("clearArchived", () => {
  it("bulk-DELETEs with status=ARCHIVED and empties the list", async () => {
    const store = useReviewSessionsStore();
    store.archived = [
      { id: 3, tag: "cat" },
      { id: 4, tag: "dog" },
    ];

    await store.clearArchived();

    expect(apiClient.delete).toHaveBeenCalledWith("/reviews", {
      params: { status: "ARCHIVED" },
    });
    expect(store.archived).toEqual([]);
  });

  it("falls back to the board when an archived receipt is the active view", async () => {
    const store = useReviewSessionsStore();
    store.archived = [{ id: 3, tag: "cat" }];
    store.openArchived(3);

    await store.clearArchived();

    expect(store.view).toEqual({ type: "board" });
  });
});

// --- Receipt stays live after decisions (F3) ------------------------------------

describe("receiptFor / decidedCountFor stay live after decisions", () => {
  const item = {
    id: 55,
    picture_id: 20,
    tag: "cat",
    direction: "remove",
    kind: "binary",
  };

  it("reflects a decision made after open (not a stale zero)", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    // Open: seed the tally from the pre-decision server receipt (all zeros).
    apiClient.get.mockResolvedValueOnce({
      data: { receipt: { removed: 0, added: 0, kept: 0 } },
    });
    await store.fetchDetail(1);
    expect(store.receiptFor(1).removed).toBe(0);

    // Decide the card (remove + No → the tag was wrong → removed).
    await store.answerBinary("no");

    // The completion receipt reflects the decision; the abort dialog now offers
    // Undo because the decided count is non-zero.
    expect(store.receiptFor(1)).toMatchObject({ removed: 1, added: 0, kept: 0 });
    expect(store.decidedCountFor(1)).toBe(1);
  });

  it("does NOT double-count after a mid-session refresh (QA regression)", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    // Open: seed from the pre-decision receipt (zeros) - this also marks the id
    // as seeded so a later refetch can't fold decisions in again.
    apiClient.get.mockResolvedValueOnce({
      data: { receipt: { removed: 0, added: 0, kept: 0 } },
    });
    await store.fetchDetail(1);

    // Decide one card → receipt shows 1.
    await store.answerBinary("no");
    expect(store.receiptFor(1).removed).toBe(1);

    // Click Refresh mid-review. The LIVE server receipt now already counts that
    // decision (removed: 1). The old server+tally sum returned 2 here.
    apiClient.get.mockImplementation((url) => {
      if (url === "/reviews/1")
        return Promise.resolve({
          data: { receipt: { removed: 1, added: 0, kept: 0 } },
        });
      if (url === "/reviews")
        return Promise.resolve({ data: [store.sessions[0]] });
      return Promise.resolve({ data: { items: [] } });
    });
    await store.refreshSession(1);

    // Still 1 - the live receipt was not folded into the already-counted tally.
    expect(store.receiptFor(1).removed).toBe(1);
    expect(store.decidedCountFor(1)).toBe(1);
  });

  it("seeds prior-session decisions from the server receipt at open, exactly once", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    // Reopened review: the server receipt already reflects 2 removals from an
    // earlier sitting. Folded into the tally once at open.
    apiClient.get.mockResolvedValueOnce({
      data: { receipt: { removed: 2, added: 0, kept: 0 } },
    });
    await store.fetchDetail(1);
    expect(store.receiptFor(1).removed).toBe(2);
    expect(store.decidedCountFor(1)).toBe(2);

    // A new decision this session adds on top.
    await store.answerBinary("no");
    expect(store.receiptFor(1).removed).toBe(3);

    // A second fetchDetail (e.g. a refresh) must NOT re-seed the same 2.
    apiClient.get.mockResolvedValueOnce({
      data: { receipt: { removed: 3, added: 0, kept: 0 } },
    });
    await store.fetchDetail(1);
    expect(store.receiptFor(1).removed).toBe(3);
  });

  it("archived reviews read the frozen server snapshot (no local tally)", () => {
    const store = useReviewSessionsStore();
    // Not an open session - an archived review's frozen receipt is authoritative.
    store.archived = [{ id: 3 }];
    store.details = { 3: { receipt: { removed: 4, added: 1, kept: 2 } } };
    expect(store.receiptFor(3)).toMatchObject({ removed: 4, added: 1, kept: 2 });
    expect(store.decidedCountFor(3)).toBe(7);
  });

  it("reset() clears details, tallies, and the seed guard", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.get.mockResolvedValueOnce({
      data: { receipt: { removed: 9, added: 0, kept: 0 } },
    });
    await store.fetchDetail(1);
    expect(store.receiptFor(1).removed).toBe(9);

    store.reset();
    expect(store.details).toEqual({});
    expect(store.tallies).toEqual({});

    // Seed guard cleared: reopening re-seeds cleanly from a fresh receipt
    // instead of skipping (which would leave a reopened review reading zero).
    seedSession(store, item);
    apiClient.get.mockResolvedValueOnce({
      data: { receipt: { removed: 5, added: 0, kept: 0 } },
    });
    await store.fetchDetail(1);
    expect(store.receiptFor(1).removed).toBe(5);
  });
});
