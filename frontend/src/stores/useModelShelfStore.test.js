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
// `/adapters?file_kind=engine` is the same route and a different result set, so
// it gets its own double. Without the split every `listAdapters.mockResolvedValue`
// below would answer the engines request with adapter rows too, and the store
// would look like it had duplicated the shelf.
const listEngines = vi.fn();
// Same split, same reason, now that `Unclassified` is on by default: it is one
// more result set behind the one route, and folding it into `listAdapters`
// would answer the unclassified request with adapter rows and make every list
// here look duplicated.
const listUnclassified = vi.fn();
// Same split again, for the two support kinds. One double serves both: they are
// one checkbox and one row bucket, so a test that wants support rows wants them
// without caring which of the two requests carried them.
const listSupport = vi.fn();

const editModels = vi.fn();
const listBaseModelCompletions = vi.fn();
const forgetModels = vi.fn();
const deleteModels = vi.fn();
const setAdapterAttachments = vi.fn();
const setModelIcon = vi.fn();
const clearModelIcons = vi.fn();

vi.mock("../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => {
    if (args[0]?.fileKind === "engine") return listEngines(...args);
    if (args[0]?.fileKind === "unknown") return listUnclassified(...args);
    if (args[0]?.fileKind === "vae" || args[0]?.fileKind === "text_encoder") {
      return listSupport(...args);
    }
    return listAdapters(...args);
  },
  listCheckpoints: (...args) => listCheckpoints(...args),
  editModels: (...args) => editModels(...args),
  listBaseModelCompletions: (...args) => listBaseModelCompletions(...args),
  forgetModels: (...args) => forgetModels(...args),
  deleteModels: (...args) => deleteModels(...args),
  setAdapterAttachments: (...args) => setAdapterAttachments(...args),
}));

vi.mock("../api/modelIcons", () => ({
  setModelIcon: (...args) => setModelIcon(...args),
  clearModelIcons: (...args) => clearModelIcons(...args),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import {
  assignReceipt,
  COLUMN_KEYS,
  deleteReceipt,
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
  listEngines.mockReset().mockResolvedValue([]);
  listUnclassified.mockReset().mockResolvedValue([]);
  listSupport.mockReset().mockResolvedValue([]);
  listBaseModelCompletions.mockReset().mockResolvedValue([]);
});

describe("defaults", () => {
  it("shows adapters, checkpoints and unclassified files", async () => {
    // `unknown` is first-class and never folded into either other list - and it
    // is asked for by default: a file nothing could classify is still on the
    // disk the shelf accounts for, and opt-in is how a 339 MB leftover in
    // PixlStash's own download folder stayed invisible (#927).
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(listAdapters).toHaveBeenCalledTimes(1);
    expect(listAdapters).toHaveBeenCalledWith();
    expect(listCheckpoints).toHaveBeenCalledTimes(1);
    expect(listUnclassified).toHaveBeenCalledWith({ fileKind: "unknown" });
    expect(store.activeCount).toBe(0);
  });

  it("asks for the engines, because nothing else on the shelf shows them", async () => {
    // The regression this pins: the backend has answered `file_kind=engine`
    // since #876 and the shelf never asked, so PixlStash's own taggers, the
    // InsightFace packs and 116 GB of HuggingFace cache were invisible while
    // the architecture note said they were listed.
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(listEngines).toHaveBeenCalledWith({ fileKind: "engine" });
  });

  it("stops asking when the box is unticked", async () => {
    // Over-fetching is its own regression: the block is opt-out, not forced.
    const store = useModelShelfStore();
    await store.setFilters({ engines: false }, { refetch: true });
    expect(listEngines).not.toHaveBeenCalled();
  });

  it("keeps engine rows in their own bucket, so an adapter refetch cannot drop them", async () => {
    // `blockOf` decides what a refetch replaces. An engine landing in the
    // adapters bucket would be wiped by the next adapters-only fetch.
    listEngines.mockResolvedValue([
      adapter({
        id: 900,
        file_kind: "engine",
        kind: "tagger",
        display_name: "WD14 ConvNeXt tagger v3",
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.setFilters({ checkpoints: false }, { refetch: true });
    expect(store.rows.some((r) => r.id === 900)).toBe(true);
  });

  it("asks the adapters block for the unclassified files", async () => {
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(listUnclassified).toHaveBeenCalledWith({ fileKind: "unknown" });
    // Never /checkpoints: an unknown must not render as a checkpoint.
    expect(listCheckpoints).not.toHaveBeenCalledWith(
      expect.objectContaining({ fileKind: "unknown" }),
    );
  });

  it("stops asking for the unclassified files when the box is unticked", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ unclassified: false }, { refetch: true });
    expect(listUnclassified).not.toHaveBeenCalled();
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
  it("facets and filters on the folded algorithm, so one box means one algorithm", async () => {
    // `model.kind` is free text and `PATCH /models` stores what it is given,
    // so `LoRA` and `lora` are one algorithm and two strings. Faceting raw
    // offered two boxes the panel now draws with the SAME label, each ticking
    // half the rows - and the `feature` axis folds them into one group, so
    // ticking either emptied half of a group the user can see.
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "LoRA" }),
      adapter({ id: 3, kind: " lokr " }),
    ]);
    await store.fetchRows();
    expect(store.adapterKindOptions).toEqual(["lokr", "lora"]);
    await store.setFilters({ adapterKinds: ["lora"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1, 2]);
  });

  it("never hides a row the facet offers no box for", async () => {
    // An adapter whose kind folds to nothing gets no checkbox - an unlabelled
    // one is not a control - so a kind selection holds no opinion about it and
    // must not exclude it. Hiding it would leave a row no box can bring back,
    // which is the failure the base-model facet already carries a sentinel to
    // avoid.
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "lokr" }),
      adapter({ id: 3, kind: "  " }),
    ]);
    await store.fetchRows();
    expect(store.adapterKindOptions).toEqual(["lokr", "lora"]);
    await store.setFilters({ adapterKinds: ["lora"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1, 3]);
  });

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

  it("counts turning unclassified off, because on is the default", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ unclassified: false }, { refetch: true });
    expect(store.activeCount).toBe(1);
  });

  it("counts turning engines off, for the same reason", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ engines: false }, { refetch: true });
    expect(store.activeCount).toBe(1);
  });

  it("counts turning support files off, for the same reason", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ support: false }, { refetch: true });
    expect(store.activeCount).toBe(1);
  });
});

