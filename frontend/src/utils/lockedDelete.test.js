import { describe, it, expect } from "vitest";
import {
  LOCKED_DELETE_ALL_LOCKED,
  LOCKED_DELETE_HINT,
  LOCKED_DELETE_PARTIAL,
  buildLockedDeleteMessage,
  buildLockedPurgeNote,
  deleteForeverDestroyCounts,
} from "./lockedDelete.js";

describe("buildLockedDeleteMessage - nothing locked", () => {
  it("says nothing when the delete did what was asked", () => {
    expect(
      buildLockedDeleteMessage({ lockedCount: 0, deletedCount: 7 }),
    ).toBeNull();
  });

  it("says nothing when both counts are zero", () => {
    expect(
      buildLockedDeleteMessage({ lockedCount: 0, deletedCount: 0 }),
    ).toBeNull();
  });

  it("says nothing for missing / malformed input", () => {
    expect(buildLockedDeleteMessage()).toBeNull();
    expect(buildLockedDeleteMessage({})).toBeNull();
    expect(
      buildLockedDeleteMessage({ lockedCount: NaN, deletedCount: 3 }),
    ).toBeNull();
    expect(
      buildLockedDeleteMessage({ lockedCount: -2, deletedCount: 3 }),
    ).toBeNull();
  });
});

describe("buildLockedDeleteMessage - everything locked", () => {
  it("reports that nothing was deleted, with the count", () => {
    const msg = buildLockedDeleteMessage({ lockedCount: 3, deletedCount: 0 });
    expect(msg.kind).toBe(LOCKED_DELETE_ALL_LOCKED);
    expect(msg.title).toBe("Nothing was deleted");
    expect(msg.body).toBe(
      "All 3 selected pictures are in locked sets, so they were kept.",
    );
    expect(msg.hint).toBe(LOCKED_DELETE_HINT);
  });

  it("uses the singular throughout for one picture", () => {
    const msg = buildLockedDeleteMessage({ lockedCount: 1, deletedCount: 0 });
    expect(msg.body).toBe(
      "All 1 selected picture is in a locked set, so it was kept.",
    );
  });

  it("always names the lever", () => {
    const msg = buildLockedDeleteMessage({ lockedCount: 5, deletedCount: 0 });
    expect(msg.hint).toMatch(/unlock/i);
  });
});

describe("buildLockedDeleteMessage - partial", () => {
  it("reports both halves of the outcome", () => {
    const msg = buildLockedDeleteMessage({ lockedCount: 3, deletedCount: 4 });
    expect(msg.kind).toBe(LOCKED_DELETE_PARTIAL);
    expect(msg.title).toBe("Some pictures were kept");
    expect(msg.body).toBe(
      "4 pictures moved to the scrapheap; 3 are in locked sets and were kept.",
    );
    expect(msg.hint).toBe(LOCKED_DELETE_HINT);
  });

  it("handles one of each", () => {
    const msg = buildLockedDeleteMessage({ lockedCount: 1, deletedCount: 1 });
    expect(msg.body).toBe(
      "1 picture moved to the scrapheap; 1 is in a locked set and was kept.",
    );
  });

  it("never claims more was deleted than was", () => {
    const msg = buildLockedDeleteMessage({ lockedCount: 9, deletedCount: 1 });
    expect(msg.body).toMatch(/^1 picture moved/);
    expect(msg.body).toMatch(/9 are in locked sets/);
  });
});

describe("buildLockedPurgeNote", () => {
  it("is empty when nothing is locked", () => {
    expect(buildLockedPurgeNote(0)).toBe("");
    expect(buildLockedPurgeNote()).toBe("");
    // Defensive: a server that has not shipped `locked_count` yet.
    expect(buildLockedPurgeNote(undefined)).toBe("");
    expect(buildLockedPurgeNote(NaN)).toBe("");
  });

  it("states that neither action destroys the locked pictures", () => {
    expect(buildLockedPurgeNote(2)).toBe(
      "2 pictures are in locked sets and will be kept - neither action below deletes them.",
    );
  });

  it("uses the singular for one picture", () => {
    expect(buildLockedPurgeNote(1)).toBe(
      "1 picture is in a locked set and will be kept - neither action below deletes it.",
    );
  });
});

describe("deleteForeverDestroyCounts", () => {
  // The exact preview the backend lane verified live against real deletions.
  const VERIFIED = {
    totalCount: 5,
    lockedCount: 3,
    protectedCount: 1,
    unprotectedCount: 1,
  };

  it("matches the verified live preview", () => {
    expect(deleteForeverDestroyCounts(VERIFIED)).toEqual({
      deleteAll: 2, // unprotected + protected
      deleteUnprotectedOnly: 1, // unprotected
      kept: 3, // locked
    });
  });

  it("treats the three buckets as a partition of the total", () => {
    const { deleteAll, kept } = deleteForeverDestroyCounts(VERIFIED);
    expect(deleteAll + kept).toBe(VERIFIED.totalCount);
  });

  it("never derives a count from totalCount", () => {
    // A total that disagrees with the buckets must not change any figure - the
    // server's classification wins, so the UI cannot drift from the sweep.
    const counts = deleteForeverDestroyCounts({ ...VERIFIED, totalCount: 999 });
    expect(counts).toEqual(deleteForeverDestroyCounts(VERIFIED));
  });

  it("keeps locked pictures out of both destroyable figures", () => {
    const counts = deleteForeverDestroyCounts({
      lockedCount: 40,
      protectedCount: 0,
      unprotectedCount: 0,
    });
    expect(counts.deleteAll).toBe(0);
    expect(counts.deleteUnprotectedOnly).toBe(0);
    expect(counts.kept).toBe(40);
  });

  it("collapses to the plain counts when nothing is locked", () => {
    expect(
      deleteForeverDestroyCounts({ protectedCount: 2, unprotectedCount: 5 }),
    ).toEqual({ deleteAll: 7, deleteUnprotectedOnly: 5, kept: 0 });
  });

  // Defensive default: an older server omits `locked_count` entirely.
  it("reads a missing locked_count as zero", () => {
    const counts = deleteForeverDestroyCounts({
      protectedCount: 1,
      unprotectedCount: 4,
    });
    expect(counts.kept).toBe(0);
    expect(counts.deleteAll).toBe(5);
  });

  it("survives missing / malformed input", () => {
    expect(deleteForeverDestroyCounts()).toEqual({
      deleteAll: 0,
      deleteUnprotectedOnly: 0,
      kept: 0,
    });
    expect(
      deleteForeverDestroyCounts({
        protectedCount: NaN,
        unprotectedCount: -3,
        lockedCount: undefined,
      }),
    ).toEqual({ deleteAll: 0, deleteUnprotectedOnly: 0, kept: 0 });
  });
});
