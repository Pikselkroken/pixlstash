// The shelf store's Models workspace section (#2168, #2203): places, the
// open folder, the type bands and set membership.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

let setsBody;
vi.mock("../api/modelShelf", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchWorkflowSets: vi.fn(async () => setsBody),
}));

import {
  GRID_GROUP_BY,
  RECENT_DAYS,
  useModelShelfStore,
} from "./useModelShelfStore";
import { useFavouritesStore } from "./useFavouritesStore";

const DAY = 86400000;
const ago = (days) => new Date(Date.now() - days * DAY).toISOString();
const at = (folderId, relpath = "m.safetensors") => [
  { folder_id: folderId, relpath, state: "present" },
];

// Folder 1 holds 1 at its root, 3 in `Lora` and 4 two levels down, written
// with a Windows separator as a Windows host stores it.
const ROWS = [
  { id: 1, file_kind: "checkpoint", base_model: "Flux.1 dev", added_at: ago(2), locations: at(1, "ckpt.safetensors"), sha256: "a" },
  { id: 2, file_kind: "adapter", kind: "lora", base_model: "Flux.1 dev", added_at: ago(90), locations: at(2), sha256: "b" },
  { id: 3, file_kind: "vae", base_model: "SDXL", added_at: ago(RECENT_DAYS + 1), locations: at(1, "Lora/vae.safetensors"), sha256: "c" },
  { id: 4, file_kind: "engine", added_at: ago(1), locations: at(1, "Lora\\flux\\e.onnx"), sha256: "d" },
];

function body(overrides = {}) {
  return {
    combinations: [],
    no_set: [],
    hand_made: [
      { id: 7, name: "Mine", members: [{ id: 1, on_shelf: true, sha256: "a" }] },
      { id: 8, name: "Hidden", members: [{ id: 2, on_shelf: true, sha256: "b" }] },
    ],
    set_checks: [],
    hidden_sets: ["hand:8"],
    hidden_models: [],
    ...overrides,
  };
}

let store;
const ids = () => store.visibleRows.map((row) => row.id).sort();

beforeEach(() => {
  localStorage.clear();
  setActivePinia(createPinia());
  setsBody = body();
  store = useModelShelfStore();
  store.rows = ROWS;
});

describe("places", () => {
  it("Sets is the grid and every other place the list's own grouping", () => {
    store.setPlace("all");
    expect(store.view.groupBy).toBe("type");
    store.setPlace("sets");
    expect(store.view.groupBy).toBe(GRID_GROUP_BY);
  });

  it("Recently added keeps what a scan first saw within the window", () => {
    store.setPlace("recent");
    expect(ids()).toEqual([1, 4]);
  });

  it("Favourites lists the favourite models", () => {
    useFavouritesStore().keys = { model: ["b"], set: [] };
    store.setPlace("favourites");
    expect(ids()).toEqual([2]);
  });

  it("In no set yet lists nothing until the sets are read, then the loose ones", async () => {
    store.setPlace("loose");
    expect(ids()).toEqual([]);
    expect(store.placeCounts.loose).toBe(null);
    await store.loadWorkflowSets();
    // Model 2 is in one of the owner's sets that is hidden here: still a set.
    expect(ids()).toEqual([3, 4]);
    expect(store.placeCounts.loose).toBe(2);
    // The Sets place counts what the grid draws, which leaves the hidden out.
    expect(store.placeCounts.sets).toBe(1);
  });

  it("leaves the set grid unnarrowed by the sidebar", () => {
    store.setPlace("sets");
    store.folderScope = { folderId: 2, path: "", locked: false };
    expect(ids()).toEqual([1, 2, 3, 4]);
  });
});

describe("the place follows the grid", () => {
  it("keeps the place in step with a groupBy written outside setPlace", () => {
    store.setPlace("favourites");
    store.setView({ groupBy: GRID_GROUP_BY });
    expect(store.place).toBe("sets");
    store.setView({ groupBy: "none" });
    expect(store.place).toBe("all");
    // A place opened the ordinary way keeps its own name.
    store.setPlace("recent");
    expect(store.place).toBe("recent");
    store.setView({ sortKey: "name" });
    expect(store.place).toBe("recent");
  });
});

