import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const { sessionResetHandlers } = vi.hoisted(() => ({
  sessionResetHandlers: new Set(),
}));

// The store reads `isReadOnly` off the apiClient singleton and talks to the
// backend only through `api/operations`. Both are mocked so no HTTP happens and
// every branch (read-only, 409, batch coalescing) can be driven directly.
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: (handler) => {
    sessionResetHandlers.add(handler);
    return () => sessionResetHandlers.delete(handler);
  },
  sessionContext: { value: null },
  apiClient: { get: vi.fn(), post: vi.fn() },
  isReadOnly: { value: false },
  // useWsStore mirrors its client id into the client's module scope on setup.
  setRequestClientId: vi.fn(),
}));

vi.mock("../api/operations", () => ({
  listOperations: vi.fn(),
  getUndoState: vi.fn(),
  undoLastOperation: vi.fn(),
  redoOperation: vi.fn(),
  undoOperation: vi.fn(),
  undoBatch: vi.fn(),
}));

import { isReadOnly as readOnly } from "../utils/apiClient";
import {
  getUndoState,
  listOperations,
  redoOperation,
  undoBatch,
  undoLastOperation,
  undoOperation,
} from "../api/operations";
import {
  DESTRUCTIVE_RECEIPT_MS,
  GHOST_ADOPT_TIMEOUT_MS,
  RECEIPT_MS,
  WS_REFRESH_DEBOUNCE_MS,
  formatOperationTime,
  iconForOpType,
  isDestructiveOpType,
  summarizeOperation,
  useOperationStore,
} from "./useOperationStore";
import { useNoticeStore } from "./useNoticeStore";
import { useWsStore } from "./useWsStore";

const MY_CLIENT = "client-me";
const OTHER_CLIENT = "client-them";

