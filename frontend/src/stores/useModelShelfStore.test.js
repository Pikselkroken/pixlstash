// The shelf's `Show` selection and the rows it resolves to.
//
// Three of these pin decisions that are easy to "simplify" back into bugs:
// a null base model is a selectable value rather than a dropped row, an
// unchecked Adapters parent greys its kinds instead of clearing them, and the
// badge counts filter SECTIONS rather than ticked boxes.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const listAdapters = vi.fn();
const listCheckpoints = vi.fn();

vi.mock("../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
}));

import { useModelShelfStore } from "./useModelShelfStore";

/** One row of the shape `/adapters` really returns (see the fixture probe). */
function adapter(overrides = {}) {
  return {
    id: 1,
    sha256: "a".repeat(64),
    file_kind: "adapter",
    kind: "lora",
    display_name: "Cyanwood Style",
    filename: "Cyanwood_Style_000000250.safetensors",
    base_model: "flux.1-dev",
    file_size: 358733183,
    locations: [{ state: "present", folder_path: "/m", relpath: "a.st" }],
    attachments: [],
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listAdapters.mockReset().mockResolvedValue([]);
  listCheckpoints.mockReset().mockResolvedValue([]);
});

describe("defaults", () => {
  it("shows adapters and checkpoints but not unclassified files", async () => {
    // `unknown` is first-class, not a bucket to hide things in — but a file we
    // could not identify is not what someone came to the shelf to find, so it
    // is opt-in and never folded into either other list.
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(listAdapters).toHaveBeenCalledTimes(1);
    expect(listAdapters).toHaveBeenCalledWith();
    expect(listCheckpoints).toHaveBeenCalledTimes(1);
    expect(store.activeCount).toBe(0);
  });

  it("asks the adapters block for the unclassified files", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ unclassified: true }, { refetch: true });
    expect(listAdapters).toHaveBeenCalledWith({ fileKind: "unknown" });
    // Never /checkpoints: an unknown must not render as a checkpoint.
    expect(listCheckpoints).not.toHaveBeenCalledWith(
      expect.objectContaining({ fileKind: "unknown" }),
    );
  });
});

describe("base model", () => {
  it("offers 'not set' as a value, with the sentinel the API spells", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, base_model: "sdxl" }),
      adapter({ id: 2, base_model: null }),
    ]);
    await store.fetchRows();
    expect(store.baseModelOptions).toEqual(["sdxl", "UNASSIGNED"]);
  });

  it("selects the rows that record none, rather than dropping them", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, base_model: "sdxl" }),
      adapter({ id: 2, base_model: null }),
    ]);
    await store.fetchRows();
    await store.setFilters({ baseModels: ["UNASSIGNED"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([2]);
  });

  it("treats an empty base-model selection as unconstrained", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, base_model: "sdxl" }),
      adapter({ id: 2, base_model: null }),
    ]);
    await store.fetchRows();
    expect(store.visibleRows).toHaveLength(2);
  });
});

describe("adapter kinds", () => {
  it("keeps the kind selection when the parent is unchecked", async () => {
    // Greys, does not clear: re-checking Adapters has to restore exactly what
    // was picked, or the parent checkbox is a destructive control.
    const store = useModelShelfStore();
    await store.setFilters({ adapterKinds: ["lokr"] });
    await store.setFilters({ adapters: false }, { refetch: true });
    expect(store.filters.adapterKinds).toEqual(["lokr"]);
    await store.setFilters({ adapters: true }, { refetch: true });
    expect(store.filters.adapterKinds).toEqual(["lokr"]);
  });

  it("narrows adapters by kind without touching the other blocks", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "lokr" }),
    ]);
    listCheckpoints.mockResolvedValue([
      adapter({ id: 3, file_kind: "checkpoint", kind: null }),
    ]);
    await store.fetchRows();
    await store.setFilters({ adapterKinds: ["lokr"] });
    expect(store.visibleRows.map((r) => r.id).sort()).toEqual([2, 3]);
  });
});

describe("the badge", () => {
  it("counts sections that deviate, not ticked boxes", async () => {
    // Counting boxes would report "9" for a mild narrowing and the number
    // would stop meaning anything.
    const store = useModelShelfStore();
    await store.setFilters({ adapterKinds: ["lora", "lokr", "dora"] });
    expect(store.activeCount).toBe(1);
    await store.setFilters({ baseModels: ["sdxl", "UNASSIGNED"] });
    expect(store.activeCount).toBe(2);
  });

  it("counts turning unclassified on, because off is the default", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ unclassified: true }, { refetch: true });
    expect(store.activeCount).toBe(1);
  });
});

describe("empty states", () => {
  it("distinguishes 'nothing selected' from 'nothing matched'", async () => {
    const store = useModelShelfStore();
    expect(store.nothingSelected).toBe(false);
    await store.setFilters(
      { adapters: false, checkpoints: false, unclassified: false },
      { refetch: true },
    );
    expect(store.nothingSelected).toBe(true);
  });
});

describe("persistence", () => {
  it("remembers the selection and restores it next visit", () => {
    const store = useModelShelfStore();
    store.setFilters({ unclassified: true, baseModels: ["UNASSIGNED"] });
    setActivePinia(createPinia());
    const restored = useModelShelfStore();
    expect(restored.filters.unclassified).toBe(true);
    expect(restored.filters.baseModels).toEqual(["UNASSIGNED"]);
  });

  it("falls back to the defaults on a corrupt blob", () => {
    window.localStorage.setItem("pixlstash:modelShelfFilters", "{not json");
    const store = useModelShelfStore();
    expect(store.filters.adapters).toBe(true);
    expect(store.filters.unclassified).toBe(false);
  });
});
