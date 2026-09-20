// The Workflows grid's three sort keys, and the one-stack-at-a-time rule.
//
// Every card here is deliberately inconsistent across the three axes — the
// best-rated card has the fewest pictures and the oldest use — so a sort that
// reads the wrong field cannot come out in the right order by accident.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
const getWorkflowCard = vi.fn();
vi.mock("../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
}));

import { PANEL_COLLAPSE_MS, useWorkflowsStore } from "./useWorkflowsStore";

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
    store.select("the-stack", { whole: true });
    expect(store.selectedKeys).toEqual(["the-stack", "m1", "m2"]);
  });

  it("a key means one card unless the caller asks for the stack", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // A member card is not in `cards` at all.
    store.select("m1");
    expect(store.selectedKeys).toEqual(["m1"]);

    // The cover IS, under the very key the panel's first row carries — so
    // leaving `whole` off is what keeps that row separable from the stack it
    // heads, and what keeps the `?topology=` deep link naming one workflow.
    store.select("the-stack");
    expect(store.selectedKeys).toEqual(["the-stack"]);
  });

  it("Ctrl adds the whole stack, and takes the whole stack back out", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    store.select("workhorse");
    store.select("the-stack", { additive: true, whole: true });
    expect(store.selectedKeys).toEqual(["workhorse", "the-stack", "m1", "m2"]);

    store.select("the-stack", { additive: true, whole: true });
    expect(store.selectedKeys).toEqual(["workhorse"]);
  });

  it("Ctrl on a half-selected stack completes it rather than emptying it", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // The half-selected state you actually reach: select the stack, then take
    // one member out. Toggling on the CLICKED key alone emptied this, because
    // the cover is still in — so the gesture that means "make this whole" did
    // the opposite.
    store.select("the-stack", { whole: true });
    store.select("m1", { additive: true });
    expect(store.selectedKeys).toEqual(["the-stack", "m2"]);

    store.select("the-stack", { additive: true, whole: true });
    expect(store.selectedKeys).toEqual(["the-stack", "m2", "m1"]);

    // Whole now, so the next Ctrl does empty it.
    store.select("the-stack", { additive: true, whole: true });
    expect(store.selectedKeys).toEqual([]);
  });

  it("the other half-selected state completes too, and no key lands twice", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    store.select("m1");
    store.select("the-stack", { additive: true, whole: true });
    expect(store.selectedKeys).toEqual(["m1", "the-stack", "m2"]);
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
});

