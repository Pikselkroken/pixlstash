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

const editModels = vi.fn();
const forgetModels = vi.fn();

vi.mock("../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
  editModels: (...args) => editModels(...args),
  forgetModels: (...args) => forgetModels(...args),
}));

import {
  editReceipt,
  forgetReceipt,
  useModelShelfStore,
} from "./useModelShelfStore";
import { useNoticeStore } from "./useNoticeStore";

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

describe("overlapping fetches", () => {
  // Three checkboxes each refetch, so two flights are one double-click apart.
  it("ignores a flight the user has already overtaken", async () => {
    const store = useModelShelfStore();
    let landFirst;
    listAdapters
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            landFirst = () => resolve([adapter({ id: 1, kind: "lora" })]);
          }),
      )
      .mockImplementationOnce(() =>
        Promise.resolve([adapter({ id: 2, kind: "lokr" })]),
      );

    const overtaken = store.fetchRows();
    const winner = store.fetchRows();
    await winner;
    expect(store.rows.map((r) => r.id)).toEqual([2]);

    landFirst();
    await overtaken;
    expect(store.rows.map((r) => r.id)).toEqual([2]);
  });

  it("leaves the spinner up while the newest flight is still running", async () => {
    const store = useModelShelfStore();
    listAdapters
      .mockImplementationOnce(() => Promise.resolve([adapter({ id: 1 })]))
      .mockImplementationOnce(() => new Promise(() => {}));

    const first = store.fetchRows();
    store.fetchRows();
    await first;

    expect(store.loading).toBe(true);
  });
});

describe("the option vocabularies", () => {
  it("survives a fetch narrowed by the type checkboxes", async () => {
    // Both option lists are derived from the fetched rows, so a fetch that
    // overwrote them deleted the kind checkboxes the parent is documented to
    // grey, and dropped base models that stayed selected and persisted.
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, kind: "lokr", base_model: "sdxl" }),
    ]);
    listCheckpoints.mockResolvedValue([
      adapter({
        id: 2,
        file_kind: "checkpoint",
        kind: null,
        base_model: "flux.1-dev",
      }),
    ]);
    await store.fetchRows();

    await store.setFilters({ adapters: false }, { refetch: true });
    expect(store.adapterKindOptions).toEqual(["lokr"]);
    expect(store.visibleRows.map((r) => r.id)).toEqual([2]);

    await store.setFilters(
      { adapters: true, checkpoints: false },
      { refetch: true },
    );
    expect(store.baseModelOptions).toEqual(["flux.1-dev", "sdxl"]);
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
  });

  it("survives a refresh that fails", async () => {
    // Clearing the rows on error emptied both vocabularies and unmounted the
    // Show panel's nested checkboxes, which is the bug above reached down the
    // error path. The error renders ahead of the list, so keeping them costs
    // nothing on screen.
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, kind: "lokr", base_model: "sdxl" }),
    ]);
    listCheckpoints.mockResolvedValue([
      adapter({
        id: 2,
        file_kind: "checkpoint",
        kind: null,
        base_model: "flux.1-dev",
      }),
    ]);
    await store.fetchRows();

    listAdapters.mockRejectedValueOnce(new Error("the shelf is unreachable"));
    await store.fetchRows();

    expect(store.error).toBe("the shelf is unreachable");
    expect(store.adapterKindOptions).toEqual(["lokr"]);
    expect(store.baseModelOptions).toEqual(["flux.1-dev", "sdxl"]);
    expect(store.rows.map((r) => r.id)).toEqual([1, 2]);
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

// ── F2: sorting and grouping ───────────────────────────────────────────────
//
// The assertions here guard the two measured realities that make a naive
// grouping look broken on real data: 37% of real adapters record no base model,
// so "not set" is one of the LARGEST groups rather than a tail, and folders
// cluster hard, so a grouping that assumes an even spread is wrong. The rest
// pin decisions that are easy to undo: sorting never refetches, a null value
// sorts last in BOTH directions, and collapse is namespaced per axis.

