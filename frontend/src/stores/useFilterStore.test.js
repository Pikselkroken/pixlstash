import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";

import { useFilterStore } from "./useFilterStore";

// "Unscored" is the complement of a score range, not a point on it, so the two
// are mutually exclusive. That is enforced in the store's setters rather than in
// either surface that writes them (the filter panel's star row and the stats
// sidebar's score histogram), so both inherit it.

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("useFilterStore unscored filter", () => {
  it("clears the score range when unscored goes on", () => {
    const s = useFilterStore();
    s.minScoreFilter = 2;
    s.maxScoreFilter = 4;
    s.unscoredOnlyFilter = true;
    expect(s.minScoreFilter).toBe(null);
    expect(s.maxScoreFilter).toBe(null);
    expect(s.unscoredOnlyFilter).toBe(true);
  });

  it("clears unscored when either score bound is set", () => {
    const s = useFilterStore();
    s.unscoredOnlyFilter = true;
    s.minScoreFilter = 3;
    expect(s.unscoredOnlyFilter).toBe(false);

    s.unscoredOnlyFilter = true;
    s.maxScoreFilter = 3;
    expect(s.unscoredOnlyFilter).toBe(false);
  });

  it("leaves unscored alone when a bound is cleared", () => {
    const s = useFilterStore();
    s.unscoredOnlyFilter = true;
    s.minScoreFilter = null;
    s.maxScoreFilter = undefined;
    expect(s.unscoredOnlyFilter).toBe(true);
  });

  // Miss isActive and the panel's Clear button stays disabled while the filter
  // is on; miss activeCount and the toolbar badge undercounts.
  it("counts as an active filter, and resetFilters clears it", () => {
    const s = useFilterStore();
    expect(s.isActive).toBe(false);
    s.unscoredOnlyFilter = true;
    expect(s.isActive).toBe(true);
    expect(s.activeCount).toBe(1);
    s.resetFilters();
    expect(s.unscoredOnlyFilter).toBe(false);
    expect(s.isActive).toBe(false);
    expect(s.activeCount).toBe(0);
  });
});
