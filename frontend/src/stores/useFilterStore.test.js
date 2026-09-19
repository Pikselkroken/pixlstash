import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";

import { useFilterStore } from "./useFilterStore";

// A score range and "unscored" combine: the filter menu's "Include unscored"
// adds the unrated to the range. Neither setter may clear the other.

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("useFilterStore unscored filter", () => {
  it("keeps the score range when unscored goes on, and the reverse", () => {
    const s = useFilterStore();
    s.minScoreFilter = 2;
    s.maxScoreFilter = 4;
    s.unscoredOnlyFilter = true;
    expect(s.minScoreFilter).toBe(2);
    expect(s.maxScoreFilter).toBe(4);
    expect(s.unscoredOnlyFilter).toBe(true);

    s.minScoreFilter = 3;
    expect(s.unscoredOnlyFilter).toBe(true);
  });

  it("leaves unscored alone when a bound is cleared", () => {
    const s = useFilterStore();
    s.unscoredOnlyFilter = true;
    s.minScoreFilter = null;
    s.maxScoreFilter = undefined;
    expect(s.unscoredOnlyFilter).toBe(true);
  });

  it("resetFilters clears it", () => {
    const s = useFilterStore();
    s.unscoredOnlyFilter = true;
    s.resetFilters();
    expect(s.unscoredOnlyFilter).toBe(false);
  });
});