describe("the panel follows a change of selection", () => {
  // `select` starts the member fetch and does not wait for it; nothing here
  // asserts on members, so one microtask is enough to let the flip land.
  const settle = () => Promise.resolve();

  it("does not open anything: selecting is not a way in", async () => {
    // ▸, Enter and a double-click open a stack. A plain click is a selection,
    // and a rule that opened on one would make every click down the grid throw
    // a band open under it.
    const store = useWorkflowsStore();
    await store.fetchCards();

    store.select("the-stack", { whole: true });
    await settle();
    expect(store.openStackKey).toBe(null);
  });

  it("moves an open panel to the stack that was picked", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    // The band MOVES. A second panel would put itself a screen below the card
    // that opened it, which is why there is only ever one.
    store.select("few-but-loved", { whole: true });
    await settle();
    expect(store.openStackKey).toBe("few-but-loved");
  });

  it("leaves the panel alone for a card with nothing under it", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    // A member row. Every row inside the panel is one of these, so a rule that
    // closed here would shut the panel as somebody picked out of it.
    store.select("m1");
    await settle();
    expect(store.openStackKey).toBe("the-stack");

    // A plain card elsewhere in the grid.
    store.select("workhorse");
    await settle();
    expect(store.openStackKey).toBe("the-stack");

    // And the cover key ALONE, which is what `?topology=` selects: it names
    // one workflow, not the pile it sits in.
    store.select("few-but-loved");
    await settle();
    expect(store.openStackKey).toBe("the-stack");
  });

  it("closes the panel when the selection reaches outside the stack", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");
    store.select("the-stack", { whole: true });
    await settle();

    store.select("workhorse", { additive: true });
    expect(store.selectedKeys).toEqual(["the-stack", "m1", "m2", "workhorse"]);
    // Still open, and marked as collapsing: the member rows are what the
    // animation is drawn on, so they outlive the gesture.
    expect(store.openStackKey).toBe("the-stack");
    expect(store.panelClosing).toBe(true);

    store.finishCollapse();
    expect(store.openStackKey).toBe(null);
    expect(store.panelClosing).toBe(false);
  });

  it("closes it for a range that leaves the stack too", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    store.selectRange(["the-stack", "m1", "m2", "workhorse"]);
    expect(store.panelClosing).toBe(true);
    store.finishCollapse();
    expect(store.openStackKey).toBe(null);
  });

  it("keeps the panel open while the selection stays inside it", async () => {
    // The grid declares `aria-multiselectable`, so Ctrl and Shift inside an
    // open stack are gestures it promises. A rule counting selected keys
    // instead of asking where they are would destroy the rows being picked
    // from on the second pick.
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    store.select("m1");
    store.select("m2", { additive: true });
    expect(store.selectedKeys).toEqual(["m1", "m2"]);
    expect(store.openStackKey).toBe("the-stack");
    expect(store.panelClosing).toBe(false);

    // The cover counts as inside: it is the panel's first row as well as the
    // grid's stack card.
    store.selectRange(["the-stack", "m2"]);
    expect(store.openStackKey).toBe("the-stack");
    expect(store.panelClosing).toBe(false);
  });

  it("lets the caret close the stack it also selects", async () => {
    // ▸ deliberately does not stop its click, so closing a stack from the
    // caret also selects that card — and that selection asks for the same
    // stack. Reopening on it would leave the caret unable to close anything.
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    store.toggleStack("the-stack");
    store.select("the-stack", { whole: true });
    await settle();
    expect(store.panelClosing).toBe(true);

    store.finishCollapse();
    expect(store.openStackKey).toBe(null);
  });

  it("abandons a collapse in progress when another stack is picked", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    store.collapseStack();
    store.select("few-but-loved", { whole: true });
    await settle();
    expect(store.openStackKey).toBe("few-but-loved");
    expect(store.panelClosing).toBe(false);

    // Wrong if this nulls the key: the abandoned collapse would shut the panel
    // the reader had just opened.
    store.finishCollapse();
    expect(store.openStackKey).toBe("few-but-loved");
  });

  it("does not put the failure of a stack you have left over the one you are on", async () => {
    // A plain click moves the panel, so a member read for the stack the reader
    // has already left is routinely still on the wire when it fails. Its
    // failure is worth recording; it is never worth telling them the stack
    // they are now looking at could not be read.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    let fail;
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve, reject) => {
          if (key === "m1" || key === "m2")
            fail = () => reject(new Error("no"));
          else resolve({ card: { key, member_keys: [] } });
        }),
    );
    const store = useWorkflowsStore();
    await store.fetchCards();

    const left = store.openStack("the-stack");
    await store.openStack("few-but-loved");
    expect(store.openStackKey).toBe("few-but-loved");

    fail();
    await left;
    expect(store.error).toBe("");
    warn.mockRestore();
  });

  it("does not animate a close that has nothing left to draw", async () => {
    // `forgetMembers` and a filter that takes the cover off the grid both call
    // `closeStack`, and both mean the panel's CONTENT has stopped being true.
    // Collapsing would spend the animation drawing a stack being torn down.
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    store.forgetMembers();
    expect(store.openStackKey).toBe(null);
    expect(store.panelClosing).toBe(false);
  });
});