describe("the support block", () => {
  it("puts both kinds in one bucket from two requests", async () => {
    // One checkbox, two `file_kind`s. The route takes a single kind, so the
    // block is two requests, and a reader deciding what to keep sees one list.
    const store = useModelShelfStore();
    listSupport
      .mockResolvedValueOnce([adapter({ id: 1, file_kind: "vae", kind: null })])
      .mockResolvedValueOnce([
        adapter({ id: 2, file_kind: "text_encoder", kind: null }),
      ]);

    await store.fetchRows();

    expect(store.rows.map((r) => r.id)).toEqual([1, 2]);
  });

  it("replaces only its own rows when it refetches", async () => {
    // `blockOf` has to map BOTH kinds to `support`, or a refetch leaves the
    // kind it forgot about behind as a duplicate.
    const store = useModelShelfStore();
    listSupport
      .mockResolvedValueOnce([adapter({ id: 1, file_kind: "vae", kind: null })])
      .mockResolvedValueOnce([
        adapter({ id: 2, file_kind: "text_encoder", kind: null }),
      ]);
    await store.fetchRows();

    listSupport
      .mockResolvedValueOnce([adapter({ id: 3, file_kind: "vae", kind: null })])
      .mockResolvedValueOnce([
        adapter({ id: 4, file_kind: "text_encoder", kind: null }),
      ]);
    await store.fetchRows();

    expect(store.rows.map((r) => r.id)).toEqual([3, 4]);
  });

  it("asks for nothing when the box is off", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ support: false }, { refetch: true });
    expect(listSupport).not.toHaveBeenCalled();
  });
});

describe("empty states", () => {
  it("distinguishes 'nothing selected' from 'nothing matched'", async () => {
    const store = useModelShelfStore();
    expect(store.nothingSelected).toBe(false);
    await store.setFilters(
      {
        adapters: false,
        checkpoints: false,
        unclassified: false,
        engines: false,
        support: false,
      },
      { refetch: true },
    );
    expect(store.nothingSelected).toBe(true);
  });

  it("does not call a shelf showing engines alone empty", async () => {
    // The block was added to the fetch and to the row buckets but not to this
    // check, so ticking Engines alone fetched its rows, counted them in the
    // toolbar, and drew "Nothing is selected in Show" over the top of them.
    const store = useModelShelfStore();
    await store.setFilters(
      { adapters: false, checkpoints: false, unclassified: false },
      { refetch: true },
    );
    expect(store.nothingSelected).toBe(false);
  });
});

describe("persistence", () => {
  it("remembers the selection and restores it next visit", () => {
    const store = useModelShelfStore();
    store.setFilters({ unclassified: false, baseModels: ["UNASSIGNED"] });
    setActivePinia(createPinia());
    const restored = useModelShelfStore();
    expect(restored.filters.unclassified).toBe(false);
    expect(restored.filters.baseModels).toEqual(["UNASSIGNED"]);
  });

  it("falls back to the defaults on a corrupt blob", () => {
    window.localStorage.setItem("pixlstash:modelShelfFilters", "{not json");
    const store = useModelShelfStore();
    expect(store.filters.adapters).toBe(true);
    expect(store.filters.unclassified).toBe(true);
  });

  it("discards a blob an older build wrote, so a changed default applies", () => {
    // The regression this pins is #927 in its second half. `Unclassified`
    // shipped off, so every blob written before this carries
    // `unclassified: false` whether or not anyone chose it - and honouring it
    // would keep the leftovers hidden from precisely the people who have used
    // the shelf longest.
    window.localStorage.setItem(
      "pixlstash:modelShelfFilters",
      JSON.stringify({ adapters: false, unclassified: false }),
    );
    const store = useModelShelfStore();
    expect(store.filters.unclassified).toBe(true);
    expect(store.filters.adapters).toBe(true);
  });

  it("folds a remembered algorithm rather than leaving an unclearable ghost filter", async () => {
    // A shipped build faceted on the RAW `model.kind` and persisted it, so a
    // blob holding `LoRA` outlives the fold. Matched raw it selects nothing and
    // appears in no checkbox: an empty shelf with the Adapters box reading
    // fully on, the one algorithm box reading off, and nothing on screen naming
    // the filter doing it - escapable only by `Reset filters`, which also
    // discards the base models and capabilities the user did not ask to lose.
    //
    // Folded on read rather than discarded by a `FILTERS_SCHEMA_VERSION` bump,
    // because the selection is still exactly what the user chose.
    window.localStorage.setItem(
      "pixlstash:modelShelfFilters",
      // `"  "` is the other half of the migration: the shipped facet gated on
      // raw truthiness, so a whitespace-only kind DID get a tickable box that
      // could be persisted. Folded it is `""`, which no box offers and every
      // row fails - the same unclearable ghost, arrived at from the other side.
      JSON.stringify({ v: 1, adapterKinds: ["LoRA", "lora", " LORA ", "  "] }),
    );
    listAdapters.mockResolvedValue([adapter({ id: 1, kind: "LoRA" })]);
    const store = useModelShelfStore();
    // Three spellings of one algorithm are also one remembered selection.
    expect(store.filters.adapterKinds).toEqual(["lora"]);
    await store.fetchRows();
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
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
  it("starts a NEW key at its own end, whichever control asked", () => {
    // The rule lives in `setView` rather than in the column headings, because
    // the Sort panel writes `sortKey` too and writes it WITHOUT a direction
    // (`ShelfSortPanel.vue`). With the rule in the heading, the panel carried
    // "Newest first" onto Name and handed back Z to A while the heading beside
    // it gave A to Z.
    const store = useModelShelfStore();
    expect(store.view.sortKey).toBe("added_at");
    expect(store.view.sortDirection).toBe("desc");

    store.setView({ sortKey: "name" });
    expect(store.view.sortDirection).toBe("asc");
    store.setView({ sortKey: "size" });
    expect(store.view.sortDirection).toBe("desc");
    store.setView({ sortKey: "base_model" });
    expect(store.view.sortDirection).toBe("asc");
  });

  it("leaves the direction alone when the key is unchanged or given", () => {
    // Otherwise the direction toggle could never reach the non-default end,
    // and re-picking the key you are already on would silently reset it.
    const store = useModelShelfStore();
    store.setView({ sortKey: "name" });
    store.setView({ sortDirection: "desc" });
    expect(store.view.sortDirection).toBe("desc");
    store.setView({ sortKey: "name" });
    expect(store.view.sortDirection).toBe("desc");
    store.setView({ sortKey: "size", sortDirection: "asc" });
    expect(store.view.sortDirection).toBe("asc");
    // And a patch that is about something else entirely does not touch it.
    store.setView({ groupBy: "folder" });
    expect(store.view.sortDirection).toBe("asc");
  });

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
    store.setFilters({ unclassified: false });
    await store.resetFilters();
    expect(store.filters.unclassified).toBe(true);
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

describe("the column widths", () => {
  it("clamps a width to the bounds rather than taking what it is given", () => {
    const store = useModelShelfStore();
    store.setColumnWidth("size", 4000);
    // 400 is a sanity bound on a stored blob and nothing else: the limit a
    // drag meets is the Name track, measured in the component.
    expect(store.view.columnWidths.size).toBe(400);
    store.setColumnWidth("size", -20);
    expect(store.view.columnWidths.size).toBe(56);
  });

  it("floors each column on its own content, not on one shared figure", () => {
    // `Kind` holds a word like `Checkpoint` and `Size` holds five characters,
    // so one floor for both put the wordy columns into permanent ellipsis.
    // Every floor is at or under that column's default, or a stored default
    // would be clamped UP on the way back in.
    const store = useModelShelfStore();
    for (const key of COLUMN_KEYS) store.setColumnWidth(key, 0);
    expect(store.view.columnWidths).toEqual({
      kind: 64,
      base: 72,
      size: 56,
      date: 80,
    });
  });

  it("ignores a column it does not have and a width that is not one", () => {
    // Every one of these coerces to 0 through `Number()` and would come back
    // as the column's FLOOR rather than being refused - `null` in particular is a
    // value JSON can carry, so a stored blob would silently hand back a
    // floored column where the read-back loop is meant to fall through to the
    // default.
    const store = useModelShelfStore();
    store.setColumnWidth("name", 120);
    for (const bad of ["wide", null, undefined, "", "64", [], {}, true, NaN]) {
      store.setColumnWidth("kind", bad);
    }
    expect(store.view.columnWidths).toEqual({
      kind: 64,
      base: 84,
      size: 74,
      date: 96,
    });
  });

  it("falls through to the default for a stored width that is not a number", () => {
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({
        v: 1,
        columnWidths: { kind: null, base: "84", size: 90 },
      }),
    );
    const store = useModelShelfStore();
    expect(store.view.columnWidths).toEqual({
      kind: 64,
      base: 84,
      size: 90,
      date: 96,
    });
  });

  it("restores what was dragged, and clamps that too", () => {
    // A blob edited by hand - or written by a build with different bounds -
    // must not hand back a shelf whose Size column is 4,000px wide.
    const store = useModelShelfStore();
    store.setColumnWidth("base", 150);
    const blob = JSON.parse(
      window.localStorage.getItem("pixlstash:modelShelfView"),
    );
    expect(blob.columnWidths).toEqual({
      kind: 64,
      base: 150,
      size: 74,
      date: 96,
    });

    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ ...blob, columnWidths: { base: 150, size: 9000 } }),
    );
    setActivePinia(createPinia());
    const restored = useModelShelfStore();
    expect(restored.view.columnWidths).toEqual({
      kind: 64,
      base: 150,
      size: 400,
      date: 96,
    });
  });

  it("takes the defaults from a blob written before columns could be dragged", () => {
    // Per field rather than behind a schema bump, for the reason the folder
    // layout is: that blob is still a perfectly good remembered sort.
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 1, sortKey: "name", sortDirection: "asc" }),
    );
    const store = useModelShelfStore();
    expect(store.view.sortKey).toBe("name");
    expect(store.view.columnWidths).toEqual({
      kind: 64,
      base: 84,
      size: 74,
      date: 96,
    });
  });
});

