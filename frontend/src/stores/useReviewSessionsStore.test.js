import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

// The store imports a singleton apiClient; mock the module so no real HTTP
// happens and we can assert which per-item endpoints a decision hits.
vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
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
  apiClient.get.mockResolvedValue({ data: [] });
  apiClient.post.mockResolvedValue({ data: {} });
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

function seedSession(store, item) {
  store.sessions = [
    {
      id: 1,
      tag: item.tag,
      stats: { scanned: 100, found: 2, prev_reviewed: 0 },
      progress: { done: 0, pending: 2 },
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
    expect(store.sessions[0].progress).toEqual({ done: 1, pending: 1 });
  });

  it("rolls back the head, tally, and progress when the write fails", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.post.mockRejectedValueOnce(new Error("boom"));

    await store.answerBinary("no");

    expect(store.queues[1].items[0]).toEqual(item);
    expect(store.tallies[1]).toEqual({ removed: 0, added: 0, kept: 0 });
    expect(store.sessions[0].progress).toEqual({ done: 0, pending: 2 });
    expect(store.error).toBeTruthy();
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
    // A skip is not a decision: done unchanged, pending drained.
    expect(store.sessions[0].progress).toEqual({ done: 0, pending: 1 });
    expect(store.undoStacks[1]).toHaveLength(1);
    expect(store.undoStacks[1][0].action).toBe("skip");
  });

  it("degrades gracefully when the endpoint 404s (interim testing)", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.post.mockRejectedValueOnce({ response: { status: 404 } });

    await store.skip();

    // The client-side removal stands; no error surfaced; undo still possible.
    expect(store.queues[1].items).toHaveLength(0);
    expect(store.tallies[1].skipped).toBe(1);
    expect(store.error).toBeNull();
    expect(store.undoStacks[1]).toHaveLength(1);
  });

  it("rolls back on a real failure", async () => {
    const store = useReviewSessionsStore();
    seedSession(store, item);
    apiClient.post.mockRejectedValueOnce(new Error("boom"));

    await store.skip();

    expect(store.queues[1].items[0]).toEqual(item);
    expect(store.tallies[1].skipped).toBe(0);
    expect(store.sessions[0].progress).toEqual({ done: 0, pending: 2 });
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
    expect(store.sessions[0].progress).toEqual({ done: 0, pending: 2 });
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

  it("counts only decisions as changes — skips are not changes", async () => {
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
});