function deferred() {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function transitionSession() {
  for (const handler of sessionResetHandlers) handler();
}

function op(overrides = {}) {
  return {
    id: 1,
    batch_id: null,
    created_at: "2026-07-29T12:00:00",
    op_type: "pictures.tags.add",
    target_type: "picture",
    target_ids: [1, 2],
    target_count: 2,
    source: "ui",
    origin_client_id: MY_CLIENT,
    undoable: true,
    status: "applied",
    summary: "Added tag 'portrait'",
    ...overrides,
  };
}

/** Seed the mocked API with a history and an undo-state derived from it. */
function serve(rows, state = {}) {
  listOperations.mockResolvedValue(rows);
  getUndoState.mockResolvedValue({
    can_undo: rows.some((r) => r.status === "applied" && r.undoable),
    can_redo: rows.some((r) => r.status === "undone"),
    next_undo: rows.find((r) => r.status === "applied" && r.undoable) ?? null,
    next_redo: rows.find((r) => r.status === "undone") ?? null,
    ...state,
  });
}

/** Bring the store to a settled state so the NEXT refresh can narrate. */
async function primed(rows) {
  const store = useOperationStore();
  serve(rows);
  await store.refresh();
  return store;
}

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  sessionResetHandlers.clear();
  readOnly.value = false;
  for (const fn of [
    listOperations,
    getUndoState,
    undoLastOperation,
    redoOperation,
    undoOperation,
    undoBatch,
  ]) {
    fn.mockReset();
  }
  // A stable per-tab client id so "mine" vs "external" is decidable.
  useWsStore().clientId = MY_CLIENT;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("iconForOpType", () => {
  it("maps a known op type to its glyph", () => {
    expect(iconForOpType("pictures.tags.add")).toBe("mdi-tag-plus-outline");
    expect(iconForOpType("stacks.dissolve")).toBe("mdi-layers-off-outline");
  });

  it("falls through to a substring rule for a type this build has not seen", () => {
    // The scrapheap lane lands its own op types alongside these.
    expect(iconForOpType("pictures.scrapheap.move")).toBe(
      "mdi-trash-can-outline",
    );
    expect(iconForOpType("pictures.tags.something_new")).toBe(
      "mdi-tag-outline",
    );
  });

  it("falls back to a neutral glyph for anything unrecognised", () => {
    expect(iconForOpType("plugins.frobnicate")).toBe("mdi-history");
    expect(iconForOpType(undefined)).toBe("mdi-history");
  });
});

describe("isDestructiveOpType", () => {
  it("flags scrapheap and clearing operations", () => {
    expect(isDestructiveOpType("pictures.scrapheap.move")).toBe(true);
    expect(isDestructiveOpType("pictures.tags.clear")).toBe(true);
    expect(isDestructiveOpType("stacks.dissolve")).toBe(true);
  });

  // Keep cover only moves every copy but the cover to the scrapheap, but its op
  // type says what the user asked for rather than where the pictures went, so
  // none of the other patterns catch it. Without this it would take the ordinary
  // 5s receipt: the shortest undo window on the most consequential action.
  it("flags keeping only a stack's cover", () => {
    expect(isDestructiveOpType("stack.keep_cover_only")).toBe(true);
    expect(iconForOpType("stack.keep_cover_only")).toBe("mdi-layers-minus");
  });

  it("leaves ordinary edits alone", () => {
    expect(isDestructiveOpType("pictures.tags.add")).toBe(false);
    expect(isDestructiveOpType("pictures.score")).toBe(false);
  });
});

describe("summarizeOperation", () => {
  it("appends the target count when more than one picture is touched", () => {
    expect(summarizeOperation(op({ target_count: 12 }))).toBe(
      "Added tag 'portrait' · 12",
    );
  });

  it("groups a bulk count the way the rest of the product does", () => {
    expect(summarizeOperation(op({ target_count: 2700 }))).toBe(
      `Added tag 'portrait' · ${(2700).toLocaleString()}`,
    );
  });

  it("omits the count for a single picture", () => {
    expect(summarizeOperation(op({ target_count: 1 }))).toBe(
      "Added tag 'portrait'",
    );
  });

  it("humanises the op type when the server recorded no summary", () => {
    expect(summarizeOperation(op({ summary: null, target_count: 1 }))).toBe(
      "Pictures tags add",
    );
  });
});

describe("formatOperationTime", () => {
  it("returns an empty string for a missing or unparseable timestamp", () => {
    expect(formatOperationTime(null)).toBe("");
    expect(formatOperationTime("not a date")).toBe("");
  });

  it("parses the API's naive UTC timestamp as UTC, not as local time", () => {
    // Without the Z the browser would read this as local and shift the row.
    const withMarker = formatOperationTime("2026-07-29T12:00:00Z");
    const naive = formatOperationTime("2026-07-29T12:00:00");
    expect(naive).toBe(withMarker);
    expect(naive).toMatch(/\d{2}[:.]\d{2}/);
  });
});

describe("useOperationStore - refresh and the history split", () => {
  it("splits applied rows into past and undone rows into future", async () => {
    const store = useOperationStore();
    serve([
      op({ id: 5, status: "undone" }),
      op({ id: 4 }),
      op({ id: 3 }),
      op({ id: 2, status: "superseded" }),
    ]);
    await store.refresh();

    expect(store.past.map((o) => o.id)).toEqual([4, 3]);
    expect(store.future.map((o) => o.id)).toEqual([5]);
    // A superseded row was cleared by a later action; it is in neither list.
    expect(store.historyCount).toBe(2);
    expect(store.hasHistory).toBe(true);
  });

  it("skips the server entirely in a read-only session", async () => {
    readOnly.value = true;
    const store = useOperationStore();
    await store.refresh();
    expect(listOperations).not.toHaveBeenCalled();
    expect(getUndoState).not.toHaveBeenCalled();
  });

  it("keeps the last known state when the read fails", async () => {
    const store = await primed([op({ id: 4 })]);
    listOperations.mockRejectedValue(new Error("boom"));
    await store.refresh();
    expect(store.past.map((o) => o.id)).toEqual([4]);
  });
});

describe("useOperationStore - receipts narrate this client only", () => {
  it("raises no receipt on the first load", async () => {
    const store = useOperationStore();
    serve([op({ id: 9 })]);
    await store.refresh();
    expect(store.receipt).toBeNull();
  });

  it("raises a receipt for a new operation from this client", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10, summary: "Scored ★★★★" }), op({ id: 9 })]);
    await store.refresh();

    expect(store.receipt).toMatchObject({
      mode: "did",
      operationId: 10,
      summary: "Scored ★★★★ · 2",
      icon: "mdi-tag-plus-outline",
    });
  });

  // A dedup verdict now has TWO narration triggers: the response-driven
  // refresh (useDedupStore.narrateVerdictOperation, added because verdicts
  // once emitted no WS event) and the backend's own-origin pictures_changed
  // echo a moment later. The high-water mark is what keeps that ONE receipt:
  // the echo's refresh finds nothing newer to narrate.
  it("narrates one gesture once even when a WS echo follows the response-driven refresh", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([
      op({ id: 10, op_type: "dedup.stack", summary: "Stacked 3 duplicates" }),
      op({ id: 9 }),
    ]);
    // The verdict response triggers the first refresh, which narrates...
    await store.refresh();
    expect(store.receipt).toMatchObject({ mode: "did", operationId: 10 });
    store.dismissReceipt();
    expect(store.receipt).toBeNull();

    // ...and the WS echo's refresh, same rows, raises nothing new.
    await store.refresh();
    expect(store.receipt).toBeNull();
  });

  it("stays silent for an operation from another client", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10, origin_client_id: OTHER_CLIENT }), op({ id: 9 })]);
    await store.refresh();

    expect(store.receipt).toBeNull();
    // The stack still moved - silently is not the same as not at all.
    expect(store.past.map((o) => o.id)).toEqual([10, 9]);
  });

  it("marks an operation recorded for audit as not undoable", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10, undoable: false }), op({ id: 9 })]);
    await store.refresh();
    expect(store.receipt.mode).toBe("blocked");
  });

  it("counts the batch siblings as the coalesced +N", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([
      op({ id: 12, batch_id: "b1" }),
      op({ id: 11, batch_id: "b1" }),
      op({ id: 10, batch_id: "b1" }),
      op({ id: 9 }),
    ]);
    await store.refresh();
    expect(store.receipt.mergedCount).toBe(2);
  });

  // The chip-delete gesture: `tags/remove_all` + the reject that makes it
  // durable, both stamped with one client gesture id. Two operations land at
  // once and the user must see ONE receipt for the one thing they did.
  it("narrates a compound gesture as a single receipt", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([
      op({
        id: 11,
        batch_id: "cli-gesture-1",
        op_type: "pictures.tags.reject",
        summary: "Removed tag 'sunset'",
      }),
      op({
        id: 10,
        batch_id: "cli-gesture-1",
        op_type: "pictures.tags.remove_all",
        summary: "Removed tag 'sunset' from 2 pictures",
      }),
      op({ id: 9 }),
    ]);
    await store.refresh();

    expect(store.receipt).toMatchObject({
      mode: "did",
      operationId: 11,
      batchId: "cli-gesture-1",
      mergedCount: 1,
    });
  });

  it("reports no +N for a lone operation", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10 }), op({ id: 9 })]);
    await store.refresh();
    expect(store.receipt.mergedCount).toBe(0);
  });

  it("never stacks two receipts - the newest replaces the current one", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10 }), op({ id: 9 })]);
    await store.refresh();
    const first = store.receipt.key;
    serve([op({ id: 11, summary: "Second" }), op({ id: 10 }), op({ id: 9 })]);
    await store.refresh();

    expect(store.receipt.key).toBeGreaterThan(first);
    expect(store.receipt.operationId).toBe(11);
  });

  it("holds a destructive receipt for 8s and an ordinary one for 5s", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10 }), op({ id: 9 })]);
    await store.refresh();
    expect(store.receipt.durationMs).toBe(RECEIPT_MS);

    serve([
      op({ id: 11, op_type: "pictures.scrapheap.move" }),
      op({ id: 10 }),
      op({ id: 9 }),
    ]);
    await store.refresh();
    expect(store.receipt.durationMs).toBe(DESTRUCTIVE_RECEIPT_MS);
    expect(store.receipt.icon).toBe("mdi-trash-can-outline");
  });
});

