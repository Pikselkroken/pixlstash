import { describe, it, expect } from "vitest";
import {
  describeSet,
  diskByType,
  largestFiles,
  modelFolder,
  selectionCount,
  setVerdict,
  viewName,
} from "./modelsInspector";

describe("the head's name", () => {
  it("names a runs page, a sidebar filter, else the place", () => {
    expect(viewName({ runs: true, place: "sets", sidebarFilter: null })).toBe("Training runs");
    expect(
      viewName({ runs: false, place: "all", sidebarFilter: { axis: "type", key: "lora" } }),
    ).toBe("LoRAs");
    expect(
      viewName({ runs: false, place: "all", sidebarFilter: { axis: "base", key: "SDXL" } }),
    ).toBe("SDXL");
    expect(viewName({ runs: false, place: "loose", sidebarFilter: null })).toBe("In no set yet");
  });

  it("counts a mix as selected, and each kind by its own noun", () => {
    expect(selectionCount(2, 0)).toBe("2 sets");
    expect(selectionCount(0, 1)).toBe("1 model");
    expect(selectionCount(0, 3)).toBe("3 models");
    expect(selectionCount(1, 2)).toBe("3 selected");
  });
});

describe("the disk figures", () => {
  const rows = [
    { id: 1, file_kind: "checkpoint", file_size: 300 },
    { id: 2, file_kind: "adapter", file_size: 100 },
    { id: 3, file_kind: "vae", file_size: 50 },
    { id: 4, file_kind: "text_encoder", file_size: 50 },
    { id: 5, file_kind: "upscaler", file_size: 0 },
    { id: 6, file_kind: "controlnet", file_size: 0 },
  ];

  it("buckets encoders with VAEs and puts everything else in Other", () => {
    const buckets = diskByType(rows);
    expect(buckets.map((b) => [b.key, b.bytes])).toEqual([
      ["checkpoint", 300],
      ["encoders", 100],
      ["lora", 100],
      ["other", 0],
    ]);
    expect(buckets[0].pct).toBeCloseTo(60);
  });

  it("gives every share as zero on an empty view, not NaN", () => {
    expect(diskByType([]).every((b) => b.pct === 0)).toBe(true);
  });

  it("lists the largest files biggest first, and skips a row with no size", () => {
    const top = largestFiles([
      { id: 1, file_size: 10 },
      { id: 2, file_size: null },
      { id: 3, file_size: 30 },
      { id: 4, file_size: 20 },
    ], 2);
    expect(top.map((r) => r.id)).toEqual([3, 4]);
  });
});

describe("a set's words", () => {
  it("says a set with every file run is fine, and names a missing file", () => {
    expect(setVerdict(false)).toBe("Each of these has run with each other.");
    expect(setVerdict(true)).toContain("replace it in the workflows that load it");
  });

  it("reads a hand-made set's members, its base and who made it", () => {
    const rowsById = new Map([[7, { id: 7, file_size: 2048 }]]);
    const set = describeSet(
      {
        key: "hand:9",
        set: {
          id: 9,
          name: "Portraits",
          recipes: 5,
          history_runs: 2,
          members: [
            { id: 7, name: "aria", on_shelf: true, slot: "lora" },
            { id: null, name: "gone", on_shelf: false, slot: "checkpoint" },
          ],
        },
      },
      { rowsById, combinations: [] },
    );
    expect(set.name).toBe("Portraits");
    expect(set.madeBy).toBe("You");
    expect(set.missing).toBe(true);
    expect(set.members.map((m) => [m.name, m.onShelf])).toEqual([
      ["aria", true],
      ["gone", false],
    ]);
    expect(set.size).toBe(2048);
    expect(set.lastUsed).toBe("never");
    expect([set.recipes, set.runs]).toEqual([5, 2]);
  });

  it("reads an evidence card's own combinations, and adds a missing head as a missing file", () => {
    const group = {
      key: "missing:flux1-dev.safetensors",
      head: { missing: true, name: "flux1-dev.safetensors", filename: "flux1-dev.safetensors" },
      models: [{ id: 3, filename: "clip_l.safetensors", on_shelf: true }],
      recipes: 3,
      historyRuns: 0,
      combinations: [
        {
          models: [{ id: 3 }],
          covered_by: [],
          workflows: [{ id: "wf-1", name: "Portrait", picture_count: 4 }],
          latest: [{ picture_id: 11, at: "2026-10-01T10:00:00Z" }],
          last_used_at: "2026-10-01T10:00:00Z",
          picture_count: 4,
        },
      ],
    };
    const set = describeSet(group, { rowsById: new Map(), combinations: [] });
    expect(set.madeBy).toBe("Read from your pictures");
    expect(set.members.at(-1)).toMatchObject({ onShelf: false });
    expect(set.activity.workflows).toEqual([{ id: "wf-1", name: "Portrait", picture_count: 4 }]);
    expect(set.activity.pictures).toBe(4);
    expect([set.recipes, set.runs]).toEqual([3, 0]);
  });
});

describe("a set's missing files", () => {
  it("names a file a combination lacks even when the head is on the shelf", () => {
    const group = {
      key: "model:1",
      head: { id: 1, name: "realvis.safetensors", filename: "realvis.safetensors" },
      models: [{ id: 1, filename: "realvis.safetensors", on_shelf: true }],
      combinations: [{ models: [{ id: 1 }], missing: [{ name: "detail_lora.safetensors" }] }],
    };
    const set = describeSet(group, { rowsById: new Map(), combinations: [] });
    expect(set.missing).toBe(true);
    expect(set.members.filter((m) => !m.onShelf).map((m) => m.name)).toEqual([
      "detail_lora.safetensors",
    ]);
  });
});

describe("a model's folder", () => {
  it("is the folder of its first copy on disk, and empty when none is", () => {
    expect(
      modelFolder({
        locations: [
          { state: "not_downloaded", folder_path: "/a", relpath: "x" },
          { state: "present", folder_path: "/m/checkpoints", relpath: "sd.safetensors" },
        ],
      }),
    ).toBe("/m/checkpoints/sd.safetensors");
    expect(modelFolder({ locations: [] })).toBe("");
  });
});
