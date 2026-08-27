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

  it("keeps watching after a failed reading and consumes the finish", async () => {
    // #1018: a failed read is "status unknown", not "stop observing forever".
    // Giving up on one left `busy` true off a stale `running` snapshot, which
    // disabled every move entry point AND the adoption path that could have
    // recovered it - so the tab could only be freed by a reload.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    getModelMoveStatus
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(
        snapshot({
          status: "finished",
          done: 2,
          results: [{ status: "moved" }, { status: "moved" }],
        }),
      );

    await vi.advanceTimersByTimeAsync(1000); // the reading that fails
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
    expect(store.busy).toBe(true);
    expect(useNoticeStore().notices).toHaveLength(0);

    // The retry is BACKED OFF, so it is not due one interval later.
    await vi.advanceTimersByTimeAsync(1000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
    expect(store.busy).toBe(true);

    await vi.advanceTimersByTimeAsync(1000); // two intervals after the failure
    expect(getModelMoveStatus).toHaveBeenCalledTimes(2);
    expect(store.status).toBe("finished");
    expect(store.busy).toBe(false);
    expect(useNoticeStore().notices.at(-1).text).toBe("Moved 2 files.");
    expect(fetchRows).toHaveBeenCalledTimes(1);
    expect(refreshFolders).toHaveBeenCalledTimes(1);

    // And then it stops: a loop that outlives its job keeps a timer and a
    // request per second going for the rest of the session.
    await vi.advanceTimersByTimeAsync(60000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(2);
  });

  it("reports the finish even when the refresh behind it fails", async () => {
    // The move landed and the receipt is the news. The two reads that follow it
    // are a repaint, and letting one of them reject out of a timer callback is
    // an unhandled rejection nobody is positioned to catch - so it is caught
    // and said out loud here instead.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    fetchRows.mockRejectedValue(new Error("offline"));
    getModelMoveStatus.mockResolvedValue(
      snapshot({ status: "finished", done: 2, results: [{ status: "moved" }] }),
    );

    await vi.advanceTimersByTimeAsync(1000);
    expect(useNoticeStore().notices.at(-1).text).toBe("Moved 1 file.");
    expect(store.busy).toBe(false);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();

    // And the store is idle rather than wedged: the next move takes the slot
    // and gets a loop of its own.
    fetchRows.mockResolvedValue(undefined);
    getModelMoveStatus.mockClear().mockResolvedValue(snapshot());
    startModelMove.mockResolvedValue(snapshot());
    expect(await store.start(3, ITEMS)).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
  });

  it("does not let a reading throw out of the timer callback", async () => {
    // A terminal snapshot whose `results` is not a list: the tally of "did
    // anything fail?" throws on it, from inside a timer callback where there is
    // nobody left to catch it. That receipt is lost either way - an unhandled
    // rejection that also strands the loop is the part that must not happen.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    getModelMoveStatus.mockResolvedValue(
      snapshot({ status: "finished", results: {} }),
    );

    await vi.advanceTimersByTimeAsync(1000);
    expect(error).toHaveBeenCalled();
    error.mockRestore();

    // The store is idle rather than wedged, and the next move gets a loop.
    expect(store.busy).toBe(false);
    startModelMove.mockResolvedValue(snapshot());
    getModelMoveStatus.mockClear().mockResolvedValue(snapshot());
    expect(await store.start(3, ITEMS)).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
  });

  it("still reports a job that finished before its own POST returned", async () => {
    // A same-drive rename of a handful of files can beat the read the start
    // route does on its way out, so the accepted job comes back ALREADY
    // terminal. Ending the loop on "the job is not running" rather than on
    // "the finish has been reported" loses the receipt and both refreshes here,
    // because the one reading that would have consumed it is the one that fails.
    const store = useModelMovesStore();
    const finished = snapshot({
      status: "finished",
      done: 1,
      results: [{ status: "moved" }],
    });
    startModelMove.mockResolvedValue(finished);
    getModelMoveStatus
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(finished);

    await store.start(2, ITEMS);
    await vi.advanceTimersByTimeAsync(5000);
    expect(useNoticeStore().notices.at(-1).text).toBe("Moved 1 file.");
    expect(fetchRows).toHaveBeenCalledTimes(1);
  });

  it("backs off to a ceiling rather than doubling away from the owner", async () => {
    // Unbounded doubling would put the reading that notices the server came
    // back minutes out. Bounded, five minutes of outage is tens of readings,
    // not the nine an uncapped 2^n would manage.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    getModelMoveStatus.mockReset().mockRejectedValue(new Error("offline"));

    await vi.advanceTimersByTimeAsync(300000);
    expect(getModelMoveStatus.mock.calls.length).toBeGreaterThan(15);
    expect(store.busy).toBe(true);
  });

  it("never has two readings in flight at once", async () => {
    // The next reading is booked only once the current one lands. An interval
    // would stack a request per tick on top of a status read that is hanging.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    getModelMoveStatus.mockReset().mockReturnValue(new Promise(() => {}));

    await vi.advanceTimersByTimeAsync(10000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
  });

  it("abandons a reading already in flight when the session resets", async () => {
    // Host paths and folder ids are owner-only, so the new session has no
    // standing to watch a job the old one started - including the answer to a
    // request it had already sent.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    let answer;
    getModelMoveStatus
      .mockReset()
      .mockReturnValue(new Promise((resolve) => (answer = resolve)));

    await vi.advanceTimersByTimeAsync(1000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
    store.resetForSession();
    answer(
      snapshot({ status: "finished", done: 2, results: [{ status: "moved" }] }),
    );

    await vi.advanceTimersByTimeAsync(60000);
    expect(store.job).toBe(null);
    expect(store.busy).toBe(false);
    expect(useNoticeStore().notices).toHaveLength(0);
    expect(fetchRows).not.toHaveBeenCalled();
    // The loop is gone with the session, not merely quiet for one tick.
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
  });

  it("does not leave a second loop behind when a move follows a reset", async () => {
    // The abandoned reading lands after the new session has started a move of
    // its own, so "is anything still being watched?" is the wrong question -
    // something is, just not this loop's job. Two loops on one job is two
    // requests a second and two receipts for the same finish.
    const store = useModelMovesStore();
    await store.start(2, ITEMS);
    let answer;
    getModelMoveStatus
      .mockReset()
      .mockReturnValueOnce(new Promise((resolve) => (answer = resolve)))
      .mockResolvedValue(snapshot());

    await vi.advanceTimersByTimeAsync(1000); // the abandoned reading, in flight
    store.resetForSession();
    await store.start(3, ITEMS);
    answer(snapshot());

    // From here every reading hangs, so the new session's loop can account for
    // exactly one of them and a second could only be a loop still alive from
    // before the reset.
    getModelMoveStatus.mockClear().mockReturnValue(new Promise(() => {}));
    await vi.advanceTimersByTimeAsync(60000);
    expect(getModelMoveStatus).toHaveBeenCalledTimes(1);
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
    // The server keeps such a file `moved` on purpose - losing a preview must
    // not cost the weights - so the status tallies cannot see it and a receipt
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
