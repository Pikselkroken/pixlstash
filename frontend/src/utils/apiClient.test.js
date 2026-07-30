// Gesture batch ids: the client half of "one gesture, one undo step".
//
// A compound gesture (chip delete = remove_all + reject) stamps every request
// it issues with one `X-Operation-Batch-Id`; the backend records them as one
// batch and a single Ctrl+Z reverses the whole gesture
// (docs/backend_architecture.md §21.2).

import { describe, it, expect, vi } from "vitest";

// The module builds an axios instance at import time; stub it so the session
// tests below can call logout() without a real request.
vi.mock("axios", () => {
  const instance = {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  };
  return { default: { create: () => instance } };
});

import {
  activateShareToken,
  logout,
  newOperationBatchId,
  onSessionReset,
  operationBatchHeaders,
} from "./apiClient";

describe("newOperationBatchId", () => {
  // Load-bearing: the backend accepts client ids only in the `cli-` namespace
  // and mints its own as `srv-`, so a client can never name — and attach itself
  // to — a server-created batch. It also validates the charset and a bounded
  // length, and IGNORES anything else, which would silently unbatch the gesture.
  it("mints an id in the namespace and charset the backend accepts", () => {
    const id = newOperationBatchId();
    expect(id).toMatch(/^cli-[A-Za-z0-9_-]{4,76}$/);
    expect(id.length).toBeLessThanOrEqual(80);
  });

  it("is unique per gesture", () => {
    const ids = new Set(Array.from({ length: 50 }, () => newOperationBatchId()));
    expect(ids.size).toBe(50);
  });
});

// The single chokepoint every store holding scope-filtered server data hangs
// its cache-drop on (issue #646, condition C1). One mechanism, not one per
// store — a store that had to detect a credential change itself would be a
// store that eventually misses one.
describe("onSessionReset", () => {
  it("fires on logout, before the request that ends the session", async () => {
    const calls = [];
    const stop = onSessionReset(() => calls.push("reset"));
    const pending = logout();
    // Synchronous: nothing that outlives this call may still hold the previous
    // credential's data, even if the POST hangs or fails.
    expect(calls).toEqual(["reset"]);
    await pending;
    stop();
  });

  it("fires on share-token entry", () => {
    const handler = vi.fn();
    const stop = onSessionReset(handler);
    activateShareToken("share-token-abc");
    expect(handler).toHaveBeenCalledTimes(1);
    stop();
  });

  it("stops calling a handler once it unregisters", () => {
    const handler = vi.fn();
    onSessionReset(handler)();
    activateShareToken("another-token");
    expect(handler).not.toHaveBeenCalled();
  });

  it("keeps going when one handler throws", () => {
    const survivor = vi.fn();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const stopFirst = onSessionReset(() => {
      throw new Error("handler exploded");
    });
    const stopSecond = onSessionReset(survivor);
    activateShareToken("token");
    expect(survivor).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalled();
    stopFirst();
    stopSecond();
    error.mockRestore();
  });
});

describe("operationBatchHeaders", () => {
  it("builds the header only when there is a gesture to correlate", () => {
    expect(operationBatchHeaders("cli-abcd1234")).toEqual({
      headers: { "X-Operation-Batch-Id": "cli-abcd1234" },
    });
    expect(operationBatchHeaders()).toBeUndefined();
    expect(operationBatchHeaders("")).toBeUndefined();
    expect(operationBatchHeaders(null)).toBeUndefined();
  });
});
