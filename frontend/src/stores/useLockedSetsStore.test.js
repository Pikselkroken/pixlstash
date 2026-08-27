import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

// The store imports the singleton apiClient; mock it so no real HTTP happens and
// we can drive the locked-members payload.
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn() },
  onSessionReset: () => () => {},
}));

import { apiClient } from "../utils/apiClient";
import { useLockedSetsStore, buildLockReason } from "./useLockedSetsStore";

beforeEach(() => {
  setActivePinia(createPinia());
  apiClient.get.mockReset();
  apiClient.get.mockResolvedValue({ data: { sets: [] } });
});

// A locked set 12 ("Eval slice") locks pictures 1 and 2; set 13 ("Frozen v2")
// also locks picture 2 - so picture 2 is locked by two sets.
function seed(store) {
  store.sets = [
    { id: 12, name: "Eval slice", picture_ids: [1, 2] },
    { id: 13, name: "Frozen v2", picture_ids: [2, 3] },
  ];
}

describe("buildLockReason", () => {
  it("names a single locking set", () => {
    const reason = buildLockReason(["Eval slice"]);
    expect(reason).toContain("the locked set 'Eval slice'");
    expect(reason).toMatch(/^Locked - /);
    expect(reason).toContain("untick Locked in Edit set");
  });

  it("joins multiple set names with commas", () => {
    expect(buildLockReason(["Eval slice", "Frozen v2"])).toContain(
      "the locked set 'Eval slice, Frozen v2'",
    );
  });

  it("returns an empty string when there are no names", () => {
    expect(buildLockReason([])).toBe("");
    expect(buildLockReason(null)).toBe("");
  });
});

describe("isLocked / lockedSetNames mapping", () => {
  it("reports a picture in a locked set as locked", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.isLocked(1)).toBe(true);
    expect(store.lockedSetNames(1)).toEqual(["Eval slice"]);
  });

  it("reports an unlocked picture as not locked", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.isLocked(999)).toBe(false);
    expect(store.lockedSetNames(999)).toEqual([]);
  });

  it("treats null/undefined ids as not locked", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.isLocked(null)).toBe(false);
    expect(store.isLocked(undefined)).toBe(false);
  });

  it("accepts string ids (grid ids arrive as strings)", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.isLocked("1")).toBe(true);
    expect(store.lockedSetNames("1")).toEqual(["Eval slice"]);
  });

  it("collects every locking set name for a multi-set picture", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.lockedSetNames(2)).toEqual(["Eval slice", "Frozen v2"]);
  });
});

describe("lockReason", () => {
  it("builds the single-source tooltip for a single set", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.lockReason(1)).toContain("the locked set 'Eval slice'");
  });

  it("joins multiple locking set names with commas", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.lockReason(2)).toContain(
      "the locked set 'Eval slice, Frozen v2'",
    );
  });

  it("is empty for an unlocked picture", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.lockReason(999)).toBe("");
  });
});

describe("lockedSetIds", () => {
  it("exposes the ids of all locked sets as a Set", () => {
    const store = useLockedSetsStore();
    seed(store);
    expect(store.lockedSetIds).toBeInstanceOf(Set);
    expect(store.lockedSetIds.has(12)).toBe(true);
    expect(store.lockedSetIds.has(13)).toBe(true);
    expect(store.lockedSetIds.has(99)).toBe(false);
  });
});

describe("fetch", () => {
  it("loads the locked-members payload from the endpoint", async () => {
    const store = useLockedSetsStore();
    apiClient.get.mockResolvedValueOnce({
      data: { sets: [{ id: 5, name: "Frozen", picture_ids: [10, 11] }] },
    });

    await store.fetch();

    expect(apiClient.get).toHaveBeenCalledWith("/picture_sets/locked-members");
    expect(store.isLocked(10)).toBe(true);
    expect(store.lockedSetNames(11)).toEqual(["Frozen"]);
  });

  it("keeps the last known state and does not throw when the fetch fails", async () => {
    const store = useLockedSetsStore();
    seed(store);
    apiClient.get.mockRejectedValueOnce(new Error("network"));

    await expect(store.fetch()).resolves.toBeUndefined();
    // Advisory badges survive a failed refresh rather than vanishing.
    expect(store.isLocked(1)).toBe(true);
  });

  it("tolerates a malformed payload (missing sets array)", async () => {
    const store = useLockedSetsStore();
    apiClient.get.mockResolvedValueOnce({ data: {} });
    await store.fetch();
    expect(store.sets).toEqual([]);
  });
});
