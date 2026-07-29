import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../api/dedup", () => ({
  getPolicy: vi.fn(),
  listGroups: vi.fn(),
  getCounts: vi.fn(),
  startScan: vi.fn(),
  stackGroup: vi.fn(),
  keepGroupSeparate: vi.fn(),
  reopenGroup: vi.fn(),
  autoStackExact: vi.fn(),
  GLOBAL_SCOPE: "global",
}));

import {
  getPolicy,
  listGroups,
  getCounts,
  startScan,
  stackGroup,
  keepGroupSeparate,
  reopenGroup,
  autoStackExact,
} from "../api/dedup";
import { useDedupStore, scopeKey, QUEUE_PAGE_SIZE } from "./useDedupStore";

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
  setActivePinia(createPinia());
  vi.spyOn(console, "warn").mockImplementation(() => {});
  for (const fn of [
    getPolicy,
    listGroups,
    getCounts,
    startScan,
    stackGroup,
    keepGroupSeparate,
    reopenGroup,
    autoStackExact,
  ]) {
    fn.mockReset();
  }
  getPolicy.mockResolvedValue({
    defaults: { near_enabled: false, embedding_enabled: false, threshold: 0.9 },
    bounds: BOUNDS,
  });
  getCounts.mockResolvedValue({
    unresolved_groups: 0,
    by_tier: {},
    scopes: [],
  });
});

describe("useDedupStore — the policy", () => {
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

describe("useDedupStore — loading the queue", () => {
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

describe("useDedupStore — paging", () => {
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
    listGroups.mockResolvedValueOnce({ groups: [group("g3")], total: 9, scan: {} });
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

describe("useDedupStore — the focus", () => {
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

describe("useDedupStore — cover and exclusion", () => {
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

describe("useDedupStore — verdicts and auto-advance", () => {
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
    getCounts.mockImplementation(
      () => new Promise(() => {}),
    );
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

describe("useDedupStore — reopen", () => {
  // Keep-separate records no operation, so this is the only way back from it.
  it("reopens a decided group and reloads the queue", async () => {
    servePage([], { total: 0 });
    reopenGroup.mockResolvedValue({
      signature: "g1",
      previous_verdict: "keep_separate",
      group_returned_to_queue: true,
    });
    const store = useDedupStore();
    listGroups.mockClear();
    const result = await store.reopen("g1");
    expect(reopenGroup).toHaveBeenCalledWith("g1");
    expect(result.group_returned_to_queue).toBe(true);
    expect(listGroups).toHaveBeenCalledTimes(1);
  });

  it("reports nothing when the reopen fails", async () => {
    reopenGroup.mockRejectedValue(new Error("nope"));
    const store = useDedupStore();
    expect(await store.reopen("g1")).toBe(null);
  });
});

describe("useDedupStore — the tier gate", () => {
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

describe("useDedupStore — the threshold", () => {
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

describe("useDedupStore — scope", () => {
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

describe("useDedupStore — counts", () => {
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

describe("useDedupStore — multi-select", () => {
  async function openWith(groups) {
    servePage(groups);
    const store = useDedupStore();
    await store.loadPolicy();
    await store.loadFirstPage();
    return store;
  }

  it("toggles per group and ranges from the anchor", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3"), group("g4")]);
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
  });

  it("stacks every selected group under one client batch id", async () => {
    const store = await openWith([group("g1"), group("g2"), group("g3")]);
    store.toggleSelected(0);
    store.toggleSelected(2);
    stackGroup.mockResolvedValue({});

    const result = await store.stack(store.groups.find((g) => g.signature === "g3"));
    expect(result).toBeTruthy();
    expect(stackGroup).toHaveBeenCalledTimes(2);
    expect(stackGroup.mock.calls.map((c) => c[0])).toEqual(["g1", "g3"]);
    const ids = stackGroup.mock.calls.map((c) => c[1].batchId);
    expect(ids[0]).toMatch(/^cli-/);
    expect(ids[1]).toBe(ids[0]);
    // The gesture is over: nothing stays selected, and the rows are gone.
    expect(store.selectionCount).toBe(0);
    expect(store.groups.map((g) => g.signature)).toEqual(["g2"]);
  });

  it("keeps every selected group separate in one gesture", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.toggleSelected(0);
    store.toggleSelected(1);
    keepGroupSeparate.mockResolvedValue({});
    const result = await store.keepSeparate(store.groups[0]);
    expect(result).toBeTruthy();
    expect(keepGroupSeparate).toHaveBeenCalledTimes(2);
    expect(store.selectionCount).toBe(0);
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

  it("stops at the first failure and keeps the unresolved groups selected", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.toggleSelected(0);
    store.toggleSelected(1);
    stackGroup.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("locked"));
    const result = await store.stack(store.groups[0]);
    expect(result).toBeNull();
    // g1 landed and left; g2 failed and stays both queued and selected.
    expect(store.groups.map((g) => g.signature)).toEqual(["g2"]);
    expect(store.isSelected("g2")).toBe(true);
  });

  it("clears the selection when the list reloads", async () => {
    const store = await openWith([group("g1"), group("g2")]);
    store.toggleSelected(0);
    await store.loadFirstPage();
    expect(store.selectionCount).toBe(0);
  });
});

describe("useDedupStore — scans and bulk auto-stack", () => {
  it("opening the queue queues a scan: the cache only fills when one runs", async () => {
    // The regression this pins: openQueue read the (empty) cache and nothing
    // ever requested a scan, so the queue stayed empty no matter which tiers
    // or threshold the user set.
    servePage([]);
    const store = useDedupStore();
    await store.openQueue({});
    expect(startScan).toHaveBeenCalledTimes(1);
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
