// The result pill's "In text" switch belongs to one search: any change of the
// query, a clear included, puts it back on All so no later search is silently
// narrowed (#1197).

import { describe, it, expect, beforeEach } from "vitest";
import { nextTick } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { useSearchStore } from "./useSearchStore";

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("useSearchStore - textMatchesOnly", () => {
  it("starts on All", () => {
    expect(useSearchStore().textMatchesOnly).toBe(false);
  });

  it("resets when the query changes", async () => {
    const store = useSearchStore();
    store.searchQuery = "coffee";
    await nextTick();
    store.textMatchesOnly = true;
    store.searchQuery = "tea";
    await nextTick();
    expect(store.textMatchesOnly).toBe(false);
  });

  it("resets when the search is cleared", async () => {
    const store = useSearchStore();
    store.searchQuery = "coffee";
    await nextTick();
    store.textMatchesOnly = true;
    store.searchQuery = "";
    await nextTick();
    expect(store.textMatchesOnly).toBe(false);
  });

  it("survives anything that is not a query change", async () => {
    const store = useSearchStore();
    store.searchQuery = "coffee";
    await nextTick();
    store.textMatchesOnly = true;
    store.searchInput = "cof";
    await nextTick();
    expect(store.textMatchesOnly).toBe(true);
  });
});
