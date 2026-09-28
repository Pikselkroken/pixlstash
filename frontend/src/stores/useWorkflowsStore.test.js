// The Workflows grid's sort keys, the selection, and the Merge / Split writes.
//
// Every card here is deliberately inconsistent across the numeric axes — the
// best-rated card has the fewest pictures and the oldest use — so a sort that
// reads the wrong field cannot come out in the right order by accident.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
const mergeWorkflows = vi.fn();
const splitWorkflow = vi.fn();
vi.mock("../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  mergeWorkflows: (...args) => mergeWorkflows(...args),
  splitWorkflow: (...args) => splitWorkflow(...args),
}));

import { useWorkflowsStore } from "./useWorkflowsStore";

// Input order is none of the sorted orders, and the undated card leads:
// a comparator that reads NaN (`Date.parse(null)`) leaves it where it started,
// so "last" has to be earned. `rank` and `rating` disagree on purpose too —
// "the-stack" ranks above the workhorse but is rated below it.
const CARDS = [
  {
    id: "never-kept-a-picture",
    name: "Alpha v10",
    rank: 3.0,
    rating: null,
    picture_count: 0,
    last_used: null,
  },
  {
    id: "few-but-loved",
    name: "zebra",
    rank: 4.6,
    rating: 5,
    picture_count: 2,
    last_used: "2026-01-04T00:00:00Z",
    topologies: ["t1", "t2"],
  },
  {
    id: "workhorse",
    name: "alpha v2",
    rank: 4.1,
    rating: 4.2,
    picture_count: 90,
    last_used: "2026-06-30T00:00:00Z",
  },
  {
    id: "the-stack",
    name: "Écru",
    rank: 4.3,
    rating: 3.5,
    picture_count: 40,
    last_used: "2026-09-01T00:00:00Z",
  },
];

const order = (store) => store.sortedCards.map((card) => card.id);

beforeEach(async () => {
  setActivePinia(createPinia());
  listWorkflowCards.mockReset();
  listWorkflowCards.mockResolvedValue({ cards: CARDS, one_offs: 3, hidden: 1 });
  mergeWorkflows.mockReset();
  splitWorkflow.mockReset();
});

describe("the sort keys", () => {
  it("each reads its own field, and the order differs for each", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();

    // Your ratings reads `rank`, the mean smoothed towards the library's own —
    // which is why "the-stack" leads the workhorse here and trails it under the
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

    // Name reads A to Z, the one ascending key: numbers compare as numbers
    // ("v2" before "v10") and case and accents do not split the alphabet.
    store.setSortKey("name");
    expect(order(store)).toEqual([
      "workhorse",
      "never-kept-a-picture",
      "the-stack",
      "few-but-loved",
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

describe("the selection", () => {
  it("replaces on a click and toggles one id on Ctrl", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.select("workhorse");
    expect(store.selectedKeys).toEqual(["workhorse"]);
    store.select("the-stack", { additive: true });
    expect(store.selectedKeys).toEqual(["workhorse", "the-stack"]);
    store.select("workhorse", { additive: true });
    expect(store.selectedKeys).toEqual(["the-stack"]);
  });

  it("runs the one selected card and nothing for several", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.select("workhorse");
    expect(store.runnableCard?.id).toBe("workhorse");
    store.select("the-stack", { additive: true });
    expect(store.runnableCard).toBe(null);
  });
});

// Merge and Split (#1623) replace the stack routes. Each re-reads the grid and
// moves the selection to the workflow the write produced: the ids it named
// before are gone or changed.
describe("merge and split", () => {
  it("merges the selection in its order and selects the result", async () => {
    mergeWorkflows.mockResolvedValue({
      id: "merged",
      ids: ["the-stack", "workhorse"],
    });
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["the-stack", "workhorse"]);
    listWorkflowCards.mockClear();

    expect(await store.mergeSelected()).toBe(true);

    // The FIRST selected is the cover, so the order is the request.
    expect(mergeWorkflows).toHaveBeenCalledWith(["the-stack", "workhorse"]);
    expect(listWorkflowCards).toHaveBeenCalledTimes(1);
    expect(store.selectedKeys).toEqual(["merged"]);
    expect(store.verbBusy).toBe("");
  });

  it("refuses to merge one workflow, and says why a refused merge failed", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.select("workhorse");
    expect(await store.mergeSelected()).toBe(false);
    expect(mergeWorkflows).not.toHaveBeenCalled();

    mergeWorkflows.mockRejectedValue(new Error("400"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      store.selectRange(["workhorse", "the-stack"]);
      expect(await store.mergeSelected()).toBe(false);
      expect(store.error).not.toBe("");
      // The selection stands: nothing was merged.
      expect(store.selectedKeys).toEqual(["workhorse", "the-stack"]);
    } finally {
      warn.mockRestore();
    }
  });

  it("splits one topology out and selects the new workflow", async () => {
    splitWorkflow.mockResolvedValue({ id: "split-off" });
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.select("few-but-loved");
    listWorkflowCards.mockClear();

    expect(await store.splitOut("few-but-loved", "t2")).toBe("split-off");

    expect(splitWorkflow).toHaveBeenCalledWith("few-but-loved", "t2");
    expect(listWorkflowCards).toHaveBeenCalledTimes(1);
    expect(store.selectedKeys).toEqual(["split-off"]);
  });
});

describe("a session reset stops the old session's answers landing", () => {
  it("drops a fetch that resolves after the reset", async () => {
    // Without an epoch stamp this writes the previous credential's rows —
    // and their cover thumbnail URLs — into the new session's store.
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
  });
});

describe("refetch", () => {
  it("drops a read on the wire and leaves a grid read able to start", async () => {
    const store = useWorkflowsStore();
    let releaseCards;
    listWorkflowCards.mockReturnValue(
      new Promise((resolve) => {
        releaseCards = () =>
          resolve({ cards: [{ id: "stale" }], one_offs: 0, hidden: 0 });
      }),
    );
    const pending = store.fetchCards();
    listWorkflowCards.mockResolvedValue({
      cards: CARDS,
      one_offs: 3,
      hidden: 1,
    });

    await store.refetch();
    releaseCards();
    await pending;

    // The fresh read landed and the stale one, answering after it, did not.
    expect(store.cards).toHaveLength(4);
    expect(store.loading).toBe(false);
  });
});

// ── invalidate(): the one door in from outside the view ───────────────────
//
// A ghost purge in Settings › Privacy forgets a model name or a picture ghost,
// which changes what a card names and how many pictures it counts.
describe("invalidate", () => {
  it("reads the grid again once it has been read", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    listWorkflowCards.mockClear();

    store.invalidate();
    await Promise.resolve();

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

  it("leaves a first read still on the wire to land", async () => {
    // Bumping the epoch under it would discard its answer, and an unread
    // store issues no replacement, so the grid would stay empty.
    let release;
    listWorkflowCards.mockImplementationOnce(
      () => new Promise((resolve) => (release = resolve)),
    );
    const store = useWorkflowsStore();
    const first = store.fetchCards();

    store.invalidate();
    release({ cards: [{ id: "auto:" + "a".repeat(64) }], one_offs: 0, hidden: 0 });
    await first;

    expect(store.cards).toHaveLength(1);
  });
});
