// The queue row's one stack-expansion band.
//
// The invariant these pin is structural rather than cosmetic: `DuplicateQueue`
// sizes both of its scroll spacers from a single uniform row pitch, so a second
// open band — or one left behind on a row the cursor has walked away from —
// is a second variable-height row and the whole scroll track stops meaning
// anything.

import { describe, it, expect, beforeEach, vi } from "vitest";

import { useDedupRowExpansion } from "./useDedupRowExpansion";

/** A group of one deck (stack 12, four deep) and one loose picture. */
function deckGroup(signature = "d1", stackId = 12) {
  return {
    signature,
    candidates: [{ picture_id: 503, stack_id: stackId }, { picture_id: 700 }],
    stacks: {
      [stackId]: {
        stack_id: stackId,
        member_count: 4,
        leader_picture_id: 501,
      },
    },
  };
}

/** The member page, as `GET /dedup/stacks/{id}/members` serves it. */
function memberPage(stackId) {
  return {
    stack_id: stackId,
    members: [
      { picture_id: 501, thumbnail_version: "a" },
      { picture_id: 502, thumbnail_version: "b" },
    ],
    next_offset: null,
  };
}

let fetchMembers;
let expansion;

beforeEach(() => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  fetchMembers = vi.fn((stackId) => Promise.resolve(memberPage(stackId)));
  expansion = useDedupRowExpansion({ fetchMembers });
});

describe("useDedupRowExpansion — opening and closing", () => {
  it("reads the members once, at the server's own page ceiling", async () => {
    expect(expansion.toggle("d1", 12)).toBe(true);
    expect(fetchMembers).toHaveBeenCalledWith(12, { limit: 200 });
    await Promise.resolve();
    await Promise.resolve();

    expect(expansion.stackIdFor("d1")).toBe(12);
    expect(expansion.members.value).toEqual([
      { id: 501, thumbnail_version: "a" },
      { id: 502, thumbnail_version: "b" },
    ]);
    expect(expansion.loading.value).toBe(false);
    expect(expansion.failed.value).toBe(false);
  });

  it("closes on a second toggle of the same stack", () => {
    expansion.toggle("d1", 12);
    expect(expansion.toggle("d1", 12)).toBe(false);
    expect(expansion.stackIdFor("d1")).toBeNull();
    expect(expansion.members.value).toEqual([]);
  });

  // The hard constraint: one band in the whole queue.
  it("closes the previous band when another opens", async () => {
    expansion.toggle("d1", 12);
    await Promise.resolve();
    expansion.toggle("d2", 20);
    expect(expansion.stackIdFor("d1")).toBeNull();
    expect(expansion.stackIdFor("d2")).toBe(20);
  });

  // A band can be closed, moved or collapsed while its read is in flight, and
  // a late response must not draw one stack's members under another's badge.
  it("discards a response that outlived its own open", async () => {
    let settleFirst;
    fetchMembers.mockImplementationOnce(
      () => new Promise((resolve) => (settleFirst = resolve)),
    );
    expansion.toggle("d1", 12);
    expansion.toggle("d2", 20);
    await Promise.resolve();
    await Promise.resolve();
    const held = [...expansion.members.value];

    settleFirst(memberPage(12));
    await Promise.resolve();
    await Promise.resolve();
    expect(expansion.stackIdFor("d2")).toBe(20);
    expect(expansion.members.value).toEqual(held);
  });
});

describe("useDedupRowExpansion — the band follows the focus", () => {
  // Stated as "keep it only on this row" rather than "collapse on any focus
  // change" BECAUSE the gestures arrive in the other order: the badge on an
  // unfocused row emits focus and THEN the toggle, so a blind collapse would
  // close the band the same click had just opened.
  it("survives a focus change that lands on its own row", async () => {
    expansion.toggle("d1", 12);
    await Promise.resolve();
    expansion.keepOnlyOn("d1");
    expect(expansion.stackIdFor("d1")).toBe(12);
  });

  it("collapses when the focus moves to another row", async () => {
    expansion.toggle("d1", 12);
    await Promise.resolve();
    expansion.keepOnlyOn("d2");
    expect(expansion.stackIdFor("d1")).toBeNull();
    expect(expansion.members.value).toEqual([]);
  });

  it("collapses when the queue runs out of focused rows", async () => {
    expansion.toggle("d1", 12);
    await Promise.resolve();
    expansion.keepOnlyOn("");
    expect(expansion.stackIdFor("d1")).toBeNull();
  });
});

describe("useDedupRowExpansion — the E key's target", () => {
  it("opens the focused group's first deck", () => {
    expect(expansion.toggleForGroup(deckGroup())).toEqual({
      open: true,
      stackId: 12,
    });
    expect(fetchMembers).toHaveBeenCalledWith(12, { limit: 200 });
  });

  it("closes the band it already has open", async () => {
    const group = deckGroup();
    expansion.toggleForGroup(group);
    await Promise.resolve();
    expect(expansion.toggleForGroup(group)).toEqual({
      open: false,
      stackId: 12,
    });
    expect(expansion.stackIdFor("d1")).toBeNull();
  });

  // A group of loose pictures has nothing folded away. Reporting that is what
  // lets the view say so instead of leaving the key looking dead.
  it("reports that a group with no deck has nothing to open", () => {
    expect(
      expansion.toggleForGroup({
        signature: "g1",
        candidates: [{ picture_id: 1 }, { picture_id: 2 }],
      }),
    ).toBeNull();
    expect(fetchMembers).not.toHaveBeenCalled();
  });
});

describe("useDedupRowExpansion — failure", () => {
  it("reports a rejected read and retries the same stack", async () => {
    fetchMembers.mockRejectedValueOnce(new Error("boom"));
    expansion.toggle("d1", 12);
    await Promise.resolve();
    await Promise.resolve();
    expect(expansion.failed.value).toBe(true);
    expect(expansion.loading.value).toBe(false);
    expect(console.warn).toHaveBeenCalled();

    expansion.retry();
    await Promise.resolve();
    await Promise.resolve();
    expect(expansion.failed.value).toBe(false);
    expect(expansion.members.value).toHaveLength(2);
  });

  // The route answers 404 rather than serving an empty membership, so an empty
  // list always means something went wrong on the way.
  it("treats an empty member list as a failed read", async () => {
    fetchMembers.mockResolvedValueOnce({ stack_id: 12, members: [] });
    expansion.toggle("d1", 12);
    await Promise.resolve();
    await Promise.resolve();
    expect(expansion.failed.value).toBe(true);
  });

  it("has nothing to retry with no band open", () => {
    expansion.retry();
    expect(fetchMembers).not.toHaveBeenCalled();
  });
});
