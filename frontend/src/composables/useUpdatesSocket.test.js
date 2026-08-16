// The socket's ROUTING, not the decision tables behind it.
//
// `useUpdatesSocket` owns which store each `/ws/updates` message reaches;
// `useGridRealtimeSync` and `useDedupStore` own what each one then does, and
// both have their own tests. What is pinned here is the seam: a scrapheap move
// has to reach the Duplicates queue's store, which is the wiring that was
// missing when a deleted picture kept its tile in an open queue.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { ref } from "vue";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

// Hoisted with the `vi.mock` factories that close over them: the factories run
// before the module body, so a plain `const` here would still be uninitialised.
const {
  applyPictureEvent,
  handleMessage,
  isReadOnly,
  isFullRestoreRequestInFlight,
  prepareForFullRestoreTransition,
  reloadAfterFullRestore,
} = vi.hoisted(() => ({
  applyPictureEvent: vi.fn(() => ({ action: "ignored", reason: "test" })),
  handleMessage: vi.fn(() => ({ action: "ignored", reason: "test" })),
  // Only ever read as `.value`, so a bare box stands in for the real ref
  // without dragging Vue into the hoisted block.
  isReadOnly: { value: false },
  isFullRestoreRequestInFlight: vi.fn(() => false),
  prepareForFullRestoreTransition: vi.fn(),
  reloadAfterFullRestore: vi.fn(),
}));

vi.mock("../stores/useDedupStore", () => ({
  useDedupStore: () => ({ applyPictureEvent }),
}));

vi.mock("./useGridRealtimeSync", () => ({
  useGridRealtimeSync: () => ({ handleMessage, flushNow: vi.fn() }),
}));

vi.mock("../utils/fullRestoreTransition", () => ({
  isFullRestoreRequestInFlight,
  prepareForFullRestoreTransition,
  reloadAfterFullRestore,
}));

vi.mock("../utils/apiClient", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, isReadOnly };
});

import {
  handleUpdatesSocketClose,
  useUpdatesSocket,
  VRAM_OOM_NOTICE_KEY,
} from "./useUpdatesSocket";
import { useNoticeStore } from "../stores/useNoticeStore";
import { API_BASE_URL } from "../utils/apiClient";

describe("updates socket close lifecycle", () => {
  it("reloads a non-initiating client after a library switch", () => {
    const reload = vi.fn();
    const reconnect = vi.fn();

    handleUpdatesSocketClose(
      { code: 1012, reason: "Library switched" },
      { reload, reconnect },
    );

    expect(reload).toHaveBeenCalledOnce();
    expect(reconnect).not.toHaveBeenCalled();
  });

  it("reconnects after an ordinary transport close", () => {
    const reload = vi.fn();
    const reconnect = vi.fn();

    handleUpdatesSocketClose({ code: 1006, reason: "" }, { reload, reconnect });

    expect(reload).not.toHaveBeenCalled();
    expect(reconnect).toHaveBeenCalledOnce();
  });
});

/** The last socket the composable opened, so a test can push a frame at it. */
let socket = null;
/** The component the composable is mounted in, unmounted after each case. */
let host = null;
let socketCount = 0;

class FakeWebSocket {
  static OPEN = 1;
  constructor(url) {
    socketCount += 1;
    this.url = url;
    this.readyState = 1;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    socket = this;
  }
  send() {}
  close(code = 1000) {
    this.onclose?.({ code });
  }
}

/** Deliver one server frame, exactly as the browser would. */
function receive(payload) {
  socket.onmessage({ data: JSON.stringify(payload) });
}

beforeEach(() => {
  setActivePinia(createPinia());
  socket = null;
  socketCount = 0;
  isReadOnly.value = false;
  applyPictureEvent.mockClear();
  handleMessage.mockClear();
  reloadAfterFullRestore.mockClear();
  prepareForFullRestoreTransition.mockClear();
  isFullRestoreRequestInFlight.mockReset();
  isFullRestoreRequestInFlight.mockReturnValue(false);
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  host?.unmount();
  host = null;
});

/**
 * Open the socket from inside a component, as App.vue does.
 *
 * The composable registers an `onUnmounted` cleanup, so calling it bare would
 * both warn and leave the socket open between cases.
 */