describe("sorting", () => {
  it("never refetches: every sort field is already on the row", async () => {
    // `fetchRows` merges up to three parallel requests, so a server-sorted list
    // per block would be destroyed by the concatenation anyway. Sorting client
    // side is therefore the correct answer, not a shortcut, and a direction
    // flip must cost nothing.
    const store = useModelShelfStore();
    await store.fetchRows();
    listAdapters.mockClear();
    listCheckpoints.mockClear();

    store.setView({ sortKey: "size", sortDirection: "asc" });
    store.setView({ sortDirection: "desc" });
    store.setView({ groupBy: "base_model" });

    expect(listAdapters).not.toHaveBeenCalled();
    expect(listCheckpoints).not.toHaveBeenCalled();
  });

  it("sorts a row that cannot answer the key last in BOTH directions", async () => {
    // The API's own contract for these keys. A row with no recorded size is not
    // the smallest file; it is an unanswered question, and letting a third of
    // the shelf pile up at whichever end the arrow points is how a sort stops
    // being one.
    listAdapters.mockResolvedValue([
      adapter({ id: 1, display_name: "big", file_size: 900 }),
      adapter({ id: 2, display_name: "unknown", file_size: null }),
      adapter({ id: 3, display_name: "small", file_size: 100 }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();

    store.setView({ sortKey: "size", sortDirection: "desc" });
    expect(store.groups[0].rows.map((r) => r.display_name)).toEqual([
      "big",
      "small",
      "unknown",
    ]);

    store.setView({ sortDirection: "asc" });
    expect(store.groups[0].rows.map((r) => r.display_name)).toEqual([
      "small",
      "big",
      "unknown",
    ]);
  });

  it("holds equal rows in one order when a refetch reorders the blocks", async () => {
    // `Array.prototype.sort` is stable, but stability preserves the INPUT
    // order, and the input changes: `fetchRows` re-concatenates the blocks
    // every time. Without the id tiebreak two adapters of the same size swap
    // places on a refresh, which reads as a rendering fault.
    const store = useModelShelfStore();
    const a = adapter({ id: 1, display_name: "a", file_size: 100 });
    const b = adapter({ id: 2, display_name: "b", file_size: 100 });
    store.setView({ sortKey: "size" });

    listAdapters.mockResolvedValue([a, b]);
    await store.fetchRows();
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([1, 2]);

    listAdapters.mockResolvedValue([b, a]);
    await store.fetchRows();
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([1, 2]);
  });

  it("uses the stack's own size and date, not its cover's", async () => {
    // A six-step run understates by about six times when read off the cover, in
    // the column the shelf exists to answer.
    listAdapters.mockResolvedValue([
      adapter({ id: 1, display_name: "solo", file_size: 500 }),
      adapter({
        id: 2,
        display_name: "stack",
        file_size: 100,
        total_size: 600,
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ sortKey: "size", sortDirection: "desc" });
    expect(store.groups[0].rows.map((r) => r.display_name)).toEqual([
      "stack",
      "solo",
    ]);
  });
});

describe("grouping", () => {
  it("puts the models that record no base model last, in both directions", async () => {
    // 37% of real adapters record nothing, so this group is one of the largest
    // on the shelf. It sorts last because it is the ABSENCE of a value rather
    // than a value: it never joins the alphabetical run and never swaps ends
    // when the direction flips, which would otherwise bury everything
    // identifiable underneath it every other click.
    listAdapters.mockResolvedValue([
      adapter({ id: 1, base_model: "sdxl" }),
      adapter({ id: 2, base_model: null }),
      adapter({ id: 3, base_model: "flux.1-dev" }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "base_model" });

    expect(store.groups.map((g) => g.label)).toEqual([
      "flux.1-dev",
      "sdxl",
      "Base model not set",
    ]);

    store.setView({ sortDirection: "asc" });
    expect(store.groups.map((g) => g.label)).toEqual([
      "flux.1-dev",
      "sdxl",
      "Base model not set",
    ]);
  });

  it("lists a model under every folder holding a copy of it", async () => {
    // Copied into two folders, or an interrupted move. A "primary location"
    // would be a fiction the shelf then has to explain, and it makes the
    // storage answer wrong: the file really does occupy both disks.
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_path: "/a", relpath: "x.st" },
          { state: "missing", folder_path: "/b", relpath: "x.st" },
        ],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "folder" });

    expect(store.groups.map((g) => g.label)).toEqual(["/a", "/b"]);
    // ...and each row reports THAT folder's state, not the merged one, or a
    // file present here and gone there would claim to be fine where it is not.
    expect(store.groups[0].rows[0].locState).toBe("present");
    expect(store.groups[1].rows[0].locState).toBe("missing");
    // One model, two rows drawn: the toolbar states both numbers.
    expect(store.visibleRows.length).toBe(1);
    expect(store.renderedCount).toBe(2);
  });

  it("names the group for a model no folder holds any more", async () => {
    listAdapters.mockResolvedValue([adapter({ id: 1, locations: [] })]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "folder" });
    expect(store.groups.map((g) => g.label)).toEqual(["No registered copy"]);
  });

  it("still renders one group when nothing is grouped, so the list has one shape", async () => {
    listAdapters.mockResolvedValue([adapter()]);
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(store.view.groupBy).toBe("none");
    expect(store.groups.length).toBe(1);
    expect(store.groups[0].label).toBe("");
    expect(store.renderedCount).toBe(1);
  });

  it("does not degenerate on the measured shape of a real folder", async () => {
    // Generated to the distribution `scripts/generate_model_shelf_fixtures.py`
    // measured on 2026-08-09: flux.1-dev dominant, 37% recording no base model
    // at all, and everything in two folders. A grouping that assumed an even
    // spread would look broken here, which is the point of checking at scale
    // rather than on three rows.
    const bases = ["flux.1-dev", "flux.1-dev", "sdxl", "qwen-image"];
    const rows = [];
    for (let i = 0; i < 1800; i += 1) {
      const unnamed = i % 100 < 37;
      rows.push(
        adapter({
          id: i + 1,
          display_name: unnamed ? null : `model ${i}`,
          base_model: unnamed ? null : bases[i % bases.length],
          locations: [
            { state: "present", folder_path: i % 3 ? "/big" : "/small" },
          ],
        }),
      );
    }
    listAdapters.mockResolvedValue(rows);
    const store = useModelShelfStore();
    await store.fetchRows();

    store.setView({ groupBy: "base_model" });
    const labels = store.groups.map((g) => g.label);
    // Four groups, the largest two being ~46% and ~37% of the shelf. The point
    // is that the biggest bucket is a real group with a header and a count,
    // never a silent tail, and that "not set" is still last at that size.
    expect(labels).toEqual([
      "flux.1-dev",
      "qwen-image",
      "sdxl",
      "Base model not set",
    ]);
    expect(labels[labels.length - 1]).toBe("Base model not set");
    expect(store.groups[3].rows.length).toBe(666);
    expect(store.renderedCount).toBe(1800);

    // Folders cluster hard: 2 of 1,800 files in one folder and the rest in the
    // other is the normal shape, not a defect to smooth over.
    store.setView({ groupBy: "folder" });
    expect(store.groups.map((g) => g.rows.length)).toEqual([1200, 600]);
  });
});

describe("collapsing a group", () => {
  it("is namespaced per axis, so one axis cannot collapse the other", async () => {
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        base_model: "/a",
        locations: [{ state: "present", folder_path: "/a" }],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();

    store.setView({ groupBy: "base_model" });
    store.toggleGroup("/a");
    expect(store.isCollapsed("/a")).toBe(true);

    // The same key on the other axis is a different group entirely.
    store.setView({ groupBy: "folder" });
    expect(store.isCollapsed("/a")).toBe(false);

    store.setView({ groupBy: "base_model" });
    expect(store.isCollapsed("/a")).toBe(true);
  });

  it("leaves the group in the list, with its count, while it is collapsed", async () => {
    // A collapsed group that vanished would be indistinguishable from a
    // filtered-out one, which is the conflation F1's three empty states exist
    // to avoid.
    listAdapters.mockResolvedValue([adapter({ id: 1, base_model: "sdxl" })]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "base_model" });
    store.toggleGroup("sdxl");
    expect(store.groups.map((g) => g.label)).toEqual(["sdxl"]);
    expect(store.groups[0].rows.length).toBe(1);
  });
});

