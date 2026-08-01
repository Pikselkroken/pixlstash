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
const { applyPictureEvent, handleMessage, isReadOnly } = vi.hoisted(() => ({
  applyPictureEvent: vi.fn(() => ({ action: "ignored", reason: "test" })),
  handleMessage: vi.fn(() => ({ action: "ignored", reason: "test" })),
  // Only ever read as `.value`, so a bare box stands in for the real ref
  // without dragging Vue into the hoisted block.
  isReadOnly: { value: false },
}));

vi.mock("../stores/useDedupStore", () => ({
  useDedupStore: () => ({ applyPictureEvent }),
}));

vi.mock("./useGridRealtimeSync", () => ({
  useGridRealtimeSync: () => ({ handleMessage, flushNow: vi.fn() }),
}));

vi.mock("../utils/apiClient", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, isReadOnly };
});

import { useUpdatesSocket } from "./useUpdatesSocket";

/** The last socket the composable opened, so a test can push a frame at it. */
let socket = null;
/** The component the composable is mounted in, unmounted after each case. */
let host = null;

class FakeWebSocket {
  static OPEN = 1;
  constructor(url) {
    this.url = url;
    this.readyState = 1;
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    socket = this;
  }
  send() {}
  close() {}
}

/** Deliver one server frame, exactly as the browser would. */
function receive(payload) {
  socket.onmessage({ data: JSON.stringify(payload) });
}

beforeEach(() => {
  setActivePinia(createPinia());
  socket = null;
  isReadOnly.value = false;
  applyPictureEvent.mockClear();
  handleMessage.mockClear();
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

describe("useUpdatesSocket — routing to the duplicate queue", () => {
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
  // echo is as new to it as another tab's — unlike the grid, which suppresses it.
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
