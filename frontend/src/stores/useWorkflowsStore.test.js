// The Workflows grid's sort keys, the selection, its filters and the deletes.
//
// Every card here is deliberately inconsistent across the numeric axes — the
// best-rated card has the fewest pictures and the oldest use — so a sort that
// reads the wrong field cannot come out in the right order by accident.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const listWorkflowCards = vi.fn();
const deleteWorkflow = vi.fn();
const getWorkflowCard = vi.fn();
vi.mock("../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  deleteWorkflow: (...args) => deleteWorkflow(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
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

describe("deleteSelected", () => {
  it("deletes each selected workflow, clears the selection and says recipes moved", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["workhorse", "the-stack"]);
    deleteWorkflow.mockReset();
    deleteWorkflow.mockResolvedValue({ deleted: "x" });
    const epoch = store.recipesEpoch;

    const result = await store.deleteSelected();

    expect(result).toMatchObject({ done: 2, refused: 0 });
    expect(deleteWorkflow.mock.calls.map(([id]) => id)).toEqual([
      "workhorse",
      "the-stack",
    ]);
    // Gone from the grid, so gone from the selection.
    expect(store.selectedKeys).toEqual([]);
    // Their saved recipes are unfiled now: the Unfiled list re-reads.
    expect(store.recipesEpoch).toBe(epoch + 1);
  });

  it("keeps the selection, and moves no recipe, when every delete is refused", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["workhorse"]);
    deleteWorkflow.mockReset();
    deleteWorkflow.mockRejectedValue(new Error("409"));
    const epoch = store.recipesEpoch;

    expect(await store.deleteSelected()).toMatchObject({ done: 0, refused: 1 });
    expect(store.selectedKeys).toEqual(["workhorse"]);
    expect(store.recipesEpoch).toBe(epoch);
  });

  it("drops only the deleted workflows from the selection when some are refused", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["workhorse", "the-stack"]);
    deleteWorkflow.mockReset();
    deleteWorkflow.mockImplementation((id) =>
      id === "workhorse" ? Promise.resolve({ deleted: "x" }) : Promise.reject(new Error("409")),
    );

    expect(await store.deleteSelected()).toMatchObject({ done: 1, refused: 1 });
    // The pill counts what is still there, not the deleted card.
    expect(store.selectedKeys).toEqual(["the-stack"]);
  });
});

describe("the Checkpoint filter", () => {
  it("lists and finds a two-loader workflow under each of its base models", async () => {
    const wan = (id, ...names) => ({
      id,
      name: id,
      models: names.map((name) => ({ name, kind: "unet" })),
    });
    listWorkflowCards.mockResolvedValue({
      cards: [wan("pair", "wan_high", "wan_low"), wan("low-only", "wan_low")],
    });
    const store = useWorkflowsStore();
    await store.fetchCards();
    const counts = store.filterOptions.checkpoints.map((c) => [c.id, c.count]);
    expect(Object.fromEntries(counts)).toEqual({ wan_low: 2, wan_high: 1 });
    store.setFilters({ checkpoint: "wan_low" });
    expect(store.filteredCards.map((card) => card.id)).toEqual(["pair", "low-only"]);
  });
});

describe("the Checkpoint filter's labels", () => {
  it("filters by file and names the option as the shelf does", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        {
          id: "titled",
          name: "titled",
          models: [{ name: "juggernautXL_v9", title: "Juggernaut XL", kind: "checkpoint" }],
        },
      ],
    });
    const store = useWorkflowsStore();
    await store.fetchCards();
    expect(store.filterOptions.checkpoints).toEqual([
      { id: "juggernautXL_v9", label: "Juggernaut XL", count: 1 },
    ]);
  });
});

// ── workflows_changed from the socket ──────────────────────────────────
describe("onWorkflowsChanged", () => {
  it("follows a retired id to its heir and re-reads the grid", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["workhorse", "the-stack"]);
    listWorkflowCards.mockClear();

    store.onWorkflowsChanged({
      reason: "regrouped",
      renamed: { workhorse: "auto:heir", "the-stack": "auto:heir" },
    });
    await Promise.resolve();

    // Both retired into one heir: selected once, not twice.
    expect(store.selectedKeys).toEqual(["auto:heir"]);
    expect(listWorkflowCards).toHaveBeenCalledTimes(1);
  });

  it("leaves the selection alone without a rename, and says recipes moved", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["workhorse"]);
    const epoch = store.recipesEpoch;

    store.onWorkflowsChanged({ reason: "recipes", renamed: {} });

    expect(store.selectedKeys).toEqual(["workhorse"]);
    expect(store.recipesEpoch).toBe(epoch + 1);
  });
});

describe("onWorkflowsChanged with reason pictures", () => {
  it("re-reads only the named card and keeps every other object", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    const before = [...store.cards];
    listWorkflowCards.mockClear();
    getWorkflowCard.mockReset();
    const fresh = { ...CARDS[2], picture_count: 91, covers: [{ url: "/new" }] };
    getWorkflowCard.mockResolvedValue({ card: fresh });

    await store.onWorkflowsChanged({ reason: "pictures", keys: ["workhorse"] });
    await new Promise((resolve) => setTimeout(resolve));

    expect(getWorkflowCard).toHaveBeenCalledWith("workhorse");
    expect(listWorkflowCards).not.toHaveBeenCalled();
    expect(store.cards.find((card) => card.id === "workhorse")).toEqual(fresh);
    for (const card of store.cards.filter((c) => c.id !== "workhorse")) {
      expect(before).toContain(card);
    }
  });

  it("re-reads the whole grid rather than race a refresh still on the wire", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    listWorkflowCards.mockClear();
    getWorkflowCard.mockReset();
    let answerOld;
    getWorkflowCard.mockReturnValueOnce(
      new Promise((resolve) => {
        answerOld = resolve;
      }),
    );

    store.onWorkflowsChanged({ reason: "pictures", keys: ["workhorse"] });
    store.onWorkflowsChanged({ reason: "pictures", keys: ["workhorse"] });
    await new Promise((resolve) => setTimeout(resolve));
    expect(listWorkflowCards).toHaveBeenCalledTimes(1);

    // The first read lands late, with older data: it must not be swapped in.
    answerOld({ card: { ...CARDS[2], picture_count: 1 } });
    await new Promise((resolve) => setTimeout(resolve));
    expect(
      store.cards.find((card) => card.id === "workhorse").picture_count,
    ).toBe(90);
  });

  it("re-reads the whole grid for a card it does not draw", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    listWorkflowCards.mockClear();
    getWorkflowCard.mockReset();

    store.onWorkflowsChanged({ reason: "pictures", keys: ["auto:one-off"] });
    await Promise.resolve();

    expect(getWorkflowCard).not.toHaveBeenCalled();
    expect(listWorkflowCards).toHaveBeenCalledTimes(1);
  });
});

describe("onWorkflowsChanged across a burst", () => {
  it("follows a chain of renames to its end", async () => {
    const store = useWorkflowsStore();
    await store.fetchCards();
    store.selectRange(["workhorse"]);
    store.onWorkflowsChanged({
      reason: "regrouped",
      renamed: { workhorse: "auto:b", "auto:b": "auto:c" },
    });
    expect(store.selectedKeys).toEqual(["auto:c"]);
  });
});
