// Gesture batch ids: the client half of "one gesture, one undo step".
//
// A compound gesture (chip delete = remove_all + reject) stamps every request
// it issues with one `X-Operation-Batch-Id`; the backend records them as one
// batch and a single Ctrl+Z reverses the whole gesture
// (docs/backend_architecture.md §21.2).

import { describe, it, expect } from "vitest";
import { newOperationBatchId, operationBatchHeaders } from "./apiClient";

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
