// The one model move, and the poll that watches it.
//
// Three of these pin decisions that are easy to lose. A finished job must be
// reported EXACTLY once, or a completed move announces itself on every mount.
// Progress is counted in items and not bytes, because a same-drive move copies
// zero bytes. And a poll that fails is not a move that failed.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const startModelMove = vi.fn();
const getModelMoveStatus = vi.fn();
const cancelModelMove = vi.fn();

vi.mock("../api/modelMoves", () => ({
  startModelMove: (...args) => startModelMove(...args),
  getModelMoveStatus: (...args) => getModelMoveStatus(...args),
  cancelModelMove: (...args) => cancelModelMove(...args),
}));

// The two stores the completion path refreshes. Stubbed to their one method so
// this suite does not drag the whole shelf and folder registry in behind them.
const fetchRows = vi.fn();
const refreshFolders = vi.fn();
vi.mock("./useModelShelfStore", () => ({
  useModelShelfStore: () => ({ fetchRows }),
}));
vi.mock("./useModelFoldersStore", () => ({
  useModelFoldersStore: () => ({ refresh: refreshFolders }),
}));

import { moveReceipt, useModelMovesStore } from "./useModelMovesStore";
import { useNoticeStore } from "./useNoticeStore";

const ITEMS = [{ folder_id: 1, relpath: "a.safetensors" }];

function snapshot(overrides = {}) {
  return {
    status: "running",
    total: 2,
    done: 0,
    bytes_to_copy: 0,
    cancel_requested: false,
    results: [],
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
  startModelMove.mockReset().mockResolvedValue(snapshot());
  getModelMoveStatus.mockReset().mockResolvedValue(snapshot());
  cancelModelMove.mockReset();
  fetchRows.mockReset().mockResolvedValue(undefined);
  refreshFolders.mockReset().mockResolvedValue(undefined);
});

describe("starting a move", () => {
  it("refuses a second one while the first runs", async () => {
    // The server's rule, not a convenience: two moves would race for the same
    // free space that both of them checked before either started.
    const store = useModelMovesStore();
    expect(await store.start(2, ITEMS)).toBe(true);
    expect(await store.start(3, ITEMS)).toBe(false);
    expect(startModelMove).toHaveBeenCalledTimes(1);
  });

  it("reports a refusal as a notice and stays idle", async () => {
    // The POST plans the whole batch before the first byte, so a 4xx here is a
    // reason and NOT a half-done move. Showing a failed job would say otherwise.
    const store = useModelMovesStore();
    startModelMove.mockRejectedValue(new Error("no room"));
    expect(await store.start(2, ITEMS)).toBe(false);
    expect(store.status).toBe("idle");
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });

  it("does nothing with an empty item list", async () => {
    const store = useModelMovesStore();
    expect(await store.start(2, [])).toBe(false);
    expect(startModelMove).not.toHaveBeenCalled();
  });
});