describe("useOperationStore - the receipt countdown", () => {
  async function withLiveReceipt() {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10 }), op({ id: 9 })]);
    await store.refresh();
    return store;
  }

  it("dismisses itself when the window elapses", async () => {
    const store = await withLiveReceipt();
    vi.advanceTimersByTime(RECEIPT_MS - 1);
    expect(store.receipt).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(store.receipt).toBeNull();
  });

  it("pauses on hover and resumes from where it stopped (WCAG 2.2.1)", async () => {
    const store = await withLiveReceipt();
    vi.advanceTimersByTime(2000);
    store.pauseReceipt();
    vi.advanceTimersByTime(60000);
    expect(store.receipt).not.toBeNull();

    store.resumeReceipt();
    vi.advanceTimersByTime(2999);
    expect(store.receipt).not.toBeNull();
    vi.advanceTimersByTime(1);
    expect(store.receipt).toBeNull();
  });

  it("dismisses on demand", async () => {
    const store = await withLiveReceipt();
    store.dismissReceipt();
    expect(store.receipt).toBeNull();
  });
});

describe("useOperationStore - undo and redo", () => {
  it("undoes, then flips the receipt to the undone state with Redo offered", async () => {
    const store = await primed([op({ id: 10, summary: "Moved to Scrapheap" })]);
    undoLastOperation.mockResolvedValue({ operations: [op({ id: 10 })] });
    serve([op({ id: 10, status: "undone" })]);

    await store.undo();

    expect(undoLastOperation).toHaveBeenCalledTimes(1);
    expect(store.receipt).toMatchObject({ mode: "undone", operationId: 10 });
    expect(store.canRedo).toBe(true);
  });

  it("refuses to undo when there is nothing to undo", async () => {
    const store = useOperationStore();
    serve([]);
    await store.refresh();
    await store.undo();
    expect(undoLastOperation).not.toHaveBeenCalled();
  });

  it("refuses to undo in a read-only session", async () => {
    const store = await primed([op({ id: 10 })]);
    readOnly.value = true;
    await store.undo();
    expect(undoLastOperation).not.toHaveBeenCalled();
  });

  it("surfaces a 409 as a warning and re-reads the stack", async () => {
    const store = await primed([op({ id: 10 })]);
    undoLastOperation.mockRejectedValue({
      response: { status: 409, data: { detail: "Nothing to undo" } },
    });
    serve([op({ id: 10, status: "undone" })]);

    await store.undo();

    expect(store.receipt).toBeNull();
    // The stack was re-read rather than left showing a step that is gone.
    expect(store.past).toHaveLength(0);
  });

  it("redoes and narrates the replayed step", async () => {
    const store = useOperationStore();
    serve([op({ id: 10, status: "undone" })]);
    await store.refresh();
    redoOperation.mockResolvedValue({ operations: [op({ id: 10 })] });
    serve([op({ id: 10 })]);

    await store.redo();

    expect(redoOperation).toHaveBeenCalledTimes(1);
    expect(store.receipt).toMatchObject({ mode: "did", operationId: 10 });
  });

  it("undoes one whole bulk action by batch id", async () => {
    const store = await primed([
      op({ id: 11, batch_id: "b1" }),
      op({ id: 10, batch_id: "b1" }),
    ]);
    undoBatch.mockResolvedValue({ operations: [] });
    serve([
      op({ id: 11, batch_id: "b1", status: "undone" }),
      op({ id: 10, batch_id: "b1", status: "undone" }),
    ]);

    await store.undoBatchById("b1");

    expect(undoBatch).toHaveBeenCalledWith(
      "b1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(store.receipt).toMatchObject({ mode: "undone" });
  });
});

describe("useOperationStore - undoTo walks the stack", () => {
  it("undoes every step from the newest down to the clicked one", async () => {
    const store = await primed([
      op({ id: 13 }),
      op({ id: 12 }),
      op({ id: 11 }),
    ]);
    undoOperation.mockImplementation(async (id) => ({
      operations: [op({ id })],
    }));
    serve([
      op({ id: 13, status: "undone" }),
      op({ id: 12, status: "undone" }),
      op({ id: 11 }),
    ]);

    const reverted = await store.undoTo(12);

    expect(undoOperation.mock.calls.map(([id]) => id)).toEqual([13, 12]);
    expect(reverted).toBe(2);
    expect(store.receipt).toMatchObject({ mode: "undone", steps: 2 });
  });

  it("does not re-request a batch sibling the server already reverted", async () => {
    const store = await primed([
      op({ id: 13, batch_id: "b1" }),
      op({ id: 12, batch_id: "b1" }),
      op({ id: 11 }),
    ]);
    // Undoing 13 takes its whole batch (12) with it.
    undoOperation.mockResolvedValue({
      operations: [op({ id: 13 }), op({ id: 12 })],
    });
    serve([op({ id: 11 })]);

    await store.undoTo(12);

    expect(undoOperation).toHaveBeenCalledTimes(1);
    expect(undoOperation).toHaveBeenCalledWith(
      13,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("ignores a step that is not on the stack", async () => {
    const store = await primed([op({ id: 13 })]);
    expect(await store.undoTo(999)).toBe(0);
    expect(undoOperation).not.toHaveBeenCalled();
  });
});

describe("useOperationStore - WebSocket reconciliation", () => {
  /** Fire an event and let the trailing-edge debounce elapse. */
  async function fire(store, payload) {
    const settled = store.onPictureEvent(payload);
    await vi.advanceTimersByTimeAsync(WS_REFRESH_DEBOUNCE_MS);
    await settled;
  }

  it("narrates an own-origin picture event", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10 }), op({ id: 9 })]);
    await fire(store, {
      type: "pictures_changed",
      origin_client_id: MY_CLIENT,
    });
    expect(store.receipt).not.toBeNull();
  });

  it("updates the stack silently for an external picture event", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10, origin_client_id: OTHER_CLIENT }), op({ id: 9 })]);
    await fire(store, {
      type: "pictures_changed",
      origin_client_id: OTHER_CLIENT,
    });
    expect(store.receipt).toBeNull();
    expect(store.past.map((o) => o.id)).toEqual([10, 9]);
  });

  it("collapses a burst into one re-read", async () => {
    const store = await primed([op({ id: 9 })]);
    listOperations.mockClear();
    serve([op({ id: 10 }), op({ id: 9 })]);

    // A bulk action emits a continuous stream of these; without the debounce
    // the two reads would poll back to back for the whole run.
    for (let i = 0; i < 20; i += 1) {
      store.onPictureEvent({
        type: "pictures_changed",
        origin_client_id: MY_CLIENT,
      });
      await vi.advanceTimersByTimeAsync(50);
    }
    await vi.advanceTimersByTimeAsync(WS_REFRESH_DEBOUNCE_MS);
    expect(listOperations).toHaveBeenCalledTimes(1);
  });

  it("keeps the window narratable when a background event lands last", async () => {
    const store = await primed([op({ id: 9 })]);
    serve([op({ id: 10 }), op({ id: 9 })]);

    // The user's own change, then a background job's echo inside the same
    // window. Dropping the flag would silently swallow the user's receipt.
    store.onPictureEvent({
      type: "tags_changed",
      origin_client_id: MY_CLIENT,
    });
    const settled = store.onPictureEvent({ type: "pictures_changed" });
    await vi.advanceTimersByTimeAsync(WS_REFRESH_DEBOUNCE_MS);
    await settled;

    expect(store.receipt).not.toBeNull();
  });

  it("stays off the wire in a read-only session", async () => {
    readOnly.value = true;
    const store = useOperationStore();
    await store.onPictureEvent({ type: "pictures_changed" });
    await vi.advanceTimersByTimeAsync(WS_REFRESH_DEBOUNCE_MS);
    expect(listOperations).not.toHaveBeenCalled();
  });
});