describe("stacks are atomic", () => {
  /** A three-step run plus one loose adapter. */
  function shelfWithARun() {
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1, stack_id: 7, stack_position: 0 }),
      adapter({
        id: 2,
        stack_id: 7,
        stack_position: 1,
        sha256: "b".repeat(64),
      }),
      adapter({
        id: 3,
        stack_id: 7,
        stack_position: 2,
        sha256: "c".repeat(64),
      }),
      adapter({ id: 4, sha256: "d".repeat(64) }),
    ];
    return store;
  }

  it("draws one row for a run, not one per step", () => {
    const store = shelfWithARun();
    expect(store.visibleRows.map((r) => r.id)).toEqual([1, 4]);
    expect(store.visibleRows[0].memberCount).toBe(3);
  });

  it("selects the whole run from one click", () => {
    // `services/stack_membership`: a grouping mutation applies to EVERY member
    // "so state can never go partial". Selecting the cover alone would let Move
    // take one step of three and leave the rest.
    const store = shelfWithARun();
    store.selectFromClick(1, {}, [1, 4]);
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3]);
  });

  it("gives the verbs every member, not just the cover", () => {
    const store = shelfWithARun();
    store.selectFromClick(1, {}, [1, 4]);
    expect([...store.selectedModelIds].sort()).toEqual([1, 2, 3]);
    // ...while the bar still counts it as the one row the reader sees.
    expect(store.selectedRows).toHaveLength(1);
  });

  it("toggles a run in and out as one unit", () => {
    const store = shelfWithARun();
    store.selectFromClick(1, { ctrl: true }, [1, 4]);
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3]);
    store.selectFromClick(1, { ctrl: true }, [1, 4]);
    expect([...store.selectedIds]).toEqual([]);
  });

  it("takes whole runs in a Shift range", () => {
    const store = shelfWithARun();
    store.selectFromClick(4, {}, [1, 4]);
    store.selectFromClick(1, { shift: true }, [1, 4]);
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3, 4]);
  });

  it("never hands a verb the same model twice, even drawn in two folders", () => {
    // The review of #881 read `selectedRows` as one entry per DRAWN row, which
    // would duplicate under folder grouping - a model with copies in two
    // folders is drawn twice. It is not: `selectedRows` filters `visibleRows`,
    // which is one entry per model, and the per-folder duplication happens in
    // `groups` for rendering only. Asserted rather than argued, and it stays
    // asserted so a future change to that derivation cannot quietly reintroduce
    // duplicate ids on the wire.
    const store = useModelShelfStore();
    store.rows = [
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_id: 1, folder_path: "/a", relpath: "x" },
          { state: "present", folder_id: 2, folder_path: "/b", relpath: "x" },
        ],
      }),
    ];
    store.setView({ groupBy: "folder" });
    store.toggleSelected(1);

    // Drawn twice...
    const drawn = store.groups.flatMap((g) => g.rows.map((r) => r.id));
    expect(drawn).toEqual([1, 1]);
    // ...selected once, and sent once.
    expect(store.selectedRows.map((r) => r.id)).toEqual([1]);
    expect(store.selectedModelIds).toEqual([1]);
  });

  it("selects every member with Select visible", () => {
    const store = shelfWithARun();
    store.selectVisible();
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3, 4]);
  });

  it("hands the verbs one member when one member is what was picked", () => {
    // The exception the expanded strip added (#1005). A member is not in
    // `visibleRows` - the run is one row there - so without this the selection
    // resolves to nothing and every verb gates on a phantom.
    const store = shelfWithARun();
    store.selectFromClick(3, {}, [1, 3, 4]);
    expect(store.selectedRows.map((r) => r.id)).toEqual([3]);
    expect(store.selectedModelIds).toEqual([3]);
  });

  it("keeps a Shift range inside the strip it was aimed at", () => {
    // An OPEN run is in the drawn order member by member, so its cover must
    // stand for itself inside a range - expanding it as well reaches past the
    // row the reader clicked and takes the steps below it.
    const store = shelfWithARun();
    const drawn = [4, 1, 2, 3];
    store.selectFromClick(4, {}, drawn);
    store.selectFromClick(2, { shift: true }, drawn);
    expect([...store.selectedIds].sort()).toEqual([1, 2, 4]);
  });

  it("still takes whole runs in a range over CLOSED rows", () => {
    // The other half: with the strip shut, the cover is the only thing drawn
    // for the run, so it has to stand for all of it.
    const store = shelfWithARun();
    store.selectFromClick(4, {}, [1, 4]);
    store.selectFromClick(1, { shift: true }, [1, 4]);
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3, 4]);
  });

  it("narrows a member's copies to the folder it is drawn under", () => {
    // The row is narrowed so the file line answers "where is this" with a path
    // in the folder it is under; an expanded strip has to follow, or the steps
    // answer with a path from the folder above.
    const store = useModelShelfStore();
    const twoFolders = (id, position) =>
      adapter({
        id,
        sha256: String(id).repeat(64).slice(0, 64),
        stack_id: 7,
        stack_position: position,
        locations: [
          { state: "present", folder_id: 1, folder_path: "/a", relpath: "x" },
          { state: "present", folder_id: 2, folder_path: "/b", relpath: "x" },
        ],
      });
    store.rows = [twoFolders(1, 0), twoFolders(2, 1)];
    store.setView({ groupBy: "folder" });

    for (const group of store.groups) {
      for (const row of group.rows) {
        for (const member of row.members) {
          expect(member.locations.map((l) => l.folder_id)).toEqual([
            group.folderId,
          ]);
        }
      }
    }
  });

  it("counts a part of a run as its parts, not as the run", () => {
    // Ctrl-clicking one file out of a selected run leaves two files, and the
    // bar has to say two. Reading the cover's presence as "the run" instead
    // would let a verb write the file the reader just unticked.
    const store = shelfWithARun();
    store.selectFromClick(1, {}, [1, 4]);
    expect(store.selectedRows).toHaveLength(1);
    store.selectFromClick(2, { ctrl: true }, [1, 2, 3, 4]);
    expect(store.selectedRows.map((r) => r.id)).toEqual([1, 3]);
    expect(store.selectedModelIds).toEqual([1, 3]);
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

describe("Assign", () => {
  beforeEach(() => {
    setAdapterAttachments.mockReset().mockResolvedValue({ attachments: [] });
    listAdapters.mockResolvedValue([]);
    listCheckpoints.mockResolvedValue([]);
  });

  it("sends the union, so attaching one entity keeps the others", async () => {
    // The load-bearing assertion of the whole verb. `PUT .../attachments`
    // REPLACES the set, so a write of just the new entity silently detaches
    // every character already using the model - a data loss with no undo behind
    // it and no error to notice.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1, attachments: [{ entity_type: "set", entity_id: 9 }] }),
    ];
    store.toggleSelected(1);

    await store.setAttachment({
      entityType: "character",
      entityId: 4,
      entityName: "Alice",
      subjectIds: ["1"],
    });

    expect(setAdapterAttachments).toHaveBeenCalledWith("a".repeat(64), [
      { entity_type: "set", entity_id: 9 },
      { entity_type: "character", entity_id: 4 },
    ]);
  });

  it("detaches by omission and leaves the rest standing", async () => {
    const store = useModelShelfStore();
    store.rows = [
      adapter({
        id: 1,
        attachments: [
          { entity_type: "character", entity_id: 4 },
          { entity_type: "set", entity_id: 9 },
        ],
      }),
    ];
    store.toggleSelected(1);

    await store.setAttachment({
      entityType: "character",
      entityId: 4,
      subjectIds: ["1"],
      attach: false,
    });

    expect(setAdapterAttachments).toHaveBeenCalledWith("a".repeat(64), [
      { entity_type: "set", entity_id: 9 },
    ]);
  });

  it("never duplicates an entity already attached", async () => {
    // Partial resolves UP in the picker, so a row that is already attached can
    // be re-sent as part of a wider gesture.
    const store = useModelShelfStore();
    store.rows = [
      adapter({
        id: 1,
        attachments: [{ entity_type: "character", entity_id: 4 }],
      }),
    ];
    store.toggleSelected(1);

    await store.setAttachment({
      entityType: "character",
      entityId: 4,
      subjectIds: ["1"],
    });

    expect(setAdapterAttachments).toHaveBeenCalledWith("a".repeat(64), [
      { entity_type: "character", entity_id: 4 },
    ]);
  });

  it("writes only rows still selected and still addressable", async () => {
    // The picker emits the ids it was handed when the menu opened. A row
    // deselected since then, or one with no hash to address, is dropped here
    // rather than sent as a request the server has to refuse.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: null }),
      adapter({ id: 3, sha256: "c".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);

    await store.setAttachment({
      entityType: "set",
      entityId: 7,
      subjectIds: ["1", "2", "3"],
    });

    expect(setAdapterAttachments).toHaveBeenCalledTimes(1);
    expect(setAdapterAttachments.mock.calls[0][0]).toBe("a".repeat(64));
  });

  it("reports the ones that landed when some of the N calls fail", async () => {
    // N calls means a partial failure is an outcome, not an error: reporting
    // only the failure would send the reader back to re-run the verb on rows
    // that already have it.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    setAdapterAttachments
      .mockResolvedValueOnce({ attachments: [] })
      .mockRejectedValueOnce(new Error("gone"));

    expect(
      await store.setAttachment({
        entityType: "character",
        entityId: 4,
        entityName: "Alice",
        subjectIds: ["1", "2"],
      }),
    ).toBe(true);
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("success");
    expect(notice.text).toBe(
      "Assigned 1 model to Alice. 1 model could not be written.",
    );
  });
});

describe("the thumbnail verb", () => {
  beforeEach(() => {
    setModelIcon.mockReset().mockResolvedValue({ icon_sha256: "a".repeat(64) });
    clearModelIcons.mockReset().mockResolvedValue({ cleared: [] });
    listAdapters.mockResolvedValue([]);
    listCheckpoints.mockResolvedValue([]);
  });

  it("sets the icon on the one selected model", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    const file = new Blob(["x"], { type: "image/png" });

    expect(await store.setIconOnSelected(file)).toBe(true);
    expect(setModelIcon).toHaveBeenCalledWith(1, file);
  });

  it("marks every selected model, not just the first", async () => {
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    const file = new Blob(["x"]);

    expect(await store.setIconOnSelected(file)).toBe(true);
    expect(setModelIcon).toHaveBeenCalledTimes(2);
    expect(setModelIcon).toHaveBeenCalledWith(1, file);
    expect(setModelIcon).toHaveBeenCalledWith(2, file);
    expect(useNoticeStore().notices.at(-1).text).toBe(
      "Set the thumbnail on 2 models.",
    );
  });

  it("counts a partial failure rather than claiming the whole selection", async () => {
    // The receipt is the only record - there is no undo - so a batch that half
    // landed must not read like one that landed.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    setModelIcon.mockRejectedValueOnce(new Error("nope"));

    expect(await store.setIconOnSelected(new Blob(["x"]))).toBe(true);
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("warning");
    expect(notice.text).toBe(
      "Set the thumbnail on 1 model. 1 model could not be written.",
    );
  });

  it("reports an error when nothing landed", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    setModelIcon.mockRejectedValue(new Error("nope"));

    expect(await store.setIconOnSelected(new Blob(["x"]))).toBe(false);
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });

  it("does nothing without a selection, and says nothing either", async () => {
    // The notice matters as much as the write: without the guard this falls
    // through to "none landed" and pushes an error about a set nobody asked
    // for.
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    const before = useNoticeStore().notices.length;
    expect(await store.setIconOnSelected(new Blob(["x"]))).toBe(false);
    expect(setModelIcon).not.toHaveBeenCalled();
    expect(useNoticeStore().notices).toHaveLength(before);
  });

  it("marks every model in a ticked run, not just its cover", async () => {
    // A fully-selected stack is ONE selected row, and its id is the cover's.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1, stack_id: 9, stack_position: 0 }),
      adapter({ id: 2, sha256: "b".repeat(64), stack_id: 9, stack_position: 1 }),
      adapter({ id: 3, sha256: "c".repeat(64), stack_id: 9, stack_position: 2 }),
    ];
    store.selectVisible();
    expect(store.selectedRows).toHaveLength(1);

    expect(await store.setIconOnSelected(new Blob(["x"]))).toBe(true);
    expect(setModelIcon.mock.calls.map((c) => c[0]).sort()).toEqual([1, 2, 3]);
    expect(useNoticeStore().notices.at(-1).text).toBe(
      "Set the thumbnail on 3 models.",
    );
  });

  it("refuses a selection past the ceiling rather than firing 501 uploads", async () => {
    const store = useModelShelfStore();
    store.rows = Array.from({ length: 501 }, (_, i) =>
      adapter({ id: i + 1, sha256: String(i).padStart(64, "0") }),
    );
    store.selectVisible();

    expect(await store.setIconOnSelected(new Blob(["x"]))).toBe(false);
    expect(setModelIcon).not.toHaveBeenCalled();
    expect(useNoticeStore().notices.at(-1).text).toContain("At most 500");
  });

  it("does nothing without a file, rather than posting an empty body", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    expect(await store.setIconOnSelected(null)).toBe(false);
    expect(setModelIcon).not.toHaveBeenCalled();
  });

  it("reports what the clear changed, not what was sent", async () => {
    // A selection of two where one had an icon is "1 model", not "2".
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1, icon_sha256: "a".repeat(64) }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    clearModelIcons.mockResolvedValue({ cleared: [1] });

    await store.clearIconsOnSelected();
    expect(clearModelIcons).toHaveBeenCalledWith([1, 2]);
    expect(useNoticeStore().notices.at(-1).text).toBe(
      "Cleared the thumbnail on 1 model.",
    );
  });

  it("says so plainly when none of them had one", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    await store.clearIconsOnSelected();
    expect(useNoticeStore().notices.at(-1).text).toContain("None of those");
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
    expect(forgetReceipt(0, 1)).toBe(
      "Nothing was forgotten. 1 model still has a copy and was kept.",
    );
    expect(forgetReceipt(0, 0)).toBe("Nothing to forget.");
  });

  it("names the entity Assign wrote to, not its type", () => {
    // "Assigned to a character" is not checkable against what the reader meant.
    expect(assignReceipt(3, 0, "Alice", true)).toBe(
      "Assigned 3 models to Alice.",
    );
    expect(assignReceipt(1, 0, "Winter shoot", false)).toBe(
      "Removed 1 model from Winter shoot.",
    );
    expect(assignReceipt(0, 2, "Alice", true)).toBe(
      "Nothing was assigned. 2 models could not be written.",
    );
  });

  it("keeps 'already gone' apart from 'still has a copy'", () => {
    // Two different pieces of news. A row that no longer exists was forgotten
    // by something else; saying it "still has a copy" tells the reader their
    // file is safe when the row is not there at all.
    expect(forgetReceipt(2, 0, 1)).toBe(
      "Forgot 2 models. 1 model was already gone.",
    );
    expect(forgetReceipt(1, 2, 3)).toBe(
      "Forgot 1 model. 2 models still have copies and were kept. " +
        "3 models were already gone.",
    );
    expect(forgetReceipt(0, 0, 2)).toBe(
      "Nothing was forgotten. 2 models were already gone.",
    );
  });

  it("counts a vanished row as vanished, not as one that was kept", async () => {
    // The seam: the store reads `refused` and has to split it by reason. An
    // unrecognised reason counts as "kept", the conservative reading.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ];
    store.toggleSelected(1);
    store.toggleSelected(2);
    forgetModels.mockResolvedValue({
      forgotten: [],
      refused: [
        { id: 1, reason: "no_such_model" },
        { id: 2, reason: "still_has_a_copy" },
      ],
    });

    await store.forgetSelected();
    const text = useNoticeStore().notices.at(-1).text;
    expect(text).toContain("1 model still has a copy");
    expect(text).toContain("1 model was already gone");
  });

  it("does not report a refused engine as a file that is still on disk", () => {
    // The reported bug: an engine row whose only copy is gone came back as
    // "still has a copy", sending the reader to look on the disk for a file
    // the shelf itself draws as missing.
    expect(forgetReceipt(0, 0, 0, 1)).toBe(
      "Nothing was forgotten. 1 model is one PixlStash downloaded for " +
        "itself and would fetch again.",
    );
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

describe("the folded base model", () => {
  const folded = (id, raw, canonical) =>
    adapter({ id, base_model: raw, base_model_folded: canonical });

  it("groups four spellings of one base under one header", async () => {
    const store = useModelShelfStore();
    store.rows = [
      folded(1, "sdxl_base_v1-0", "SDXL 1.0"),
      folded(2, "SDXL", "SDXL 1.0"),
      folded(3, "flux.1-dev", "FLUX.1 dev"),
    ];
    store.setView({ groupBy: "base_model" });

    const labels = store.groups.map((g) => g.label);
    expect(labels).toEqual(["FLUX.1 dev", "SDXL 1.0"]);
  });

  it("offers one facet per base, not one per spelling", () => {
    const store = useModelShelfStore();
    store.rows = [
      folded(1, "sdxl_base_v1-0", "SDXL 1.0"),
      folded(2, "stable diffusion xl", "SDXL 1.0"),
    ];
    expect(store.baseModelOptions).toEqual(["SDXL 1.0"]);
  });

  it("selects every spelling when its one facet is ticked", () => {
    // The half that would break silently: a facet built from folded values and
    // a filter matching raw ones would tick a box that hides most of its rows.
    const store = useModelShelfStore();
    store.rows = [
      folded(1, "sdxl_base_v1-0", "SDXL 1.0"),
      folded(2, "SDXL", "SDXL 1.0"),
      folded(3, "flux.1-dev", "FLUX.1 dev"),
    ];
    store.setFilters({ baseModels: ["SDXL 1.0"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1, 2]);
  });

  it("keeps an unrecognised base model selectable in its own right", () => {
    const store = useModelShelfStore();
    store.rows = [folded(1, "my private base v3", null), folded(2, null, null)];
    expect(store.baseModelOptions).toEqual([
      "my private base v3",
      "UNASSIGNED",
    ]);
  });
});

describe("capabilities", () => {
  /** An engine row as `/adapters?file_kind=engine` returns one. */
  function engine(overrides = {}) {
    return adapter({
      id: 900,
      file_kind: "engine",
      kind: "captioner",
      display_name: "florence-community/Florence-2-base",
      capabilities: ["captioner", "detector"],
      ...overrides,
    });
  }

  it("lists a two-capability model under EVERY feature it serves", async () => {
    // The rule this whole change exists for. A model that captions AND detects
    // cannot be filed under one heading, so the feature axis draws it twice -
    // the same fan-out `folder` already does for a file copied into two places.
    listEngines.mockResolvedValue([engine()]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    const byKey = Object.fromEntries(store.groups.map((g) => [g.key, g]));
    expect(Object.keys(byKey).sort()).toEqual(["captioner", "detector"]);
    expect(byKey.captioner.rows.map((r) => r.id)).toEqual([900]);
    expect(byKey.detector.rows.map((r) => r.id)).toEqual([900]);
    // The stored word is machine vocabulary; the header is the screen's.
    expect(byKey.captioner.label).toBe("Captioning");
    expect(byKey.detector.label).toBe("Detection");
  });

  it("marks each feature header with that feature's own glyph", async () => {
    // The header falls back to the AXIS's glyph when a group carries none, so
    // every feature used to wear one star. Two headers, two marks.
    listEngines.mockResolvedValue([engine()]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    const byKey = Object.fromEntries(store.groups.map((g) => [g.key, g]));
    expect(byKey.captioner.icon).toBe("mdi-text-box-outline");
    expect(byKey.detector.icon).toBe("mdi-shape-outline");
  });

  it("gives each draw its own rowKey, so focus cannot land on two at once", async () => {
    // The collision `folder` already had: one key across several draws put
    // `tabindex="0"` on all of them and made `indexOf` return the first.
    listEngines.mockResolvedValue([engine()]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    const keys = store.groups.flatMap((g) => g.rows.map((r) => r.rowKey));
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(2);
  });

  it("files an adapter under its algorithm, spelled as the Kind column spells it", async () => {
    // Most of the shelf. The row's own Kind cell has always read `LoRA`, so
    // The catch-all was the axis contradicting the cell beside it and
    // collapsing the whole shelf into one bucket. Two spellings of one
    // algorithm fold, which is why the key is the label and not `row.kind`.
    listAdapters.mockResolvedValue([
      adapter({ id: 1, capabilities: [] }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        kind: "LORA",
        capabilities: [],
      }),
      adapter({
        id: 3,
        sha256: "c".repeat(64),
        kind: "lokr",
        capabilities: [],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    expect(store.groups.map((g) => g.label)).toEqual(["LoKr", "LoRA"]);
    expect(store.groups.find((g) => g.label === "LoRA").rows).toHaveLength(2);
    // Keyed on the FOLDED value, never the label: the key is what a collapsed
    // group is remembered by, so a key of `kind:LoRA` would orphan every
    // stored collapse the day the label table learns a new spelling. Prefixed
    // to keep an algorithm out of the capability keyspace.
    expect(store.groups.map((g) => g.key)).toEqual(["kind:lokr", "kind:lora"]);
    // `name`, never `path`: `path` is the mono, un-uppercased face the folder
    // axis owns, and an algorithm is not a filesystem path.
    expect(store.groups[0].labelKind).toBe("name");
  });

  it("groups on the algorithm only for an ADAPTER, whatever else carries a kind", async () => {
    // The `file_kind` guard. The row that pins it is one carrying a kind while
    // not being an adapter. An engine keeps its ROLE in `model.kind`, so the
    // shape is the engine's - though today's declarations always emit that role
    // as a capability too (`declared_capabilities`), so this exact row is
    // synthetic. It is the guard that is being pinned, not a state the hub
    // currently writes: without it, anything that grows a `kind` starts heading
    // feature groups.
    listEngines.mockResolvedValue([
      engine({ id: 4, kind: "tagger", capabilities: [] }),
    ]);
    listAdapters.mockResolvedValue([]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].label).toBe("Other");
    expect(store.groups[0].rows).toHaveLength(1);
  });

  it("files a scanned checkpoint under Checkpoint, beside the declared ones", async () => {
    // The reported bug: 80 rows whose Kind cell reads `Checkpoint` sat under
    // the catch-all while a `Checkpoint` header two rows up held the
    // single packaged model that declares the capability. Nothing writes
    // `model_capability` for a scanned file, so the file kind has to be what
    // heads the group - and it has to be the SAME key the capability uses, or
    // the shelf draws two groups spelled the same.
    listCheckpoints.mockResolvedValue([
      adapter({ id: 5, file_kind: "checkpoint", kind: null, capabilities: [] }),
    ]);
    // Shaped as the declaration writes it: an HF-cache repo enters as
    // `file_kind='engine'` (`DeclaredEntry.file_kind`) and carries the
    // capability, so the merge has to survive the two rows disagreeing about
    // their file kind - which is the whole shape of the reported bug.
    listEngines.mockResolvedValue([
      engine({
        id: 6,
        sha256: "d".repeat(64),
        kind: "checkpoint",
        display_name: "Qwen/Qwen-Image",
        capabilities: ["checkpoint"],
      }),
    ]);
    listAdapters.mockResolvedValue([]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].key).toBe("checkpoint");
    expect(store.groups[0].label).toBe("Checkpoint");
    expect(store.groups[0].icon).toBe("mdi-package-variant-closed");
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([5, 6]);
  });

  it("files the support kinds under their own names too", async () => {
    // Same bug, same fix, one file kind over: a VAE and a text encoder declare
    // no capability either, and their Kind cells have always named them.
    listAdapters.mockResolvedValue([
      adapter({ id: 9, file_kind: "vae", kind: null, capabilities: [] }),
      adapter({
        id: 10,
        sha256: "e".repeat(64),
        file_kind: "text_encoder",
        kind: null,
        capabilities: [],
      }),
      // Unclassified stays a shrug, and the shrug is `Other`: a heading called
      // "Unclassified" beside it would be the same answer twice.
      adapter({
        id: 11,
        sha256: "f".repeat(64),
        file_kind: "unknown",
        kind: null,
        capabilities: [],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    // Alphabetical, `Other` included: it is a capability like the rest now
    // rather than the pinned-last "not set" group, which is what the base-model
    // and folder axes still have and this axis no longer does.
    expect(store.groups.map((g) => g.label)).toEqual([
      "Other",
      "Text encoder",
      "VAE",
    ]);
    // No capability marks a VAE or a text encoder, so those headers keep the
    // axis's own glyph rather than borrowing a wrong one.
    expect(store.groups[1].icon).toBe("");
  });

  it("says nothing for an adapter whose kind is blank, rather than heading a group with it", async () => {
    // The hub CHECK makes an adapter's `kind` NOT NULL but not NON-EMPTY, and
    // `PATCH /models` stores free text, so a whitespace-only kind is reachable
    // over the raw API. Folded it is `""`, which would head a group with no
    // label at all - and put an unlabelled checkbox in the Show panel.
    listAdapters.mockResolvedValue([
      adapter({ id: 7, kind: "   ", capabilities: [] }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].label).toBe("Other");
    expect(store.adapterKindOptions).toEqual([]);
  });

  it("gives a file_kind this build does not know no algorithm at all", async () => {
    // `blockOf` funnels an unrecognised `file_kind` into the adapters bucket so
    // the row is never dropped from the shelf - which is why the guards here
    // test `file_kind === "adapter"` and not `blockOf(row) === "adapters"`.
    // Widening them would let a kind PixlStash cannot interpret head a feature
    // group, and this is the row that proves the narrow test is deliberate.
    listAdapters.mockResolvedValue([
      adapter({ id: 8, file_kind: "sidecar", kind: "lora", capabilities: [] }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    // Still on the shelf...
    expect(store.visibleRows.map((r) => r.id)).toEqual([8]);
    // ...and still without a feature, or a checkbox claiming it has one.
    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].label).toBe("Other");
    expect(store.adapterKindOptions).toEqual([]);
  });

  it("refuses to call the classifier's shrug an algorithm", async () => {
    // `detect_adapter_kind` returns the literal `unknown` (`KIND_UNKNOWN`) for
    // an adapter whose tensor markers match nothing it knows, and the file is
    // still `file_kind=adapter`. Heading that `UNKNOWN` would stand a second
    // shrug beside the real one and call it a feature.
    listAdapters.mockResolvedValue([
      adapter({ id: 6, kind: "unknown", capabilities: [] }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "feature" });

    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].label).toBe("Other");
  });

  it("matches HAS this capability rather than IS this kind", async () => {
    // Ticking one feature keeps a model that also serves another: that is the
    // difference between the capability filter and the old single label.
    listEngines.mockResolvedValue([
      engine(),
      engine({
        id: 901,
        kind: "tagger",
        display_name: "WD14",
        capabilities: ["tagger"],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();

    await store.setFilters({ capabilities: ["detector"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([900]);

    // The same row survives a tick of its OTHER capability.
    await store.setFilters({ capabilities: ["captioner"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([900]);
  });

  it("narrows only the engines, exactly as the kind boxes narrow only adapters", async () => {
    // A nested filter narrows the block it hangs under. Ticking `Captioning`
    // hiding every LoRA would be the same defect as ticking `lora` hiding every
    // checkpoint.
    listAdapters.mockResolvedValue([adapter({ id: 1 })]);
    listEngines.mockResolvedValue([engine()]);
    const store = useModelShelfStore();
    await store.fetchRows();

    await store.setFilters({ capabilities: ["tagger"] });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
  });

  it("facets every capability present and counts as one active section", async () => {
    listEngines.mockResolvedValue([
      engine(),
      engine({ id: 901, capabilities: ["scorer", "search"] }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();

    // Sorted by the LABEL, because that is what the reader sees: `scorer`
    // renders as "Quality score" and belongs under Q, not S.
    expect(store.capabilityOptions).toEqual([
      "captioner",
      "detector",
      "scorer",
      "search",
    ]);
    expect(store.activeCount).toBe(0);
    await store.setFilters({ capabilities: ["detector"] });
    expect(store.activeCount).toBe(1);
  });
});

describe("what the base-model field completes against", () => {
  /** One adapter row, as `/adapters` really returns it. */
  function row(overrides = {}) {
    return {
      id: 1,
      sha256: "a".repeat(64),
      file_kind: "adapter",
      kind: "lora",
      filename: "a.safetensors",
      base_model: null,
      base_model_folded: null,
      locations: [{ state: "present", folder_path: "/m", relpath: "a" }],
      attachments: [],
      ...overrides,
    };
  }

  it("drops a row spelling the server already folded, and keeps one it did not", async () => {
    // The server drops an alias of a label it ships, so re-adding every row
    // verbatim would offer `sdxl base` beside the `SDXL 1.0` it means - and
    // with eight slots in the menu, push the canonical label off it. The client
    // cannot fold on its own; `base_model_folded` is the server's answer for
    // this row, and it is the only thing that can carry the rule.
    listBaseModelCompletions.mockResolvedValue(["SDXL 1.0"]);
    listAdapters.mockResolvedValue([
      row({ id: 1, base_model: "sdxl base", base_model_folded: "SDXL 1.0" }),
      row({ id: 2, base_model: "Clementine ZIB 3B", base_model_folded: null }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.loadBaseModelCompletions();

    expect(store.baseModelCompletions).toEqual([
      "SDXL 1.0",
      "Clementine ZIB 3B",
    ]);
  });

  it("fetches once, and again after a write that could have changed it", async () => {
    listBaseModelCompletions.mockResolvedValue(["SDXL 1.0"]);
    editModels.mockResolvedValue({ updated: [1], fields: ["base_model"] });
    const store = useModelShelfStore();

    await store.loadBaseModelCompletions();
    await store.loadBaseModelCompletions();
    expect(listBaseModelCompletions).toHaveBeenCalledTimes(1);

    // A base model the server had never seen is a completion target the moment
    // it is stored.
    await store.editModelIds([1], { base_model: "Brand New 9" });
    await store.loadBaseModelCompletions();
    expect(listBaseModelCompletions).toHaveBeenCalledTimes(2);

    // A write that does not touch the column leaves the list alone.
    editModels.mockResolvedValue({ updated: [1], fields: ["display_name"] });
    await store.editModelIds([1], { display_name: "Nope" });
    await store.loadBaseModelCompletions();
    expect(listBaseModelCompletions).toHaveBeenCalledTimes(2);
  });

  it("does not ask again on every keystroke when the fetch failed", async () => {
    // The field calls this as the reader types. Clearing the stamp on the error
    // path turned one dead endpoint into one request per character.
    listBaseModelCompletions.mockRejectedValue(new Error("nope"));
    const store = useModelShelfStore();

    await store.loadBaseModelCompletions();
    await store.loadBaseModelCompletions();
    await store.loadBaseModelCompletions();

    expect(listBaseModelCompletions).toHaveBeenCalledTimes(1);
  });

  it("forgets the list when the session resets", async () => {
    // It is derived from this machine's model rows and the credential that
    // could read them has just changed. Left alone, the stamp beside it would
    // also say "already fetched" forever.
    listBaseModelCompletions.mockResolvedValue(["SDXL 1.0"]);
    const store = useModelShelfStore();
    await store.loadBaseModelCompletions();
    expect(store.baseModelCompletions).toEqual(["SDXL 1.0"]);

    store.resetForSession();
    expect(store.baseModelCompletions).toEqual([]);

    await store.loadBaseModelCompletions();
    expect(listBaseModelCompletions).toHaveBeenCalledTimes(2);
  });
});

describe("deleting from disk", () => {
  it("sends every member of the selection and says where the files went", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    listAdapters.mockResolvedValue([]);
    deleteModels.mockResolvedValue({
      deleted: [1],
      files_removed: 1,
      permanent: false,
      refused: [],
    });

    expect(await store.deleteSelected()).toBe(true);
    expect(deleteModels).toHaveBeenCalledWith([1], { permanent: false });
    // Nothing left to act on, exactly as after a forget.
    expect([...store.selectedIds]).toEqual([]);
    expect(useNoticeStore().notices.at(-1).text).toContain("to the Trash");
  });

  it("passes the permanent flag straight through", async () => {
    // The gesture decides. Nothing in the store may re-derive it.
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    listAdapters.mockResolvedValue([]);
    deleteModels.mockResolvedValue({
      deleted: [1],
      files_removed: 1,
      permanent: true,
      refused: [],
    });

    await store.deleteSelected({ permanent: true });
    expect(deleteModels).toHaveBeenCalledWith([1], { permanent: true });
    expect(useNoticeStore().notices.at(-1).text).toContain(
      "Permanently deleted",
    );
  });

  it("says what failed rather than swallowing it", async () => {
    const store = useModelShelfStore();
    store.rows = [adapter({ id: 1 })];
    store.toggleSelected(1);
    deleteModels.mockRejectedValue(new Error("nope"));

    expect(await store.deleteSelected()).toBe(false);
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });
});

describe("deleteReceipt", () => {
  it("names where the bytes went, because that is what recoverable means", () => {
    expect(deleteReceipt(3, [], false, "Recycle Bin", 3)).toBe(
      "Moved 3 models to the Recycle Bin.",
    );
    expect(deleteReceipt(1, [], true, "Trash", 1)).toBe(
      "Permanently deleted 1 model.",
    );
    expect(deleteReceipt(0, [], false, "Trash", 0)).toBe("Nothing to delete.");
  });

  it("does not claim a trip to the trash when no file moved", () => {
    // Every copy was already off the disk, so the row went and nothing else
    // did. "Moved 1 model to the Trash" would send the reader somewhere the
    // file was never put.
    expect(deleteReceipt(1, [], false, "Trash", 0)).toBe(
      "Removed 1 model from the shelf; the files were already gone.",
    );
  });

  it("says plainly when a model lost copies before the delete failed", () => {
    // The one refusal that has already destroyed something.
    const text = deleteReceipt(
      0,
      [{ id: 1, reason: "partly_deleted" }],
      false,
      "Trash",
      1,
    );
    expect(text).toContain("lost some of its copies");
  });

  it("keeps the refusals apart, because they are acted on differently", () => {
    // "Plug the drive in" and "stop trying to delete PixlStash's own" are two
    // different next steps; a combined "2 kept" would leave the reader
    // re-selecting rows to find out which was which.
    const text = deleteReceipt(
      1,
      [
        { id: 2, reason: "unreachable_copy" },
        { id: 3, reason: "not_a_user_folder" },
      ],
      false,
      "Trash",
      1,
    );
    expect(text).toContain("Moved 1 model to the Trash.");
    expect(text).toContain("not plugged in");
    expect(text).toContain("PixlStash keeps for itself");
  });

  it("says how to get past a machine with no trash", () => {
    const text = deleteReceipt(
      0,
      [{ id: 1, reason: "trash_unavailable" }],
      false,
      "Trash",
      0,
    );
    expect(text).toContain("no Trash this server can reach");
    expect(text).toContain("Shift");
  });

  it("names a failure it does not recognise rather than dropping it", () => {
    // A row still on the shelf with its file still on disk is the one outcome
    // the reader must not be left to discover for themselves.
    const text = deleteReceipt(
      0,
      [
        { id: 1, reason: "delete_failed" },
        { id: 2, reason: "invented_later" },
      ],
      true,
      "Trash",
      0,
    );
    expect(text).toBe(
      "Nothing was deleted. 2 models could not be deleted; the server log says why.",
    );
  });
});

// The same bytes, written twice. The hub is content-addressed - one `model`
// row per SHA-256 - so a second `present` copy is not a second model, it is
// disk the owner can have back. Before this the fact lived only in a tooltip.
describe("duplicate copies", () => {
  it("counts only the copies that are actually on the disk", async () => {
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_path: "/m", relpath: "a.st" },
          { state: "present", folder_path: "/m", relpath: "same-bytes.st" },
        ],
      }),
      // One copy and one registration. Deleting the row would free nothing, so
      // this must not read as a duplicate - the failure mode is a shelf that
      // offers the owner a saving that is not there.
      adapter({
        id: 2,
        locations: [
          { state: "present", folder_path: "/m", relpath: "b.st" },
          { state: "missing", folder_path: "/n", relpath: "b.st" },
        ],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(store.visibleRows.map((r) => r.copies)).toEqual([2, 1]);
  });

  it("narrows to the rows written more than once", async () => {
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_path: "/m", relpath: "a.st" },
          { state: "present", folder_path: "/n", relpath: "a.st" },
        ],
      }),
      adapter({ id: 2 }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    expect(store.visibleRows.length).toBe(2);

    await store.setFilters({ duplicatesOnly: true });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
    // Counted as one active filter, or `Reset` reads as doing nothing.
    expect(store.activeCount).toBe(1);

    await store.resetFilters();
    expect(store.visibleRows.length).toBe(2);
  });

  it("is a question, not a preference: it does not survive a reload", async () => {
    const store = useModelShelfStore();
    await store.setFilters({ duplicatesOnly: true });
    // A remembered `Only duplicates` is a shelf that opens showing four rows of
    // eighteen hundred with nothing on screen saying why.
    setActivePinia(createPinia());
    expect(useModelShelfStore().filters.duplicatesOnly).toBe(false);
  });

  it("keeps the count when the folder axis narrows a draw to one copy", async () => {
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_path: "/m", relpath: "a.st" },
          { state: "present", folder_path: "/n", relpath: "a.st" },
        ],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "folder" });
    // The draw carries one location and still reports both copies: the count is
    // a fact about the file, not about the group it is drawn under.
    expect(store.groups[0].rows[0].locations.length).toBe(1);
    expect(store.groups[0].rows.map((r) => r.copies)).toEqual([2]);
  });

  it("draws two copies in one folder under two keys, not one key twice", async () => {
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_path: "/m", relpath: "qwen_3_4b.st" },
          { state: "present", folder_path: "/m", relpath: "zimage_te.st" },
        ],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "folder" });
    const drawn = store.groups[0].rows;
    // One folder, one header, two draws - and the whole point is that they are
    // distinguishable. Equal `rowKey`s put `tabindex="0"` on both at once and
    // made `indexOf` answer with the first, which is the collision the push
    // site was already warning about on the other axis.
    expect(drawn.length).toBe(2);
    expect(new Set(drawn.map((r) => r.rowKey)).size).toBe(2);
    expect(drawn.map((r) => r.locations[0].relpath)).toEqual([
      "qwen_3_4b.st",
      "zimage_te.st",
    ]);
  });
});