function connect() {
  host = mount({
    setup() {
      useUpdatesSocket({
        gridContainer: ref(null),
        refreshSidebar: vi.fn(),
        refreshSidebarPicturesDebounced: vi.fn(),
      }).connectUpdatesSocket();
      return () => null;
    },
  });
}

describe("useUpdatesSocket: routing to the duplicate queue", () => {
  it("hands a scrapheap move to the dedup store", () => {
    connect();
    const payload = {
      type: "pictures_changed",
      change_kind: "removed",
      picture_ids: [7, 8],
      source: "ui",
    };
    receive(payload);
    expect(applyPictureEvent).toHaveBeenCalledWith(payload);
  });

  // The queue never applies a scrapheap move optimistically, so its own tab's
  // echo is as new to it as another tab's: unlike the grid, which suppresses it.
  it("hands over its own tab's echo too", () => {
    connect();
    receive({
      type: "pictures_changed",
      change_kind: "removed",
      picture_ids: [7],
      source: "ui",
      origin_client_id: "whoever",
    });
    expect(applyPictureEvent).toHaveBeenCalledTimes(1);
  });

  it("hands over a restore", () => {
    connect();
    receive({
      type: "pictures_changed",
      change_kind: "restored",
      picture_ids: [7],
      source: "ui",
    });
    expect(applyPictureEvent).toHaveBeenCalledTimes(1);
  });

  it("leaves other message types alone", () => {
    connect();
    receive({ type: "tags_changed", picture_ids: [7] });
    receive({ type: "picture_imported", picture_ids: [7] });
    expect(applyPictureEvent).not.toHaveBeenCalled();
  });

  // A share/read-only session has no duplicate queue to keep current, and its
  // token cannot read the dedup routes a refill would call.
  it("stays out of a read-only session", () => {
    isReadOnly.value = true;
    connect();
    receive({
      type: "pictures_changed",
      change_kind: "removed",
      picture_ids: [7],
      source: "ui",
    });
    expect(applyPictureEvent).not.toHaveBeenCalled();
  });
});

describe("useUpdatesSocket: connection lifecycle", () => {
  it("opens the socket on the configured backend origin", () => {
    connect();
    const backend = new URL(API_BASE_URL);
    const opened = new URL(socket.url);
    expect(opened.protocol).toBe(
      backend.protocol === "https:" ? "wss:" : "ws:",
    );
    expect(opened.host).toBe(backend.host);
    expect(opened.pathname).toBe("/api/v1/ws/updates");
  });

  it("does not reconnect after an intentional disconnect", async () => {
    vi.useFakeTimers();
    connect();
    expect(socketCount).toBe(1);

    host.unmount();
    host = null;
    await vi.advanceTimersByTimeAsync(2500);

    expect(socketCount).toBe(1);
    vi.useRealTimers();
  });

  it("reconnects after an ordinary unexpected close", async () => {
    vi.useFakeTimers();
    connect();
    socket.onclose({ code: 1006 });
    await vi.advanceTimersByTimeAsync(2000);
    expect(socketCount).toBe(2);
    vi.useRealTimers();
  });

  it("hard reloads a non-initiating tab from the pre-drain restore event", async () => {
    vi.useFakeTimers();
    connect();
    const drainedSocket = socket;

    receive({ type: "restore_started", resource_type: "full" });
    expect(prepareForFullRestoreTransition).toHaveBeenCalledTimes(1);
    expect(reloadAfterFullRestore).not.toHaveBeenCalled();
    drainedSocket.onclose({ code: 1012 });
    drainedSocket.onclose({ code: 1012 });
    drainedSocket.onmessage({
      data: JSON.stringify({
        type: "restore_completed",
        resource_type: "full",
      }),
    });
    await vi.advanceTimersByTimeAsync(2500);

    expect(reloadAfterFullRestore).toHaveBeenCalledTimes(1);
    expect(socketCount).toBe(1);
    vi.useRealTimers();
  });

  it("hard reloads a read-only tab when the restore barrier drains it", () => {
    isReadOnly.value = true;
    connect();

    socket.onclose({ code: 1012 });

    expect(reloadAfterFullRestore).toHaveBeenCalledTimes(1);
  });

  it("uses close code 1012 when the tab missed restore_started", async () => {
    vi.useFakeTimers();
    connect();

    socket.onclose({ code: 1012 });
    await vi.advanceTimersByTimeAsync(2500);

    expect(reloadAfterFullRestore).toHaveBeenCalledTimes(1);
    expect(socketCount).toBe(1);
    vi.useRealTimers();
  });

  it("leaves the initiating tab to its request-driven reload", async () => {
    vi.useFakeTimers();
    isFullRestoreRequestInFlight.mockReturnValue(true);
    connect();

    receive({ type: "restore_started", resource_type: "full" });
    socket.onclose({ code: 1012 });
    await vi.advanceTimersByTimeAsync(2500);

    expect(reloadAfterFullRestore).not.toHaveBeenCalled();
    expect(socketCount).toBe(1);
    vi.useRealTimers();
  });

  it("hard reloads a full restore without incremental store reads", () => {
    connect();
    receive({ type: "restore_completed", resource_type: "full" });
    expect(reloadAfterFullRestore).toHaveBeenCalledTimes(1);
  });
});