describe("the collapse's backstop", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("drops the panel on its own if the animation never reports", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");

    store.collapseStack();
    vi.advanceTimersByTime(PANEL_COLLAPSE_MS);
    expect(store.openStackKey).toBe(null);
    expect(store.panelClosing).toBe(false);
  });

  it("a session reset takes the timer with it", async () => {
    // Left armed it fires against the new credential's store and closes a
    // panel that belongs to a different library.
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");
    store.collapseStack();
    expect(store.panelClosing).toBe(true);

    store.reset();
    expect(store.panelClosing).toBe(false);

    await store.fetchCards();
    await store.openStack("few-but-loved");
    vi.advanceTimersByTime(PANEL_COLLAPSE_MS * 2);
    expect(store.openStackKey).toBe("few-but-loved");
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

describe("forgetting the members a re-key invalidated", () => {
  it("collapses the open stack rather than leaving it reporting a failure", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("the-stack");
    expect(store.members["the-stack"]).toHaveLength(3);

    store.forgetMembers();
    // Left open with no members, `openMembers` falls back to the cover alone
    // and `StackPanel` renders `size - members.length` as "2 could not be
    // read" — a failure message for a deliberate invalidation. Collapsed,
    // re-expanding re-reads them through the path that already exists.
    expect(store.members).toEqual({});
    expect(store.openStackKey).toBe(null);
    expect(store.openMembers).toEqual([]);
  });

  it("drops a member read that was already on the wire, and can read again", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    let releaseMember;
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve) => {
          releaseMember = () => resolve({ card: { key, name: "stale" } });
        }),
    );
    // One member, so one promise to release.
    const open = store.openStack("few-but-loved");

    store.forgetMembers();
    releaseMember();
    await open;
    // The pre-flip members, written back into the map this just emptied:
    // `openStack`'s completion path guards only on the epoch, so clearing
    // `inflight` alone would not stop it.
    expect(store.members).toEqual({});

    // And `inflight` has to be cleared with it, or re-expanding the stack
    // returns early for ever: the key is still in it and nothing will take
    // it out, because the old epoch's `finally` is epoch-guarded too.
    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve({ card: { key, name: "fresh" } }),
    );
    await store.openStack("few-but-loved");
    expect(store.members["few-but-loved"]?.[1]?.name).toBe("fresh");
  });

  it("leaves a grid read able to start again", async () => {
    const store = useWorkflowsStore();
    let releaseCards;
    listWorkflowCards.mockReturnValue(
      new Promise((resolve) => {
        releaseCards = () => resolve({ cards: CARDS, one_offs: 3, hidden: 1 });
      }),
    );
    const pending = store.fetchCards();

    store.forgetMembers();
    releaseCards();
    await pending;
    // Every caller does `forgetMembers()` then `await fetchCards()`, and
    // `fetchCards` returns early while `loading` is set. The old epoch's
    // `finally` will not clear it, so this has to.
    expect(store.loading).toBe(false);

    listWorkflowCards.mockResolvedValue({
      cards: CARDS,
      one_offs: 3,
      hidden: 1,
    });
    await store.fetchCards();
    expect(store.cards).toHaveLength(4);
  });
});

// ── invalidate(): the one door in from outside the view ───────────────────
//
// A ghost purge in Settings › Privacy forgets a model name or a picture ghost,
// which changes what a card names and how many pictures it counts. The shelf
// had the same door and the same test; both went with it.
describe("invalidate", () => {
  it("drops the cached stack members and reads the grid again", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    await store.openStack("few-but-loved");
    expect(store.members["few-but-loved"]).toBeTruthy();
    expect(store.openStackKey).toBe("few-but-loved");
    listWorkflowCards.mockClear();

    store.invalidate();
    await Promise.resolve();

    // The members are forgotten rather than redrawn from stale cards, the
    // panel is collapsed rather than left open over nothing, and the grid is
    // re-read - a card can have lost the very name that was purged.
    expect(store.members).toEqual({});
    expect(store.openStackKey).toBe(null);
    expect(listWorkflowCards).toHaveBeenCalledTimes(1);
  });

  it("asks for nothing when the grid has never been read", async () => {
    // Settings is reachable without ever opening Workflows, and a purge made
    // there must not fire a request for a screen nobody is looking at.
    const store = useWorkflowsStore();
    expect(store.loaded).toBe(false);
    listWorkflowCards.mockClear();

    store.invalidate();
    await Promise.resolve();

    expect(listWorkflowCards).not.toHaveBeenCalled();
  });
});
