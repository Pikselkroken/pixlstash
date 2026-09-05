import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/dedup", () => ({
  getPolicy: vi.fn(),
  listGroups: vi.fn(),
  getCounts: vi.fn(),
  startScan: vi.fn(),
  stackGroup: vi.fn(),
  keepGroupSeparate: vi.fn(),
  applyVerdictBatch: vi.fn(),
  reopenGroup: vi.fn(),
  autoStackExact: vi.fn(),
  // The third page's contract (design D5). It is lazy: a cold all-stack score
  // must not occupy the database worker during ordinary queue startup.
  listMixedStacks: vi.fn(),
  splitMixedStack: vi.fn(),
  unstackMixedStack: vi.fn(),
  keepMixedStack: vi.fn(),
  clearMixedStackKeep: vi.fn(),
  GLOBAL_SCOPE: "global",
}));

// The dedup store narrates a recorded verdict through the shared operation
// store's refresh-and-narrate pipeline; the mock observes that call without
// dragging the real store's network reads into these tests.
vi.mock("./useOperationStore", () => {
  const refresh = vi.fn();
  return { useOperationStore: () => ({ refresh }) };
});

import {
  getPolicy,
  listGroups,
  getCounts,
  startScan,
  stackGroup,
  keepGroupSeparate,
  applyVerdictBatch,
  reopenGroup,
  autoStackExact,
  listMixedStacks,
  splitMixedStack,
  unstackMixedStack,
  keepMixedStack,
  clearMixedStackKeep,
} from "../api/dedup";
import { useOperationStore } from "./useOperationStore";
// The refusal readers the view uses, asserted here on the rejection the store
// carried up: a 423 that reaches the view unreadable is a named refusal that
// silently degrades to the generic sentence.
import { lockedPictureIds, serverDetail } from "../utils/dedup";
import {
  useDedupStore,
  scopeKey,
  QUEUE_PAGE_SIZE,
  SELECT_ALL_MAX,
} from "./useDedupStore";

/** The bounds `GET /dedup/policy` publishes, as the shipped backend does. */
const BOUNDS = {
  min_threshold: 0.65,
  max_threshold: 0.99999,
  tiers: ["exact", "near", "embedding"],
  always_on_tiers: ["exact"],
  tier_requires: { exact: null, near: "exact", embedding: "near" },
  scope_types: ["global", "project", "set", "character", "folder"],
  verdicts: ["stacked", "keep_separate"],
  max_page_size: 200,
};

/** A queue group in the backend's shape, with `n` candidates of falling size. */
function group(signature, n = 2, over = {}) {
  const base = Number(signature.replace(/\D/g, "")) * 100;
  return {
    signature,
    tier: "near",
    confidence: 0.9,
    member_count: n,
    cover_picture_id: null,
    why: [],
    candidates: Array.from({ length: n }, (_, i) => ({
      picture_id: base + i,
      width: 4000 - i * 1000,
      height: 3000 - i * 750,
      megapixels: ((4000 - i * 1000) * (3000 - i * 750)) / 1e6,
      tag_count: 0,
      score: 0,
      format: "JPEG",
      is_raw: false,
      created_at: "2026-05-12T14:22:00Z",
    })),
    ...over,
  };
}

/** The ids of a group's candidates, in order. */
const idsOf = (g) => g.candidates.map((c) => c.picture_id);

function servePage(groups, over = {}) {
  listGroups.mockResolvedValue({
    groups,
    total: groups.length,
    offset: 0,
    limit: QUEUE_PAGE_SIZE,
    scan: { status: "complete", scanned_pictures: 10, total_pictures: 10 },
    ...over,
  });
}

beforeEach(() => {
  // Filter and size selections are remembered in localStorage; a test that
  // sets one must not leak it into the next test's fresh store.
  window.localStorage.clear();
  setActivePinia(createPinia());
  vi.spyOn(console, "warn").mockImplementation(() => {});
  for (const fn of [
    getPolicy,
    listGroups,
    getCounts,
    startScan,
    stackGroup,
    keepGroupSeparate,
    applyVerdictBatch,
    reopenGroup,
    autoStackExact,
    listMixedStacks,
    splitMixedStack,
    unstackMixedStack,
    keepMixedStack,
    clearMixedStackKeep,
  ]) {
    fn.mockReset();
  }
  useOperationStore().refresh.mockReset();
  getPolicy.mockResolvedValue({
    defaults: { near_enabled: false, embedding_enabled: false, threshold: 0.9 },
    bounds: BOUNDS,
  });
  getCounts.mockResolvedValue({
    unresolved_groups: 0,
    by_tier: {},
    scopes: [],
  });
  // An empty Mixed stacks list is the default answer when the page is opened.
  listMixedStacks.mockResolvedValue({
    threshold: 0.9,
    total: 0,
    kept_total: 0,
    live_stack_count: 0,
    offset: 0,
    limit: 100,
    next_offset: null,
    stacks: [],
  });
});

describe("useDedupStore - the policy", () => {
  // Every bound the UI renders comes from here. A threshold stated in the
  // client as well would be the same number in two places that can drift.
  it("loads the bounds and adopts the server's default threshold", async () => {
    const store = useDedupStore();
    await store.loadPolicy();
    expect(store.bounds.min_threshold).toBe(0.65);
    expect(store.threshold).toBe(0.9);
    expect(store.policyLoaded).toBe(true);
  });

  it("loads the policy once", async () => {
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadPolicy();
    expect(getPolicy).toHaveBeenCalledTimes(1);
  });

  it("builds the tier rows from the server's list", async () => {
    const store = useDedupStore();
    await store.loadPolicy();
    store.byTier = { exact: 1204, near: 96, embedding: 9 };
    const rows = store.tierRows;
    expect(rows.map((r) => r.id)).toEqual(["exact", "near", "embedding"]);
    expect(rows[0].locked).toBe(true);
    expect(rows[0].enabled).toBe(true);
    expect(rows[1].requires).toBe("exact");
    expect(rows[2].count).toBe(9);
  });
});

describe("useDedupStore - loading the queue", () => {
  it("loads the first page and focuses the first group", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.groups).toHaveLength(2);
    expect(store.focusIndex).toBe(0);
    expect(listGroups).toHaveBeenCalledWith({
      nearEnabled: false,
      embeddingEnabled: false,
      decided: false,
      // The Decided page's verdict gate; empty is every decision, and it is
      // sent empty from the open queue, whose groups carry no verdict at all.
      verdicts: [],
      scopeType: "global",
      scopeId: null,
      offset: 0,
      limit: QUEUE_PAGE_SIZE,
    });
  });

  // The queue opens on whatever has been found so far; the banner reports the
  // rest. Blocking on a full pass is the thing this feature exists to avoid.
  it("adopts partial scan progress alongside a partial queue", async () => {
    servePage([group("g1")], {
      scan: {
        status: "running",
        scanned_pictures: 6200,
        total_pictures: 12400,
      },
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.isScanning).toBe(true);
    expect(store.scan.percent).toBe(50);
  });

  // The server reports pictures and buckets but never a percentage.
  it("derives the percentage the server does not send", async () => {
    servePage([], {
      scan: { status: "running", scanned_pictures: 1, total_pictures: 4 },
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.scan.percent).toBe(25);
  });

  // Tier 2 streams groups in per candidate bucket, so a scope whose picture
  // total is not known yet still has honest progress to report.
  it("falls back to bucket progress when no picture total is known", async () => {
    servePage([], {
      scan: {
        status: "running",
        scanned_pictures: 0,
        total_pictures: 0,
        scanned_buckets: 3,
        total_buckets: 12,
      },
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.scan.percent).toBe(25);
  });

  it("reports an empty queue rather than a stale focus", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.hasGroups).toBe(false);
    expect(store.focusIndex).toBe(-1);
  });

  it("clears the list when the load fails", async () => {
    listGroups.mockRejectedValue(new Error("boom"));
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.groups).toEqual([]);
    expect(store.focusIndex).toBe(-1);
    expect(store.hasMore).toBe(false);
    expect(store.error).toBeInstanceOf(Error);
  });
});

describe("useDedupStore - paging", () => {
  it("appends the next page at the next offset", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 2,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.hasMore).toBe(true);
    listGroups.mockResolvedValueOnce({
      groups: [group("g2")],
      total: 2,
      scan: {},
    });
    await store.loadMore();
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2"]);
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 1 }),
    );
    expect(store.hasMore).toBe(false);
  });

  // Offset paging over a table a scan is still inserting into can re-serve a
  // group the client already holds. A duplicated row could be resolved twice,
  // and the second verdict would 400.
  it("drops a group an offset page repeats", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1"), group("g2")],
      total: 4,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockResolvedValueOnce({
      groups: [group("g2"), group("g3")],
      total: 4,
      scan: {},
    });
    await store.loadMore();
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2", "g3"]);
  });

  // The server counted the rows it served even though this client discarded
  // one, so the offset advances by the page's full length or the next page
  // re-serves the same window forever.
  it("advances the offset by the served page length, not the kept one", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1"), group("g2")],
      total: 6,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockResolvedValueOnce({
      groups: [group("g2"), group("g3")],
      total: 6,
      scan: {},
    });
    await store.loadMore();
    listGroups.mockResolvedValueOnce({ groups: [], total: 6, scan: {} });
    await store.loadMore();
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 4 }),
    );
  });

  it("does not page past the end", async () => {
    servePage([group("g1")], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockClear();
    await store.loadMore();
    expect(listGroups).not.toHaveBeenCalled();
  });

  // A total that shrank under a concurrent verdict would otherwise leave the
  // read-ahead looping on an empty page.
  it("stops when a page comes back empty whatever the total says", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 99,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockResolvedValueOnce({ groups: [], total: 99, scan: {} });
    await store.loadMore();
    expect(store.hasMore).toBe(false);
  });

  // A keyset cursor over the queue's ordering cannot re-serve or skip a group
  // while a scan inserts rows, so it is the primary path the moment the server
  // publishes one.
  it("pages from the cursor once the server serves one", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 3,
      next_cursor: "c1",
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.nextCursor).toBe("c1");
    expect(store.hasMore).toBe(true);

    listGroups.mockResolvedValueOnce({
      groups: [group("g2")],
      total: 3,
      next_cursor: "c2",
      scan: {},
    });
    await store.loadMore();
    const args = listGroups.mock.calls.at(-1)[0];
    expect(args.cursor).toBe("c1");
    expect(args.offset).toBeUndefined();
    expect(store.nextCursor).toBe("c2");
  });

  // A cursor outranks the offset arithmetic in the other direction too: a
  // `total` that has not caught up with a running scan must not end the queue
  // while the server is still handing out cursors.
  it("keeps paging on a cursor even when the total says it is done", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 1,
      next_cursor: "c1",
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.hasMore).toBe(true);
    listGroups.mockResolvedValueOnce({
      groups: [group("g2")],
      total: 1,
      next_cursor: "c2",
      scan: {},
    });
    await store.loadMore();
    expect(store.groups).toHaveLength(2);
  });

  // A cursor server that runs out mid-queue hands the offset path back a
  // consistent position, so the fallback is seamless in that direction as well.
  it("hands the offset path a correct position when the cursor stops", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1"), group("g2")],
      total: 9,
      next_cursor: null,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.nextCursor).toBe(null);
    expect(store.hasMore).toBe(true);
    listGroups.mockResolvedValueOnce({
      groups: [group("g3")],
      total: 9,
      scan: {},
    });
    await store.loadMore();
    expect(listGroups.mock.calls.at(-1)[0].offset).toBe(2);
  });

  it("stops when the cursor runs out", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 2,
      next_cursor: "c1",
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockResolvedValueOnce({
      groups: [group("g2")],
      total: 2,
      next_cursor: null,
      scan: {},
    });
    await store.loadMore();
    expect(store.hasMore).toBe(false);
    expect(store.nextCursor).toBe(null);
  });

  // The fallback has to be seamless in both directions: a server with no
  // cursor pages exactly as before, mitigations and all.
  it("falls back to the offset path when no cursor is served", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 2,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.nextCursor).toBe(null);
    listGroups.mockResolvedValueOnce({
      groups: [group("g2")],
      total: 2,
      scan: {},
    });
    await store.loadMore();
    const args = listGroups.mock.calls.at(-1)[0];
    expect(args.offset).toBe(1);
    expect(args.cursor).toBeUndefined();
  });

  // A cursor names a position in the ordering, not a count of rows before it,
  // so resolving a group must not shift it the way it shifts an offset.
  it("leaves the cursor alone when a verdict removes a group", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1"), group("g2")],
      total: 9,
      next_cursor: "c1",
      scan: {},
    });
    stackGroup.mockResolvedValue({});
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.stack(store.groups[0]);
    listGroups.mockResolvedValue({ groups: [], total: 8, scan: {} });
    await store.loadMore();
    expect(listGroups.mock.calls.at(-1)[0].cursor).toBe("c1");
  });

  // A first page is always offset 0: a cursor is a position inside one
  // ordering, and the policy or the scope may just have changed under it.
  it("restarts from the top rather than reusing a cursor", async () => {
    listGroups.mockResolvedValue({
      groups: [group("g1")],
      total: 1,
      next_cursor: "c1",
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.loadFirstPage();
    const args = listGroups.mock.calls.at(-1)[0];
    expect(args.offset).toBe(0);
    expect(args.cursor).toBeUndefined();
  });

  // The keyboard is the primary way through the queue, so the read-ahead is
  // driven by the focus rather than by scrolling.
  it("fetches ahead when the focus walks near the tail", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1"), group("g2"), group("g3"), group("g4")],
      total: 40,
      scan: {},
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockClear();
    listGroups.mockResolvedValue({ groups: [], total: 40, scan: {} });
    store.setFocus(0);
    expect(listGroups).not.toHaveBeenCalled();
    store.setFocus(2);
    expect(listGroups).toHaveBeenCalledTimes(1);
  });
});

