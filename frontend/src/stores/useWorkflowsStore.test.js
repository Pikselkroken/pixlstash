// The Workflows grid's three sort keys, and the one-stack-at-a-time rule.
//
// Every card here is deliberately inconsistent across the three axes — the
// best-rated card has the fewest pictures and the oldest use — so a sort that
// reads the wrong field cannot come out in the right order by accident.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
const getWorkflowCard = vi.fn();
vi.mock("../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
}));

import { useWorkflowsStore } from "./useWorkflowsStore";

// Input order is none of the three sorted orders, and the undated card leads:
// a comparator that reads NaN (`Date.parse(null)`) leaves it where it started,
// so "last" has to be earned. `rank` and `rating` disagree on purpose too —
// the stack ranks above the workhorse but is rated below it.
const CARDS = [
  {
    key: "never-kept-a-picture",
    rank: 3.0,
    rating: null,
    picture_count: 0,
    last_used: null,
    member_keys: [],
  },
  {
    key: "few-but-loved",
    rank: 4.6,
    rating: 5,
    picture_count: 2,
    last_used: "2026-01-04T00:00:00Z",
    stack_size: 2,
    member_keys: ["m3"],
  },
  {
    key: "workhorse",
    rank: 4.1,
    rating: 4.2,
    picture_count: 90,
    last_used: "2026-06-30T00:00:00Z",
    member_keys: [],
  },
  {
    key: "the-stack",
    rank: 4.3,
    rating: 3.5,
    picture_count: 40,
    last_used: "2026-09-01T00:00:00Z",
    stack_size: 3,
    member_keys: ["m1", "m2"],
  },
];

const order = (store) => store.sortedCards.map((card) => card.key);

beforeEach(async () => {
  setActivePinia(createPinia());
  listWorkflowCards.mockResolvedValue({ cards: CARDS, one_offs: 3, hidden: 1 });
  getWorkflowCard.mockImplementation((key) =>
    Promise.resolve({ card: { key, member_keys: [] } }),
  );
});

describe("the three sort keys", () => {
  it("each reads its own field, and the order differs for all three", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // Your ratings reads `rank`, the mean smoothed towards the library's own —
    // which is why the stack leads the workhorse here and trails it under the
    // plain `rating` the cards display.
    expect(order(store)).toEqual([
      "few-but-loved",
      "the-stack",
      "workhorse",
      "never-kept-a-picture",
    ]);

    store.setSortKey("pictures");
    expect(order(store)).toEqual([
      "workhorse",
      "the-stack",
      "few-but-loved",
      "never-kept-a-picture",
    ]);

    store.setSortKey("used");
    expect(order(store)).toEqual([
      "the-stack",
      "workhorse",
      "few-but-loved",
      // No last use at all sorts BELOW every dated card. `-Infinity`, not 0:
      // "never" is not a date, and reading it as one is how a workflow with
      // no kept pictures would outrank every workflow made before 1970.
      "never-kept-a-picture",
    ]);
  });

  it("refuses a key it does not know rather than emptying the grid", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.setSortKey("whatever");
    expect(store.sortKey).toBe("rating");
    expect(order(store)).toHaveLength(4);
  });
});

describe("one stack open at a time", () => {
  it("opening a second replaces the first, and members are fetched once", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    await store.openStack("the-stack");
    expect(store.openStackKey).toBe("the-stack");
    // The cover leads; `member_keys` excludes it, so it is prepended here.
    expect(store.openMembers.map((card) => card.key)).toEqual([
      "the-stack",
      "m1",
      "m2",
    ]);
    expect(getWorkflowCard).toHaveBeenCalledTimes(2);

    await store.openStack("few-but-loved");
    expect(store.openStackKey).toBe("few-but-loved");
    expect(store.openMembers.map((card) => card.key)).toEqual([
      "few-but-loved",
      "m3",
    ]);

    // Back to the first: kept, so nothing is requested a second time.
    await store.openStack("the-stack");
    expect(getWorkflowCard).toHaveBeenCalledTimes(3);
    expect(store.openMembers).toHaveLength(3);

    store.closeStack();
    expect(store.openStackKey).toBe(null);
    expect(store.openMembers).toEqual([]);
  });

  it("refuses a card that is not a stack", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    // `workhorse` has `stack_size` 1. A panel for it would draw that one card
    // a second time, flagged "Cover", under the card itself.
    await store.openStack("workhorse");
    expect(store.openStackKey).toBe(null);
    expect(getWorkflowCard).not.toHaveBeenCalled();
  });

  it("shows the cover at once and does not re-request while a fetch is in flight", async () => {
    // One resolver per member request: the stack has two, and a single
    // `release` would only ever unblock the last of them.
    const release = [];
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve) => {
          release.push(() => resolve({ card: { key, member_keys: [] } }));
        }),
    );
    const store = useWorkflowsStore();
    await store.fetchCards();

    const first = store.openStack("the-stack");
    // Nothing has landed, but the panel has something to draw: the cover the
    // grid already held, and the size it declares.
    expect(store.openMembers.map((card) => card.key)).toEqual(["the-stack"]);
    expect(store.openStackSize).toBe(3);
    expect(store.membersLoading).toBe(true);

    // A second open of the same stack (a double-click on ▸) must not re-issue
    // the member requests just because none of them has resolved yet.
    const second = store.openStack("the-stack");
    expect(getWorkflowCard).toHaveBeenCalledTimes(2);

    release.forEach((resolve) => resolve());
    await Promise.all([first, second]);
    expect(store.openMembers).toHaveLength(3);
    expect(store.membersLoading).toBe(false);
  });

  it("a member that will not load leaves the cover rather than an empty panel", async () => {
    getWorkflowCard.mockRejectedValue(new Error("nope"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const store = useWorkflowsStore();
    await store.fetchCards();

    await store.openStack("the-stack");
    expect(store.openMembers.map((card) => card.key)).toEqual(["the-stack"]);
    expect(store.error).toBeTruthy();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
