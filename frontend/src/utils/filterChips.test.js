import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";

import { useFilterStore } from "../stores/useFilterStore";
import { filterChips, scoreChipValue } from "./filterChips";

beforeEach(() => {
  setActivePinia(createPinia());
});

function chipText(store, view) {
  return filterChips(store, view).map((c) => `${c.kind} ${c.value}`);
}

describe("filterChips", () => {
  it("shows nothing for an unfiltered store", () => {
    expect(filterChips(useFilterStore())).toEqual([]);
  });

  it("gives every active filter its own chip, one per tag or model", () => {
    const s = useFilterStore();
    s.noCharacterFilter = true;
    s.noSetFilter = true;
    s.minScoreFilter = 3;
    s.maxScoreFilter = 4;
    s.tagFilter = ["outdoors"];
    s.tagRejectedFilter = ["blurry"];
    s.tagConfidenceAboveFilter = ["hat:0.80"];
    s.tagConfidenceBelowFilter = ["hat:0.60"];
    s.comfyuiModelFilter = ["flux1-dev.safetensors", "sdxl.ckpt"];
    s.sharedOnlyFilter = true;
    expect(chipText(s)).toEqual([
      "Problem no character",
      "Problem in no set",
      "Sharing shared",
      "Score 3–4",
      "Has tag outdoors",
      "Lacks tag blurry",
      "Missing tag hat 80%+",
      "Doubtful tag hat under 60%",
      "Checkpoint flux1-dev",
      "Checkpoint sdxl",
    ]);
  });

  it("gives the filters the menu has no row for a chip too", () => {
    const s = useFilterStore();
    s.mediaTypeFilter = "videos";
    s.faceBboxFilter = "with_face";
    s.stackStateFilter = "stacked";
    s.impossibleSources = ["no_face", "no_humans"];
    s.smartScoreBucketFilter = "3-4";
    s.resolutionBucketFilter = "lt1mp";
    s.comfyuiLoraFilter = ["detail.safetensors"];
    s.minScoreFilter = 1;
    expect(chipText(s)).toEqual([
      "Problem face tags, no face",
      "Problem people tags, no humans",
      "Media video",
      "Faces has face",
      "Stacks stacked",
      "Score 1+",
      "Smart score 3–4",
      "Resolution under 1 MP",
      "LoRA detail",
    ]);
    // Removing every chip is the same as resetting the store.
    filterChips(s).forEach((c) => c.remove());
    expect(filterChips(s)).toEqual([]);
  });

  it("removes exactly the filter its chip names", () => {
    const s = useFilterStore();
    s.tagFilter = ["a", "b"];
    s.comfyuiLoraFilter = ["x.safetensors"];
    s.minScoreFilter = 2;
    s.unscoredOnlyFilter = true;
    const chips = filterChips(s);
    chips.find((c) => c.key === "tag:a").remove();
    expect(s.tagFilter).toEqual(["b"]);
    expect(s.comfyuiLoraFilter).toEqual(["x.safetensors"]);
    chips.find((c) => c.key === "score").remove();
    expect(s.minScoreFilter).toBe(null);
    expect(s.unscoredOnlyFilter).toBe(false);
    expect(s.tagFilter).toEqual(["b"]);
  });

  it("still shows a stack state the menu does not offer", () => {
    const s = useFilterStore();
    s.stackStateFilter = "unresolved";
    expect(chipText(s)).toEqual(["Stacks unresolved"]);
  });

  it("hides No character and In no set outside All Pictures", () => {
    const s = useFilterStore();
    s.noCharacterFilter = true;
    s.noSetFilter = true;
    expect(filterChips(s, { allPicturesView: false })).toEqual([]);
  });

  it("keeps a tag with a colon intact in a confidence chip", () => {
    const s = useFilterStore();
    s.tagConfidenceAboveFilter = ["ratio:16:9:0.70"];
    expect(chipText(s)).toEqual(["Missing tag ratio:16:9 70%+"]);
  });
});

describe("scoreChipValue", () => {
  it.each([
    [3, 4, false, "3–4"],
    [3, 3, false, "3"],
    [3, null, false, "3+"],
    [null, 4, false, "up to 4"],
    [null, 4, true, "up to 4"],
    [null, 0, true, "unscored"],
    [3, 4, true, "3–4 or unscored"],
    [null, null, true, "unscored"],
    [null, null, false, ""],
  ])("min %s max %s unscored %s → %s", (min, max, unscored, want) => {
    expect(scoreChipValue(min, max, unscored)).toBe(want);
  });
});