describe("useDedupStore - the focus", () => {
  it("clamps the focus to the list", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    const store = useDedupStore();
    await store.loadFirstPage();
    store.focusPrev();
    expect(store.focusIndex).toBe(0);
    store.setFocus(99);
    expect(store.focusIndex).toBe(1);
    store.focusNext();
    expect(store.focusIndex).toBe(1);
  });

  it("reports no focus for an empty queue", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadFirstPage();
    store.setFocus(0);
    expect(store.focusIndex).toBe(-1);
    expect(store.focusedGroup).toBe(null);
  });
});

describe("useDedupStore - jumping to the true end (focusEnd)", () => {
  // The regression this pins: End focused the last LOADED row, so on a paging
  // queue it had to be pressed once per page to actually reach the end. The
  // total is known a priori, so one gesture must land on the real last group.
  // A SMALL gap (at most two browsing pages) is chased in sequence rather
  // than jumped: rebasing the window for one missing page is churn.
  it("chases a small gap in and lands on the true last group without rebasing", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 55 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockClear();

    let served = 20;
    listGroups.mockImplementation(async ({ limit }) => {
      const size = Math.min(limit, 55 - served);
      const page = Array.from({ length: size }, (_, i) =>
        group(`g${served + i + 1}`),
      );
      served += size;
      return { groups: page, total: 55, offset: served, limit };
    });

    await store.focusEnd();
    expect(store.groups).toHaveLength(55);
    expect(store.focusIndex).toBe(54);
    expect(store.hasMore).toBe(false);
    expect(store.endChaseActive).toBe(false);
    // No rebase for a small gap: the window stays anchored at the top.
    expect(store.windowStart).toBe(0);
    // At the server's page size, like Ctrl+A: one request for the missing 35.
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups.mock.calls[0][0].limit).toBe(200);
  });

  it("behaves exactly like the old End when everything is loaded", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockClear();
    await store.focusEnd();
    expect(store.focusIndex).toBe(1);
    expect(store.endChaseActive).toBe(false);
    expect(listGroups).not.toHaveBeenCalled();
  });

  it("does nothing on an empty queue", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockClear();
    await store.focusEnd();
    expect(store.focusIndex).toBe(-1);
    expect(store.endChaseActive).toBe(false);
    expect(listGroups).not.toHaveBeenCalled();
  });

  // A stale chase that yanks the focus to the bottom seconds after the user
  // went somewhere else would be worse than the bug it fixes.
  it("a focus move mid-chase cancels it and the user's position wins", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 60 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockClear();

    let release;
    listGroups.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const chase = store.focusEnd();
    expect(store.endChaseActive).toBe(true);
    // Home, mid-chase: the user took their place back.
    store.setFocus(0);
    expect(store.endChaseActive).toBe(false);

    release({ groups: [group("g21")], total: 60 });
    await chase;
    // The page already in flight still lands, but the focus stays where the
    // user put it and no further pages are chased.
    expect(store.focusIndex).toBe(0);
    expect(store.groups).toHaveLength(21);
    expect(listGroups).toHaveBeenCalledTimes(1);
  });

  it("a queue reload cancels the chase", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 60 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockImplementation(() => new Promise(() => {}));
    store.focusEnd();
    expect(store.endChaseActive).toBe(true);
    // A tier toggle, a rescan, a scope change: they all rebuild the list
    // through loadFirstPage, and a chase over the old list must die with it.
    store.loadFirstPage();
    expect(store.endChaseActive).toBe(false);
  });

  // A server that claims 900 rows and serves an empty tail page (extreme
  // drift) must not wedge the jump: it gives up in bounded requests and the
  // focus lands on the last row actually held.
  it("terminates on an empty tail page and lands on the best-known end", async () => {
    servePage([group("g1"), group("g2")], { total: 900 });
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockClear();
    listGroups.mockResolvedValue({
      groups: [],
      total: 900,
      offset: 880,
      limit: 20,
    });

    await store.focusEnd();
    expect(store.focusIndex).toBe(1);
    expect(store.windowStart).toBe(0);
    expect(store.endChaseActive).toBe(false);
    // The served total still names the requested tail, so there is no better
    // offset to re-aim at: ONE tail request, then give up. (The second call
    // is the ordinary focus-near-tail read-ahead, from the top window.)
    const offsets = listGroups.mock.calls.map((c) => c[0].offset);
    expect(offsets.filter((o) => o === 880)).toHaveLength(1);
    expect(listGroups).toHaveBeenCalledTimes(2);
    expect(offsets[1]).toBe(2);
  });

  // The user's words: "we know how far down we have to go so we should know
  // which cards we have to fetch". Over a large gap End is random access -
  // ONE offset request for the last page, no walk through the middle.
  it("End over a large gap fetches the tail page directly and rebases", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 200 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    // A live selection points at rows the rebase drops; it must not survive
    // to silently act on rows the client no longer holds.
    store.toggleSelected(1);
    expect(store.selectionCount).toBe(2);
    listGroups.mockClear();
    listGroups.mockImplementation(async ({ offset, limit }) => ({
      groups: Array.from({ length: limit }, (_, i) =>
        group(`g${offset + i + 1}`),
      ),
      total: 200,
      offset,
      limit,
    }));

    await store.focusEnd();
    expect(listGroups).toHaveBeenCalledTimes(1);
    const args = listGroups.mock.calls[0][0];
    expect(args.offset).toBe(180);
    expect(args.limit).toBe(QUEUE_PAGE_SIZE);
    // The server 400s a cursor and an offset together; a jump is offset-only.
    expect(args.cursor).toBeUndefined();
    expect(store.windowStart).toBe(180);
    expect(store.groups).toHaveLength(20);
    expect(store.groups[0].signature).toBe("g181");
    expect(store.focusIndex).toBe(199);
    expect(store.focusedGroup.signature).toBe("g200");
    expect(store.hasMore).toBe(false);
    expect(store.endChaseActive).toBe(false);
    expect(store.selectionCount).toBe(0);
  });

  it("pages upwards from a jumped tail, keeping the focus on its group", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 200 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockImplementation(async ({ offset, limit }) => ({
      groups: Array.from({ length: limit }, (_, i) =>
        group(`g${offset + i + 1}`),
      ),
      total: 200,
      offset,
      limit,
    }));
    await store.focusEnd();
    expect(store.windowStart).toBe(180);

    listGroups.mockClear();
    await store.loadPrevious();
    expect(listGroups).toHaveBeenCalledTimes(1);
    const args = listGroups.mock.calls[0][0];
    expect(args.offset).toBe(160);
    expect(args.limit).toBe(20);
    expect(args.cursor).toBeUndefined();
    expect(store.windowStart).toBe(160);
    expect(store.groups).toHaveLength(40);
    expect(store.groups[0].signature).toBe("g161");
    // The prepend fills spacer above the cursor; the cursor does not move.
    expect(store.focusIndex).toBe(199);
    expect(store.focusedGroup.signature).toBe("g200");
  });

  it("Home after an End jump resets to the normal top window", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 200 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockImplementation(async ({ offset = 0, limit }) => ({
      groups: Array.from({ length: limit }, (_, i) =>
        group(`g${offset + i + 1}`),
      ),
      total: 200,
      offset,
      limit,
    }));
    await store.focusEnd();
    expect(store.windowStart).toBe(180);

    listGroups.mockClear();
    await store.focusStart();
    const args = listGroups.mock.calls[0][0];
    expect(args.offset).toBe(0);
    expect(args.cursor).toBeUndefined();
    expect(store.windowStart).toBe(0);
    expect(store.focusIndex).toBe(0);
    expect(store.groups[0].signature).toBe("g1");
    // A top-anchored Home is a plain focus move, no reload.
    listGroups.mockClear();
    await store.focusStart();
    expect(listGroups).not.toHaveBeenCalled();
  });

  // A running scan can move the goalposts between the count and the fetch:
  // the jump re-aims once from the served total and lands on the last row
  // actually received, in bounded requests.
  it("re-aims once when the total shrank under the jump", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 200 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockClear();
    listGroups.mockImplementation(async ({ offset, limit }) => {
      const totalNow = 150;
      const size = Math.max(0, Math.min(limit, totalNow - offset));
      return {
        groups: Array.from({ length: size }, (_, i) =>
          group(`g${offset + i + 1}`),
        ),
        total: totalNow,
        offset,
        limit,
      };
    });

    await store.focusEnd();
    expect(listGroups).toHaveBeenCalledTimes(2);
    expect(listGroups.mock.calls[0][0].offset).toBe(180);
    expect(listGroups.mock.calls[1][0].offset).toBe(130);
    expect(store.windowStart).toBe(130);
    expect(store.total).toBe(150);
    expect(store.focusIndex).toBe(149);
    expect(store.hasMore).toBe(false);
    expect(store.endChaseActive).toBe(false);
  });

  it("Ctrl+A after an End jump still selects the whole queue", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 100 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    listGroups.mockImplementation(async ({ offset, limit }) => {
      const size = Math.max(0, Math.min(limit, 100 - offset));
      return {
        groups: Array.from({ length: size }, (_, i) =>
          group(`g${offset + i + 1}`),
        ),
        total: 100,
        offset,
        limit,
      };
    });
    await store.focusEnd();
    expect(store.windowStart).toBe(80);

    const result = await store.selectAll();
    expect(result).toEqual({ selected: 100, total: 100, truncated: false });
    expect(store.selectionCount).toBe(100);
    expect(store.windowStart).toBe(0);
  });
});

describe("useDedupStore - cover and exclusion", () => {
  // The server runs the same formula and ships its answer on the group.
  it("takes the server's cover preselection", async () => {
    servePage([group("g1", 3, { cover_picture_id: 102 })], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.coverIdFor(store.groups[0])).toBe(102);
  });

  it("falls back to the local formula when no preselection arrives", async () => {
    servePage([group("g1", 3)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.coverIdFor(g)).toBe(idsOf(g)[0]);
  });

  it("lets the user override the preselection", async () => {
    servePage([group("g1", 3)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, idsOf(g)[2]);
    expect(store.coverIdFor(g)).toBe(idsOf(g)[2]);
  });

  it("counts the stack down as candidates are excluded", async () => {
    servePage([group("g1", 3)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.stackSizeFor(g)).toBe(3);
    store.toggleExcluded(g, idsOf(g)[2]);
    expect(store.stackSizeFor(g)).toBe(2);
    store.toggleExcluded(g, idsOf(g)[2]);
    expect(store.stackSizeFor(g)).toBe(3);
  });

  // X is a one-key action with no confirmation, so it must never leave the
  // group in a state the Stack button cannot act on. The server refuses a
  // one-member stack outright, so the floor is two INCLUDED members: a
  // two-candidate group accepts no exclusion at all, and letting it fall to one
  // would make the Stack the row still offers a guaranteed 400.
  it("refuses an exclusion that would leave a single member", async () => {
    servePage([group("g1", 2)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.toggleExcluded(g, idsOf(g)[1])).toBe(false);
    expect(store.toggleExcluded(g, idsOf(g)[0])).toBe(false);
    expect(store.stackSizeFor(g)).toBe(2);
    expect(store.excludedFor("g1")).toEqual([]);
    expect(store.isAtStackFloor(g)).toBe(true);
  });

  it("allows exclusions down to the floor and no further", async () => {
    servePage([group("g1", 4)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.toggleExcluded(g, idsOf(g)[3])).toBe(true);
    expect(store.toggleExcluded(g, idsOf(g)[2])).toBe(true);
    expect(store.stackSizeFor(g)).toBe(2);
    expect(store.toggleExcluded(g, idsOf(g)[1])).toBe(false);
    expect(store.stackSizeFor(g)).toBe(2);
    // Putting one back is never refused: the floor only guards the way down.
    expect(store.toggleExcluded(g, idsOf(g)[3])).toBe(true);
    expect(store.stackSizeFor(g)).toBe(3);
  });

  // The server rejects a cover that is not an included member, so this is a
  // correctness guard rather than a nicety.
  it("moves the cover off a candidate the user excludes", async () => {
    servePage([group("g1", 3)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    const first = idsOf(g)[0];
    expect(store.coverIdFor(g)).toBe(first);
    store.toggleExcluded(g, first);
    expect(store.coverIdFor(g)).toBe(idsOf(g)[1]);
  });
});

describe("useDedupStore - verdicts and auto-advance", () => {
  it("stacks with the cover and the exclusions in force", async () => {
    servePage([group("g1", 3), group("g2")], { total: 2 });
    stackGroup.mockResolvedValue({ stack_id: 7, batch_id: "b1" });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, idsOf(g)[1]);
    store.toggleExcluded(g, idsOf(g)[2]);
    await store.stack(g);
    expect(stackGroup).toHaveBeenCalledWith("g1", {
      coverPictureId: idsOf(g)[1],
      excludedPictureIds: [idsOf(g)[2]],
      batchId: undefined,
    });
  });

  // Removing the row at the focused index means the next group has already
  // slid into it, so the focus stays put and the queue advances by itself.
  it("auto-advances to the next group after a verdict", async () => {
    servePage([group("g1"), group("g2"), group("g3")], { total: 3 });
    stackGroup.mockResolvedValue({});
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.stack(store.groups[0]);
    expect(store.groups.map((g) => g.signature)).toEqual(["g2", "g3"]);
    expect(store.focusIndex).toBe(0);
    expect(store.focusedGroup.signature).toBe("g2");
  });

  it("walks the focus back when the last group is resolved", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    keepGroupSeparate.mockResolvedValue({ verdict: "keep_separate" });
    const store = useDedupStore();
    await store.loadFirstPage();
    store.setFocus(1);
    await store.keepSeparate(store.groups[1]);
    expect(store.focusIndex).toBe(0);
  });

  it("lands on the done state when the last group is resolved", async () => {
    servePage([group("g1")], { total: 1 });
    stackGroup.mockResolvedValue({});
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.stack(store.groups[0]);
    expect(store.hasGroups).toBe(false);
    expect(store.focusIndex).toBe(-1);
    expect(store.doneCount).toBe(1);
  });

  // A page can be emptied faster than the read-ahead refills it. Showing the
  // done state while the server still holds thousands of groups is the one lie
  // a to-do count cannot afford.
  it("refills rather than showing the done state early", async () => {
    listGroups.mockResolvedValueOnce({
      groups: [group("g1")],
      total: 50,
      scan: {},
    });
    stackGroup.mockResolvedValue({});
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockResolvedValueOnce({
      groups: [group("g9")],
      total: 49,
      scan: {},
    });
    await store.stack(store.groups[0]);
    await Promise.resolve();
    expect(listGroups).toHaveBeenCalledTimes(2);
  });

  it("ticks the sidebar count down with each verdict", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    stackGroup.mockResolvedValue({});
    keepGroupSeparate.mockResolvedValue({});
    const store = useDedupStore();
    await store.refreshCounts();
    getCounts.mockResolvedValue({ unresolved_groups: 2, by_tier: {} });
    await store.refreshCounts();
    await store.loadFirstPage();
    expect(store.openCount).toBe(2);
    // The optimistic tick lands with the verdict, before the reconciling
    // refetch resolves: that immediacy is the whole point of it.
    getCounts.mockImplementation(() => new Promise(() => {}));
    await store.stack(store.groups[0]);
    await store.keepSeparate(store.groups[0]);
    expect(store.openCount).toBe(0);
    expect(store.stackedCount).toBe(1);
    expect(store.separatedCount).toBe(1);
  });

  // A keep-separate mutates no picture row, so it raises no WebSocket event and
  // nothing else will ever correct the optimistic tick above. Left to drift the
  // badge is wrong in a second tab from the first verdict.
  it("reconciles the badge with the server after every verdict", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    stackGroup.mockResolvedValue({});
    keepGroupSeparate.mockResolvedValue({});
    getCounts.mockResolvedValue({ unresolved_groups: 41, by_tier: {} });
    const store = useDedupStore();
    await store.loadFirstPage();

    getCounts.mockClear();
    await store.stack(store.groups[0]);
    await Promise.resolve();
    expect(getCounts).toHaveBeenCalledTimes(1);
    expect(store.openCount).toBe(41);

    getCounts.mockClear();
    await store.keepSeparate(store.groups[0]);
    await Promise.resolve();
    expect(getCounts).toHaveBeenCalledTimes(1);
    expect(store.openCount).toBe(41);
  });

  // The reconcile must not become an unhandled rejection on a keypress, and a
  // count read that failed must not undo the verdict.
  it("survives a reconcile that fails", async () => {
    servePage([group("g1")], { total: 1 });
    stackGroup.mockResolvedValue({});
    getCounts.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(await store.stack(store.groups[0])).toBeTruthy();
    expect(store.stackedCount).toBe(1);
  });

  // A failed verdict must not consume the group: the user has to be able to
  // try again on the row they were looking at.
  it("keeps the group when the verdict fails", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    stackGroup.mockRejectedValue(new Error("409"));
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.stack(store.groups[0]);
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2"]);
    expect(store.stackedCount).toBe(0);
  });

  it("refuses a second verdict while one is in flight", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    let release;
    stackGroup.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const store = useDedupStore();
    await store.loadFirstPage();
    const first = store.stack(store.groups[0]);
    await store.stack(store.groups[1]);
    expect(stackGroup).toHaveBeenCalledTimes(1);
    release({});
    await first;
  });

  it("forgets a resolved group's cover and exclusions", async () => {
    servePage([group("g1", 3), group("g2")], { total: 2 });
    stackGroup.mockResolvedValue({});
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, idsOf(g)[1]);
    store.toggleExcluded(g, idsOf(g)[2]);
    await store.stack(g);
    expect(store.coverChoices.g1).toBeUndefined();
    expect(store.exclusions.g1).toBeUndefined();
  });
});