describe("useOperationStore - a shortcut never does nothing silently", () => {
  it("says so when there is nothing to undo", async () => {
    const store = useOperationStore();
    serve([]);
    await store.refresh();
    await store.undo();

    expect(undoLastOperation).not.toHaveBeenCalled();
    expect(useNoticeStore().notices.map((n) => n.text)).toContain(
      "Nothing to undo.",
    );
  });

  it("says so when there is nothing to redo", async () => {
    const store = useOperationStore();
    serve([]);
    await store.refresh();
    await store.redo();
    expect(useNoticeStore().notices.map((n) => n.text)).toContain(
      "Nothing to redo.",
    );
  });

  it("queues repeated presses instead of dropping them", async () => {
    const store = await primed([op({ id: 12 }), op({ id: 11 })]);
    let resolveFirst;
    undoLastOperation
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveFirst = resolve)),
      )
      .mockResolvedValue({ operations: [] });

    const first = store.undo();
    // Hammering Ctrl+Z is the canonical idiom; a press landing mid-flight
    // must not be swallowed.
    store.undo();
    resolveFirst({ operations: [op({ id: 12 })] });
    await first;
    await vi.advanceTimersByTimeAsync(0);

    expect(undoLastOperation).toHaveBeenCalledTimes(2);
  });
});