describe("the open folder", () => {
  it("lists one level, as a file manager does", () => {
    store.setFolderScope({ folderId: 1, path: "" });
    expect(store.place).toBe("folder");
    expect(store.view.groupBy).toBe(store.view.listGroupBy);
    expect(ids()).toEqual([1]);
    store.setFolderScope({ folderId: 1, path: "Lora" });
    expect(ids()).toEqual([3]);
    store.setFolderScope({ folderId: 1, path: "Lora/flux" });
    expect(ids()).toEqual([4]);
  });

  it("lists everything under a locked folder, which cannot be browsed into", () => {
    store.setFolderScope({ folderId: 1, path: "", locked: true });
    expect(ids()).toEqual([1, 3, 4]);
    store.setFolderScope({ folderId: 1, path: "Lora", locked: true });
    expect(ids()).toEqual([3, 4]);
  });

  it("lists a moved model where its file is, not where it was", () => {
    // Moved from the root into `Lora`: the old copy stays on record, missing.
    // Model 7 went missing outright and still lists where it was.
    store.rows = [
      ...ROWS,
      {
        id: 6,
        file_kind: "checkpoint",
        sha256: "f",
        locations: [
          { folder_id: 1, relpath: "moved.safetensors", state: "missing" },
          { folder_id: 1, relpath: "Lora/moved.safetensors", state: "present" },
        ],
      },
      {
        id: 7,
        file_kind: "checkpoint",
        sha256: "g",
        locations: [{ folder_id: 1, relpath: "gone.safetensors", state: "missing" }],
      },
    ];
    store.setFolderScope({ folderId: 1, path: "" });
    expect(ids()).toEqual([1, 7]);
    store.setFolderScope({ folderId: 1, path: "Lora" });
    expect(ids()).toEqual([3, 6]);
  });

  it("leaves Sets when it opens, and All models leaves it", () => {
    store.setPlace("sets");
    store.setFolderScope({ folderId: 1, path: "Lora" });
    expect(store.place).toBe("folder");
    expect(store.view.groupBy).not.toBe(GRID_GROUP_BY);
    store.setPlace("all");
    expect(store.folderScope).toBe(null);
    expect(ids()).toEqual([1, 2, 3, 4]);
  });

  it("leaves the place counts over every model", () => {
    store.setFolderScope({ folderId: 2, path: "" });
    expect(store.placeCounts.all).toBe(4);
  });

  it("is cleared by a place", () => {
    store.setFolderScope({ folderId: 1, path: "Lora" });
    store.setPlace("recent");
    expect(store.folderScope).toBe(null);
    expect(ids()).toEqual([1, 4]);
  });

  it("is never opened by name, only with a folder", () => {
    store.setPlace("folder");
    expect(store.place).not.toBe("folder");
  });

  it("leaves for All models when its folder is closed", () => {
    store.setFolderScope({ folderId: 1, path: "" });
    store.setFolderScope(null);
    expect(store.place).toBe("all");
    expect(store.folderScope).toBe(null);
  });
});