describe("useDedupStore - the verdict receipt", () => {
  // The regression this pins: the dedup verdict service emits no WebSocket
  // event, so the echo-driven receipt pipeline never fired and a stack
  // verdict produced no undo pill. The verdict RESPONSE now triggers the
  // same refresh-and-narrate path; batch_id is the marker that an operation
  // row was actually recorded.
  it("asks the operation store to narrate a recorded stack verdict", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    stackGroup.mockResolvedValue({ stack_id: 7, batch_id: "srv-1" });
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.stack(store.groups[0]);
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
    expect(useOperationStore().refresh).toHaveBeenCalledWith({
      narrate: true,
    });
  });

  it("narrates a bulk stack once - one gesture, one receipt", async () => {
    servePage([group("g1"), group("g2"), group("g3")], { total: 3 });
    applyVerdictBatch.mockResolvedValue({
      batch_id: "cli-1",
      results: [{ skipped: [] }, { skipped: [] }],
    });
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    store.toggleSelected(0);
    store.toggleSelected(1);
    await store.stack(store.groups[0]);
    expect(applyVerdictBatch).toHaveBeenCalledTimes(1);
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
  });

  it("stays silent when the verdict failed", async () => {
    servePage([group("g1")], { total: 1 });
    stackGroup.mockRejectedValue(new Error("locked"));
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.stack(store.groups[0]);
    expect(useOperationStore().refresh).not.toHaveBeenCalled();
  });

  // Keep-separate on an OLDER backend records no operation and returns no
  // batch_id: no receipt, exactly the behaviour shipped today.
  it("stays silent for a keep-separate that recorded nothing", async () => {
    servePage([group("g1")], { total: 1 });
    keepGroupSeparate.mockResolvedValue({ verdict: "keep_separate" });
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.keepSeparate(store.groups[0]);
    expect(useOperationStore().refresh).not.toHaveBeenCalled();
  });

  // A backend that has made keep-separate undoable mirrors the stack
  // response, batch_id included - and gets the same receipt.
  it("narrates a keep-separate the backend recorded", async () => {
    servePage([group("g1")], { total: 1 });
    keepGroupSeparate.mockResolvedValue({
      verdict: "keep_separate",
      batch_id: "srv-2",
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.keepSeparate(store.groups[0]);
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
  });

  it("narrates the bulk auto-stack's single batch", async () => {
    servePage([]);
    autoStackExact.mockResolvedValue({ batch_id: "srv-3", groups: 12 });
    const store = useDedupStore();
    await store.runAutoStack();
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
  });
});

describe("useDedupStore - reopen", () => {
  it("reopens a decided group and reloads the queue", async () => {
    servePage([], { total: 0 });
    reopenGroup.mockResolvedValue({
      signature: "g1",
      previous_verdict: "keep_separate",
      group_returned_to_queue: true,
      batch_id: null,
    });
    const store = useDedupStore();
    listGroups.mockClear();
    const result = await store.reopen("g1");
    expect(reopenGroup).toHaveBeenCalledWith("g1", { batchId: undefined });
    expect(result.group_returned_to_queue).toBe(true);
    expect(listGroups).toHaveBeenCalledTimes(1);
  });

  it("reports nothing when the reopen fails", async () => {
    reopenGroup.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    expect(await store.reopen("g1")).toBe(null);
  });

  // Clearing a stacked decision unstacks pictures, which the backend records
  // as one dedup.reopen operation; batch_id is the marker, exactly as for the
  // verdict paths, and it earns the same undo receipt.
  it("narrates a clear that recorded an operation", async () => {
    servePage([], { total: 0 });
    reopenGroup.mockResolvedValue({
      signature: "g1",
      previous_verdict: "stacked",
      group_returned_to_queue: true,
      batch_id: "srv-9",
      unstacked_picture_ids: [1, 2],
    });
    const store = useDedupStore();
    await store.reopen("g1");
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
    expect(useOperationStore().refresh).toHaveBeenCalledWith({ narrate: true });
  });

  // A clear that touched no picture (keep-separate, or a stack the user
  // already dissolved) records nothing: no batch_id, no receipt.
  it("stays silent for a clear that recorded nothing", async () => {
    servePage([], { total: 0 });
    reopenGroup.mockResolvedValue({
      signature: "g1",
      previous_verdict: "keep_separate",
      group_returned_to_queue: true,
      batch_id: null,
    });
    const store = useDedupStore();
    await store.reopen("g1");
    expect(useOperationStore().refresh).not.toHaveBeenCalled();
  });
});

describe("useDedupStore - the tier gate", () => {
  it("enabling tier 3 pulls tier 2 in with it", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.setTierEnabled("embedding", true);
    expect(store.nearEnabled).toBe(true);
    expect(store.embeddingEnabled).toBe(true);
  });

  // A user must not be left on "same scene" suggestions when they step back up.
  it("disabling tier 2 drops tier 3 with it", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.setTierEnabled("embedding", true);
    await store.setTierEnabled("near", false);
    expect(store.nearEnabled).toBe(false);
    expect(store.embeddingEnabled).toBe(false);
  });

  // Tier 1 has no switch at all, so a stray call must not invent one.
  it("ignores a toggle for a tier that has no switch", async () => {
    servePage([]);
    const store = useDedupStore();
    listGroups.mockClear();
    await store.setTierEnabled("exact", false);
    expect(listGroups).not.toHaveBeenCalled();
  });

  it("reloads the queue when the gate moves", async () => {
    servePage([]);
    const store = useDedupStore();
    listGroups.mockClear();
    await store.setTierEnabled("near", true);
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ nearEnabled: true }),
    );
  });

  it("does not reload when the gate did not actually move", async () => {
    servePage([]);
    const store = useDedupStore();
    listGroups.mockClear();
    await store.setTierEnabled("near", false);
    expect(listGroups).not.toHaveBeenCalled();
  });
});

describe("useDedupStore - the decided page's verdict gate", () => {
  /** A store on the Decided page with the policy loaded. */
  async function decidedStore(over = {}) {
    servePage([], { by_verdict: { stacked: 7, keep_separate: 3 }, ...over });
    const store = useDedupStore();
    await store.loadPolicy();
    await store.toggleDecided();
    return store;
  }

  // The vocabulary is the server's, exactly as the tier rows are: a verdict it
  // adds later renders under its own id rather than vanishing from the menu.
  it("builds the rows from the server's verdicts and the page's counts", async () => {
    const store = await decidedStore();
    expect(store.verdictRows.map((r) => r.id)).toEqual([
      "stacked",
      "keep_separate",
    ]);
    expect(store.verdictRows.map((r) => r.label)).toEqual([
      "Stacked",
      "Kept separate",
    ]);
    expect(store.verdictRows.map((r) => r.count)).toEqual([7, 3]);
    expect(store.verdictRows.every((r) => r.enabled)).toBe(true);
  });

  // "Everything" is expressed by ABSENCE, never by re-listing the vocabulary:
  // a full list would also drop the verdict-less tail the server still serves.
  it("sends no filter while every verdict is shown", async () => {
    const store = await decidedStore();
    expect(store.verdictArgs).toEqual([]);
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ decided: true, verdicts: [] }),
    );
  });

  it("narrows to one verdict and reloads under the same filter", async () => {
    const store = await decidedStore();
    listGroups.mockClear();
    expect(await store.setVerdictEnabled("keep_separate", false)).toBe(true);
    expect(store.enabledVerdicts).toEqual(["stacked"]);
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ decided: true, verdicts: ["stacked"] }),
    );
  });

  // Over-filtering is its own regression: the way back has to work too.
  it("puts a hidden verdict back", async () => {
    const store = await decidedStore();
    await store.setVerdictEnabled("stacked", false);
    expect(await store.setVerdictEnabled("stacked", true)).toBe(true);
    expect(store.verdictArgs).toEqual([]);
  });

  // An empty gate can only ever render an empty page, which reads as a broken
  // queue rather than as a choice the user made.
  it("refuses to switch the last verdict off", async () => {
    const store = await decidedStore();
    await store.setVerdictEnabled("stacked", false);
    listGroups.mockClear();
    expect(await store.setVerdictEnabled("keep_separate", false)).toBe(false);
    expect(store.enabledVerdicts).toEqual(["keep_separate"]);
    expect(listGroups).not.toHaveBeenCalled();
  });

  // The counts are the MENU's, so they must survive the filter that hides
  // their rows - otherwise a hidden verdict reads as "there are none".
  it("keeps the unfiltered counts while a verdict is hidden", async () => {
    const store = await decidedStore();
    await store.setVerdictEnabled("keep_separate", false);
    expect(store.verdictRows.map((r) => r.count)).toEqual([7, 3]);
  });

  // A link carries what to SHOW; anything the server does not publish is not
  // trusted, and a selection that would empty the page falls back to all.
  it("restores a selection from the URL and ignores a bogus one", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    store.applyUrlFilters({ decided: true, verdicts: ["keep_separate"] });
    expect(store.enabledVerdicts).toEqual(["keep_separate"]);
    store.applyUrlFilters({ verdicts: ["deleted"] });
    expect(store.enabledVerdicts).toEqual(["stacked", "keep_separate"]);
  });

  // The open queue's groups carry no verdict, so the gate is state waiting for
  // the flip: moving it there must not fire a pointless request.
  it("does not reload the open queue when the gate moves", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    listGroups.mockClear();
    expect(await store.setVerdictEnabled("stacked", false)).toBe(true);
    expect(listGroups).not.toHaveBeenCalled();
  });

  // The decided page is a place the user visits, not a lens they set - and
  // neither is its gate, so arriving starts from every decision.
  it("clears the gate when the queue is opened", async () => {
    const store = await decidedStore();
    await store.setVerdictEnabled("stacked", false);
    getCounts.mockResolvedValue({
      unresolved_groups: 0,
      by_tier: {},
      scopes: [],
    });
    startScan.mockResolvedValue({ status: "complete" });
    await store.openQueue();
    expect(store.enabledVerdicts).toEqual(["stacked", "keep_separate"]);
    expect(store.decidedByVerdict).toEqual({});
  });

  // The open queue never carries the counts, so a flip back must not leave the
  // decided page's numbers standing behind a menu that no longer shows them.
  it("drops the counts on the way back to the open queue", async () => {
    const store = await decidedStore();
    expect(store.decidedByVerdict).toEqual({ stacked: 7, keep_separate: 3 });
    servePage([]);
    await store.toggleDecided();
    expect(store.decidedByVerdict).toEqual({});
  });
});