describe("the view is remembered", () => {
  it("persists a sort change on its own, with nothing else to save it", () => {
    // Changing the sort and leaving is the common case; a collapse is not. An
    // earlier version of this suite only ever asserted the pair together, and
    // `setView` writing to the wrong key survived it.
    const store = useModelShelfStore();
    store.setView({ sortKey: "size", sortDirection: "asc" });
    expect(
      JSON.parse(window.localStorage.getItem("pixlstash:modelShelfView")),
    ).toMatchObject({ sortKey: "size", sortDirection: "asc" });
  });

  it("restores the grouping, the sort and what was collapsed", () => {
    const store = useModelShelfStore();
    store.setView({ groupBy: "base_model", sortKey: "size" });
    store.toggleGroup("sdxl");

    setActivePinia(createPinia());
    const restored = useModelShelfStore();
    expect(restored.view.groupBy).toBe("base_model");
    expect(restored.view.sortKey).toBe("size");
    expect(restored.isCollapsed("sdxl")).toBe(true);
  });

  it("keeps the view when the Show filters are reset", async () => {
    // Two keys on purpose: `Reset filters` promises to clear the Show panel,
    // and losing your sort order to it would be a different promise.
    const store = useModelShelfStore();
    store.setView({ sortKey: "name" });
    store.setFilters({ unclassified: true });
    await store.resetFilters();
    expect(store.filters.unclassified).toBe(false);
    expect(store.view.sortKey).toBe("name");
  });

  it("survives a session reset, holding no ids of its own", async () => {
    // Same exemption `filters` already has: an axis and a direction are the
    // user's own preference and say nothing about the previous credential.
    listAdapters.mockResolvedValue([adapter()]);
    const store = useModelShelfStore();
    store.setView({ groupBy: "folder", sortDirection: "asc" });
    await store.fetchRows();
    store.resetForSession();
    expect(store.rows).toEqual([]);
    expect(store.view.groupBy).toBe("folder");
    expect(store.view.sortDirection).toBe("asc");
  });

  it("falls back to the defaults on a blob from another schema", () => {
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 99, groupBy: "folder", sortKey: "name" }),
    );
    const store = useModelShelfStore();
    expect(store.view.groupBy).toBe("none");
    expect(store.view.sortKey).toBe("added_at");
  });

  it("refuses a grouping or sort key it does not recognise", () => {
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 1, groupBy: "colour", sortKey: "vibes" }),
    );
    const store = useModelShelfStore();
    expect(store.view.groupBy).toBe("none");
    expect(store.view.sortKey).toBe("added_at");
  });
});