describe("useUpdatesSocket: the GPU out-of-memory notice", () => {
  it("raises one warning card that counts the attempts used", () => {
    connect();

    receive({
      type: "vram_oom",
      task_type: "DescriptionTask",
      attempt: 1,
      max_attempts: 3,
      gave_up: false,
    });

    const noticeStore = useNoticeStore();
    expect(noticeStore.notices).toHaveLength(1);
    expect(noticeStore.notices[0].level).toBe("warning");
    expect(noticeStore.notices[0].text).toContain("attempt 1 of 3");
    // Must outlive the backend's pause between attempts, or the next frame
    // opens a second card instead of updating this one. The level default
    // (6 s) is too close to that pause to rely on.
    expect(noticeStore.notices[0].timeout).toBeGreaterThanOrEqual(15000);
  });

  it("updates the same card as the retries go by, rather than stacking", async () => {
    vi.useFakeTimers();
    connect();

    // Real timing: the backend pauses between attempts, so the card has to
    // still be alive when the next frame lands or the key coalesces nothing.
    receive({ type: "vram_oom", attempt: 1, max_attempts: 3, gave_up: false });
    await vi.advanceTimersByTimeAsync(5000);
    receive({ type: "vram_oom", attempt: 2, max_attempts: 3, gave_up: false });
    await vi.advanceTimersByTimeAsync(5000);
    receive({ type: "vram_oom", attempt: 3, max_attempts: 3, gave_up: true });

    const noticeStore = useNoticeStore();
    expect(noticeStore.notices).toHaveLength(1);
    expect(noticeStore.notices[0].key).toBe(VRAM_OOM_NOTICE_KEY);
    // The last frame is the one the user is left reading: all three attempts
    // are gone and nothing was written.
    expect(noticeStore.notices[0].text).toContain("after 3 of 3 attempts");
    expect(noticeStore.notices[0].text).toContain("Nothing was changed");
    vi.useRealTimers();
  });

  it("closes the card when a later attempt succeeds", async () => {
    vi.useFakeTimers();
    connect();

    receive({ type: "vram_oom", attempt: 1, max_attempts: 3, gave_up: false });
    await vi.advanceTimersByTimeAsync(5000);
    receive({
      type: "vram_oom",
      attempt: 1,
      max_attempts: 3,
      gave_up: false,
      recovered: true,
    });

    const noticeStore = useNoticeStore();
    expect(noticeStore.notices).toHaveLength(1);
    // Not left reading "retrying" about work that finished.
    expect(noticeStore.notices[0].level).toBe("success");
    expect(noticeStore.notices[0].text).toContain("freed up");
    vi.useRealTimers();
  });

  it("says nothing to a read-only session", () => {
    isReadOnly.value = true;
    connect();

    receive({ type: "vram_oom", attempt: 1, max_attempts: 3, gave_up: false });

    // A share viewer cannot act on the machine's GPU, and the backend does not
    // deliver the event to them either — this is the second lock.
    expect(useNoticeStore().notices).toHaveLength(0);
  });
});