describe("useDedupStore - the threshold", () => {
  // Below the floor is a 400 by design, so the client must not send one.
  it("clamps to the server's published bounds", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.setThreshold(0.1);
    expect(store.threshold).toBe(0.65);
    await store.setThreshold(2);
    expect(store.threshold).toBe(0.99999);
  });

  it("reloads the queue with the new threshold", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    listGroups.mockClear();
    await store.setThreshold(0.8);
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ threshold: 0.8 }),
    );
  });

  it("ignores a threshold that did not move, and a non-number", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    listGroups.mockClear();
    await store.setThreshold(0.9);
    await store.setThreshold("nope");
    expect(listGroups).not.toHaveBeenCalled();
  });
});

describe("useDedupStore - scope", () => {
  it("opens a scoped queue and remembers the pill", async () => {
    servePage([group("g1")], { total: 1 });
    const store = useDedupStore();
    await store.openQueue({
      type: "set",
      id: 12,
      label: "Release Set B",
      icon: "mdi-folder-multiple-image",
    });
    expect(store.isScoped).toBe(true);
    expect(store.scopeLabel).toBe("Release Set B");
    expect(listGroups).toHaveBeenCalledWith(
      expect.objectContaining({ scopeType: "set", scopeId: 12 }),
    );
  });

  // This lane called the unscoped case "library" before the backend named it.
  it("accepts the old name for the unscoped queue", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.openQueue({ type: "library" });
    expect(store.scopeType).toBe("global");
    expect(store.isScoped).toBe(false);
  });

  // Position 3 in a set's queue and position 3 in the global one are unrelated
  // groups, so carrying the index over would drop the cursor into a row the
  // user has never seen while the treatment insists that is where Enter lands.
  it("widening back to the whole vault returns to the first group", async () => {
    servePage([group("g1"), group("g2"), group("g3")], { total: 3 });
    const store = useDedupStore();
    await store.openQueue({ type: "set", id: 12, label: "Set B" });
    store.setFocus(2);
    await store.clearScope();
    expect(store.isScoped).toBe(false);
    expect(store.scopeLabel).toBe("");
    expect(store.focusIndex).toBe(0);
  });
});

describe("useDedupStore - counts", () => {
  it("reads the badge, the tier split and the scan in one call", async () => {
    getCounts.mockResolvedValue({
      unresolved_groups: 143,
      by_tier: { exact: 1204, near: 96, embedding: 9 },
      scopes: [],
      scan: { status: "running", scanned_pictures: 1, total_pictures: 2 },
    });
    const store = useDedupStore();
    await store.refreshCounts();
    expect(store.openCount).toBe(143);
    expect(store.exactCount).toBe(1204);
    expect(store.queueOnlyCount).toBe(105);
    expect(store.isScanning).toBe(true);
    expect(store.countsLoaded).toBe(true);
  });

  it("sends the tier policy so the counts match the queue", async () => {
    const store = useDedupStore();
    await store.setTierEnabled("near", true);
    getCounts.mockClear();
    await store.refreshCounts();
    expect(getCounts).toHaveBeenCalledWith({
      policy: { nearEnabled: true, embeddingEnabled: false },
      scopes: [],
    });
  });

  it("leaves the badge alone when the count read fails", async () => {
    getCounts.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    await store.refreshCounts();
    expect(store.openCount).toBe(0);
    expect(store.countsLoaded).toBe(false);
  });

  // The badge comes back with any scoped request, so a context menu opening
  // also refreshes the sidebar and the two cannot disagree.
  it("caches a per-scope count and refreshes the badge with it", async () => {
    getCounts.mockResolvedValue({
      unresolved_groups: 143,
      by_tier: {},
      scopes: [
        {
          scope_type: "set",
          scope_id: "12",
          key: "set:12",
          unresolved_groups: 18,
        },
      ],
    });
    const store = useDedupStore();
    expect(await store.fetchScopeCount("set", 12)).toBe(18);
    expect(store.openCount).toBe(143);
    expect(await store.fetchScopeCount("set", 12)).toBe(18);
    expect(getCounts).toHaveBeenCalledTimes(1);
    expect(store.scopeCounts[scopeKey("set", 12)]).toBe(18);
  });

  // Opening the same context menu twice in a row is the common case; a second
  // round trip there shows a flicker instead of a number.
  it("shares one request between concurrent callers", async () => {
    getCounts.mockResolvedValue({
      unresolved_groups: 4,
      scopes: [
        {
          scope_type: "folder",
          scope_id: "3",
          key: "folder:3",
          unresolved_groups: 4,
        },
      ],
    });
    const store = useDedupStore();
    const [a, b] = await Promise.all([
      store.fetchScopeCount("folder", 3),
      store.fetchScopeCount("folder", 3),
    ]);
    expect(a).toBe(4);
    expect(b).toBe(4);
    expect(getCounts).toHaveBeenCalledTimes(1);
  });

  it("reports null rather than a wrong number when the read fails", async () => {
    getCounts.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    expect(await store.fetchScopeCount("project", 1)).toBe(null);
  });

  // A verdict moves every scope that contained the group, so the cache cannot
  // survive one.
  it("drops the cached scope counts after a verdict", async () => {
    servePage([group("g1")], { total: 1 });
    stackGroup.mockResolvedValue({});
    getCounts.mockResolvedValue({
      unresolved_groups: 4,
      scopes: [
        {
          scope_type: "set",
          scope_id: "12",
          key: "set:12",
          unresolved_groups: 4,
        },
      ],
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.fetchScopeCount("set", 12);
    // The reconciling refetch that follows a verdict asks for no extra scopes,
    // so it cannot refill the cache it just dropped.
    getCounts.mockResolvedValue({ unresolved_groups: 3, scopes: [] });
    await store.stack(store.groups[0]);
    await Promise.resolve();
    expect(store.scopeCounts).toEqual({});
  });
});

describe("useDedupStore - multi-select", () => {
  async function openWith(groups) {
    servePage(groups);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    return store;
  }

  it("toggles per group and ranges from the anchor", async () => {
    const store = await openWith([
      group("g1"),
      group("g2"),
      group("g3"),
      group("g4"),
    ]);
    store.toggleSelected(1);
    expect(store.isSelected("g2")).toBe(true);
    store.selectRange(3);
    expect(store.selectionCount).toBe(3);
    expect(store.isSelected("g1")).toBe(false);
    store.toggleSelected(2);
    expect(store.selectionCount).toBe(2);
    store.clearSelection();
    expect(store.selectionCount).toBe(0);
  });

  it("the first ctrl-toggle keeps the focused row selected too (grid parity)", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.setFocus(0);
    store.toggleSelected(1);
    expect(store.isSelected("g1")).toBe(true);
    expect(store.isSelected("g2")).toBe(true);
    expect(store.selectionCount).toBe(2);
  });

  it("ctrl-toggling the focused row itself just toggles it", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.setFocus(0);
    store.toggleSelected(0);
    expect(store.selectionCount).toBe(1);
    store.toggleSelected(0);
    expect(store.selectionCount).toBe(0);
  });

  it("selects every loaded group on Ctrl+A", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.selectAll();
    expect(store.selectionCount).toBe(3);
  });

  it("clears several decisions with one reload at the end", async () => {
    const store = await openWith([group("g1")]);
    reopenGroup.mockResolvedValue({ group_returned_to_queue: true });
    listGroups.mockClear();
    const result = await store.reopenMany(["a", "b", "c"]);
    expect(result).toEqual({ cleared: 3, returned: 3 });
    expect(reopenGroup).toHaveBeenCalledTimes(3);
    // reopen() reloads per call; the bulk path must reload exactly once.
    expect(listGroups).toHaveBeenCalledTimes(1);
    // One gesture, one undo step: every clear shares one client batch id.
    const ids = reopenGroup.mock.calls.map((c) => c[1].batchId);
    expect(ids[0]).toMatch(/^cli-/);
    expect(new Set(ids).size).toBe(1);
  });

  it("narrates a bulk clear once when any clear recorded an operation", async () => {
    const store = await openWith([group("g1")]);
    reopenGroup
      .mockResolvedValueOnce({ group_returned_to_queue: true, batch_id: null })
      .mockResolvedValueOnce({
        group_returned_to_queue: true,
        batch_id: "cli-x",
      });
    await store.reopenMany(["a", "b"]);
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
  });

  it("stays silent for a bulk clear that recorded nothing", async () => {
    const store = await openWith([group("g1")]);
    reopenGroup.mockResolvedValue({ group_returned_to_queue: true });
    await store.reopenMany(["a", "b"]);
    expect(useOperationStore().refresh).not.toHaveBeenCalled();
  });

  it("stacks every selected group in one atomic server request", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.toggleSelected(0);
    store.toggleSelected(2);
    applyVerdictBatch.mockResolvedValue({
      batch_id: "cli-gesture-1",
      results: [{ skipped: [] }, { skipped: [] }],
    });

    const result = await store.stack(
      store.groups.find((g) => g.signature === "g3"),
    );
    expect(result).toBeTruthy();
    expect(stackGroup).not.toHaveBeenCalled();
    expect(applyVerdictBatch).toHaveBeenCalledTimes(1);
    const [actions, options] = applyVerdictBatch.mock.calls[0];
    expect(actions.map((action) => action.signature)).toEqual(["g1", "g3"]);
    expect(actions.every((action) => action.verdict === "stacked")).toBe(true);
    expect(options.batchId).toMatch(/^cli-/);
    // The gesture is over: nothing stays selected, and the rows are gone.
    expect(store.selectionCount).toBe(0);
    expect(store.groups.map((g) => g.signature)).toEqual(["g2"]);
  });

  it("keeps the selected queue stable until the atomic bulk stack settles", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.toggleSelected(0);
    store.toggleSelected(2);
    const before = store.groups.map((g) => g.signature);
    const beforeFocus = store.focusIndex;
    let resolveBatch;
    applyVerdictBatch.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBatch = resolve;
        }),
    );

    const pending = store.stack(store.groups[2]);
    await vi.waitFor(() => expect(resolveBatch).toBeTypeOf("function"));

    // The server transaction is still in flight, so the visible gesture has
    // not landed either.
    expect(store.groups.map((g) => g.signature)).toEqual(before);
    expect(store.selectionCount).toBe(2);
    expect(store.focusIndex).toBe(beforeFocus);

    resolveBatch({ batch_id: "cli-one", results: [] });
    await pending;
    expect(store.groups.map((g) => g.signature)).toEqual(["g2"]);
    expect(store.selectionCount).toBe(0);
    expect(store.focusIndex).toBe(0);
    expect(getCounts).toHaveBeenCalledTimes(1);
    expect(useOperationStore().refresh).toHaveBeenCalledTimes(1);
  });

  it("keeps every selected group separate in one atomic request", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.toggleSelected(0);
    store.toggleSelected(1);
    applyVerdictBatch.mockResolvedValue({
      batch_id: "cli-keep-1",
      results: [{}, {}],
    });
    const result = await store.keepSeparate(store.groups[0]);
    expect(result).toBeTruthy();
    expect(keepGroupSeparate).not.toHaveBeenCalled();
    expect(applyVerdictBatch).toHaveBeenCalledTimes(1);
    const [actions, options] = applyVerdictBatch.mock.calls[0];
    expect(actions).toEqual([
      { verdict: "keep_separate", signature: "g1" },
      { verdict: "keep_separate", signature: "g2" },
    ]);
    expect(options.batchId).toMatch(/^cli-/);
    expect(store.selectionCount).toBe(0);
  });

  it("keeps the whole selection when an atomic Keep Separate is refused", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.toggleSelected(0);
    store.selectRange(2);
    applyVerdictBatch.mockRejectedValue({ response: { status: 409 } });

    const result = await store.keepSeparate(store.groups[0]);

    expect(result).toMatchObject({
      failed: true,
      uncertain: false,
      completed: 0,
      requested: 3,
    });
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2", "g3"]);
    expect(store.selectionCount).toBe(3);
  });

  it("a verdict on a group outside the selection stays single", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.toggleSelected(0);
    store.toggleSelected(1);
    stackGroup.mockResolvedValue({});
    await store.stack(store.groups[2]);
    expect(stackGroup).toHaveBeenCalledTimes(1);
    expect(stackGroup.mock.calls[0][0]).toBe("g3");
    expect(store.selectionCount).toBe(2);
  });

  it("commits nothing after an atomic bulk failure", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.toggleSelected(0);
    store.selectRange(2);
    let rejectBatch;
    applyVerdictBatch.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectBatch = reject;
        }),
    );

    const pending = store.stack(store.groups[0]);
    await vi.waitFor(() => expect(rejectBatch).toBeTypeOf("function"));
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2", "g3"]);
    expect(store.selectionCount).toBe(3);
    expect(store.focusIndex).toBe(2);

    rejectBatch(new Error("locked"));
    const result = await pending;
    expect(result).toMatchObject({
      failed: true,
      uncertain: false,
      completed: 0,
      requested: 3,
    });
    expect(applyVerdictBatch).toHaveBeenCalledTimes(1);
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2", "g3"]);
    expect(store.isSelected("g1")).toBe(true);
    expect(store.isSelected("g2")).toBe(true);
    expect(store.isSelected("g3")).toBe(true);
    expect(store.focusIndex).toBe(2);
    expect(getCounts).not.toHaveBeenCalled();
    expect(useOperationStore().refresh).not.toHaveBeenCalled();
  });

  it("clears the selection when the list reloads", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.toggleSelected(0);
    await store.loadFirstPage();
    expect(store.selectionCount).toBe(0);
  });
});