describe("watching one to its end", () => {
  it("reports the finish exactly once and refreshes both stores", async () => {
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    getModelMoveStatus.mockResolvedValue(
      snapshot({
        status: "finished",
        done: 2,
        results: [{ status: "moved" }, { status: "moved" }],
      }),
    );

    await store.poll();
    expect(useNoticeStore().notices.at(-1).text).toBe("Moved 2 files.");
    expect(fetchRows).toHaveBeenCalledTimes(1);
    expect(refreshFolders).toHaveBeenCalledTimes(1);

    // The second reading sees the same finished job. Reporting it again is how
    // a completed move announces itself forever.
    await store.poll();
    expect(useNoticeStore().notices).toHaveLength(1);
  });

  it("says nothing about a job it was not watching", async () => {
    // A page load lands on the LAST finished job, whose receipt was already
    // shown to whoever started it.
    const store = useModelMovesStore();
    getModelMoveStatus.mockResolvedValue(
      snapshot({ status: "finished", done: 1, results: [{ status: "moved" }] }),
    );
    await store.poll();
    expect(useNoticeStore().notices).toHaveLength(0);
  });

  it("adopts a move already running but never a finished one", async () => {
    const store = useModelMovesStore();
    getModelMoveStatus.mockResolvedValue(snapshot({ status: "finished" }));
    await store.adopt();
    expect(store.running).toBe(false);

    getModelMoveStatus.mockResolvedValue(snapshot({ status: "running" }));
    await store.adopt();
    expect(store.running).toBe(true);
  });

  it("holds a run that lost files instead of letting a notice expire", async () => {
    // #900: the failure is the one outcome that must not clear itself after
    // six seconds. It is held so the shelf can put it back in the corner the
    // progress came from, and only a dismissal takes it away.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    getModelMoveStatus.mockResolvedValue(
      snapshot({
        status: "finished",
        done: 2,
        results: [{ status: "moved" }, { status: "failed" }],
      }),
    );

    await store.poll();
    expect(store.failure).toBe(
      "Moved 1 file. 1 file could not be moved and stayed put.",
    );
    expect(useNoticeStore().notices).toHaveLength(0);

    store.dismissFailure();
    expect(store.failure).toBe("");
  });

  it("clears a held failure when the next move starts", async () => {
    // A stale red card on top of live progress would report the wrong run.
    const store = useModelMovesStore();
    store.failure = "Moved 1 file. 1 file could not be moved and stayed put.";
    await store.start(2, ITEMS);
    expect(store.failure).toBe("");
  });

  it("does not turn a failed poll into a failed move", async () => {
    // The move is still running on the server. Reporting an outcome we never
    // read would be inventing one.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    getModelMoveStatus.mockRejectedValue(new Error("offline"));
    await store.poll();
    expect(useNoticeStore().notices).toHaveLength(0);
    expect(store.status).toBe("running");
  });

  it("counts progress in items, because a same-drive move copies no bytes", () => {
    const store = useModelMovesStore();
    store.job = snapshot({ total: 4, done: 1, bytes_to_copy: 0 });
    expect(store.percent).toBe(25);
  });
});

describe("the move receipt", () => {
  it("leads with what landed and then names each way one did not", () => {
    expect(
      moveReceipt([
        { status: "moved" },
        { status: "copied" },
        { status: "skipped" },
        { status: "failed" },
      ]),
    ).toBe(
      "Moved 2 files. 1 file was already there. 1 file could not be moved and stayed put.",
    );
  });

  it("keeps 'stopped before we reached it' apart from 'failed'", () => {
    // Nothing was attempted on a cancelled item, so nothing is half-done. A
    // receipt calling it a failure would send the reader looking for damage.
    expect(
      moveReceipt(
        [{ status: "moved" }, { status: "cancelled" }, { status: "cancelled" }],
        true,
      ),
    ).toBe("Stopped after moving 1 file. 2 files were left where they were.");
  });

  it("says plainly when a cancel beat the first file", () => {
    expect(moveReceipt([{ status: "cancelled" }], true)).toBe(
      "Stopped before anything moved. 1 file was left where it was.",
    );
  });

  it("names a file that moved without its training previews", () => {
    // The server keeps such a file `moved` on purpose — losing a preview must
    // not cost the weights — so the status tallies cannot see it and a receipt
    // built from them alone would call this a clean move. `importReceipt` says
    // the same thing on the import side; a loss visible on one verb and silent
    // on the other is the half-finished version of this.
    expect(
      moveReceipt([
        { status: "moved" },
        { status: "moved", detail: "Samples were not carried: …" },
      ]),
    ).toBe("Moved 2 files. 1 file moved without its training previews.");
  });

  it("does not claim a move when every file was already there", () => {
    expect(moveReceipt([{ status: "skipped" }, { status: "skipped" }])).toBe(
      "Nothing moved. 2 files were already there.",
    );
  });
});