describe("moving: cut, and the open folder's copy (#2204)", () => {
  // One model with a copy at the root of folder 1 and one in its `Lora`.
  const TWICE = {
    id: 5,
    file_kind: "adapter",
    kind: "lora",
    file_size: 300,
    added_at: ago(1),
    sha256: "e",
    locations: [
      { folder_id: 1, relpath: "dup.safetensors", state: "present" },
      { folder_id: 1, relpath: "Lora\\dup.safetensors", state: "present" },
    ],
  };

  beforeEach(() => {
    store.rows = [...ROWS, TWICE];
  });

  it("moves only the copy in the open folder, and every copy elsewhere", () => {
    store.setFolderScope({ folderId: 1, path: "Lora" });
    store.selectedIds = new Set([5]);
    expect(store.movableSelection().items).toEqual([
      { folder_id: 1, relpath: "Lora\\dup.safetensors" },
    ]);
    store.setPlace("all");
    store.selectedIds = new Set([5]);
    expect(store.movableSelection().items).toHaveLength(2);
  });

  it("holds a cut until it is cleared, and marks its rows", () => {
    store.setFolderScope({ folderId: 1, path: "" });
    store.selectedIds = new Set([5]);
    expect(store.cutSelection()).toBe(true);
    expect(store.cutCopies.items).toEqual([
      { folder_id: 1, relpath: "dup.safetensors" },
    ]);
    expect(store.cutCopies.totalBytes).toBe(300);
    expect(store.isCut(5)).toBe(true);
    expect(store.isCut(1)).toBe(false);
    store.clearCut();
    expect(store.cutCopies).toBe(null);
    expect(store.isCut(5)).toBe(false);
  });

  it("pastes only what is still where it was cut (#2204 review)", () => {
    store.setPlace("all");
    store.selectedIds = new Set([5]);
    store.cutSelection();
    expect(store.liveCut().items).toHaveLength(2);
    // One copy moved away since; another file now sits at its old path.
    store.rows = [
      ...ROWS,
      {
        ...TWICE,
        locations: [
          { folder_id: 1, relpath: "dup.safetensors", state: "present" },
          { folder_id: 1, relpath: "Elsewhere/dup.safetensors", state: "present" },
        ],
      },
      { id: 6, file_size: 1, locations: [{ folder_id: 1, relpath: "Lora\\dup.safetensors", state: "present" }] },
    ];
    expect(store.liveCut().items).toEqual([
      { folder_id: 1, relpath: "dup.safetensors" },
    ]);
    expect(store.liveCut().totalBytes).toBe(300);
  });

  it("cuts nothing when no selected copy can move", () => {
    store.rows = [
      { ...TWICE, locations: [{ folder_id: 1, relpath: "x", state: "missing" }] },
    ];
    store.setPlace("all");
    store.selectedIds = new Set([5]);
    expect(store.cutSelection()).toBe(false);
    expect(store.cutCopies).toBe(null);
  });

  it("forgets the cut, the drag and a pending drop with the session", () => {
    store.setPlace("all");
    store.selectedIds = new Set([5]);
    store.cutSelection();
    store.requestMove({ items: [] }, { folderId: 1, path: "" });
    store.resetForSession();
    expect(store.cutCopies).toBe(null);
    expect(store.moveRequest).toBe(null);
  });
});

describe("type bands", () => {
  it("bands by the sidebar's Type order, Other last", () => {
    // A text encoder sorts before VAEs by name and after them by Type.
    store.rows = [
      ...ROWS,
      { id: 5, file_kind: "text_encoder", added_at: ago(1), locations: at(1), sha256: "e" },
    ];
    store.setPlace("all");
    expect(store.groups.map((group) => group.label)).toEqual([
      "Checkpoints",
      "LoRAs",
      "VAEs",
      "Text encoders",
      "Other",
    ]);
  });
});

describe("set membership", () => {
  it("names every own set a model is in, a hidden one marked", async () => {
    await store.loadWorkflowSets();
    expect(store.setMembership.get(1)).toEqual([
      { key: "hand:7", name: "Mine", hidden: false },
    ]);
    expect(store.setMembership.get(2)).toEqual([
      { key: "hand:8", name: "Hidden", hidden: true },
    ]);
  });

  it("leaves out an evidence card the owner hid", async () => {
    setsBody = body({
      hand_made: [],
      hidden_sets: ["model:1"],
      combinations: [
        {
          key: "c1",
          models: [
            { id: 1, kind: "checkpoint", name: "a", on_shelf: true },
            { id: 3, kind: "vae", name: "c", on_shelf: true },
          ],
          picture_count: 1,
          recipes: 1,
          covered_by: [],
          missing: [],
        },
      ],
    });
    await store.loadWorkflowSets();
    expect(store.setMembership.has(3)).toBe(false);
  });
});

describe("adding a model to one of the owner's sets", () => {
  const set = {
    id: 9,
    members: [
      { id: 1, sha256: "a", slot: "checkpoint", on_shelf: true, base_model: "Flux.1 dev" },
    ],
  };

  it("refuses a model the set holds and one on another base model", () => {
    expect(store.setAddRefusal(set, [{ sha256: "a" }])).toBe("Already in it");
    expect(store.setAddRefusal(set, [{ sha256: "z", base_model: "SDXL" }])).toBe(
      "Flux.1 dev, not SDXL",
    );
    expect(store.setAddRefusal(set, [{ sha256: "z", base_model: "Flux.1 dev" }])).toBe("");
    expect(store.setAddRefusal(set, [{ sha256: "z" }])).toBe("");
  });
});
