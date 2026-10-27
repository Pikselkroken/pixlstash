// The shelf store's Models workspace section (#2168): places, the one
// sidebar row, the folder scope, the type bands and set membership.

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
const at = (folderId) => [{ folder_id: folderId, state: "present" }];

const ROWS = [
  { id: 1, file_kind: "checkpoint", added_at: ago(2), locations: at(1), sha256: "a" },
  { id: 2, file_kind: "adapter", kind: "lora", added_at: ago(90), locations: at(2), sha256: "b" },
  { id: 3, file_kind: "vae", added_at: ago(RECENT_DAYS + 1), locations: at(1), sha256: "c" },
  { id: 4, file_kind: "engine", added_at: ago(1), locations: at(1), sha256: "d" },
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
    // Model 2 is only in a hidden set, so it counts as in none.
    expect(ids()).toEqual([2, 3, 4]);
    expect(store.placeCounts.loose).toBe(3);
    expect(store.placeCounts.sets).toBe(1);
  });

  it("leaves the set grid unnarrowed by the sidebar", () => {
    store.setPlace("sets");
    store.folderScope = 2;
    expect(ids()).toEqual([1, 2, 3, 4]);
  });
});

describe("the sidebar row and the folder scope", () => {
  it("narrows by one Type row and takes it back on a second pick", () => {
    store.setPlace("sets");
    store.setSidebarFilter("type", "lora");
    expect(store.place).toBe("all");
    expect(ids()).toEqual([2]);
    store.setSidebarFilter("type", "lora");
    expect(store.sidebarFilter).toBe(null);
    expect(ids()).toEqual([1, 2, 3, 4]);
  });

  it("narrows the list and the counts to one folder", () => {
    store.setFolderScope(1);
    expect(store.place).toBe("all");
    expect(ids()).toEqual([1, 3, 4]);
    expect(store.placeCounts.all).toBe(3);
    expect(store.typeCounts.lora).toBe(0);
    expect(store.typeCounts.checkpoint).toBe(1);
  });

  it("a place clears the Type row but keeps the folder", () => {
    store.setFolderScope(1);
    store.setSidebarFilter("type", "vae");
    store.setPlace("recent");
    expect(store.sidebarFilter).toBe(null);
    expect(store.folderScope).toBe(1);
    expect(ids()).toEqual([1, 4]);
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
  it("names the shown sets a model is in and skips hidden ones", async () => {
    await store.loadWorkflowSets();
    expect(store.setMembership.get(1)).toEqual([{ key: "hand:7", name: "Mine" }]);
    expect(store.setMembership.has(2)).toBe(false);
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
