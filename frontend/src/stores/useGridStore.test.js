import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";

// The store reads the apiClient's `isReadOnly` signal at construction time to
// pick its read-only defaults, so mock it and drive the flag per test.
const isReadOnly = ref(false);
vi.mock("../utils/apiClient", () => ({
  get isReadOnly() {
    return isReadOnly;
  },
}));

import { useGridStore } from "./useGridStore";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
});

describe("useGridStore thumbnailMode default", () => {
  // Read-only sessions (the public demo, share links) never fetch
  // /users/me/config: App.vue's fetchConfig returns early for them, and the
  // endpoint 403s for READ-scoped tokens anyway. So this default is the entire
  // setting for those sessions, not merely a value awaiting a server response.
  it("starts justified for a read-only session", () => {
    isReadOnly.value = true;
    expect(useGridStore().thumbnailMode).toBe("justified");
  });

  // Owner sessions do fetch the config, and the stored preference overwrites
  // this moments later. Starting them justified would flash the wrong layout
  // for every owner whose saved mode is square, which is the whole install base
  // migrated by 0080's "square" backfill.
  it("starts square for an owner session", () => {
    isReadOnly.value = false;
    expect(useGridStore().thumbnailMode).toBe("square");
  });
});