describe("useDedupStore - remembered filters", () => {
  // The tier gate and threshold are a lens the user sets; a full page
  // refresh (or the next session) must reopen the queue through it rather
  // than on the server defaults. Same persistence tier as the queue's
  // thumbnail size: this browser's localStorage.
  it("restores the tier gate and threshold across a reload", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.setTierEnabled("near", true);
    await store.setThreshold(0.8);

    // A new Pinia is the simulated reload: same browser, fresh stores.
    setActivePinia(createPinia());
    servePage([]);
    const fresh = useDedupStore();
    await fresh.openQueue({});
    expect(fresh.nearEnabled).toBe(true);
    expect(fresh.embeddingEnabled).toBe(false);
    expect(fresh.threshold).toBe(0.8);
    // The restored selection is already in force for the FIRST page, so the
    // queue never flashes the default lens.
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ nearEnabled: true, threshold: 0.8 }),
    );
  });

  // A shared or refreshed link must open exactly as sent: explicit URL
  // filters outrank the remembered selection.
  it("lets explicit URL filters outrank the remembered ones", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.setTierEnabled("near", true);
    await store.setThreshold(0.8);

    setActivePinia(createPinia());
    servePage([]);
    const fresh = useDedupStore();
    await fresh.openQueue({ filters: { near: false } });
    expect(fresh.nearEnabled).toBe(false);
    // The URL said nothing about the threshold, so the memory stands.
    expect(fresh.threshold).toBe(0.8);
  });

  it("survives a corrupt remembered blob", async () => {
    window.localStorage.setItem("pixlstash:dedupFilters", "{not json");
    servePage([]);
    const store = useDedupStore();
    await store.openQueue({});
    expect(store.nearEnabled).toBe(false);
    expect(store.threshold).toBe(0.9);
  });
});

describe("useDedupStore - the Decided flip vs the scan poll", () => {
  // The user's report, verbatim sequence: open the queue mid-scan ("Still
  // scanning…" streaming, cache still empty), flip to Decided, decided rows
  // land - and then the scan poll's OWN reload, fired while the list was
  // empty and still on the wire from before the flip, lands last and writes
  // the open queue's empty page over the decided rows. "…which then promptly
  // disappears and I get nothing."
  it("a stale scan-poll reload cannot clobber the decided rows", async () => {
    vi.useFakeTimers();
    try {
      startScan.mockResolvedValue({
        status: "running",
        scanned_pictures: 0,
        total_pictures: 10,
      });
      getCounts.mockResolvedValue({
        unresolved_groups: 0,
        by_tier: {},
        scopes: [],
        scan: { status: "running", scanned_pictures: 1, total_pictures: 10 },
      });
      servePage([], {
        scan: { status: "running", scanned_pictures: 1, total_pictures: 10 },
      });
      const store = useDedupStore();
      await store.openQueue({});
      expect(store.groups).toHaveLength(0);

      // The poll tick's reload goes on the wire and stalls there...
      let resolvePollReload;
      listGroups.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolvePollReload = resolve;
          }),
      );
      await vi.advanceTimersByTimeAsync(2100);
      expect(resolvePollReload).toBeTruthy();

      // ...the user flips to Decided and its rows land first...
      listGroups.mockResolvedValueOnce({
        groups: [
          {
            ...group("g9"),
            verdict: "keep_separate",
            decided_at: "2026-07-30",
          },
        ],
        total: 1,
        offset: 0,
        limit: 20,
        scan: { status: "running", scanned_pictures: 5, total_pictures: 10 },
      });
      await store.toggleDecided();
      expect(store.showingDecided).toBe(true);
      expect(store.groups.map((g) => g.signature)).toEqual(["g9"]);

      // ...and the stale open-queue response (empty - the scan was still
      // streaming when it was requested) lands LAST. It must be discarded,
      // never written.
      resolvePollReload({
        groups: [],
        total: 0,
        offset: 0,
        limit: 20,
        scan: { status: "complete", scanned_pictures: 10, total_pictures: 10 },
      });
      await vi.advanceTimersByTimeAsync(0);

      expect(store.groups.map((g) => g.signature)).toEqual(["g9"]);
      expect(store.focusIndex).toBe(0);
      expect(store.showingDecided).toBe(true);
    } finally {
      useDedupStore().stopScanPoll();
      vi.useRealTimers();
    }
  });
});

