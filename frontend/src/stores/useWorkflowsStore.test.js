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
    // `stack_size` 1 AND member keys: what a card looks like once its stack is
    // unstacked (`test_unstacking_a_card_takes_it_out_of_the_automatic_group`
    // serves exactly this), so the guard that reads `stack_size` rather than
    // the list has something to refuse.
    member_keys: ["m9"],
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

  it("asks again after a failure instead of caching it as the answer", async () => {
    // A dropped request is not an answer. Writing the cover alone into
    // `members` satisfied the "already have them" guard, so the panel said
    // "2 could not be read" for the rest of the session however often the
    // reader reopened the stack — even once the network was back.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    getWorkflowCard.mockRejectedValue(new Error("nope"));
    const store = useWorkflowsStore();
    await store.fetchCards();

    await store.openStack("the-stack");
    expect(getWorkflowCard).toHaveBeenCalledTimes(2);
    store.closeStack();

    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve({ card: { key, member_keys: [] } }),
    );
    await store.openStack("the-stack");
    expect(getWorkflowCard).toHaveBeenCalledTimes(4);
    expect(store.openMembers.map((card) => card.key)).toEqual([
      "the-stack",
      "m1",
      "m2",
    ]);
    warn.mockRestore();
  });

  it("reports the OPEN stack's loading, not whichever request finished first", async () => {
    // One boolean shared by every stack cleared on the first request to
    // finish, so `b`'s completion told the panel that `e`'s members could not
    // be read while they were still on the wire.
    const release = {};
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve) => {
          release[key] = () => resolve({ card: { key, member_keys: [] } });
        }),
    );
    const store = useWorkflowsStore();
    await store.fetchCards();

    const first = store.openStack("the-stack");
    const second = store.openStack("few-but-loved");
    expect(store.openStackKey).toBe("few-but-loved");
    expect(store.membersLoading).toBe(true);

    // The stack that is NOT open finishes. The open one is still fetching.
    release.m1();
    release.m2();
    await first;
    expect(store.membersLoading).toBe(true);

    release.m3();
    await second;
    expect(store.membersLoading).toBe(false);
  });
});

describe("a stack is selected whole", () => {
  it("one click on a stack takes its members with it", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // The cover key alone was the bug: a card marked "3 workflows" selected
    // one of them, the one at the top of the pile.
    store.select("the-stack");
    expect(store.selectedKeys).toEqual(["the-stack", "m1", "m2"]);
  });

  it("a card that is not a stack takes nothing, even carrying member keys", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // `workhorse` is `stack_size: 1` with `member_keys: ["m9"]` — an unstacked
    // card. Expanding on the list rather than on `stack_size` would select a
    // card the grid draws separately and the panel never opens.
    store.select("workhorse");
    expect(store.selectedKeys).toEqual(["workhorse"]);
  });

  it("a member's own key stands for itself, the cover row included", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // A member card is not in `cards` at all.
    store.select("m1", { whole: false });
    expect(store.selectedKeys).toEqual(["m1"]);

    // And the cover IS, under the very key the panel's first row carries — so
    // only `whole: false` keeps that row separable from the stack it heads.
    store.select("the-stack", { whole: false });
    expect(store.selectedKeys).toEqual(["the-stack"]);
  });

  it("Ctrl adds the whole stack, and takes the whole stack back out", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    store.select("workhorse");
    store.select("the-stack", { additive: true });
    expect(store.selectedKeys).toEqual(["workhorse", "the-stack", "m1", "m2"]);

    store.select("the-stack", { additive: true });
    expect(store.selectedKeys).toEqual(["workhorse"]);
  });

  it("Ctrl on a half-selected stack completes it rather than emptying it", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // The half-selected state you actually reach: select the stack, then take
    // one member out. Toggling on the CLICKED key alone emptied this, because
    // the cover is still in — so the gesture that means "make this whole" did
    // the opposite.
    store.select("the-stack");
    store.select("m1", { additive: true, whole: false });
    expect(store.selectedKeys).toEqual(["the-stack", "m2"]);

    store.select("the-stack", { additive: true });
    expect(store.selectedKeys).toEqual(["the-stack", "m2", "m1"]);

    // Whole now, so the next Ctrl does empty it.
    store.select("the-stack", { additive: true });
    expect(store.selectedKeys).toEqual([]);
  });

  it("the other half-selected state completes too, and no key lands twice", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    store.select("m1", { whole: false });
    store.select("the-stack", { additive: true });
    expect(store.selectedKeys).toEqual(["m1", "the-stack", "m2"]);
  });

  it("selectRange takes the run it is handed, expanding nothing itself", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // The view expands per row, because only it knows which key came from a
    // stack CARD and which from a member row inside an open panel. A range
    // that expanded here would drag a stack's other members back in behind a
    // reader who had just taken them out.
    store.selectRange(["the-stack", "m1"]);
    expect(store.selectedKeys).toEqual(["the-stack", "m1"]);
  });

  it("stackKeys answers nothing for no stack, and itself for an unknown key", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    expect(store.stackKeys(null)).toEqual([]);
    expect(store.stackKeys("not-a-card")).toEqual(["not-a-card"]);
    expect(store.stackKeys("never-kept-a-picture")).toEqual([
      "never-kept-a-picture",
    ]);
  });

  it("a stack card with no member keys expands to itself", async () => {
    const store = useWorkflowsStore();
    listWorkflowCards.mockResolvedValue({
      // The server derives both from one list, so this cannot arrive from
      // `/workflows/cards` — but `?? []` is the difference between "the cover
      // alone" and a crash if it ever does.
      cards: [{ key: "bare", rank: 1, stack_size: 3 }],
      one_offs: 0,
      hidden: 0,
    });
    await store.fetchCards();

    expect(store.stackKeys("bare")).toEqual(["bare"]);
  });
});

describe("a session reset stops the old session's answers landing", () => {
  it("drops a fetch and a member request that resolve after the reset", async () => {
    // Without an epoch stamp both of these write the previous credential's
    // rows — and their cover thumbnail URLs — into the new session's store.
    let releaseCards;
    listWorkflowCards.mockReturnValue(
      new Promise((resolve) => {
        releaseCards = () => resolve({ cards: CARDS, one_offs: 3, hidden: 1 });
      }),
    );
    const store = useWorkflowsStore();
    const pending = store.fetchCards();

    store.reset();
    releaseCards();
    await pending;
    expect(store.cards).toEqual([]);
    expect(store.loaded).toBe(false);
    // And the refusal is not permanent: `fetchCards` returns early while
    // `loading` is set, so the reset has to clear it as well.
    expect(store.loading).toBe(false);

    listWorkflowCards.mockResolvedValue({
      cards: CARDS,
      one_offs: 3,
      hidden: 1,
    });
    await store.fetchCards();
    expect(store.cards).toHaveLength(4);

    let releaseMember;
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve) => {
          releaseMember = () => resolve({ card: { key, member_keys: [] } });
        }),
    );
    const open = store.openStack("few-but-loved");
    store.reset();
    releaseMember();
    await open;
    expect(store.members).toEqual({});
    expect(store.openStackKey).toBe(null);
  });
});