describe("useOperationStore - failure shapes", () => {
  it("reports a locked picture set as a state, not as an error", async () => {
    const store = await primed([op({ id: 10 })]);
    undoLastOperation.mockRejectedValue({
      response: { status: 423, data: { detail: "Set 'Eval slice' is locked" } },
    });
    serve([op({ id: 10 })]);

    await store.undo();

    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("warning");
    expect(notice.text).toContain("locked");
  });

  it("retires a receipt that would still offer the action that failed", async () => {
    const store = await primed([op({ id: 10 })]);
    store.showReceipt(store.buildReceipt(op({ id: 10 }), "did"));
    undoLastOperation.mockRejectedValue({
      response: { status: 409, data: { detail: "Nothing to undo" } },
    });
    serve([op({ id: 10 })]);

    await store.undo();
    expect(store.receipt).toBeNull();
  });
});

describe("useOperationStore - nextUndoIsExternal", () => {
  it("is false for this client's own step", async () => {
    const store = await primed([op({ id: 10 })]);
    expect(store.nextUndoIsExternal).toBe(false);
  });

  it("is true for another tab's step and for a background job", async () => {
    const store = await primed([
      op({ id: 10, origin_client_id: OTHER_CLIENT }),
    ]);
    expect(store.nextUndoIsExternal).toBe(true);

    const other = await primed([op({ id: 11, origin_client_id: null })]);
    expect(other.nextUndoIsExternal).toBe(true);
  });

  it("is false when there is nothing to undo at all", async () => {
    const store = useOperationStore();
    serve([]);
    await store.refresh();
    expect(store.nextUndoIsExternal).toBe(false);
  });
});