describe("useDedupStore - scans and bulk auto-stack", () => {
  it("reloads a populated queue once when a scan becomes terminal", async () => {
    vi.useFakeTimers();
    try {
      const running = {
        status: "running",
        tiers: ["exact"],
        threshold: 0.9,
        scanned_pictures: 1,
        total_pictures: 2,
      };
      servePage([group("g1")], { scan: running });
      getCounts.mockResolvedValue({
        unresolved_groups: 1,
        by_tier: {},
        scopes: [],
        scan: running,
      });
      const store = useDedupStore();
      await store.openQueue({});
      await Promise.resolve();

      getCounts.mockResolvedValue({
        unresolved_groups: 2,
        by_tier: {},
        scopes: [],
        scan: { status: "complete", scanned_pictures: 2, total_pictures: 2 },
      });
      listGroups.mockResolvedValueOnce({
        groups: [group("g1"), group("g2")],
        total: 2,
        scan: { status: "complete", scanned_pictures: 2, total_pictures: 2 },
      });
      await vi.advanceTimersByTimeAsync(2100);

      expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2"]);
      expect(listGroups).toHaveBeenCalledTimes(2);
      store.stopScanPoll();
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a pending person scan queued with unknown totals", async () => {
    vi.useFakeTimers();
    try {
      const pending = {
        status: "pending",
        tiers: ["exact"],
        threshold: 0.9,
        scanned_pictures: 0,
        total_pictures: 0,
        scanned_buckets: 0,
        total_buckets: 0,
      };
      servePage([], {
        scan: pending,
      });
      getCounts.mockResolvedValue({
        unresolved_groups: 0,
        by_tier: {},
        scopes: [],
        scan: pending,
      });
      const store = useDedupStore();
      await store.openQueue({ type: "character", id: 7, label: "Ada" });
      expect(listGroups).toHaveBeenCalledWith(
        expect.objectContaining({ scopeType: "character", scopeId: 7 }),
      );
      expect(store.scan).toMatchObject({ status: "pending", percent: 0 });
      expect(store.isScanning).toBe(true);
      expect(startScan).not.toHaveBeenCalled();
      store.stopScanPoll();
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses candidate-batch progress for a running person scan", async () => {
    vi.useFakeTimers();
    try {
      const running = {
        status: "running",
        tiers: ["exact"],
        threshold: 0.9,
        scanned_pictures: 3,
        total_pictures: 3,
        scanned_buckets: 1,
        total_buckets: 4,
      };
      servePage([], {
        scan: running,
      });
      getCounts.mockResolvedValue({
        unresolved_groups: 0,
        by_tier: {},
        scopes: [],
        scan: running,
      });
      const store = useDedupStore();
      await store.openQueue({ type: "character", id: 7, label: "Ada" });
      expect(store.scan).toMatchObject({
        status: "running",
        scanned: 3,
        total: 3,
        buckets: 1,
        totalBuckets: 4,
        percent: 25,
      });
      expect(store.isScanning).toBe(true);
      expect(startScan).not.toHaveBeenCalled();
      store.stopScanPoll();
    } finally {
      vi.useRealTimers();
    }
  });

  it("opening an unscanned queue queues its first scan", async () => {
    servePage([], { scan: { status: "idle" } });
    const store = useDedupStore();
    await store.openQueue({});
    expect(startScan).toHaveBeenCalledTimes(1);
    expect(listGroups.mock.invocationCallOrder[0]).toBeLessThan(
      startScan.mock.invocationCallOrder[0],
    );
  });

  it("does not mistake completed progress for proof the library is still fresh", async () => {
    servePage([], {
      scan: { status: "complete", scanned_pictures: 12098, total_pictures: 12098 },
    });
    const store = useDedupStore();
    await store.openQueue({});
    expect(startScan).toHaveBeenCalledTimes(1);
  });

  it("shares concurrent equivalent queue opens", async () => {
    let resolvePage;
    listGroups.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvePage = resolve;
        }),
    );
    startScan.mockResolvedValue({ status: "pending" });
    const store = useDedupStore();
    const first = store.openQueue({ type: "global" });
    const second = store.openQueue({ type: "global" });
    await vi.waitFor(() => expect(resolvePage).toBeTypeOf("function"));
    resolvePage({ groups: [], total: 0, scan: { status: "idle" } });
    await Promise.all([first, second]);
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(startScan).toHaveBeenCalledTimes(1);
    store.stopScanPoll();
  });

  it("joins an equivalent active scan without posting another request", async () => {
    vi.useFakeTimers();
    try {
      servePage([group("g1")], {
        scan: {
          status: "running",
          tiers: ["exact"],
          threshold: 0.9,
          scanned_pictures: 10,
          total_pictures: 100,
        },
      });
      getCounts.mockResolvedValue({
        unresolved_groups: 1,
        by_tier: {},
        scopes: [],
        scan: {
          status: "running",
          tiers: ["exact"],
          threshold: 0.9,
          scanned_pictures: 10,
          total_pictures: 100,
        },
      });
      const store = useDedupStore();
      await store.openQueue({});
      expect(startScan).not.toHaveBeenCalled();
      expect(store.isScanning).toBe(true);
      store.stopScanPoll();
    } finally {
      vi.useRealTimers();
    }
  });

  it("retries a different-policy active scan once after it completes", async () => {
    vi.useFakeTimers();
    try {
      servePage([group("g1")], {
        scan: {
          status: "running",
          tiers: ["exact", "near"],
          threshold: 0.9,
          scanned_pictures: 10,
          total_pictures: 100,
        },
      });
      getCounts.mockResolvedValue({
        unresolved_groups: 1,
        by_tier: {},
        scopes: [],
        scan: { status: "complete" },
      });
      startScan.mockResolvedValue({ status: "pending" });
      const store = useDedupStore();
      await store.openQueue({});
      expect(startScan).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(2100);
      expect(startScan).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(4100);
      expect(startScan).toHaveBeenCalledTimes(1);
      store.stopScanPoll();
    } finally {
      vi.useRealTimers();
    }
  });

  it("adopts a 409 busy scan and retries the requested policy only once", async () => {
    vi.useFakeTimers();
    try {
      startScan
        .mockRejectedValueOnce({
          response: {
            data: {
              detail: {
                code: "dedup_scan_busy",
                message: "another policy is active",
                active_scan: {
                  status: "running",
                  tiers: ["exact", "near"],
                  threshold: 0.9,
                },
              },
            },
          },
        })
        .mockResolvedValueOnce({ status: "pending" });
      getCounts.mockResolvedValue({
        unresolved_groups: 0,
        by_tier: {},
        scopes: [],
        scan: { status: "complete" },
      });
      const store = useDedupStore();
      await store.triggerScan();
      expect(store.isScanning).toBe(true);
      await vi.advanceTimersByTimeAsync(2100);
      expect(startScan).toHaveBeenCalledTimes(2);
      await vi.advanceTimersByTimeAsync(4100);
      expect(startScan).toHaveBeenCalledTimes(2);
      store.stopScanPoll();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops scan polling when the store is disposed", async () => {
    vi.useFakeTimers();
    try {
      const active = {
        status: "running",
        tiers: ["exact"],
        threshold: 0.9,
        scanned_pictures: 1,
        total_pictures: 10,
      };
      servePage([group("g1")], { scan: active });
      getCounts.mockResolvedValue({
        unresolved_groups: 1,
        by_tier: {},
        scopes: [],
        scan: active,
      });
      const store = useDedupStore();
      await store.openQueue({});
      await vi.advanceTimersByTimeAsync(0);
      getCounts.mockClear();
      store.$dispose();
      await vi.advanceTimersByTimeAsync(4100);
      expect(getCounts).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("rescans when a tier is enabled, not when one is disabled", async () => {
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.setTierEnabled("near", true);
    expect(startScan).toHaveBeenCalledTimes(1);
    await store.setTierEnabled("near", false);
    expect(startScan).toHaveBeenCalledTimes(1);
  });

  it("rescans when the threshold is lowered, not when it is raised", async () => {
    // A stricter scan never wrote the looser groups; a raise only narrows the
    // query over what is already cached.
    servePage([]);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.setThreshold(0.8);
    expect(startScan).toHaveBeenCalledTimes(1);
    await store.setThreshold(0.95);
    expect(startScan).toHaveBeenCalledTimes(1);
  });

  it("keeps the empty queue refreshing while a scan runs, then stops", async () => {
    vi.useFakeTimers();
    try {
      startScan.mockResolvedValue({
        status: "running",
        scanned_pictures: 0,
        total_pictures: 10,
      });
      getCounts.mockResolvedValue({
        unresolved_groups: 0,
        by_tier: {},
        scopes: [],
        scan: { status: "running", scanned_pictures: 2, total_pictures: 10 },
      });
      servePage([], {
        scan: { status: "running", scanned_pictures: 2, total_pictures: 10 },
      });
      const store = useDedupStore();
      await store.triggerScan();
      expect(store.isScanning).toBe(true);

      await vi.advanceTimersByTimeAsync(2100);
      expect(getCounts).toHaveBeenCalled();
      // The queue is empty, so the poll also surfaces the first finds.
      expect(listGroups).toHaveBeenCalled();

      getCounts.mockResolvedValue({
        unresolved_groups: 1,
        by_tier: {},
        scopes: [],
        scan: { status: "complete", scanned_pictures: 10, total_pictures: 10 },
      });
      servePage([], {
        scan: { status: "complete", scanned_pictures: 10, total_pictures: 10 },
      });
      await vi.advanceTimersByTimeAsync(2100);
      const settled = listGroups.mock.calls.length;
      await vi.advanceTimersByTimeAsync(6300);
      expect(listGroups.mock.calls.length).toBe(settled);
    } finally {
      const store = useDedupStore();
      store.stopScanPoll();
      vi.useRealTimers();
    }
  });

  it("queues a scan for the current scope under the current policy", async () => {
    startScan.mockResolvedValue({
      status: "running",
      scanned_pictures: 0,
      total_pictures: 100,
    });
    const store = useDedupStore();
    await store.triggerScan();
    expect(startScan).toHaveBeenCalledWith({
      policy: { nearEnabled: false, embeddingEnabled: false },
      scopeType: "global",
      scopeId: null,
    });
    expect(store.isScanning).toBe(true);
    store.stopScanPoll();
  });

  it("previews the auto-stack without writing", async () => {
    autoStackExact.mockResolvedValue({ dry_run: true, groups: 1204 });
    const store = useDedupStore();
    const preview = await store.previewAutoStack();
    expect(autoStackExact).toHaveBeenCalledWith(
      expect.objectContaining({ dryRun: true }),
    );
    expect(preview.groups).toBe(1204);
  });

  // The whole run reverses with one Ctrl+Z, so the batch id has to reach the
  // caller that narrates it.
  it("returns the batch id from a real run and reloads the queue", async () => {
    servePage([]);
    autoStackExact.mockResolvedValue({ batch_id: "b-9", groups: 1204 });
    const store = useDedupStore();
    listGroups.mockClear();
    const result = await store.runAutoStack();
    expect(result.batch_id).toBe("b-9");
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(getCounts).toHaveBeenCalled();
  });

  it("reports nothing when the bulk run fails", async () => {
    autoStackExact.mockRejectedValue(new Error("boom"));
    const store = useDedupStore();
    expect(await store.runAutoStack()).toBe(null);
    expect(store.busy).toBe(false);
  });
});

describe("useDedupStore - the thumbnail size", () => {
  // The size is remembered in localStorage, so each case starts from a browser
  // that has never been told one.
  beforeEach(() => {
    window.localStorage.clear();
    setActivePinia(createPinia());
  });

  // The queue's size is a VIEW preference and deliberately not the grid's
  // server-side one: a row of copies beside a column of facts wants a different
  // size from a wall of pictures. Only the ladder is shared.
  it("starts at the ladder's default and maps the level to a strip height", () => {
    const store = useDedupStore();
    expect(store.sizeLevel).toBe(3);
    expect(store.thumbHeight).toBe(196);
  });

  it("remembers a new size and clamps one off the ladder", () => {
    const store = useDedupStore();
    store.setSizeLevel(5);
    expect(store.sizeLevel).toBe(5);
    expect(store.thumbHeight).toBe(322);
    expect(window.localStorage.getItem("pixlstash:dedupSizeLevel")).toBe("5");

    store.setSizeLevel(99);
    expect(store.sizeLevel).toBe(6);
    store.setSizeLevel(-4);
    expect(store.sizeLevel).toBe(0);
  });

  it("survives a browser that refuses localStorage", () => {
    // Private mode throws from the getter. A default size is a fine outcome; a
    // store that cannot be constructed is not.
    const spy = vi
      .spyOn(window.localStorage.__proto__, "getItem")
      .mockImplementation(() => {
        throw new Error("denied");
      });
    setActivePinia(createPinia());
    expect(useDedupStore().sizeLevel).toBe(3);
    spy.mockRestore();
  });
});

describe("useDedupStore - Ctrl+A", () => {
  // The bug this replaces: select-all took whatever pages had been fetched, so
  // the gesture meant "40 groups" or "300" depending on how far the user had
  // scrolled, and nothing said which.
  it("pages the rest of the queue in and selects every group", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 55 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    expect(store.groups.length).toBe(20);
    listGroups.mockClear();

    let served = 20;
    listGroups.mockImplementation(async ({ limit }) => {
      const size = Math.min(limit, 55 - served);
      const page = Array.from({ length: size }, (_, i) =>
        group(`g${served + i + 1}`),
      );
      served += size;
      return { groups: page, total: 55, offset: served, limit };
    });

    const result = await store.selectAll();
    expect(result).toEqual({ selected: 55, total: 55, truncated: false });
    expect(store.selectionCount).toBe(55);
    // At the server's page size, not the queue's browsing one: 20 held, then
    // one request for the remaining 35.
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups.mock.calls[0][0].limit).toBe(200);
  });

  // "All" must never quietly mean "some": a bulk verdict on a set the user
  // never saw the size of is exactly what the ceiling has to be honest about.
  it("stops at the ceiling and says that it did", async () => {
    servePage(
      Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`)),
      { total: 5000 },
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();

    let served = 20;
    listGroups.mockImplementation(async ({ limit }) => {
      const page = Array.from({ length: limit }, (_, i) =>
        group(`g${served + i + 1}`),
      );
      served += limit;
      return { groups: page, total: 5000, offset: served, limit };
    });

    const result = await store.selectAll();
    expect(result.truncated).toBe(true);
    expect(result.total).toBe(5000);
    expect(result.selected).toBeGreaterThanOrEqual(SELECT_ALL_MAX);
    // Bounded: it stops one page past the ceiling at most, never runs the queue.
    expect(store.groups.length).toBeLessThan(SELECT_ALL_MAX + 200);
  });

  it("gives up on a page that adds nothing rather than spinning", async () => {
    servePage([group("g1"), group("g2")], { total: 900 });
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    // A server that keeps saying "there is more" and serving nothing.
    listGroups.mockClear();
    listGroups.mockResolvedValue({
      groups: [],
      total: 900,
      offset: 2,
      limit: 200,
    });

    const result = await store.selectAll();
    expect(result.selected).toBe(2);
    expect(listGroups).toHaveBeenCalledTimes(1);
  });
});

// ── locked-set candidates ─────────────────────────────────────────────────────
//
// A candidate the server marks `stackable: false` is frozen by a locked picture
// set: it can be in neither the stack nor the metadata union, so the queue keeps
// it out of the request rather than letting the user press Stack into a refusal.

describe("locked-set candidates", () => {
  /** A group whose candidate at `index` is frozen by a locked set. */
  function withLocked(signature, n, index, setName = "Evaluation Set") {
    const g = group(signature, n);
    g.candidates = g.candidates.map((c, i) =>
      i === index
        ? {
            ...c,
            stackable: false,
            blocked_by_sets: [{ id: 91, name: setName }],
          }
        : { ...c, stackable: true, blocked_by_sets: [] },
    );
    return g;
  }

  async function openWith(groups) {
    servePage(groups);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    return store;
  }

  it("counts only the stackable candidates in the stack size", async () => {
    const g = withLocked("g1", 3, 2);
    const store = await openWith([g]);
    expect(store.stackSizeFor(g)).toBe(2);
  });

  it("sends the locked ids as exclusions so the server never has to skip them", async () => {
    const g = withLocked("g1", 3, 2);
    const store = await openWith([g]);
    stackGroup.mockResolvedValue({
      signature: "g1",
      batch_id: "srv-1",
      skipped: [],
    });

    await store.stack(g);

    const [, options] = stackGroup.mock.calls[0];
    expect(options.excludedPictureIds).toEqual([idsOf(g)[2]]);
  });

  it("keeps the cover off a locked candidate", async () => {
    const g = withLocked("g1", 3, 0);
    const store = await openWith([g]);
    // Even an explicit user choice cannot leave the cover on a picture that is
    // not going into the stack.
    store.setCover("g1", idsOf(g)[0]);
    expect(store.coverIdFor(g)).not.toBe(idsOf(g)[0]);
    expect(idsOf(g).slice(1)).toContain(store.coverIdFor(g));
  });

  it("refuses to re-include a locked candidate, and says which refusal it was", async () => {
    const g = withLocked("g1", 3, 2);
    const store = await openWith([g]);
    // Distinct from the stack-floor refusal (false), because the two need
    // different sentences: this one is only fixable by unlocking the set.
    expect(store.toggleExcluded(g, idsOf(g)[2])).toBe("locked");
    expect(store.excludedFor("g1")).toEqual([]);
  });

  it("still lets the user exclude an unfrozen candidate alongside a locked one", async () => {
    const g = withLocked("g1", 4, 3);
    const store = await openWith([g]);
    expect(store.toggleExcluded(g, idsOf(g)[0])).toBe(true);
    expect(store.effectiveExcludedFor(g).sort()).toEqual(
      [idsOf(g)[0], idsOf(g)[3]].sort(),
    );
  });

  it("reports a partial success without aborting a bulk run", async () => {
    const a = withLocked("g1", 3, 2);
    const b = group("g2", 2);
    const store = await openWith([a, b]);
    store.toggleSelected(0);
    store.toggleSelected(1);
    applyVerdictBatch.mockResolvedValue({
      batch_id: "cli-1",
      results: [
        {
          signature: "g1",
          picture_ids: [100, 101],
          skipped: [
            {
              picture_id: 102,
              reason: "set_locked",
              sets: [{ id: 91, name: "Evaluation Set" }],
            },
          ],
        },
        { signature: "g2", picture_ids: [200, 201], skipped: [] },
      ],
    });

    const result = await store.stack(a);

    // Both groups were decided: a partial success is a success.
    expect(applyVerdictBatch).toHaveBeenCalledTimes(1);
    expect(result.gesture_skipped).toHaveLength(1);
    expect(result.gesture_skipped[0].picture_id).toBe(102);
  });

  it("treats a backend that serves no stackable field as all-stackable", async () => {
    const g = group("g1", 3);
    const store = await openWith([g]);
    expect(store.stackSizeFor(g)).toBe(3);
    expect(store.effectiveExcludedFor(g)).toEqual([]);
  });
});

// --- Units: the queue's cover, exclusion and floor all move whole stacks -----
//
// `_stack_members` folds in every member of any stack the group touches, so the
// picture-level gestures the queue used to offer could not be honoured:
// excluding one member of an existing stack was a silent no-op, and choosing
// one as cover silently re-covered that whole stack in the library.

/**
 * A group naming ONE member of a live 4-stack, plus two loose pictures.
 *
 * The stack's leader (501) is deliberately NOT a candidate: on a real library
 * one stack-touching group in three is shaped exactly like this.
 */
function stackGroupFixture(over = {}) {
  return {
    signature: "sg",
    tier: "near",
    confidence: 0.9,
    member_count: 3,
    // The server's smart-score pick is a LOOSE picture, which is what makes the
    // deck-wins rule visible: honouring it would re-curate stack 12.
    cover_picture_id: 700,
    why: [],
    candidates: [
      { picture_id: 503, stack_id: 12, width: 1000, height: 1000 },
      { picture_id: 700, width: 6000, height: 4000 },
      { picture_id: 701, width: 4000, height: 3000 },
    ],
    stacks: {
      12: {
        stack_id: 12,
        member_count: 4,
        leader_picture_id: 501,
        leader_thumbnail_version: "1024x768",
        matched_picture_ids: [503],
        stackable: true,
        blocked_by_sets: [],
      },
    },
    ...over,
  };
}

describe("useDedupStore: the cover default with a deck present", () => {
  // The whole point: the default verdict must not silently re-curate a stack
  // the user already made. The server's own preselection (a loose picture here)
  // loses to the deck.
  it("preselects the deck's LEADER over the server's smart-score pick", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(g.cover_picture_id).toBe(700);
    expect(store.coverIdFor(g)).toBe(501);
  });

  // With nothing stacked the rule is unchanged: the server's preselection wins.
  it("leaves an all-loose group's preselection alone", async () => {
    servePage([group("g1", 3, { cover_picture_id: 102 })], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.coverIdFor(store.groups[0])).toBe(102);
  });

  // Merging into the larger stack re-curates the fewest pictures.
  it("prefers the deeper deck when a group holds two", async () => {
    servePage(
      [
        stackGroupFixture({
          candidates: [
            { picture_id: 503, stack_id: 12 },
            { picture_id: 601, stack_id: 13 },
          ],
          stacks: {
            12: { stack_id: 12, member_count: 4, leader_picture_id: 501 },
            13: { stack_id: 13, member_count: 9, leader_picture_id: 600 },
          },
        }),
      ],
      { total: 1 },
    );
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.coverIdFor(store.groups[0])).toBe(600);
  });

  // ── The two gestures that arrive on setCover, told apart by the id ───────
  //
  // Choosing the DECK passes the unit's own `coverPictureId` (the leader),
  // that is what the row's tile, the digits, Compare's card and its zoom all
  // emit. Promoting a MEMBER passes one of the stack's other pictures, which
  // only Compare's expansion band ever does. Both must stick.

  // Choosing the deck resolves to its leader: the picture the tile shows, and
  // the only one the server can lead the resulting stack with.
  it("keeps a deck's choice on the stack's leader", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, 700);
    expect(store.coverIdFor(g)).toBe(700);
    // The deck, addressed the way every deck-level gesture addresses it.
    store.setCover(g.signature, 501);
    expect(store.coverIdFor(g)).toBe(501);
  });

  // The bug this closes: `unitForPictureId` resolved a promoted member back to
  // its deck and handed back the LEADER, so promoting a member the group had
  // named was a silent no-op, and the gesture only appeared to work for the
  // members the group had NOT named, which fell through the lookup by accident.
  it("honours a member promoted from inside the deck", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    // 503 is a matched member of stack 12: the case that used to snap back.
    store.setCover(g.signature, 503);
    expect(store.coverIdFor(g)).toBe(503);
  });

  // The other half of the same stack: a member the group never named. It
  // already stuck, and it must keep sticking through the new branch.
  it("honours a promoted member the group never named", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, 504);
    expect(store.coverIdFor(g)).toBe(504);
  });

  // A promotion sends that picture as the verdict's cover: the server resolves
  // it against the pictures that will END UP in the stack (backend contract
  // B2), which includes every member of a folded stack.
  it("sends a promoted member as the verdict's cover", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    stackGroup.mockResolvedValue({ signature: "sg", picture_ids: [503, 700] });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, 503);
    await store.stack(g);
    expect(stackGroup).toHaveBeenCalledWith(
      "sg",
      expect.objectContaining({ coverPictureId: 503 }),
    );
  });

  // A frozen deck cannot lead a stack it is not in, whichever of its pictures
  // was named: the promotion branch must not smuggle one past the lock.
  it("refuses a promoted member of a frozen deck", async () => {
    const frozen = stackGroupFixture();
    frozen.stacks[12].stackable = false;
    frozen.stacks[12].blocked_by_sets = [{ id: 7, name: "Portfolio" }];
    servePage([frozen], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    store.setCover(g.signature, 503);
    expect(store.coverIdFor(g)).toBe(700);
  });

  // A frozen deck cannot lead a stack it is not in, so the label stays truthful
  // before Stack is ever pressed.
  it("skips a frozen deck and falls back to the loose pictures", async () => {
    const frozen = stackGroupFixture();
    frozen.stacks[12].stackable = false;
    frozen.stacks[12].blocked_by_sets = [{ id: 7, name: "Portfolio" }];
    servePage([frozen], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.coverIdFor(store.groups[0])).toBe(700);
  });
});

describe("useDedupStore: exclusion is a whole-unit gesture", () => {
  // Excluding one member of an existing stack was a silent no-op: the rest of
  // its stack dragged it straight back in. The deck goes out entire instead.
  it("takes every picture of a deck out at once", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.toggleExcluded(g, 503)).toBe(true);
    expect(store.excludedFor("sg")).toEqual([503]);
    expect(store.includedUnitCountFor(g)).toBe(2);
    // And back in as one gesture.
    expect(store.toggleExcluded(g, 503)).toBe(true);
    expect(store.excludedFor("sg")).toEqual([]);
  });

  // The leader is usually not a group member, so it has to address the deck
  // too: that is what the row emits when the deck's tile is right-clicked.
  it("addresses a deck by its leader as well as by its members", async () => {
    servePage([stackGroupFixture()], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.toggleExcluded(g, 501)).toBe(true);
    expect(store.excludedFor("sg")).toEqual([503]);
  });

  // The floor is two UNITS, not two pictures: the server folds a stack as one
  // thing, so a deck and a loose picture is the smallest group with a decision
  // left in it however deep the deck runs.
  it("counts the floor in units", async () => {
    const two = stackGroupFixture({
      candidates: [{ picture_id: 503, stack_id: 12 }, { picture_id: 700 }],
    });
    servePage([two], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    // Five pictures in play, but only two units, so nothing may be excluded.
    expect(store.stackSizeFor(g)).toBe(2);
    expect(store.includedUnitCountFor(g)).toBe(2);
    expect(store.isAtStackFloor(g)).toBe(true);
    expect(store.toggleExcluded(g, 503)).toBe(false);
    expect(store.toggleExcluded(g, 700)).toBe(false);
    expect(store.excludedFor("sg")).toEqual([]);
  });

  // The cover moves to the surviving DECK, not to the best loose picture:
  // otherwise excluding one stack quietly re-curates the other.
  it("moves the cover onto the remaining deck when its unit goes out", async () => {
    servePage(
      [
        stackGroupFixture({
          candidates: [
            { picture_id: 503, stack_id: 12 },
            { picture_id: 601, stack_id: 13 },
            { picture_id: 700, width: 6000, height: 4000 },
          ],
          stacks: {
            12: { stack_id: 12, member_count: 9, leader_picture_id: 501 },
            13: { stack_id: 13, member_count: 4, leader_picture_id: 600 },
          },
        }),
      ],
      { total: 1 },
    );
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.coverIdFor(g)).toBe(501);
    expect(store.toggleExcluded(g, 501)).toBe(true);
    expect(store.coverIdFor(g)).toBe(600);
  });

  // A locked set freezes a WHOLE stack, including members the group never
  // named, so the request must carry every visible member of that deck.
  it("sends a frozen deck's whole visible membership as excluded", async () => {
    const frozen = stackGroupFixture({
      candidates: [
        { picture_id: 503, stack_id: 12 },
        { picture_id: 504, stack_id: 12 },
        { picture_id: 700 },
        { picture_id: 701 },
      ],
    });
    frozen.stacks[12].stackable = false;
    frozen.stacks[12].blocked_by_sets = [{ id: 7, name: "Portfolio" }];
    servePage([frozen], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    expect(store.effectiveExcludedFor(g).sort()).toEqual([503, 504]);
    // And `X` cannot walk the server's own exclusion back.
    expect(store.toggleExcluded(g, 503)).toBe("locked");
    expect(store.toggleExcluded(g, 501)).toBe("locked");
  });
});

describe("useDedupStore: a scrapheap move while the queue is open", () => {
  /** A `pictures_changed` message in the backend's shape. */
  const event = (change_kind, picture_ids) => ({
    type: "pictures_changed",
    change_kind,
    picture_ids,
    source: "ui",
  });

  /** A group whose candidates sit in one existing stack (a deck). */
  function deckGroup(signature, stackId, matched, depth, leader) {
    const g = group(signature, matched.length);
    g.candidates = matched.map((id, i) => ({
      ...g.candidates[i],
      picture_id: id,
      stack_id: stackId,
    }));
    g.stacks = {
      [String(stackId)]: {
        stack_id: stackId,
        member_count: depth,
        leader_picture_id: leader,
        leader_thumbnail_version: "800x600",
        matched_picture_ids: [...matched],
        stackable: true,
        blocked_by_sets: [],
      },
    };
    return g;
  }

  it("drops a group whose live members fall below two", async () => {
    servePage([group("g1"), group("g2"), group("g3")], { total: 3 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const doomed = idsOf(store.groups[1])[0];

    const decision = store.applyPictureEvent(event("removed", [doomed]));

    expect(decision.action).toBe("targeted");
    expect(decision.dropped).toEqual(["g2"]);
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g3"]);
  });

  // Over-filtering is its own regression: three copies minus one is still a
  // decision the user has to make.
  it("keeps a group that still has two live members, minus the deleted tile", async () => {
    servePage([group("g1", 3)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const gone = idsOf(store.groups[0])[2];

    store.applyPictureEvent(event("removed", [gone]));

    expect(store.groups).toHaveLength(1);
    const survivor = store.groups[0];
    expect(idsOf(survivor)).not.toContain(gone);
    expect(survivor.candidates).toHaveLength(2);
    // Every count on the row is the LIVE one; the server reports it the same way.
    expect(survivor.member_count).toBe(2);
  });

  it("takes the row out of the count and the queue's own total", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    getCounts.mockResolvedValue({ unresolved_groups: 2, by_tier: {} });
    const store = useDedupStore();
    await store.refreshCounts();
    await store.loadFirstPage();
    expect(store.openCount).toBe(2);
    expect(store.total).toBe(2);

    store.applyPictureEvent(event("removed", [idsOf(store.groups[0])[0]]));

    expect(store.openCount).toBe(1);
    expect(store.total).toBe(1);
  });

  it("walks the focus off a row it removes", async () => {
    servePage([group("g1"), group("g2"), group("g3")], { total: 3 });
    const store = useDedupStore();
    await store.loadFirstPage();
    store.setFocus(2);

    store.applyPictureEvent(event("removed", [idsOf(store.groups[2])[0]]));

    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2"]);
    expect(store.focusIndex).toBe(1);
    expect(store.focusedGroup.signature).toBe("g2");
  });

  // The deck stands for a whole existing stack, so a deleted member must not
  // leave a hole in its depth: the row would promise to move a picture that is
  // already in the Scrapheap.
  it("shrinks a deck's depth when one of its matched members goes", async () => {
    const g = deckGroup("g1", 7, [10, 11], 4, 10);
    g.candidates.push({ ...group("g1").candidates[0], picture_id: 99 });
    servePage([g], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();

    store.applyPictureEvent(event("removed", [11]));

    const deck = store.groups[0].stacks["7"];
    expect(deck.member_count).toBe(3);
    expect(deck.matched_picture_ids).toEqual([10]);
    expect(deck.leader_picture_id).toBe(10);
  });

  // The stack's next leader is a fact only the server holds, so the face falls
  // back to a surviving matched member rather than a 404 thumbnail.
  it("moves a deck's face off a deleted leader", async () => {
    const g = deckGroup("g1", 7, [10, 11], 4, 10);
    g.candidates.push({ ...group("g1").candidates[0], picture_id: 99 });
    servePage([g], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();

    store.applyPictureEvent(event("removed", [10]));

    const units = store.unitsFor(store.groups[0]);
    const deck = units.find((u) => u.kind === "deck");
    expect(deck.depth).toBe(3);
    expect(deck.coverPictureId).toBe(11);
    expect(store.groups[0].stacks["7"].leader_picture_id).toBeNull();
  });

  // A choice that names a picture the Scrapheap took is not a choice any more:
  // left in place it rides along as an `excluded_picture_ids` entry the server
  // cannot resolve, and `coverIdFor` hands back a raw deleted id.
  it("forgets a cover choice and an exclusion that named a deleted picture", async () => {
    servePage([group("g1", 4)], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();
    const g = store.groups[0];
    const [first, , third, fourth] = idsOf(g);
    store.toggleExcluded(g, fourth);
    store.setCover("g1", third);
    expect(store.excludedFor("g1")).toEqual([fourth]);

    store.applyPictureEvent(event("removed", [third, fourth]));

    expect(store.groups).toHaveLength(1);
    expect(store.coverChoices.g1).toBeUndefined();
    expect(store.excludedFor("g1")).toEqual([]);
    expect(store.coverIdFor(store.groups[0])).toBe(first);
  });

  // The decided page reviews decisions that have already been made; hiding a
  // row because its pictures were scrapheaped would strand the decision with no
  // way back to it.
  it("never drops a row from the decided page, only its dead tiles", async () => {
    servePage([group("g1")], { total: 1 });
    const store = useDedupStore();
    store.showingDecided = true;
    await store.loadFirstPage();
    const gone = idsOf(store.groups[0])[0];

    store.applyPictureEvent(event("removed", [gone]));

    expect(store.groups.map((g) => g.signature)).toEqual(["g1"]);
    expect(idsOf(store.groups[0])).not.toContain(gone);
  });

  // The queue is windowed and keyset-paged: a restore lands at a position in the
  // confidence ordering the client cannot compute, and rebuilding the window
  // would throw a triage in progress back to row one for one returning group.
  it("does not yank a triage in progress back to the top on a restore", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    const store = useDedupStore();
    await store.loadFirstPage();
    store.setFocus(1);
    listGroups.mockClear();

    const decision = store.applyPictureEvent(event("restored", [4242]));

    expect(decision.action).toBe("ignored");
    expect(listGroups).not.toHaveBeenCalled();
    expect(store.focusIndex).toBe(1);
  });

  // ...but "nothing left to review" while the badge says otherwise is the one
  // lie a to-do count cannot afford, and an empty window has nothing to disturb.
  it("rebuilds an empty queue when a restore puts a group back", async () => {
    servePage([], { total: 0 });
    const store = useDedupStore();
    await store.loadFirstPage();
    servePage([group("g1")], { total: 1 });

    const decision = store.applyPictureEvent(event("restored", [4242]));
    await Promise.resolve();
    await Promise.resolve();

    expect(decision.action).toBe("reload");
    expect(store.groups.map((g) => g.signature)).toEqual(["g1"]);
  });

  it("ignores events that are not a scrapheap move", async () => {
    servePage([group("g1")], { total: 1 });
    const store = useDedupStore();
    await store.loadFirstPage();

    expect(
      store.applyPictureEvent({
        type: "pictures_changed",
        change_kind: "updated",
        fields: ["smart_score"],
        picture_ids: idsOf(store.groups[0]),
      }).action,
    ).toBe("ignored");
    expect(
      store.applyPictureEvent({ type: "picture_imported", picture_ids: [1] })
        .action,
    ).toBe("ignored");
    expect(store.groups).toHaveLength(1);
  });

  it("leaves the queue alone when the deletion touches no loaded group", async () => {
    servePage([group("g1"), group("g2")], { total: 2 });
    const store = useDedupStore();
    await store.loadFirstPage();

    const decision = store.applyPictureEvent(event("removed", [999999]));

    expect(decision.action).toBe("ignored");
    expect(store.groups).toHaveLength(2);
    expect(store.total).toBe(2);
  });
});

// ── Mixed stacks: the third page (design D5) ────────────────────────────────

/** One `MixedStackModel` row in the backend's shape. */
function mixedStack(over = {}) {
  return {
    stack_id: 42,
    threshold: 0.9,
    member_count: 5,
    member_ids: [7, 8, 9, 10, 11],
    membership_fingerprint: "fp-42",
    component_count: 2,
    component_sizes: [4, 1],
    components: [[7, 8, 9, 10], [11]],
    largest_component_size: 4,
    stranded_picture_ids: [11],
    weakest_edge: 0.91,
    unhashed_picture_ids: [],
    suggested_action: "split",
    kept: false,
    leader_picture_id: 7,
    leader_thumbnail_version: null,
    ...over,
  };
}

/** One page of `GET /dedup/mixed-stacks`. */
function mixedPage(stacks, over = {}) {
  return {
    threshold: 0.9,
    total: stacks.length,
    kept_total: 0,
    live_stack_count: 209,
    offset: 0,
    limit: 100,
    next_offset: null,
    stacks,
    ...over,
  };
}

describe("useDedupStore: the mixed-stack list is bound to the threshold", () => {
  // The design's one non-negotiable here: the same stack is mixed at 0.90 and
  // one clean cluster at 0.65. A list computed at a constant would describe a
  // library the slider no longer points at.
  it("asks at the queue's own threshold, never a constant", async () => {
    listMixedStacks.mockResolvedValue(mixedPage([mixedStack()]));
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadMixedStacks();
    expect(listMixedStacks).toHaveBeenCalledWith(
      expect.objectContaining({ threshold: 0.9 }),
    );
  });

  // 26 at the default 0.90 and 9 at the 0.65 floor, on the owner's library.
  it("re-reads the list when the threshold moves", async () => {
    listGroups.mockResolvedValue({ groups: [], total: 0 });
    listMixedStacks.mockResolvedValue(
      mixedPage(
        Array.from({ length: 26 }, (_, i) => mixedStack({ stack_id: i })),
      ),
    );
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadMixedStacks();
    expect(store.mixedTotal).toBe(26);

    listMixedStacks.mockResolvedValue(
      mixedPage(
        Array.from({ length: 9 }, (_, i) => mixedStack({ stack_id: i })),
        { threshold: 0.65, total: 9 },
      ),
    );
    await store.setThreshold(0.65);
    expect(listMixedStacks).toHaveBeenLastCalledWith(
      expect.objectContaining({ threshold: 0.65 }),
    );
    expect(store.mixedTotal).toBe(9);
    // The page states what the SERVER computed at, not what the slider says:
    // the two differ for exactly as long as a reload is in flight.
    expect(store.mixedThreshold).toBe(0.65);
  });

  // The list is a page, not a bag: the server's ranking IS the ordering, worst
  // first, and the store must not re-sort it into something else.
  it("keeps the server's least-held-together-first order", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([
        mixedStack({ stack_id: 3, stranded_picture_ids: [1, 2] }),
        mixedStack({ stack_id: 1, stranded_picture_ids: [4] }),
        mixedStack({
          stack_id: 2,
          stranded_picture_ids: [],
          component_count: 2,
        }),
      ]),
    );
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(store.mixedStacks.map((s) => s.stack_id)).toEqual([3, 1, 2]);
  });

  // A failed read must never render as "no mixed stacks": that sentence is a
  // claim about the library, and nobody asked.
  it("records a failed read rather than reporting an empty library", async () => {
    listMixedStacks.mockRejectedValue(new Error("boom"));
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(store.mixedError).toBeTruthy();
    expect(store.mixedLoaded).toBe(false);
    expect(store.mixedStacks).toEqual([]);
  });

  it("pages by plain offset and drops a re-served row", async () => {
    listMixedStacks.mockResolvedValueOnce(
      mixedPage([mixedStack({ stack_id: 1 })], { total: 2, next_offset: 1 }),
    );
    const store = useDedupStore();
    await store.loadMixedStacks();
    listMixedStacks.mockResolvedValueOnce(
      mixedPage([mixedStack({ stack_id: 1 }), mixedStack({ stack_id: 2 })], {
        total: 2,
        next_offset: null,
      }),
    );
    await store.loadMoreMixedStacks();
    expect(store.mixedStacks.map((s) => s.stack_id)).toEqual([1, 2]);
    expect(store.hasMoreMixed).toBe(false);
  });
});

describe("useDedupStore: the queue's warning chip reads the same list", () => {
  // Only the STRONG case is flagged. The soft ones are often legitimate, and a
  // mark on one tile in eight stops being a warning at all.
  it("flags only the stacks with a stranded member", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([
        mixedStack({ stack_id: 11, stranded_picture_ids: [99] }),
        mixedStack({ stack_id: 12, stranded_picture_ids: [] }),
      ]),
    );
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(store.isStackFlagged(11)).toBe(true);
    expect(store.isStackFlagged("11")).toBe(true);
    expect(store.isStackFlagged(12)).toBe(false);
    expect(store.isStackFlagged(null)).toBe(false);
  });

  // The first request after cache invalidation scores every stack. It belongs to
  // the optional page, never to ordinary queue startup.
  it("defers the mixed list until the page is opened", async () => {
    listGroups.mockResolvedValue({ groups: [], total: 0 });
    startScan.mockResolvedValue({ status: "complete" });
    listMixedStacks.mockResolvedValue(mixedPage([mixedStack()]));
    const store = useDedupStore();
    await store.openQueue();
    expect(listMixedStacks).not.toHaveBeenCalled();

    await store.showMixedStacks();
    expect(listMixedStacks).toHaveBeenCalledTimes(1);
  });
});

describe("useDedupStore: the mixed-stack actions", () => {
  it("splits with the ids the row showed and drops the row", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([mixedStack({ stack_id: 42, stranded_picture_ids: [11] })]),
    );
    splitMixedStack.mockResolvedValue({
      stack_id: 42,
      split_picture_ids: [11],
      remaining_picture_ids: [7, 8, 9, 10],
      stack_dissolved: false,
      batch_id: "srv-1",
    });
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadMixedStacks();
    const result = await store.resolveMixedStack(store.mixedStacks[0]);
    expect(splitMixedStack).toHaveBeenCalledWith(
      42,
      expect.objectContaining({ pictureIds: [11], threshold: 0.9 }),
    );
    expect(result.split_picture_ids).toEqual([11]);
    expect(store.mixedStacks).toEqual([]);
    expect(store.mixedTotal).toBe(0);
    // One operation, so the standard receipt covers it: no second undo
    // mechanism is invented for this surface.
    expect(useOperationStore().refresh).toHaveBeenCalled();
  });

  // ONE call carries both outcomes. The split route takes any live member of
  // the stack, so an unstack is "every member leaves" and the server dissolves
  // a stack that would be left with fewer than two members either way. Routing
  // between two endpoints on a client-side prediction only creates a case where
  // the prediction and the request disagree.
  it("unstacks through the same split call, naming every member", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([
        mixedStack({
          stack_id: 7,
          member_count: 2,
          member_ids: [7, 8],
          suggested_action: "unstack",
          stranded_picture_ids: [],
          components: [[7], [8]],
        }),
      ]),
    );
    splitMixedStack.mockResolvedValue({
      stack_id: 7,
      split_picture_ids: [7, 8],
      remaining_picture_ids: [],
      stack_dissolved: true,
      batch_id: "srv-2",
    });
    const store = useDedupStore();
    await store.loadMixedStacks();
    await store.resolveMixedStack(store.mixedStacks[0], [7, 8]);
    expect(splitMixedStack).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ pictureIds: [7, 8] }),
    );
    expect(unstackMixedStack).not.toHaveBeenCalled();
    expect(store.mixedStacks).toEqual([]);
  });

  // The threshold the LIST was computed at, not the slider's live value. The
  // two differ for exactly as long as a reload is in flight, and that is the
  // window in which a write built from the rows on screen would be bounded by a
  // threshold those rows were never computed at.
  it("sends the threshold the list was served at, not the live slider", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([mixedStack()], { threshold: 0.72 }),
    );
    splitMixedStack.mockResolvedValue({
      stack_id: 42,
      split_picture_ids: [11],
      remaining_picture_ids: [7, 8, 9, 10],
      stack_dissolved: false,
      batch_id: "srv-3",
    });
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadMixedStacks();
    expect(store.threshold).toBe(0.9);
    expect(store.mixedThreshold).toBe(0.72);
    await store.resolveMixedStack(store.mixedStacks[0]);
    expect(splitMixedStack).toHaveBeenCalledWith(
      42,
      expect.objectContaining({ threshold: 0.72 }),
    );
  });

  // A 400 means the row no longer describes the stack: a marked member left it
  // since the list was read. Without a handler the button simply did nothing,
  // which is the definition of a dead control. The list is re-read so the row
  // either comes back correct or leaves.
  it("re-reads the list when the server says the row is stale", async () => {
    listMixedStacks.mockResolvedValue(mixedPage([mixedStack()]));
    splitMixedStack.mockRejectedValue({
      response: { status: 400, data: { detail: "not members of stack 42" } },
    });
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(listMixedStacks).toHaveBeenCalledTimes(1);
    expect(await store.resolveMixedStack(store.mixedStacks[0])).toBeNull();
    expect(listMixedStacks).toHaveBeenCalledTimes(2);
    // The row stays: nothing was written, so nothing may leave the list on the
    // strength of a refusal.
    expect(store.mixedStacks).toHaveLength(1);
  });

  // A failed action must leave the row where it was: the page's whole promise
  // is that nothing changes until it says it did.
  it("keeps the row when the action fails", async () => {
    listMixedStacks.mockResolvedValue(mixedPage([mixedStack()]));
    splitMixedStack.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(await store.resolveMixedStack(store.mixedStacks[0])).toBeNull();
    expect(store.mixedStacks).toHaveLength(1);
  });

  // A locked picture set refuses the WHOLE stack with 423 and writes nothing,
  // so the row has to stay AND has to stop offering an outcome that cannot
  // land. The refusal is fresher truth than the page the row was read from, so
  // it is what the row is marked with.
  it("keeps a 423-refused row and marks it with the sets the server named", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([mixedStack({ stack_id: 42 })]),
    );
    splitMixedStack.mockRejectedValue({
      response: {
        status: 423,
        data: {
          detail: {
            code: "pictures_locked",
            action: "split a stack",
            sets: [{ id: 3, name: "Frozen" }],
            picture_ids: [11],
          },
        },
      },
    });
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(await store.resolveMixedStack(store.mixedStacks[0])).toBeNull();
    expect(store.mixedStacks).toHaveLength(1);
    expect(store.mixedTotal).toBe(1);
    expect(store.mixedStacks[0].stackable).toBe(false);
    expect(store.mixedStacks[0].blocked_by_sets).toEqual([
      { id: 3, name: "Frozen" },
    ]);
    // The rejection is still carried up, so the view can name the sets and
    // flash the pictures the refusal listed.
    expect(lockedPictureIds(store.error)).toEqual([11]);
    expect(serverDetail(store.error)).toContain("Frozen");
  });

  // An unstack takes the same refusal, and the row it leaves behind must read
  // the same way: one refusal, one marking. It travels as a split over every
  // member, so this is the same call refused with the same code.
  it("marks the row when an unstack is refused by a lock", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([
        mixedStack({
          stack_id: 7,
          member_count: 2,
          member_ids: [7, 8],
          suggested_action: "unstack",
          stranded_picture_ids: [],
          components: [[7], [8]],
        }),
      ]),
    );
    splitMixedStack.mockRejectedValue({
      response: {
        status: 423,
        data: {
          detail: {
            code: "pictures_locked",
            sets: [
              { id: 3, name: "Frozen" },
              { id: 4, name: "Archive" },
            ],
            picture_ids: [7, 8],
          },
        },
      },
    });
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(await store.resolveMixedStack(store.mixedStacks[0])).toBeNull();
    expect(store.mixedStacks).toHaveLength(1);
    expect(store.mixedStacks[0].stackable).toBe(false);
    expect(store.mixedStacks[0].blocked_by_sets).toHaveLength(2);
  });

  // Over-marking is its own regression: a network blip is not a lock, and a row
  // that quietly disabled itself on one would strand work the user could do.
  it("does not mark a row locked when the failure was not a lock", async () => {
    listMixedStacks.mockResolvedValue(mixedPage([mixedStack()]));
    splitMixedStack.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    await store.loadMixedStacks();
    expect(await store.resolveMixedStack(store.mixedStacks[0])).toBeNull();
    expect(store.mixedStacks).toHaveLength(1);
    expect(store.mixedStacks[0].stackable).toBeUndefined();
  });

  // Keep is what makes the list drainable. It changes no picture, so it records
  // no operation and DELETE is the only way back.
  it("Keep removes the row, and clearing the Keep brings it back", async () => {
    listMixedStacks.mockResolvedValue(
      mixedPage([mixedStack({ stack_id: 42 })]),
    );
    keepMixedStack.mockResolvedValue({
      stack_id: 42,
      dismissed: true,
      created: true,
      membership_fingerprint: "fp-42",
    });
    const store = useDedupStore();
    await store.loadMixedStacks();
    await store.keepMixed(store.mixedStacks[0]);
    expect(keepMixedStack).toHaveBeenCalledWith(42);
    expect(store.mixedStacks).toEqual([]);
    expect(store.mixedKeptTotal).toBe(1);
    // No operation was recorded, so no receipt is raised for it.
    expect(useOperationStore().refresh).not.toHaveBeenCalled();

    clearMixedStackKeep.mockResolvedValue({
      stack_id: 42,
      dismissed: false,
      removed: 1,
    });
    listMixedStacks.mockResolvedValue(
      mixedPage([mixedStack({ stack_id: 42 })]),
    );
    await store.unkeepMixedStack(42);
    expect(clearMixedStackKeep).toHaveBeenCalledWith(42);
    expect(store.mixedStacks.map((s) => s.stack_id)).toEqual([42]);
  });
});

describe("useDedupStore: the two-way shortcut", () => {
  /** A queue group that folds in one existing stack. */
  function groupWithStack(signature, stackId) {
    const g = group(signature, 2);
    g.candidates[0].stack_id = stackId;
    g.stacks = {
      [String(stackId)]: {
        stack_id: stackId,
        member_count: 5,
        leader_picture_id: 7,
        matched_picture_ids: [g.candidates[0].picture_id],
        stackable: true,
        blocked_by_sets: [],
      },
    };
    return g;
  }

  it("finds the loaded group a stack appears in, in ABSOLUTE indices", async () => {
    listGroups.mockResolvedValue({
      groups: [groupWithStack("a1", 1), groupWithStack("a2", 42)],
      total: 2,
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    expect(store.groupIndexForStack(42)).toBe(1);
    expect(store.groupIndexForStack("42")).toBe(1);
    expect(store.groupIndexForStack(999)).toBe(-1);
  });

  // A shortcut that scrolled to a guessed row would be worse than one that is
  // not offered, so the store refuses rather than moving the cursor anywhere.
  it("declines when no loaded group holds the stack", async () => {
    listGroups.mockResolvedValue({
      groups: [groupWithStack("a1", 1)],
      total: 1,
    });
    const store = useDedupStore();
    await store.loadFirstPage();
    store.showingMixed = true;
    expect(store.showQueueForStack(42)).toBe(false);
    expect(store.showingMixed).toBe(true);
  });

  it("puts the queue's focus on the group and leaves the page", async () => {
    listGroups.mockResolvedValue({
      groups: [groupWithStack("a1", 1), groupWithStack("a2", 42)],
      total: 2,
    });
    listMixedStacks.mockResolvedValue(
      mixedPage([mixedStack({ stack_id: 42 })]),
    );
    const store = useDedupStore();
    await store.loadFirstPage();
    await store.showMixedStacks(42);
    expect(store.showingMixed).toBe(true);
    expect(store.mixedFocusStackId).toBe("42");
    // The queue is untouched while the page is up, which is what lets the
    // return restore exactly what the user left.
    expect(store.focusIndex).toBe(0);

    expect(store.showQueueForStack(42)).toBe(true);
    expect(store.showingMixed).toBe(false);
    expect(store.focusIndex).toBe(1);
  });

  // Flipping the page must not reload the queue: it is a page of the same
  // destination, not a route away.
  it("costs the queue no reload in either direction", async () => {
    listGroups.mockResolvedValue({ groups: [group("a1")], total: 1 });
    listMixedStacks.mockResolvedValue(mixedPage([mixedStack()]));
    const store = useDedupStore();
    await store.loadFirstPage();
    listGroups.mockClear();
    await store.showMixedStacks();
    store.hideMixedStacks();
    expect(listGroups).not.toHaveBeenCalled();
    expect(store.showingMixed).toBe(false);
  });
});