describe("the verbs", () => {
  beforeEach(() => {
    editModels.mockReset();
    forgetModels.mockReset();
    listAdapters.mockResolvedValue([]);
    listCheckpoints.mockResolvedValue([]);
  });

  it("sends only the fields the verb owns", async () => {
    // The whole reason `PATCH /models` distinguishes an absent field from a
    // null one: Set base model across a selection must not blank the names.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    editModels.mockResolvedValue({ updated: [1, 2], fields: ["base_model"] });

    await store.editSelected({ base_model: "FLUX.2" });
    expect(editModels).toHaveBeenCalledWith([1, 2], { base_model: "FLUX.2" });
  });

  it("keeps the selection after an edit and drops it after a forget", async () => {
    // An edit is something you may want to follow with another edit on the same
    // rows. A forget leaves nothing to act on.
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    // The refetch that follows an edit brings the row back, so the selection
    // has something to survive on. (A row that really left the shelf is pruned,
    // which is the next test's business.)
    listAdapters.mockResolvedValue([adapter({ id: 1 })]);
    editModels.mockResolvedValue({ updated: [1], fields: ["kind"] });
    await store.editSelected({ kind: "lokr" });
    expect([...store.selectedIds]).toEqual([1]);

    forgetModels.mockResolvedValue({ forgotten: [1], refused: [] });
    await store.forgetSelected();
    expect([...store.selectedIds]).toEqual([]);
  });

  it("says what failed rather than swallowing it", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    editModels.mockRejectedValue(new Error("nope"));

    expect(await store.editSelected({ base_model: "X" })).toBe(false);
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });
});

describe("the receipts", () => {
  it("names the columns it wrote, because there is no undo to inspect", () => {
    expect(editReceipt(12, { base_model: "FLUX.2" })).toBe(
      "Set the base model on 12 models.",
    );
    expect(editReceipt(1, { display_name: "Clementine" })).toBe(
      "Renamed to Clementine.",
    );
    expect(editReceipt(1, { display_name: null })).toContain(
      "derived from the filename",
    );
  });

  it("reports the refusals, which are the interesting half", () => {
    // "3 forgotten, 2 still on disk" is the normal outcome of a selection made
    // a minute ago; a receipt naming only the 3 reads as a silent partial
    // failure.
    expect(forgetReceipt(3, 2)).toBe(
      "Forgot 3 models. 2 models still have copies and were kept.",
    );
    expect(forgetReceipt(1, 0)).toBe("Forgot 1 model.");
    expect(forgetReceipt(0, 1)).toContain("nothing was forgotten");
  });
});

describe("what a verb may reach", () => {
  it("drops a selected row that the filters stop showing", () => {
    // Load-bearing: `selectedRows` reads `visibleRows`, not `rows`. A verb must
    // never act on something the reader cannot see, and with no undo behind any
    // of it that is the safer half of the trade.
    const store = useModelShelfStore();
    store.setFilters({ unclassified: true });
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, file_kind: "unknown", kind: null }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    expect(store.selectedRows.map((r) => r.id)).toEqual([1, 2]);

    store.setFilters({ unclassified: false });
    expect(store.selectedRows.map((r) => r.id)).toEqual([1]);
    // The id is still remembered, so re-ticking the box brings it back rather
    // than making the reader select it again.
    expect([...store.selectedIds]).toEqual([1, 2]);
  });
});
