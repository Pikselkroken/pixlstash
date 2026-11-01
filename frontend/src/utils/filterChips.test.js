import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";

import { useFilterStore } from "../stores/useFilterStore";
import { useTagGroupsStore } from "../stores/useTagGroupsStore";
import {
  filterChips,
  scoreChipValue,
  tagTermLabel,
  workflowFilterChips,
} from "./filterChips";

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

  it("leads with Favourites only, and its x turns it off", () => {
    const s = useFilterStore();
    s.favouriteOnlyFilter = true;
    s.sharedOnlyFilter = true;
    const chips = filterChips(s);
    expect(chips.map((c) => `${c.kind} ${c.value}`)).toEqual([
      "Show favourites only",
      "Sharing shared",
    ]);
    chips[0].remove();
    expect(s.favouriteOnlyFilter).toBe(false);
    s.favouriteOnlyFilter = true;
    s.resetFilters();
    expect(s.favouriteOnlyFilter).toBe(false);
  });

  it("names an Imported from folder by its last component, and clears it", () => {
    const s = useFilterStore();
    s.importedFromFilter = "/home/me/ComfyUI/output/";
    const chips = filterChips(s);
    expect(chips.map((c) => `${c.kind} ${c.value}`)).toEqual([
      "Imported from output",
    ]);
    chips[0].remove();
    expect(s.importedFromFilter).toBe(null);
    s.importedFromFilter = "C:\\Users\\me\\Renders";
    expect(filterChips(s)[0].value).toBe("Renders");
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

describe("a workflow and the value opened with it (#1653)", () => {
  function opened(kind, value, label) {
    const s = useFilterStore();
    s.workflowFilter = [
      { id: "w1", name: "Portrait", opened: { kind, value, label } },
    ];
    if (kind === "model") s.comfyuiModelFilter = [value];
    else s.comfyuiLoraFilter = [value];
    return s;
  }

  it("puts the Workflow chip first, and the value under the tab's own name", () => {
    const s = opened("model", "SDXL/realvisXL.safetensors", "RealVis XL");
    expect(chipText(s)).toEqual(["Workflow Portrait", "Checkpoint RealVis XL"]);
  });

  it("drops the value and keeps the workflow on the value chip's ×", () => {
    const s = opened("lora", "bo.safetensors", "Bo");
    filterChips(s).find((c) => c.key === "lora:bo.safetensors").remove();
    expect(s.comfyuiLoraFilter).toEqual([]);
    expect(s.workflowFilter).toEqual([
      {
        id: "w1",
        name: "Portrait",
        opened: { kind: "lora", value: "bo.safetensors", label: "Bo" },
      },
    ]);
  });

  it("takes the value it was opened with on the Workflow chip's ×, and only that", () => {
    const s = opened("model", "a.safetensors", "A");
    s.comfyuiModelFilter = ["a.safetensors", "b.safetensors"];
    s.comfyuiLoraFilter = ["a.safetensors"];
    filterChips(s).find((c) => c.key === "workflow:w1").remove();
    expect(s.workflowFilter).toEqual([]);
    expect(s.comfyuiModelFilter).toEqual(["b.safetensors"]);
    expect(s.comfyuiLoraFilter).toEqual(["a.safetensors"]);
  });

  it("splits the pile's LoRA into a chip of its own (F-4)", () => {
    const s = useFilterStore();
    s.workflowFilter = [
      { id: "w1", name: "Portrait", lora: "asset:abc", loraName: "Bo" },
    ];
    expect(chipText(s)).toEqual(["Workflow Portrait", "LoRA Bo"]);
    filterChips(s).find((c) => c.key === "workflow-lora").remove();
    expect(s.workflowFilter).toEqual([{ id: "w1", name: "Portrait" }]);
  });

  it("Clear all, chip by chip, leaves no workflow behind a LoRA chip", () => {
    const s = useFilterStore();
    s.workflowFilter = [
      { id: "w1", name: "Portrait", lora: "asset:abc", loraName: "Bo" },
    ];
    // What Clear all does: every chip of the list taken at the start.
    for (const chip of filterChips(s)) chip.remove();
    expect(s.workflowFilter).toEqual([]);
  });

  it("gives each of several workflows a chip, and × takes only its own (#1797)", () => {
    const s = useFilterStore();
    s.workflowFilter = [
      { id: "w1", name: "Portrait", lora: "asset:abc", loraName: "Bo" },
      { id: "w2", name: "Flux product shot" },
    ];
    // The pile's LoRA narrows one workflow only, so beside two it has no chip.
    expect(chipText(s)).toEqual([
      "Workflow Portrait",
      "Workflow Flux product shot",
    ]);
    filterChips(s).find((c) => c.key === "workflow:w2").remove();
    expect(s.workflowFilter.map((w) => w.id)).toEqual(["w1"]);
  });
});

describe("workflowFilterChips", () => {
  it("uses the label it has", () => {
    const chips = workflowFilterChips({ origin: "comfyui" }, [], () => {});
    expect(chips.find((chip) => chip.key === "origin").value).toBe("ComfyUI");
  });
});

describe("filterChips: a group term reads as its words (#2469)", () => {
  it("names a group term by its group, and a tag by its own name", () => {
    const groups = useTagGroupsStore();
    groups.groups = [{ id: 3, name: "Character", position: 0 }];
    const s = useFilterStore();
    s.tagFilter = ["__group:3", "hat"];
    s.tagRejectedFilter = ["__group:none"];
    const chips = filterChips(s);
    expect(chips.map((c) => `${c.kind} ${c.value}`)).toEqual([
      "Has tag any Character tag",
      "Has tag hat",
      "Lacks tag any Ungrouped tag",
    ]);
    // The raw term rides on the chip for the copy and the remove.
    expect(chips.map((c) => c.term)).toEqual(["__group:3", "hat", "__group:none"]);
    chips[0].remove();
    expect(s.tagFilter).toEqual(["hat"]);
  });

  it("names a group that is gone, or not loaded, without crashing", () => {
    const s = useFilterStore();
    s.tagFilter = ["__group:9"];
    expect(chipText(s)).toEqual(["Has tag any tag in a group"]);
  });

  it("keeps a malformed term as the literal tag the server reads it as", () => {
    const s = useFilterStore();
    s.tagFilter = ["__group:x", "__group:", "__group:3a"];
    expect(chipText(s)).toEqual([
      "Has tag __group:x",
      "Has tag __group:",
      "Has tag __group:3a",
    ]);
  });

  it("labels a term directly", () => {
    expect(tagTermLabel("hat")).toBe("hat");
    expect(tagTermLabel("__group:none")).toBe("any Ungrouped tag");
  });
});