describe("useOperationStore - reset", () => {
  it("drops the history, the receipt and the enablement flags", async () => {
    const store = await primed([op({ id: 10 })]);
    store.showReceipt(store.buildReceipt(op({ id: 10 }), "did"));
    store.reset();

    expect(store.operations).toEqual([]);
    expect(store.receipt).toBeNull();
    expect(store.canUndo).toBe(false);
    expect(store.canRedo).toBe(false);
    expect(store.loaded).toBe(false);
  });

  it("discards history reads that settle after the session changes", async () => {
    const store = useOperationStore();
    const rows = deferred();
    const state = deferred();
    listOperations.mockReturnValueOnce(rows.promise);
    getUndoState.mockReturnValueOnce(state.promise);

    const refreshing = store.refresh();
    transitionSession();
    rows.resolve([op({ id: 99 })]);
    state.resolve({ can_undo: true, next_undo: op({ id: 99 }) });
    await refreshing;

    expect(store.operations).toEqual([]);
    expect(store.loaded).toBe(false);
    expect(store.canUndo).toBe(false);
  });

  it("does not raise a receipt when an undo settles in a later session", async () => {
    const store = await primed([op({ id: 10 })]);
    const undoResult = deferred();
    undoLastOperation.mockReturnValueOnce(undoResult.promise);

    const undoing = store.undo();
    transitionSession();
    undoResult.resolve({ operations: [op({ id: 10 })] });
    await undoing;

    expect(store.busy).toBe(false);
    expect(store.receipt).toBeNull();
    expect(store.operations).toEqual([]);
  });

  it("does not raise a receipt when the session changes during post-undo refresh", async () => {
    const store = await primed([op({ id: 10 })]);
    undoLastOperation.mockResolvedValue({ operations: [op({ id: 10 })] });
    const rows = deferred();
    const state = deferred();
    listOperations.mockReturnValueOnce(rows.promise);
    getUndoState.mockReturnValueOnce(state.promise);

    const undoing = store.undo();
    await Promise.resolve();
    transitionSession();
    rows.resolve([]);
    state.resolve({ can_undo: false, can_redo: false });
    await undoing;

    expect(store.receipt).toBeNull();
    expect(store.operations).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Ghost tiles - the `none → pending → committed` machine
// ---------------------------------------------------------------------------
//
// A move to the Scrapheap leaves its tiles on screen, ghosted, for as long as
// the undo is one click away. The window is the RECEIPT's, never a clock of its
// own, so every test below drives the ghosts by driving the receipt - that is
// the only way the two can be shown not to drift.

const MOVE = "pictures.scrapheap.move";

/** Raise the receipt a scrapheap move would raise, so it adopts the set. */
function raiseMoveReceipt(store, overrides = {}) {
  const row = op({
    id: 99,
    op_type: MOVE,
    summary: "Moved 2 pictures to the Scrapheap",
    ...overrides,
  });
  store.showReceipt(
    store.buildReceipt(row, row.undoable === false ? "blocked" : "did"),
  );
  return row;
}

describe("useOperationStore - ghost tiles", () => {
  it("ghosts a moved set and holds it for the receipt's destructive dwell", () => {
    const store = useOperationStore();
    expect(store.markGhosted([1, 2])).toBe(true);
    expect(store.ghostPictureIds).toEqual([1, 2]);
    expect(store.ghostState).toBe("pending");

    raiseMoveReceipt(store);
    expect(store.receipt.durationMs).toBe(DESTRUCTIVE_RECEIPT_MS);

    // Still ghosted one tick before the dwell ends…
    vi.advanceTimersByTime(DESTRUCTIVE_RECEIPT_MS - 1);
    expect(store.ghostState).toBe("pending");
    expect(store.collapsingPictureIds).toEqual([]);

    // …and handed to the grid the moment it does.
    vi.advanceTimersByTime(1);
    expect(store.ghostState).toBe("none");
    expect(store.takeCollapsingGhosts()).toEqual([1, 2]);
    expect(store.collapsingPictureIds).toEqual([]);
  });

  it("freezes the ghost window while the receipt is hovered", () => {
    const store = useOperationStore();
    store.markGhosted([1]);
    raiseMoveReceipt(store);

    vi.advanceTimersByTime(3000);
    store.pauseReceipt();
    // The whole remaining dwell passes with the pointer on the pill.
    vi.advanceTimersByTime(DESTRUCTIVE_RECEIPT_MS * 3);
    expect(store.ghostState).toBe("pending");
    expect(store.collapsingPictureIds).toEqual([]);

    store.resumeReceipt();
    vi.advanceTimersByTime(DESTRUCTIVE_RECEIPT_MS - 3000 - 1);
    expect(store.ghostState).toBe("pending");
    vi.advanceTimersByTime(1);
    expect(store.takeCollapsingGhosts()).toEqual([1]);
  });

  it("collapses the first set when a second move replaces the receipt in place", () => {
    const store = useOperationStore();
    store.markGhosted([1, 2]);
    raiseMoveReceipt(store, { id: 99 });

    store.markGhosted([3, 4]);
    expect(store.ghostPictureIds).toEqual([3, 4]);
    // The first set's one-click undo went with its pill, so its tiles go too.
    expect(store.takeCollapsingGhosts()).toEqual([1, 2]);
  });

  it("collapses the set when an unrelated action takes the pill's slot", () => {
    const store = useOperationStore();
    store.markGhosted([5]);
    raiseMoveReceipt(store);

    // A tag edit raises its own receipt; the move's Undo is no longer offered.
    store.showReceipt(store.buildReceipt(op({ id: 100 }), "did"));
    expect(store.ghostState).toBe("none");
    expect(store.takeCollapsingGhosts()).toEqual([5]);
  });

  it("collapses immediately when the operation turns out not to be undoable", () => {
    const store = useOperationStore();
    store.markGhosted([6]);
    raiseMoveReceipt(store, { undoable: false });
    // A ghost promising an undo that does not exist is a lie.
    expect(store.takeCollapsingGhosts()).toEqual([6]);
  });

  it("collapses a set that no receipt ever adopts", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const store = useOperationStore();
    store.markGhosted([7]);

    vi.advanceTimersByTime(GHOST_ADOPT_TIMEOUT_MS);
    expect(store.ghostState).toBe("none");
    expect(store.takeCollapsingGhosts()).toEqual([7]);
    // Never silent: a dropped socket or an unrecorded operation is a real
    // condition and has to be traceable.
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("un-ghosts in place when an undo puts the pictures back", async () => {
    const store = await primed([op({ id: 10, op_type: MOVE })]);
    store.markGhosted([1, 2]);
    raiseMoveReceipt(store);

    undoLastOperation.mockResolvedValue({
      operations: [op({ id: 10, op_type: MOVE, status: "undone" })],
      restored_picture_ids: [1, 2],
      scrapheaped_picture_ids: [],
    });
    serve([op({ id: 10, op_type: MOVE, status: "undone" })]);
    await store.undo();

    expect(store.ghostState).toBe("none");
    // Un-ghosted, NOT collapsed: the tiles stay exactly where they are, so the
    // undo costs no refetch and no flash.
    expect(store.collapsingPictureIds).toEqual([]);
  });

  it("re-ghosts on redo, so the offer is symmetric", async () => {
    const store = await primed([
      op({ id: 10, op_type: MOVE, status: "undone" }),
    ]);
    redoOperation.mockResolvedValue({
      operations: [op({ id: 10, op_type: MOVE })],
      restored_picture_ids: [],
      scrapheaped_picture_ids: [1, 2],
    });
    serve([op({ id: 10, op_type: MOVE })]);
    await store.redo();

    expect(store.ghostPictureIds).toEqual([1, 2]);
    expect(store.ghostState).toBe("pending");
  });

  it("forgets the set silently when the grid was rebuilt underneath it", () => {
    const store = useOperationStore();
    store.markGhosted([1, 2]);
    raiseMoveReceipt(store);
    store.dropGhosts();

    expect(store.ghostState).toBe("none");
    // No collapse: the pictures are already absent from the refetched grid.
    expect(store.collapsingPictureIds).toEqual([]);
    // …and the receipt is untouched - undo is still on offer.
    expect(store.receipt).not.toBeNull();
  });

  it("declines to ghost in a read-only session", () => {
    readOnly.value = true;
    const store = useOperationStore();
    // No undo endpoints, so no window to hold the tiles open for; the caller
    // drops them outright instead.
    expect(store.markGhosted([1])).toBe(false);
    expect(store.ghostState).toBe("none");
  });

  it("drops every ghost on reset", () => {
    const store = useOperationStore();
    store.markGhosted([1]);
    store.reset();
    expect(store.ghostState).toBe("none");
    expect(store.ghostPictureIds).toEqual([]);
    expect(store.collapsingPictureIds).toEqual([]);
  });
});

// A second sentence on the SAME pill, for what an action deliberately did not
// do. Keep cover only skips a whole stack when a locked set or a character link
// would lose data, and that belongs beside the move it reports: splitting one
// action across a pill and a notice is how the half that needed a decision gets
// dismissed along with the half that did not.
describe("the receipt's second sentence", () => {
  const KEEP_COVER = {
    id: 2,
    op_type: "stack.keep_cover_only",
    summary: "Kept the cover of 3 stacks · 414 pictures to the Scrapheap",
    target_count: 414,
  };

  it("appends the caller's note to the operation the note names", async () => {
    const store = await primed([op()]);
    store.noteNextReceipt(
      "stack.keep_cover_only",
      "2 stacks skipped: held by a locked picture set.",
    );
    serve([op(KEEP_COVER), op()]);
    await store.refresh();

    expect(store.receipt.summary).toContain("414 pictures to the Scrapheap");
    expect(store.receipt.note).toBe(
      "2 stacks skipped: held by a locked picture set.",
    );
  });

  // A note that outlived its action would eventually describe an unrelated one,
  // and a wrong second sentence is worse than none.
  it("never lands on an operation of a different type", async () => {
    const store = await primed([op()]);
    store.noteNextReceipt("stack.keep_cover_only", "2 stacks skipped.");
    serve([op({ id: 2 }), op()]);
    await store.refresh();
    expect(store.receipt.note).toBe("");

    // …and it is consumed rather than carried forward to the next receipt.
    serve([op(KEEP_COVER), op({ id: 2 }), op()]);
    await store.refresh();
    expect(store.receipt.note).toBe("");
  });

  it("treats a blank note as no note", async () => {
    const store = await primed([op()]);
    store.noteNextReceipt("stack.keep_cover_only", "");
    serve([op(KEEP_COVER), op()]);
    await store.refresh();
    expect(store.receipt.note).toBe("");
  });
});
