import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

// The store imports a singleton apiClient; mock the module so no real HTTP happens
// and we can assert which endpoints a tag pick hits.
vi.mock("../utils/apiClient", () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
  isReadOnly: { value: false },
}));

import { apiClient } from "../utils/apiClient";
import { useReviewFixesStore } from "./useReviewFixesStore";

const SCAN = "/tag_suggestions/scan";

beforeEach(() => {
  setActivePinia(createPinia());
  apiClient.get.mockReset();
  apiClient.post.mockReset();
  apiClient.get.mockImplementation((url) => {
    // Summary returns one already-queued tag so "cat" counts as known/cached.
    if (url === "/tag_suggestions/summary") {
      return Promise.resolve({ data: [{ tag: "cat", count: 3 }] });
    }
    return Promise.resolve({ data: [] });
  });
  apiClient.post.mockResolvedValue({ data: { tag: "cat", count: 0 } });
});

describe("useReviewFixesStore.selectOrScan", () => {
  it("re-scans even when the picked tag already has a cached pending queue", async () => {
    const store = useReviewFixesStore();
    // Populate the summary so "cat" is a known tag with an existing queue — the case
    // that used to load the stale cache instead of re-scanning.
    await store.fetchSummary();
    apiClient.post.mockClear();

    await store.selectOrScan("cat");

    const scanCalls = apiClient.post.mock.calls.filter((c) => c[0] === SCAN);
    expect(scanCalls).toHaveLength(1);
    expect(scanCalls[0][1]).toEqual({ tag: "cat" });
  });

  it("scans a brand-new tag too", async () => {
    const store = useReviewFixesStore();
    await store.selectOrScan("dog");

    const scanCalls = apiClient.post.mock.calls.filter((c) => c[0] === SCAN);
    expect(scanCalls).toHaveLength(1);
    expect(scanCalls[0][1]).toEqual({ tag: "dog" });
  });

  it("ignores blank picks without hitting the scan endpoint", async () => {
    const store = useReviewFixesStore();
    await store.selectOrScan("   ");

    expect(apiClient.post.mock.calls.some((c) => c[0] === SCAN)).toBe(false);
  });
});
