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
// Same split again, for the support kinds. One double serves them all: they are
// one checkbox and one row bucket, so a test that wants support rows wants them
// without caring which of the requests carried them.
const listSupport = vi.fn();

const editModels = vi.fn();
const listBaseModelCompletions = vi.fn();
const forgetModels = vi.fn();
const deleteModels = vi.fn();
const setAdapterAttachments = vi.fn();
const fetchWorkflowSets = vi.fn();
const createWorkflowSet = vi.fn();
const deleteWorkflowSet = vi.fn();
const setWorkflowSetDeclines = vi.fn();
const setWorkflowSetsHidden = vi.fn();
const setModelsHidden = vi.fn();
const addWorkflowSetMembers = vi.fn();
const removeWorkflowSetMembers = vi.fn();
const mergeWorkflowSets = vi.fn();
const markWorkflowSetSeen = vi.fn();
const setModelIcon = vi.fn();
const clearModelIcons = vi.fn();

vi.mock("../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => {
    if (args[0]?.fileKind === "engine") return listEngines(...args);
    if (args[0]?.fileKind === "unknown") return listUnclassified(...args);
    if (["vae", "text_encoder", "controlnet", "upscaler"].includes(args[0]?.fileKind)) {
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
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
  createWorkflowSet: (...args) => createWorkflowSet(...args),
  deleteWorkflowSet: (...args) => deleteWorkflowSet(...args),
  setWorkflowSetDeclines: (...args) => setWorkflowSetDeclines(...args),
  setWorkflowSetsHidden: (...args) => setWorkflowSetsHidden(...args),
  setModelsHidden: (...args) => setModelsHidden(...args),
  addWorkflowSetMembers: (...args) => addWorkflowSetMembers(...args),
  removeWorkflowSetMembers: (...args) => removeWorkflowSetMembers(...args),
  mergeWorkflowSets: (...args) => mergeWorkflowSets(...args),
  markWorkflowSetSeen: (...args) => markWorkflowSetSeen(...args),
}));

const mergeModelCopies = vi.fn();

vi.mock("../api/modelFiles", () => ({
  mergeModelCopies: (...args) => mergeModelCopies(...args),
}));

const setFavourites = vi.fn(async () => ({ model: [], workflow: [], set: [] }));
vi.mock("../api/favourites", () => ({
  listFavourites: vi.fn(async () => ({ model: [], workflow: [], set: [] })),
  setFavourites: (...args) => setFavourites(...args),
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
  mergeReceipt,
  editReceipt,
  forgetReceipt,
  lookupReceipt,
  useModelShelfStore,
} from "./useModelShelfStore";
import { useNoticeStore } from "./useNoticeStore";
import { useOperationStore } from "./useOperationStore";

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

/**
 * Put the store on the ROW LIST, which is the screen these suites are about.
 *
 * Since #1438 the DEFAULT axis is the set grid, and it is a different screen
 * over the same models: `selectedRows`, `selectVisible` and `modelsBehind` all
 * read the rows THAT screen draws, so a test that seeds `store.rows` and never
 * says which screen it is on is asking the grid about a list it does not draw.
 * `ModelShelf.test.js` makes the same choice in `mountDefaultShelf`, and for the
 * same reason: a new test lands on the list without having to know this.
 */
function onTheRowList() {
  useModelShelfStore().setView({ groupBy: "none" });
}

/** The set keys the active library hides, as the doubles' server holds them. */
let hiddenOnServer = new Set();
let modelsHiddenOnServer = new Set();

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  // What the server holds as this library's hidden set keys (#1869). The store
  // reads it from the set payload and writes it through `setWorkflowSetsHidden`,
  // so this stands in for the hub row rather than any browser storage.
  hiddenOnServer = new Set();
  setWorkflowSetsHidden.mockReset().mockImplementation(async (keys, hidden) => {
    for (const key of keys) {
      if (hidden) hiddenOnServer.add(key);
      else hiddenOnServer.delete(key);
    }
    return [...hiddenOnServer];
  });
  // And the models it hides: the hub's `model_hidden` rows for this library.
  modelsHiddenOnServer = new Set();
  setModelsHidden.mockReset().mockImplementation(async (ids, hidden) => {
    for (const id of ids) {
      if (hidden) modelsHiddenOnServer.add(id);
      else modelsHiddenOnServer.delete(id);
    }
    return [...modelsHiddenOnServer];
  });
  listAdapters.mockReset().mockResolvedValue([]);
  listCheckpoints.mockReset().mockResolvedValue([]);
  listEngines.mockReset().mockResolvedValue([]);
  listUnclassified.mockReset().mockResolvedValue([]);
  listSupport.mockReset().mockResolvedValue([]);
  listBaseModelCompletions.mockReset().mockResolvedValue([]);
  fetchWorkflowSets.mockReset().mockResolvedValue({
    combinations: [],
    no_set: [],
  });
});

describe("models arriving from the Downloads folder", () => {
  it("raises the sidebar tag and badges the arrival on a shelf already read", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([adapter({ id: 1 })]);
    await store.fetchRows();
    listAdapters.mockResolvedValue([adapter({ id: 1 }), adapter({ id: 2 })]);

    store.onModelsArrived();
    await vi.waitFor(() => expect(store.loading).toBe(false));

    expect(store.unseenArrivals).toBe(true);
    expect(listAdapters).toHaveBeenCalledTimes(2);
    expect(store.visibleRows.find((r) => r.id === 2)?.isNew).toBe(true);
    expect(store.visibleRows.find((r) => r.id === 1)?.isNew).toBe(false);
  });

  it("reads nothing for a shelf never opened, where every row would be new", () => {
    const store = useModelShelfStore();
    store.onModelsArrived();
    expect(store.unseenArrivals).toBe(true);
    expect(listAdapters).not.toHaveBeenCalled();
  });
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
  it("puts every kind in one bucket from one request each", async () => {
    // One checkbox, four `file_kind`s. The route takes a single kind, so the
    // block is four requests, and a reader deciding what to keep sees one list.
    const store = useModelShelfStore();
    listSupport
      .mockResolvedValueOnce([adapter({ id: 1, file_kind: "vae", kind: null })])
      .mockResolvedValueOnce([
        adapter({ id: 2, file_kind: "text_encoder", kind: null }),
      ])
      .mockResolvedValueOnce([
        adapter({ id: 5, file_kind: "controlnet", kind: null }),
      ])
      .mockResolvedValueOnce([
        adapter({ id: 7, file_kind: "upscaler", kind: null }),
      ]);

    await store.fetchRows();

    expect(store.rows.map((r) => r.id)).toEqual([1, 2, 5, 7]);
    expect(listSupport.mock.calls.map(([args]) => args.fileKind)).toEqual([
      "vae",
      "text_encoder",
      "controlnet",
      "upscaler",
    ]);
  });

  it("replaces only its own rows when it refetches", async () => {
    // `blockOf` has to map EVERY kind to `support`, or a refetch leaves the
    // kind it forgot about behind as a duplicate.
    const store = useModelShelfStore();
    listSupport
      .mockResolvedValueOnce([adapter({ id: 1, file_kind: "vae", kind: null })])
      .mockResolvedValueOnce([
        adapter({ id: 2, file_kind: "text_encoder", kind: null }),
      ])
      .mockResolvedValueOnce([
        adapter({ id: 5, file_kind: "controlnet", kind: null }),
      ])
      .mockResolvedValueOnce([
        adapter({ id: 7, file_kind: "upscaler", kind: null }),
      ]);
    await store.fetchRows();

    listSupport
      .mockResolvedValueOnce([adapter({ id: 3, file_kind: "vae", kind: null })])
      .mockResolvedValueOnce([
        adapter({ id: 4, file_kind: "text_encoder", kind: null }),
      ])
      .mockResolvedValueOnce([
        adapter({ id: 6, file_kind: "controlnet", kind: null }),
      ])
      .mockResolvedValueOnce([
        adapter({ id: 8, file_kind: "upscaler", kind: null }),
      ]);
    await store.fetchRows();

    expect(store.rows.map((r) => r.id)).toEqual([3, 4, 6, 8]);
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
    store.setView({ groupBy: "base_model" });
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

describe("sorting on every column", () => {
  it("sorts on Type by the words the cell shows", async () => {
    listAdapters.mockResolvedValue([adapter({ id: 1, display_name: "lora" })]);
    listCheckpoints.mockResolvedValue([
      adapter({ id: 2, file_kind: "checkpoint", kind: null, display_name: "ckpt" }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "none", sortKey: "type" });
    expect(store.view.sortDirection).toBe("asc");
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([2, 1]);
    store.setView({ sortDirection: "desc" });
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([1, 2]);
  });

  it("sorts on Sets by how many sets hold the model, most first", async () => {
    const member = (id) => ({
      name: "x",
      kind: "lora",
      slot: "lora",
      id,
      sha256: String(id).repeat(64),
      on_shelf: true,
    });
    const set = (id, members) => ({
      id,
      name: `set ${id}`,
      declined: [],
      removed: [],
      members: members.map(member),
    });
    listAdapters.mockResolvedValue([1, 2, 3].map((id) =>
      adapter({ id, sha256: String(id).repeat(64), display_name: `m${id}` }),
    ));
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [set(7, [3, 2]), set(8, [3])],
    });
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.setMembership.get(3)).toHaveLength(2);
    store.setView({ groupBy: "none", sortKey: "sets" });
    expect(store.view.sortDirection).toBe("desc");
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([3, 2, 1]);
    store.setView({ sortDirection: "asc" });
    expect(store.groups[0].rows.map((r) => r.id)).toEqual([1, 2, 3]);
  });
});

describe("grouping", () => {
  it("draws an open folder as one band, and keeps the grouping for leaving it", async () => {
    listAdapters.mockResolvedValue([
      adapter({ id: 1, locations: [{ state: "present", folder_id: 1, relpath: "a.st" }] }),
    ]);
    listCheckpoints.mockResolvedValue([
      adapter({
        id: 2,
        file_kind: "checkpoint",
        kind: null,
        locations: [{ state: "present", folder_id: 1, relpath: "b.st" }],
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "type", listGroupBy: "type" });
    expect(store.groups).toHaveLength(2);

    store.setFolderScope({ folderId: 1, path: "" });
    expect(store.groupAxis).toBe("none");
    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].rows.map((r) => r.id).sort()).toEqual([1, 2]);
    expect(store.view.groupBy).toBe("type");

    store.setPlace("all");
    expect(store.groupAxis).toBe("type");
    expect(store.groups).toHaveLength(2);
  });

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

  it("still renders one group when nothing is grouped, so the list has one shape", async () => {
    // `none` is no longer the default axis (#1438), so it is chosen here: the
    // one-group shape is what keeps the flat list and the banded list from
    // being two copies of the row markup, and that still has to hold.
    listAdapters.mockResolvedValue([adapter()]);
    const store = useModelShelfStore();
    await store.fetchRows();
    store.setView({ groupBy: "none" });
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

    // Type is the band that replaced Folder (#2168): 1,800 rows, one band each.
    store.setView({ groupBy: "type" });
    expect(store.groups.reduce((n, g) => n + g.rows.length, 0)).toBe(1800);
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
    store.setView({ groupBy: "feature" });
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
    store.setView({ groupBy: "base_model", sortDirection: "asc" });
    await store.fetchRows();
    store.resetForSession();
    expect(store.rows).toEqual([]);
    expect(store.view.groupBy).toBe("base_model");
    expect(store.view.sortDirection).toBe("asc");
  });

  it("reads a remembered Folder grouping back as Type", () => {
    // Folder is no longer an axis (#2168): what a reader chose as Folder lands
    // on the band that the sidebar's folder slot did not replace, not on a
    // grouping that no longer exists.
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({
        v: 2,
        groupBy: "folder",
        listGroupBy: "folder",
        sortKey: "name",
        sortDirection: "asc",
      }),
    );
    const store = useModelShelfStore();
    expect(store.view.listGroupBy).toBe("type");
    expect(store.view.groupBy).toBe("type");
    expect(store.view.sortKey).toBe("name");
  });

  it("keeps a remembered list grouping, and migrates one from before it existed", () => {
    // A blob that carries its own listGroupBy keeps it, whatever groupBy says.
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 2, groupBy: "base_model", listGroupBy: "none" }),
    );
    expect(useModelShelfStore().view.listGroupBy).toBe("none");

    // One from before #2168 carries none: its list grouping is its groupBy.
    setActivePinia(createPinia());
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 2, groupBy: "base_model" }),
    );
    expect(useModelShelfStore().view.listGroupBy).toBe("base_model");
  });

  it("falls back to the defaults on a blob from another schema", () => {
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 99, groupBy: "folder", sortKey: "name" }),
    );
    const store = useModelShelfStore();
    expect(store.view.groupBy).toBe("workflow_set");
    expect(store.view.sortKey).toBe("added_at");
  });

  it("migrates a blob from before the set grid instead of discarding it", () => {
    // The default of `groupBy` changed, and nothing else in the blob did. A
    // version 1 blob could only ever say `none`, so that field is not evidence
    // of a choice and is the one thing not carried forward; the widths, the
    // collapsed groups and the sort are exactly what the
    // reader set and survive. The first version of #1438 bumped the version and
    // threw all of it away for a change to one field.
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({
        v: 1,
        groupBy: "none",
        sortKey: "name",
        sortDirection: "asc",
        columnWidths: { kind: 120, base: 84, size: 74, date: 96 },
        collapsed: { base_model: ["sdxl"] },
      }),
    );

    const store = useModelShelfStore();

    expect(store.view.groupBy).toBe("workflow_set");
    expect(store.view.sortKey).toBe("name");
    expect(store.view.sortDirection).toBe("asc");
    expect(store.view.columnWidths.kind).toBe(120);
    store.setView({ groupBy: "base_model" });
    expect(store.isCollapsed("sdxl")).toBe(true);
  });

  it("still discards a blob from a version it does not know", () => {
    // The migration table names the versions this build can read. A future shape
    // change that leaves itself out of it falls through to the defaults whole,
    // which is what a bump is for.
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({
        v: 99,
        sortKey: "name",
        columnWidths: { kind: 120 },
        collapsed: { base_model: ["sdxl"] },
      }),
    );

    const store = useModelShelfStore();

    expect(store.view.sortKey).toBe("added_at");
    expect(store.view.columnWidths.kind).toBe(64);
    store.setView({ groupBy: "base_model" });
    expect(store.isCollapsed("sdxl")).toBe(false);
  });

  it("refuses a grouping or sort key it does not recognise", () => {
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 2, groupBy: "colour", sortKey: "vibes" }),
    );
    const store = useModelShelfStore();
    expect(store.view.groupBy).toBe("workflow_set");
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
      sets: 44,
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
      base: 140,
      size: 74,
      date: 96,
      sets: 56,
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
      base: 140,
      size: 90,
      date: 96,
      sets: 56,
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
      sets: 56,
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
      sets: 56,
    });
  });

  it("drops a Base width stored before its default widened, and only that", () => {
    // Every view change below version 3 wrote the old 84px default into the
    // blob, so the stored figure says nothing about a drag.
    window.localStorage.setItem(
      "pixlstash:modelShelfView",
      JSON.stringify({ v: 2, columnWidths: { kind: 120, base: 84 } }),
    );
    const store = useModelShelfStore();
    expect(store.view.columnWidths.base).toBe(140);
    expect(store.view.columnWidths.kind).toBe(120);
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
      base: 140,
      size: 74,
      date: 96,
      sets: 56,
    });
  });
});

describe("stacks are atomic", () => {
  beforeEach(onTheRowList);

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

  it("never hands a verb the same model twice, even drawn under two features", () => {
    // The review of #881 read `selectedRows` as one entry per DRAWN row, which
    // would duplicate under the feature axis - a model serving two features is
    // drawn twice. It is not: `selectedRows` filters `visibleRows`,
    // which is one entry per model, and the per-folder duplication happens in
    // `groups` for rendering only. Asserted rather than argued, and it stays
    // asserted so a future change to that derivation cannot quietly reintroduce
    // duplicate ids on the wire.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1, capabilities: ["captioner", "detector"] }),
    ];
    store.setView({ groupBy: "feature" });
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
  beforeEach(onTheRowList);

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
  beforeEach(onTheRowList);

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

  it("assigns every model in a ticked stack, not just its cover", async () => {
    // A fully-selected stack is ONE selected row carrying the cover's id and
    // hash; the picker is handed member ids and each member keeps its own
    // attachments in the union.
    const store = useModelShelfStore();
    store.rows = [
      adapter({ id: 1, stack_id: 9, stack_position: 0 }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        stack_id: 9,
        stack_position: 1,
        attachments: [{ entity_type: "set", entity_id: 3 }],
      }),
    ];
    store.selectVisible();
    expect(store.selectedRows).toHaveLength(1);

    await store.setAttachment({
      entityType: "character",
      entityId: 4,
      entityName: "Alice",
      subjectIds: ["1", "2"],
    });

    expect(setAdapterAttachments).toHaveBeenCalledTimes(2);
    expect(setAdapterAttachments).toHaveBeenCalledWith("a".repeat(64), [
      { entity_type: "character", entity_id: 4 },
    ]);
    expect(setAdapterAttachments).toHaveBeenCalledWith("b".repeat(64), [
      { entity_type: "set", entity_id: 3 },
      { entity_type: "character", entity_id: 4 },
    ]);
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
  beforeEach(onTheRowList);

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
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        stack_id: 9,
        stack_position: 1,
      }),
      adapter({
        id: 3,
        sha256: "c".repeat(64),
        stack_id: 9,
        stack_position: 2,
      }),
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
  beforeEach(onTheRowList);

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
    // A collapsed stack's rename writes every member, and is still one name.
    expect(editReceipt(5, { display_name: "Clementine" })).toBe(
      "Renamed to Clementine.",
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

  it("lookupReceipt adds up to the selection and says why some were not asked", () => {
    expect(
      lookupReceipt({
        filled: [{ id: 1, base_model: "SDXL 1.0" }],
        not_found: [2, 3],
        skipped: [{ id: 4, reason: "owner_set" }],
        not_asked: [5, 6, 7],
      }),
    ).toBe(
      "Filled 1 model · 2 not on Civitai · 1 left alone · 3 not asked (Civitai stopped answering)",
    );
    expect(
      lookupReceipt({ filled: [], not_found: [], skipped: [], not_asked: [] }),
    ).toBe("Filled 0 models");
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
  beforeEach(onTheRowList);

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
  beforeEach(onTheRowList);

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

  it("narrows by the shown name or the filename, ignoring case", async () => {
    listAdapters.mockResolvedValue([
      adapter({ id: 1 }),
      adapter({
        id: 2,
        display_name: null,
        filename: "Other_Thing_v2.safetensors",
      }),
    ]);
    const store = useModelShelfStore();
    await store.fetchRows();

    await store.setFilters({ name: "   " });
    expect(store.activeCount).toBe(0);
    expect(store.visibleRows.length).toBe(2);
    await store.setFilters({ name: "  CYANwood " });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
    expect(store.activeCount).toBe(1);
    // The filename, for someone typing what is on disk.
    await store.setFilters({ name: "other_thing_V2" });
    expect(store.visibleRows.map((r) => r.id)).toEqual([2]);

    // A search is not remembered across a reload, and typing one writes
    // nothing to storage.
    const writes = vi.spyOn(Storage.prototype, "setItem");
    await store.setFilters({ name: "cyan" });
    expect(writes).not.toHaveBeenCalled();
    writes.mockRestore();
    setActivePinia(createPinia());
    expect(useModelShelfStore().filters.name).toBe("");

    await store.resetFilters();
    expect(store.filters.name).toBe("");
    expect(store.visibleRows.length).toBe(2);
  });
});

describe("mergeCopies", () => {
  it("sends the keeper the reader chose and re-reads the row it kept", async () => {
    // The row is NOT removed from the list: the model is still on the shelf with
    // one fewer copy, so the list is re-read rather than the row taken out of it.
    listAdapters.mockResolvedValue({ models: [] });
    listCheckpoints.mockResolvedValue({ models: [] });
    listEngines.mockResolvedValue({ models: [] });
    listUnclassified.mockResolvedValue({ models: [] });
    listSupport.mockResolvedValue({ models: [] });
    mergeModelCopies.mockResolvedValue({
      merged: [7],
      files_removed: 1,
      permanent: false,
      dry_run: false,
      trash_name: "Trash",
      comfyui_reads: [],
      refused: [],
    });
    const store = useModelShelfStore();
    const keep = [{ model_id: 7, folder_id: 2, relpath: "b.safetensors" }];

    expect(await store.mergeCopies(keep)).toBe(true);
    expect(mergeModelCopies).toHaveBeenCalledWith(keep, { permanent: false });
    // The refetch is the "re-reads" half of the title, and it is asserted rather
    // than assumed: the row survives a merge with one fewer copy, so the list has
    // to be re-read or the copy count on screen stays wrong until the next visit.
    expect(listAdapters).toHaveBeenCalled();
    expect(useNoticeStore().notices.at(-1).text).toContain("Kept one copy");
  });

  it("says so when the call fails and keeps the shelf as it was", async () => {
    mergeModelCopies.mockRejectedValue(new Error("nope"));
    const store = useModelShelfStore();
    expect(
      await store.mergeCopies([
        { model_id: 7, folder_id: 2, relpath: "b.safetensors" },
      ]),
    ).toBe(false);
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });

  it("makes no call with nothing to merge", async () => {
    mergeModelCopies.mockClear();
    const store = useModelShelfStore();
    expect(await store.mergeCopies([])).toBe(false);
    expect(mergeModelCopies).not.toHaveBeenCalled();
  });
});

describe("mergeReceipt", () => {
  it("leads with what was kept, because that is what a merge is", () => {
    // A reader who has just removed a copy of a 20 GB checkpoint needs to know
    // the model is still there, or this reads like the delete they did not ask
    // for. Asserted whole, because a receipt nobody finishes reading is not a
    // receipt: the whole point of the wording is that it is one short line.
    expect(mergeReceipt(1, [], false, "Trash", 1)).toBe(
      "Kept one copy of 1 model. The spare copy is in your Trash.",
    );
  });

  it("names where the bytes went, and names no place on a permanent one", () => {
    expect(mergeReceipt(1, [], true, "Trash", 2)).toBe(
      "Kept one copy of 1 model. 2 spare copies are deleted.",
    );
    expect(mergeReceipt(1, [], true, "Trash", 2)).not.toContain("Trash");
  });

  it("tells the two refusals this route owns apart", () => {
    // "The copy you chose is gone" is the refusal that saved the reader from a
    // redownload; "only one copy" is a duplicate list that was a minute old.
    // Rolling them together would leave them guessing which happened.
    expect(
      mergeReceipt(
        0,
        [{ id: 1, reason: "keeper_not_present" }],
        false,
        "Trash",
      ),
    ).toContain("The copy you chose to keep is gone");
    expect(
      mergeReceipt(0, [{ id: 1, reason: "not_a_duplicate" }], false, "Trash"),
    ).toContain("already had only one copy");
  });

  it("says plainly when two names turned out to be one file", () => {
    // The symlink refusal, which reads as nonsense ("nothing was removed")
    // without a sentence of its own.
    expect(
      mergeReceipt(
        0,
        [{ id: 1, reason: "keeper_is_that_copy" }],
        false,
        "Trash",
      ),
    ).toContain("two names for one file");
  });

  it("reports a reason it has never seen as kept rather than dropping it", () => {
    const text = mergeReceipt(
      0,
      [{ id: 1, reason: "sundered" }],
      false,
      "Trash",
    );
    expect(text).toContain("left alone");
  });
});

describe("the workflow sets", () => {
  /** Two adapters on the shelf; one has run in a recipe and one has not. */
  function shelfWithASet() {
    listAdapters.mockResolvedValue([
      adapter({ id: 1, sha256: "a".repeat(64), filename: "used.st" }),
      adapter({ id: 2, sha256: "b".repeat(64), filename: "never.st" }),
    ]);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "1",
          models: [{ id: 1, name: "used.st", kind: "checkpoint" }],
          recipes: 3,
          picture_count: 12,
          covers: [],
        },
      ],
      no_set: [2],
    });
    return useModelShelfStore();
  }

  it("reads the sets once, however many times something asks", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    await store.loadWorkflowSets();
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
    // With the rows in hand, so "once" is not standing in for "the payload was
    // never used": the second call found the first one's answer, not an empty
    // grid.
    expect(store.setGroups).toHaveLength(1);
  });

  it("discards a read the reader has already overtaken", async () => {
    // The epoch guard. A `force` read fired while an earlier one is in flight
    // must win whatever order they resolve in - a session reset or a scan is
    // what fires the second, and landing the stale answer over it puts the
    // previous credential's or the pre-scan sets back on screen. Deleting the
    // guard left the whole store suite green (#1479 review).
    const store = shelfWithASet();
    await store.fetchRows();

    let releaseFirst;
    fetchWorkflowSets.mockImplementationOnce(
      () => new Promise((resolve) => (releaseFirst = resolve)),
    );
    const stale = store.loadWorkflowSets();
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "9",
          models: [{ id: 1, name: "used.st", kind: "checkpoint" }],
          recipes: 1,
          picture_count: 99,
          covers: [],
        },
      ],
      no_set: [],
    });
    await store.loadWorkflowSets({ force: true });
    // Now let the OVERTAKEN request answer, with the older payload.
    releaseFirst({ combinations: [], no_set: [] });
    await stale;

    expect(store.workflowSets.combinations.map((c) => c.key)).toEqual(["9"]);
    expect(store.setsLoaded).toBe(true);
  });

  it("drops a second read made while the first is still on the wire", async () => {
    // Reachable: the grid asks on mount and the dialog asks when it opens, and
    // each request is a window over every kept picture in the vault.
    const store = shelfWithASet();
    await store.fetchRows();
    await Promise.all([store.loadWorkflowSets(), store.loadWorkflowSets()]);
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
  });

  it("finds a set that reached the shelf through a run's second step", async () => {
    // **`visibleRows` is not a list of models.** A run is drawn as its cover, so
    // a stacked row's `id` is the cover's and the rest live in `memberIds`.
    // Matching a combination's members on `row.id` alone dropped every set that
    // reached the shelf through any other step, so the grid's empty state
    // claimed no picture recorded it.
    listAdapters.mockResolvedValue([
      adapter({
        id: 1,
        sha256: "a".repeat(64),
        filename: "cover.st",
        stack_id: 9,
      }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        filename: "step-two.st",
        stack_id: 9,
      }),
      adapter({
        id: 3,
        sha256: "c".repeat(64),
        filename: "lonely.st",
        stack_id: 9,
      }),
    ]);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "2",
          models: [{ id: 2, name: "step-two.st", kind: "checkpoint" }],
          recipes: 2,
          picture_count: 500,
          covers: [],
        },
      ],
      no_set: [3],
    });
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.loadWorkflowSets();

    // One drawn row, three models behind it.
    expect(store.visibleRows.map((row) => row.id)).toEqual([1]);
    expect(store.visibleRows[0].memberIds).toEqual([1, 2, 3]);

    expect(store.visibleCombinations).toHaveLength(1);
    expect(store.setGroups[0].card.pictures).toBe(500);
  });

  it("draws a card per base model and none for the models with no set", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();

    expect(store.setGroups.map((group) => group.card.name)).toEqual([
      "used.st",
    ]);
    expect(store.setGroups[0].card.pictures).toBe(12);
  });

  it("does not refetch the sets when a filter refetches the rows", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    // `refetch: true` is what a `Show` checkbox passes, so this really does
    // re-run the row query: the sets must NOT ride along with it, or every tick
    // costs a window over every picture in the vault.
    await store.setFilters({ checkpoints: false }, { refetch: true });
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
  });

  it("does refetch after a scan, because a scan can add a model", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    await store.fetchRows({ markNew: true });
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(2);
  });

  it("leaves a card out when Show hides every one of its members", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.visibleCombinations).toHaveLength(1);

    await store.setFilters({ adapters: false });

    expect(store.visibleCombinations).toEqual([]);
  });

  it("answers Works with from the whole payload, not from what Show draws", async () => {
    listAdapters.mockResolvedValue([adapter({ id: 1, filename: "a.st" })]);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "1,2",
          models: [
            { id: 1, name: "a.st", kind: "checkpoint" },
            { id: 2, name: "b.st", kind: "vae" },
          ],
          recipes: 4,
          picture_count: 9,
          covers: [],
        },
      ],
      no_set: [],
    });
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.loadWorkflowSets();
    // Untick the block the anchor is in: the grid now draws no card at all, and
    // the question "what has this file run with" is unchanged by that. Reading
    // the grid's own narrowed list here would answer it with silence.
    await store.setFilters({ adapters: false });
    expect(store.visibleCombinations).toEqual([]);

    const { companions, recipes } = store.worksWithModel(1);

    expect(companions.map((c) => c.name)).toEqual(["b.st"]);
    expect(recipes).toBe(4);
  });

  it("reports a failed read and leaves the last payload standing", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    fetchWorkflowSets.mockRejectedValue(new Error("hub is busy"));

    await store.loadWorkflowSets({ force: true });

    expect(store.setsError).toContain("hub is busy");
    expect(store.visibleCombinations).toHaveLength(1);
  });

  it("opens one stack at a time and closes the one that is open", async () => {
    const store = shelfWithASet();
    store.toggleSet("1,2");
    expect(store.openSetKey).toBe("1,2");
    store.toggleSet("3,4");
    expect(store.openSetKey).toBe("3,4");
    store.toggleSet("3,4");
    expect(store.openSetKey).toBe("");
  });

  it("forgets the sets when the credential changes", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.resetForSession();
    expect(store.workflowSets.combinations).toEqual([]);
    expect(store.setsLoaded).toBe(false);
    await store.loadWorkflowSets();
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(2);
  });

  it("groups every combination under its own base model, off one payload", async () => {
    listAdapters.mockResolvedValue([
      adapter({ id: 1, sha256: "a".repeat(64), filename: "ckpt.st" }),
      adapter({ id: 2, sha256: "b".repeat(64), filename: "vae.st" }),
      adapter({ id: 3, sha256: "c".repeat(64), filename: "lora.st" }),
    ]);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "1,2",
          models: [
            { id: 1, name: "ckpt.st", kind: "checkpoint" },
            { id: 2, name: "vae.st", kind: "vae" },
          ],
          recipes: 5,
          picture_count: 20,
          covers: [],
        },
        {
          key: "1,2,3",
          models: [
            { id: 1, name: "ckpt.st", kind: "checkpoint" },
            { id: 2, name: "vae.st", kind: "vae" },
            { id: 3, name: "lora.st", kind: "adapter" },
          ],
          recipes: 2,
          picture_count: 5,
          covers: [],
        },
      ],
      no_set: [],
    });
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.loadWorkflowSets();

    // Two combinations, ONE base model: one card, whose tray holds all three
    // models, with both combinations' evidence summed onto it.
    expect(store.setGroups).toHaveLength(1);
    expect(store.setGroups[0].models.map((m) => m.name)).toEqual([
      "ckpt.st",
      "vae.st",
      "lora.st",
    ]);
    expect(store.setGroups[0].card.facts).toEqual([
      "3 models",
      "7 recipes",
      "25 pictures",
    ]);
    // The LoRA carries its OWN evidence rather than the group's.
    const lora = store.setGroups[0].models.find((m) => m.name === "lora.st");
    expect([lora.recipes, lora.pictures]).toEqual([2, 5]);
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
  });

  it("remembers how the tray draws its models between visits", () => {
    const store = useModelShelfStore();
    expect(store.view.trayView).toBe("grid");
    store.setView({ trayView: "list" });
    setActivePinia(createPinia());
    expect(useModelShelfStore().view.trayView).toBe("list");
  });

  /**
   * A checkpoint the row list shows and a VAE it does not, both in one tray.
   *
   * The shape `setGridModelIds` exists for: a combination survives `Show` on any
   * one visible member and is then drawn WHOLE, so unticking the support files
   * takes the VAE off the row list and leaves it on the card.
   */
  async function shelfWithAHiddenCompanion() {
    listCheckpoints.mockResolvedValue([
      adapter({
        id: 1,
        sha256: "a".repeat(64),
        filename: "ckpt.st",
        file_kind: "checkpoint",
        kind: null,
      }),
    ]);
    // `/adapters` is one route serving both support kinds, so the double has to
    // answer `vae` and `text_encoder` differently or the VAE arrives twice.
    listSupport.mockImplementation((args) =>
      Promise.resolve(
        args?.fileKind === "vae"
          ? [
              adapter({
                id: 2,
                sha256: "b".repeat(64),
                filename: "vae.st",
                file_kind: "vae",
                kind: null,
              }),
            ]
          : [],
      ),
    );
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "1,2",
          models: [
            { id: 1, name: "ckpt.st", kind: "checkpoint" },
            { id: 2, name: "vae.st", kind: "vae" },
          ],
          recipes: 2,
          picture_count: 4,
          covers: [],
        },
      ],
      no_set: [],
    });
    const store = useModelShelfStore();
    // The default axis, said out loud: these are the grid's own assertions.
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    await store.loadWorkflowSets();
    return store;
  }

  it("only counts a tray's models as actionable while its tray is open", async () => {
    const store = await shelfWithAHiddenCompanion();
    expect([...store.setGridModelIds].sort()).toEqual([1]);

    store.setFilters({ support: false });
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
    // Open the card: its hidden companion is now visibly represented in the
    // tray and is safe to select.
    store.toggleSet("model:1");
    expect([...store.setGridModelIds].sort()).toEqual([1, 2]);

    // Closing it removes the only representation of the companion again.
    store.toggleSet("model:1");
    expect([...store.setGridModelIds].sort()).toEqual([1]);
  });

  it("hands the verbs a model picked off a card the row list is hiding", async () => {
    const store = await shelfWithAHiddenCompanion();
    store.setFilters({ support: false });
    store.toggleSet("model:1");
    store.toggleSelected(2);

    // `selectedRows` is built from `visibleRows`, which does not hold it - so
    // without the grid's own reach the click would tick a row the bar then
    // could not see, and every verb would be aimed at nothing.
    expect(store.selectedRows.map((r) => r.id)).toEqual([2]);
    // Wearing the shape a listed row has, because the verbs read these fields:
    // `name` is `modelName`'s `{text, state}` pair and not the raw column, and
    // the location state is reduced. A raw `rows` entry has neither.
    expect(store.selectedRows[0].name).toMatchObject({
      text: "Cyanwood Style",
    });
    expect(store.selectedRows[0].locState).toBe("present");
  });

  it("takes that reach away again the moment the row list is back", async () => {
    // The invariant the row list rests on: a verb may only ever act on
    // something the reader can SEE, and off the grid they cannot see it.
    const store = await shelfWithAHiddenCompanion();
    store.setFilters({ support: false });
    store.toggleSet("model:1");
    store.toggleSelected(2);
    expect(store.selectedRows).toHaveLength(1);

    store.setView({ groupBy: "none" });
    expect(store.selectedRows).toHaveLength(0);
  });

  it("arms no verb over a model the grid draws nowhere", async () => {
    // The hazard the hidden verb bar used to stand in for. A selection survives
    // an axis switch on purpose, so a reader can Ctrl+A the row list, switch to
    // the grid and find the bar still offering Delete over 1,800 models that
    // screen shows none of. `selectedRows` reads the rows the SCREEN draws, so
    // the offer goes with the screen.
    const store = await shelfWithAHiddenCompanion();
    store.setView({ groupBy: "none" });
    store.selectVisible();
    expect(store.selectedRows.map((r) => r.id).sort()).toEqual([1, 2]);

    // A model on no card at all: `no_set` is not drawn on the grid.
    fetchWorkflowSets.mockResolvedValue({ combinations: [], no_set: [1, 2] });
    await store.loadWorkflowSets({ force: true });
    store.setView({ groupBy: "workflow_set" });

    expect(store.selectedRows).toEqual([]);
    // And the ids are still held, so going back offers them again.
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    store.setView({ groupBy: "none" });
    expect(store.selectedRows.map((r) => r.id).sort()).toEqual([1, 2]);
  });

  it("refuses Select all shown rather than clearing what is held", async () => {
    // A key that says "select" must never be a silent clear. The guard used to
    // sit at one caller and read `visibleRows`, which is the row list's list -
    // so on the grid it passed and `selectVisible` then emptied the selection.
    // The pill's own button never had the guard at all.
    const store = await shelfWithAHiddenCompanion();
    store.setView({ groupBy: "none" });
    store.selectVisible();
    expect([...store.selectedIds].sort()).toEqual([1, 2]);

    fetchWorkflowSets.mockResolvedValue({ combinations: [], no_set: [] });
    await store.loadWorkflowSets({ force: true });
    store.setView({ groupBy: "workflow_set" });
    expect(store.setGridRows).toEqual([]);
    // The row list still holds two rows, which is exactly what the old guard
    // was reading when it let this through.
    expect(store.visibleRows).toHaveLength(2);

    store.selectVisible();
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
  });

  it("takes ONE file from a card, though that file is a step of a run", async () => {
    // **A card is one model and stands for that model alone.** An earlier draft
    // folded runs here, on the reasoning that a run is atomic: the effect was
    // that clicking one card lit up every other card built on the same run, and
    // a verb aimed at the set in front of the reader reached files in sets they
    // were not looking at. The grid's question is *what is in THIS workflow
    // set*, and a card names exactly one file in it.
    //
    // Two cards on one run, which is the shape that made it visible: a recipe
    // names step two, another names step one.
    listAdapters.mockResolvedValue([
      adapter({ id: 1, sha256: "a".repeat(64), stack_id: 7, stack_position: 0 }),
      adapter({ id: 2, sha256: "b".repeat(64), stack_id: 7, stack_position: 1 }),
    ]);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "2",
          models: [{ id: 2, name: "step-two.st", kind: "checkpoint" }],
          recipes: 1,
          picture_count: 1,
          covers: [],
        },
        {
          key: "1",
          models: [{ id: 1, name: "step-one.st", kind: "checkpoint" }],
          recipes: 1,
          picture_count: 1,
          covers: [],
        },
      ],
      no_set: [],
    });
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    await store.loadWorkflowSets();

    // Two cards, and two rows behind them: the grid does not fold the run.
    expect(store.setGroups).toHaveLength(2);
    expect(store.setGridRows.map((r) => r.id).sort()).toEqual([1, 2]);
    expect(store.setGridRows.every((r) => !r.memberIds)).toBe(true);

    store.selectFromClick(2, {}, [1, 2]);

    // Asserted on identity, not on a count: the point is that the OTHER card's
    // model did not come with it, which a length of 1 would not tell you.
    expect([...store.selectedIds]).toEqual([2]);
    expect(store.selectedRows.map((r) => r.id)).toEqual([2]);
    expect(store.selectedModelIds).toEqual([2]);
    expect(store.isSelected(1)).toBe(false);

    // And the row list still takes the run whole, which is where the reader can
    // actually see that it is one.
    store.setView({ groupBy: "none" });
    store.selectFromClick(1, {}, [1]);
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
  });

  it("answers Select all shown with the grid's models, on the grid", async () => {
    const store = await shelfWithAHiddenCompanion();
    store.setFilters({ support: false });
    store.toggleSet("model:1");

    store.selectVisible();
    expect([...store.selectedIds].sort()).toEqual([1, 2]);

    // And with the row list's own answer, on the row list.
    store.setView({ groupBy: "none" });
    store.selectVisible();
    expect([...store.selectedIds]).toEqual([1]);
  });
});

describe("sets from pictures are hidden, never deleted", () => {
  const checkpoint = { id: 1, name: "base.st", kind: "checkpoint" };
  const lora = { id: 2, name: "style.st", kind: "adapter" };

  /** One set from pictures: a checkpoint that ran with one LoRA. */
  function shelfWithASet(handMade = []) {
    listAdapters.mockResolvedValue([
      adapter({ id: 1, sha256: "a".repeat(64), filename: "base.st" }),
      adapter({ id: 2, sha256: "b".repeat(64), filename: "style.st" }),
    ]);
    // Answered when asked, as the hub is: a read after a hide or an undo sees
    // what the server holds then, not what it held when the shelf was built.
    fetchWorkflowSets.mockImplementation(async () => ({
      combinations: [
        {
          key: "1+2",
          models: [checkpoint, lora],
          recipes: 2,
          picture_count: 5,
          covers: [],
          // What the server answers once a hand-made set holds both.
          covered_by: handMade.length ? [handMade[0].id] : [],
        },
      ],
      no_set: [],
      hand_made: handMade,
      hidden_sets: [...hiddenOnServer],
    }));
    return useModelShelfStore();
  }

  function member(model, sha) {
    return { ...model, sha256: sha, slot: "checkpoint", on_shelf: true };
  }

  it("takes only the hand-made sets the grid draws on Select all", async () => {
    // A hidden hand-made set leaves the grid (#1869), so Select all must not
    // take it: Delete would otherwise delete a set the reader cannot see.
    hiddenOnServer.add("hand:7");
    const store = shelfWithASet([
      {
        id: 7,
        name: "seven",
        declined: [],
        members: [member(checkpoint, "a".repeat(64))],
      },
      {
        id: 8,
        name: "eight",
        declined: [],
        members: [member(lora, "b".repeat(64))],
      },
    ]);
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.handMadeGroups.map((group) => group.set.id)).toEqual([8]);

    store.selectVisible();
    expect([...store.selectedSetIds]).toEqual([8]);
  });

  it("narrows the hand-made cards by name, and only the grid's", async () => {
    const store = shelfWithASet([
      {
        id: 7,
        name: "Seven Up",
        declined: [],
        members: [member(checkpoint, "a".repeat(64))],
      },
      {
        id: 8,
        name: "eight",
        declined: [],
        members: [member(lora, "b".repeat(64))],
      },
      {
        id: 9,
        name: "nine",
        declined: [],
        members: [
          member({ id: 99, name: "Lonely_Ink.safetensors" }, "c".repeat(64)),
        ],
      },
    ]);
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    await store.loadWorkflowSets();
    const drawn = () => store.handMadeGroups.map((group) => group.set.id);

    // By a member's filename: only set 8 holds `style.st`.
    store.setFilters({ name: "sTyLe.st" });
    expect(drawn()).toEqual([8]);
    // By the set's own name.
    store.setFilters({ name: "seven" });
    expect(drawn()).toEqual([7]);
    // The rail and the set chooser still offer every set.
    expect(store.shownHandMadeSets.map((set) => set.id)).toEqual([7, 8, 9]);
    // By the name a member's card shows when the shelf has no row for it (its
    // block not fetched): set 9's member is not among the rows.
    store.setFilters({ name: "LONELY ink" });
    expect(drawn()).toEqual([9]);
    store.setFilters({ name: "" });
    expect(drawn()).toEqual([7, 8, 9]);
  });

  it("hides a set, remembers it, and draws it again on request", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    const [group] = store.setGroups;
    expect(group.key).toBe("model:1");

    store.hideSets([group]);
    await flush();
    expect(store.setGroups).toHaveLength(0);
    expect(store.hiddenSetCount).toBe(1);
    expect(setWorkflowSetsHidden).toHaveBeenCalledWith(["model:1"], true);
    expect([...hiddenOnServer]).toEqual(["model:1"]);

    store.showHiddenSets = true;
    expect(store.setGroups.map((g) => [g.key, g.card.hidden])).toEqual([
      ["model:1", true],
    ]);

    store.unhideSets([store.setGroups[0]]);
    await flush();
    store.showHiddenSets = false;
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
    expect(store.hiddenSetCount).toBe(0);
    expect([...hiddenOnServer]).toEqual([]);
  });

  it("hides a hand-made set the same way, under its own key", async () => {
    const store = shelfWithASet([handSet(7)]);
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.handMadeGroups.map((g) => g.key)).toEqual(["hand:7"]);

    store.hideSets([store.handMadeGroups[0]]);
    await flush();
    expect(store.handMadeGroups).toHaveLength(0);
    expect(store.hiddenSetCount).toBe(1);
    expect(setWorkflowSetsHidden).toHaveBeenCalledWith(["hand:7"], true);

    // Shown again: drawn, dimmed, and the eye reverts.
    store.showHiddenSets = true;
    expect(store.handMadeGroups.map((g) => [g.key, g.card.hidden])).toEqual([
      ["hand:7", true],
    ]);
  });

  it("toggles a selection: hides what is shown, shows again once all are hidden", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.showHiddenSets = true;

    store.toggleHiddenSets(store.setGroups);
    await flush();
    expect(store.setGroups.map((g) => g.hidden)).toEqual([true]);

    store.toggleHiddenSets(store.setGroups);
    await flush();
    expect(store.setGroups.map((g) => g.hidden)).toEqual([false]);
  });

  it("raises no hide receipt when the write did not land", async () => {
    // The receipt claims the hide happened and arms an Undo, so it must wait
    // for the PUT: over a refused write it would offer to undo nothing.
    useOperationStore().setLocalReceiptHost(true);
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    setWorkflowSetsHidden.mockRejectedValueOnce(new Error("put refused"));

    store.toggleHiddenSets(store.setGroups);
    await flush();
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
    expect(store.hiddenSetCount).toBe(0);
    expect(useNoticeStore().notices.at(-1)).toMatchObject({ level: "error" });
    expect(useOperationStore().receipt).toBe(null);
  });

  it("drops a hidden card's head from the selection, so Undo re-arms nothing", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.setView({ groupBy: "workflow_set" });
    store.toggleSelected(1);
    expect(store.selectedRows.map((r) => r.id)).toEqual([1]);

    store.toggleHiddenSets(store.setGroups);
    await flush();
    expect([...store.selectedIds]).toEqual([]);
    await useOperationStore().takeLocalReceiptAction();
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
    expect(store.selectedRows).toEqual([]);

    // Drawn dimmed instead, the card stays on screen and stays selected, so
    // Show again can follow.
    store.toggleSelected(1);
    store.showHiddenSets = true;
    store.toggleHiddenSets(store.setGroups);
    await flush();
    expect(store.setGroups.map((g) => g.hidden)).toEqual([true]);
    expect([...store.selectedIds]).toEqual([1]);
  });

  it("reopens the tray a hide closed when the hide is undone", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.toggleSet("model:1");
    store.hideSets([store.setGroups[0]]);
    await flush();
    expect(store.openSetKey).toBe("");
    await useOperationStore().takeLocalReceiptAction();
    expect(store.openSetKey).toBe("model:1");
  });

  /** A promise its test answers by hand, when the answer's place matters. */
  function deferred() {
    let resolve;
    const promise = new Promise((done) => (resolve = done));
    return { promise, resolve };
  }

  it("undoes a hide on the server and on screen", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.hideSets([store.setGroups[0]]);
    await flush();
    expect([...hiddenOnServer]).toEqual(["model:1"]);

    await useOperationStore().takeLocalReceiptAction();
    expect([...hiddenOnServer]).toEqual([]);
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
    expect(store.hiddenSetCount).toBe(0);
  });

  it("takes a hidden hand-made set out of the set selection", async () => {
    const store = shelfWithASet([handSet(7)]);
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.setView({ groupBy: "workflow_set" });
    store.selectSet(7);
    expect([...store.selectedSetIds]).toEqual([7]);

    store.hideSets([store.handMadeGroups[0]]);
    await flush();
    expect([...store.selectedSetIds]).toEqual([]);
  });

  it("favourites a selection of sets AND models, not only the sets", async () => {
    const store = shelfWithASet([handSet(7)]);
    await store.fetchRows();
    await store.loadWorkflowSets();
    // On the row list, where a model row is drawn; the set stays selected.
    store.setView({ groupBy: "none" });
    store.toggleSelected(2);
    store.selectSet(7, { ctrl: true });
    const targets = store.favouriteTargets();
    expect(targets).toEqual([
      { kind: "set", keys: ["hand:7"] },
      { kind: "model", keys: ["b".repeat(64)] },
    ]);
    setFavourites.mockClear();
    await store.toggleFavourites(targets);
    expect(setFavourites.mock.calls).toEqual([
      ["set", ["hand:7"], true],
      ["model", ["b".repeat(64)], true],
    ]);
  });

  it("favourites a selected missing-base card by its own key", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.selectMissing("missing:gone.safetensors");
    expect(store.favouriteTargets()).toEqual([
      { kind: "set", keys: ["missing:gone.safetensors"] },
    ]);
  });

  it("hiding an open hand-made tray never deletes that set", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const store = await openOn([handSet(7)]);
    store.hideSets([store.handMadeGroups[0]]);
    await flush();
    await flush();
    expect(store.openSetKey).toBe("");
    expect(deleteWorkflowSet).not.toHaveBeenCalled();
    expect(store.handMadeSets.map((set) => set.id)).toEqual([7]);
    expect([...hiddenOnServer]).toEqual(["hand:7"]);
  });

  it("turning Show them off over a hidden hand-made tray never deletes it", async () => {
    const store = await openOn([handSet(7)]);
    store.showHiddenSets = true;
    store.hideSets([store.handMadeGroups[0]]);
    await flush();
    expect(store.openSetKey).toBe("hand:7");

    // The grid drops the hidden card, which closes its tray on the way out.
    store.showHiddenSets = false;
    await flush();
    await flush();
    expect(store.openSetKey).toBe("");
    expect(deleteWorkflowSet).not.toHaveBeenCalled();
    expect([...hiddenOnServer]).toEqual(["hand:7"]);
  });

  it("applies the later hide's answer when the earlier one lands last", async () => {
    // Uncovered, so the set from pictures stays on the grid beside the hand-made one.
    const store = await openOn([handSet(7)], []);
    const first = deferred();
    const second = deferred();
    setWorkflowSetsHidden
      .mockReset()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    store.hideSets([store.setGroups[0]]);
    store.hideSets([store.handMadeGroups[0]]);
    second.resolve(["model:1", "hand:7"]);
    await flush();
    first.resolve(["model:1"]);
    await flush();
    expect(store.hiddenSetCount).toBe(2);
  });

  it("keeps a hide that lands while a set read is on the wire", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    const answer = deferred();
    // The read is served before the hide, and answers after it.
    fetchWorkflowSets.mockImplementationOnce(() => answer.promise);
    const reading = store.loadWorkflowSets({ force: true });

    store.hideSets([store.setGroups[0]]);
    await flush();
    expect(store.hiddenSetCount).toBe(1);
    // The same set, served before the hide: its own list says nothing hidden.
    answer.resolve({
      combinations: [
        { key: "1+2", models: [checkpoint, lora], recipes: 2, covers: [] },
      ],
      no_set: [],
      hidden_sets: [],
    });
    await reading;
    expect(store.hiddenSetCount).toBe(1);
    expect(store.setsLoading).toBe(false);
  });

  it("drops a hide answered after the session was reset", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    const answer = deferred();
    setWorkflowSetsHidden
      .mockReset()
      .mockImplementationOnce(() => answer.promise);

    store.hideSets([store.setGroups[0]]);
    store.resetForSession();
    // The new session reads its own rows and sets, with nothing hidden.
    await store.fetchRows();
    await store.loadWorkflowSets();
    answer.resolve(["model:1"]);
    await flush();
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
    expect(store.hiddenSetCount).toBe(0);
  });

  it("removes the retired per-browser hide list when the store starts", async () => {
    window.localStorage.setItem(
      "pixlstash:modelShelfHiddenSets",
      JSON.stringify(["model:1"]),
    );
    useModelShelfStore();
    expect(window.localStorage.getItem("pixlstash:modelShelfHiddenSets")).toBe(
      null,
    );
  });

  it("starts the store when clearing the retired list throws", async () => {
    const remove = vi
      .spyOn(Storage.prototype, "removeItem")
      .mockImplementation(() => {
        throw new Error("test-storage-denied");
      });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      expect(() => useModelShelfStore()).not.toThrow();
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining("could not clear"),
        expect.any(Error),
      );
    } finally {
      remove.mockRestore();
      warn.mockRestore();
    }
  });

  it("keeps a hidden set hidden in a new session, from the server's answer", async () => {
    // The key lives on the server (#1869), not in this browser: a fresh store
    // reads it back off the set payload.
    hiddenOnServer.add("model:1");
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.setGroups).toHaveLength(0);
    expect(store.hiddenSetCount).toBe(1);
  });

  it("keeps the precision a member's and a removed model's name has lost", async () => {
    listAdapters.mockResolvedValue([
      adapter({ id: 1, sha256: "a".repeat(64), filename: "Qwen3VL_4b_bf16.st", display_name: null, quant: "bf16" }),
      adapter({ id: 2, sha256: "b".repeat(64), filename: "Qwen3VL_4b_fp8_scaled.st", display_name: null, quant: "fp8" }),
    ]);
    const encoder = { name: "x", kind: "text_encoder", slot: "text_encoder" };
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [
        {
          id: 7,
          name: null,
          declined: [],
          members: [
            { ...encoder, id: 1, sha256: "a".repeat(64), on_shelf: true },
            {
              ...encoder,
              id: null,
              sha256: "c".repeat(64),
              on_shelf: false,
              label: "Qwen3VL_4b_Q4_K_M.gguf",
            },
            {
              ...encoder,
              id: null,
              sha256: "d".repeat(64),
              on_shelf: false,
              label: "RealVis fp16",
            },
          ],
          removed: [{ ...encoder, id: 2, sha256: "b".repeat(64) }],
        },
      ],
    });
    const store = useModelShelfStore();
    await store.fetchRows();
    await store.loadWorkflowSets();

    const [set] = store.handMadeSets;
    // One name three times over: the quant is all that tells them apart.
    expect(set.members.map((m) => [m.name, m.quant])).toEqual([
      ["Qwen3VL 4b", "bf16"],
      ["Qwen3VL 4b", "q4_k_m"],
      // A label that is no filename is kept whole, so it gets no badge too.
      ["RealVis fp16", null],
    ]);
    expect(set.removed.map((m) => [m.name, m.quant])).toEqual([
      ["Qwen3VL 4b", "fp8"],
    ]);
  });

  /** A hand-made set; `extra` adds members beyond the twin's two. */
  function handSet(id, extra = [], others = {}) {
    return {
      id,
      name: null,
      declined: [],
      members: [
        member(checkpoint, "a".repeat(64)),
        member(lora, "b".repeat(64)),
        ...extra,
      ],
      ...others,
    };
  }

  /** The store with `sets` on the hub, its first one's tray open. */
  async function openOn(sets, coveredBy = sets.map((set) => set.id)) {
    const store = shelfWithASet(sets);
    fetchWorkflowSets.mockResolvedValue({
      ...(await fetchWorkflowSets()),
      combinations: [
        {
          key: "1+2",
          models: [checkpoint, lora],
          recipes: 2,
          picture_count: 5,
          covers: [],
          covered_by: coveredBy,
        },
      ],
    });
    await store.fetchRows();
    await store.loadWorkflowSets();
    store.toggleSet(`hand:${sets[0].id}`);
    await flush();
    deleteWorkflowSet.mockReset().mockImplementation(async (id) => {
      fetchWorkflowSets.mockResolvedValue({
        combinations: [
          { key: "1+2", models: [checkpoint, lora], recipes: 2, covers: [] },
        ],
        no_set: [],
        hand_made: sets.filter((set) => set.id !== id),
        // The refetch an undo runs reads the hidden list back off the server,
        // so the double answers with what the hub now holds (#1869).
        hidden_sets: [...hiddenOnServer],
      });
      return { deleted: sets.find((set) => set.id === id) };
    });
    return store;
  }

  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

  /** A set living on the checkpoint: `lora` from the pictures, `extra` added. */
  function livingSet(id, { extra = [], removed = [], newLora = false, ...others } = {}) {
    return {
      id,
      name: null,
      declined: removed.map((m) => m.sha256),
      removed,
      automatic: !extra.length && !removed.length,
      members: [
        { ...member(checkpoint, "a".repeat(64)), origin: "pictures", stored: true },
        {
          ...member(lora, "b".repeat(64)),
          slot: "lora",
          origin: "pictures",
          stored: false,
          new: newLora,
        },
        ...extra,
      ],
      ...others,
    };
  }

  const added = {
    ...member({ id: 3, name: "x.st", kind: "vae" }, "d".repeat(64)),
    slot: "vae",
    origin: "added",
    stored: true,
    label: "x.st",
  };

  it("marks what the pictures brought in seen when its tray closes, never before", async () => {
    const store = await openOn([livingSet(7, { newLora: true })]);
    markWorkflowSetSeen.mockReset().mockResolvedValue({ seen: [] });
    // Open: the owner is looking at it, so it is still drawn new.
    expect(markWorkflowSetSeen).not.toHaveBeenCalled();
    expect(store.handMadeGroups[0].card.fresh).toBe(1);

    store.toggleSet("hand:7");
    await flush();
    expect(markWorkflowSetSeen).toHaveBeenCalledWith(7, ["b".repeat(64)]);
  });

  it("writes nothing on close when nothing in the set is new", async () => {
    const store = await openOn([livingSet(7)]);
    markWorkflowSetSeen.mockReset().mockResolvedValue({ seen: [] });
    expect(store.handMadeGroups[0].card.fresh).toBe(0);
    store.toggleSet("hand:7");
    await flush();
    expect(markWorkflowSetSeen).not.toHaveBeenCalled();
  });

  it("removes a model the pictures brought in by keeping it out, and one added by taking it out", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const set = livingSet(7, { extra: [added] });
    const store = await openOn([set]);
    setWorkflowSetDeclines.mockReset().mockResolvedValue({
      set: { declined: ["b".repeat(64)] },
      previous: [],
    });
    removeWorkflowSetMembers.mockReset().mockResolvedValue({
      removed: [{ sha256: "d".repeat(64), slot: "vae", label: "x.st" }],
    });

    await store.removeFromHandMadeSet(store.handMadeSets[0], [
      "b".repeat(64),
      "d".repeat(64),
    ]);
    expect(setWorkflowSetDeclines).toHaveBeenCalledWith(7, ["b".repeat(64)]);
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(7, ["d".repeat(64)]);

    // Undo: the added member back, and the kept-out one off the list.
    setWorkflowSetDeclines.mockClear().mockResolvedValue({ set: { declined: [] } });
    addWorkflowSetMembers.mockReset().mockResolvedValue({ added: [], set: set });
    await useOperationStore().takeLocalReceiptAction();
    expect(addWorkflowSetMembers).toHaveBeenCalledWith(7, [
      { sha256: "d".repeat(64), slot: "vae", label: "x.st" },
    ]);
    expect(setWorkflowSetDeclines).toHaveBeenCalledWith(7, []);
  });

  it("decides how to remove from the set as last read, not a stale copy of it", async () => {
    const store = await openOn([livingSet(7)]);
    setWorkflowSetDeclines.mockReset().mockResolvedValue({ set: { declined: [] } });
    removeWorkflowSetMembers.mockReset().mockResolvedValue({ removed: [] });
    // A caller's copy from before the read: it does not know the LoRA came
    // from the pictures, so a plain row delete would leave it in the set.
    await store.removeFromHandMadeSet({ id: 7, members: [] }, ["b".repeat(64)]);
    expect(setWorkflowSetDeclines).toHaveBeenCalledWith(7, ["b".repeat(64)]);
    expect(removeWorkflowSetMembers).not.toHaveBeenCalled();
  });

  it("resets from the set as last read, not a stale copy of it", async () => {
    const store = await openOn([livingSet(7, { extra: [added] })]);
    setWorkflowSetDeclines
      .mockReset()
      .mockResolvedValue({ set: { declined: [] }, previous: [] });
    removeWorkflowSetMembers.mockReset().mockResolvedValue({ removed: [] });
    // A copy from before the add: it does not know the VAE is the owner's.
    await store.resetHandMadeSet({ id: 7, members: [] });
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(7, ["d".repeat(64)]);
  });

  it("marks what a write still on the wire brought in, once it lands", async () => {
    const kept = { ...member(lora, "b".repeat(64)), slot: "lora", name: "style" };
    const store = await openOn([livingSet(7, { removed: [kept] })]);
    markWorkflowSetSeen.mockReset().mockResolvedValue({ seen: [] });
    let land;
    setWorkflowSetDeclines
      .mockReset()
      .mockImplementation(() => new Promise((resolve) => (land = resolve)));
    const restoring = store.restoreToHandMadeSet(store.handMadeSets[0], [
      "b".repeat(64),
    ]);
    // Closed while the Restore is out: nothing in the set is new yet.
    store.toggleSet("hand:7");
    await flush();
    expect(markWorkflowSetSeen).not.toHaveBeenCalled();
    // It lands, and the read after it serves the LoRA back, unseen.
    fetchWorkflowSets.mockResolvedValue({
      ...(await fetchWorkflowSets()),
      hand_made: [livingSet(7, { newLora: true })],
    });
    land({ set: { declined: [] }, previous: ["b".repeat(64)] });
    await restoring;
    await flush();
    await flush();
    expect(markWorkflowSetSeen).toHaveBeenCalledWith(7, ["b".repeat(64)]);
  });

  it("takes back the first write of a removal or a reset when the second fails", async () => {
    const store = await openOn([livingSet(7, { extra: [added] })]);
    const lists = [];
    setWorkflowSetDeclines.mockReset().mockImplementation(async (_, list) => {
      lists.push(list);
      return { set: { declined: list }, previous: ["e".repeat(64)] };
    });
    removeWorkflowSetMembers
      .mockReset()
      .mockRejectedValue(new Error("test-remove-failed"));
    // The LoRA is kept out, then the added VAE's row cannot be deleted.
    await store.removeFromHandMadeSet(store.handMadeSets[0], [
      "b".repeat(64),
      "d".repeat(64),
    ]);
    expect(lists).toEqual([["b".repeat(64)], []]);

    lists.length = 0;
    await store.resetHandMadeSet(store.handMadeSets[0]);
    // Cleared, then put back as it was.
    expect(lists).toEqual([[], ["e".repeat(64)]]);
    expect(useNoticeStore().notices.at(-1)).toMatchObject({ level: "error" });
  });

  it("restores a removed model, and resets a set to automatic with an Undo", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const kept = { ...member(lora, "b".repeat(64)), slot: "lora", name: "style" };
    const set = livingSet(7, { extra: [added], removed: [kept] });
    const store = await openOn([set]);
    setWorkflowSetDeclines
      .mockReset()
      .mockImplementation(async (_, list) => ({
        set: { declined: list },
        previous: ["b".repeat(64)],
      }));

    await store.restoreToHandMadeSet(store.handMadeSets[0], ["b".repeat(64)]);
    expect(setWorkflowSetDeclines).toHaveBeenLastCalledWith(7, []);
    expect(useOperationStore().receipt.summary).toMatch(/^Restored ".+" to /);

    removeWorkflowSetMembers.mockReset().mockResolvedValue({
      removed: [{ sha256: "d".repeat(64), slot: "vae", label: "x.st" }],
    });
    setWorkflowSetDeclines.mockClear();
    await store.resetHandMadeSet(store.handMadeSets[0]);
    // The checkpoint stays: it is what the set follows.
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(7, ["d".repeat(64)]);
    expect(setWorkflowSetDeclines).toHaveBeenCalledWith(7, []);

    addWorkflowSetMembers.mockReset().mockResolvedValue({ added: [] });
    setWorkflowSetDeclines.mockClear();
    await useOperationStore().takeLocalReceiptAction();
    expect(addWorkflowSetMembers).toHaveBeenCalledWith(7, [
      { sha256: "d".repeat(64), slot: "vae", label: "x.st" },
    ]);
    expect(setWorkflowSetDeclines).toHaveBeenCalledWith(7, ["b".repeat(64)]);
  });

  it("merges sets in one write, and Undo puts every merged set back whole", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const into = livingSet(7, { name: "Kit" });
    const other = livingSet(6, { extra: [added] });
    const store = await openOn([into, other]);
    const snapshot = {
      id: 6,
      name: "Other",
      members: [{ sha256: "a".repeat(64), slot: "checkpoint", label: "base" }],
      declined: ["e".repeat(64)],
      seen: ["c".repeat(64)],
      hidden: true,
    };
    mergeWorkflowSets.mockReset().mockResolvedValue({
      set: { ...into, declined: [] },
      added: ["d".repeat(64)],
      previous_declined: ["f".repeat(64)],
      was_hidden: false,
      merged: [snapshot],
    });

    await store.mergeHandMadeSets(into, [other]);
    expect(mergeWorkflowSets).toHaveBeenCalledWith(7, [6]);
    expect(useOperationStore().receipt.summary).toContain(
      'Merged "Other" into "Kit". No file was touched',
    );

    removeWorkflowSetMembers.mockReset().mockResolvedValue({ removed: [] });
    setWorkflowSetDeclines.mockReset().mockResolvedValue({ set: { declined: [] } });
    createWorkflowSet.mockReset().mockResolvedValue({ id: 11 });
    await useOperationStore().takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(7, ["d".repeat(64)]);
    expect(setWorkflowSetDeclines).toHaveBeenCalledWith(7, ["f".repeat(64)]);
    // What the owner had removed from it comes back in the same create, so
    // the set never holds it, even for a moment; and its hide here too.
    expect(createWorkflowSet).toHaveBeenCalledWith({
      name: "Other",
      members: snapshot.members,
      declined: ["e".repeat(64)],
    });
    expect(setWorkflowSetDeclines).not.toHaveBeenCalledWith(11, expect.anything());
    expect(hiddenOnServer.has("hand:11")).toBe(true);
    expect(markWorkflowSetSeen).toHaveBeenCalledWith(11, ["c".repeat(64)]);
  });

  it("hides nothing when the payload carries no hidden sets at all", async () => {
    // An older payload, or a library that hides nothing: the store draws every
    // set rather than treating a missing field as "hide everything".
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.hiddenSetCount).toBe(0);
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
  });

  it("reports a write that throws before it returns a promise", async () => {
    const store = shelfWithASet();
    createWorkflowSet.mockReset().mockImplementation(() => {
      throw new Error("test-sync-failure");
    });
    await expect(store.createHandMadeSet({})).resolves.toBeNull();
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
  });

  it("makes a set of the owner's from a set from pictures, marked as one", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    createWorkflowSet.mockReset().mockResolvedValue({ id: 9, members: [] });

    await store.cloneSetFromPictures(store.setGroups[0]);

    // The checkpoint alone: the pictures on it bring in the rest (#1972).
    expect(createWorkflowSet).toHaveBeenCalledWith({
      cloned: true,
      seen: true,
      members: [{ model_id: 1, slot: "checkpoint" }],
    });
    expect(store.openSetKey).toBe("hand:9");
  });

  it("makes a diffusion-file head the checkpoint", async () => {
    const store = shelfWithASet();
    createWorkflowSet.mockReset().mockResolvedValue({ id: 9, members: [] });
    await store.cloneSetFromPictures({
      head: { id: 1 },
      models: [
        { id: 1, kind: "unknown" },
        { id: 4, kind: "engine" },
        { id: 2, kind: "vae" },
      ],
    });
    expect(createWorkflowSet).toHaveBeenCalledWith({
      cloned: true,
      seen: true,
      members: [{ model_id: 1, slot: "checkpoint" }],
    });

    // A set named after a file the shelf does not hold has nothing to clone.
    createWorkflowSet.mockClear();
    await store.cloneSetFromPictures({ head: { missing: true }, models: [] });
    expect(createWorkflowSet).not.toHaveBeenCalled();
  });

  it("makes one clone when the button is pressed twice", async () => {
    const store = shelfWithASet();
    await store.fetchRows();
    await store.loadWorkflowSets();
    createWorkflowSet.mockReset().mockResolvedValue({ id: 9, members: [] });
    const [group] = store.setGroups;
    await Promise.all([
      store.cloneSetFromPictures(group),
      store.cloneSetFromPictures(group),
    ]);
    expect(createWorkflowSet).toHaveBeenCalledTimes(1);
    // Free again once the first has answered.
    await store.cloneSetFromPictures(group);
    expect(createWorkflowSet).toHaveBeenCalledTimes(2);
  });

  it("copies a clone as a clone, and a set made by hand as one made by hand", async () => {
    const store = shelfWithASet();
    createWorkflowSet.mockReset().mockResolvedValue({ id: 9, members: [] });
    await store.duplicateHandMadeSet(handSet(7, [], { cloned: true }));
    expect(createWorkflowSet.mock.calls[0][0].cloned).toBe(true);
    await store.duplicateHandMadeSet(handSet(8));
    expect(createWorkflowSet.mock.calls[1][0]).not.toHaveProperty("cloned");
  });

  it("puts a deleted clone back as a clone", async () => {
    useOperationStore().setLocalReceiptHost(true);
    const set = handSet(7, [], { cloned: true });
    const store = await openOn([set]);
    await store.deleteHandMadeSets([set]);
    createWorkflowSet.mockReset().mockResolvedValue({ id: 9 });
    await useOperationStore().takeLocalReceiptAction();
    expect(createWorkflowSet).toHaveBeenCalledWith(
      expect.objectContaining({ cloned: true }),
    );
  });
});

describe("a model is hidden in this library", () => {
  const checkpoint = { id: 1, name: "base.st", kind: "checkpoint" };
  const lora = { id: 2, name: "style.st", kind: "adapter" };
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

  /** A checkpoint and a LoRA that ran together, as the server holds them now. */
  function shelf({ handMade = [] } = {}) {
    const flagged = (row) => ({
      ...row,
      hidden: modelsHiddenOnServer.has(row.id),
    });
    listCheckpoints.mockImplementation(async () => [
      flagged(
        adapter({
          id: 1,
          sha256: "a".repeat(64),
          file_kind: "checkpoint",
          filename: "base.st",
          display_name: "Base",
        }),
      ),
    ]);
    listAdapters.mockImplementation(async () => [
      flagged(
        adapter({
          id: 2,
          sha256: "b".repeat(64),
          filename: "style.st",
          display_name: "Style",
        }),
      ),
    ]);
    fetchWorkflowSets.mockImplementation(async () => ({
      combinations: [
        {
          key: "1+2",
          models: [checkpoint, lora],
          recipes: 2,
          picture_count: 5,
          covers: [],
          covered_by: [],
        },
      ],
      no_set: [],
      hand_made: handMade,
      set_checks: [{ combo_key: "1,2", verdict: "yes", suspects: [] }],
      hidden_sets: [],
      hidden_models: [...modelsHiddenOnServer],
    }));
    return useModelShelfStore();
  }

  it("leaves the row list, the offered rows and the tray, and comes back on request", async () => {
    const store = shelf();
    await store.fetchRows();
    await store.loadWorkflowSets();
    const row = () => store.rows.find((r) => r.id === 2);

    await store.toggleHiddenModels([row()]);

    expect(setModelsHidden).toHaveBeenCalledWith([2], true);
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);
    expect(store.offeredRows.map((r) => r.id)).toEqual([1]);
    // The card stays (its head is shown), the tray loses the member, and the
    // owner's verdict on the whole set is still the card's.
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
    expect(store.setGroups[0].models.map((m) => m.id)).toEqual([1]);
    expect(store.setGroups[0].card.verdict).toBe("yes");
    // Nothing was removed: the row is still known, only not listed.
    expect(store.rows.map((r) => r.id).sort()).toEqual([1, 2]);
    expect(store.activeCount).toBe(0);

    store.setFilters({ showHidden: true });
    expect(store.activeCount).toBe(1);
    expect(store.visibleRows.map((r) => [r.id, r.hidden])).toEqual([
      [2, true],
      [1, false],
    ]);
    expect(store.setGroups[0].models.map((m) => [m.id, m.hidden])).toEqual([
      [1, undefined],
      [2, true],
    ]);

    // Every one selected is hidden, so the same verb shows it again.
    await store.toggleHiddenModels([row()]);
    expect(setModelsHidden).toHaveBeenLastCalledWith([2], false);
    store.setFilters({ showHidden: false });
    expect(store.visibleRows.map((r) => r.id).sort()).toEqual([1, 2]);
  });

  it("takes the card with it when the hidden model is the one the set is named after", async () => {
    const store = shelf();
    await store.fetchRows();
    await store.loadWorkflowSets();

    await store.toggleHiddenModels([store.rows.find((r) => r.id === 1)]);

    expect(store.setGroups).toHaveLength(0);
    // Not one of the hidden SETS: "Show them" under the grid is not its way back.
    expect(store.hiddenSetCount).toBe(0);
    store.setFilters({ showHidden: true });
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:1"]);
  });

  it("hides the selected models that are shown, and leaves a hidden one hidden", async () => {
    modelsHiddenOnServer.add(2);
    const store = shelf();
    store.setFilters({ showHidden: true });
    await store.fetchRows();

    await store.toggleHiddenModels(store.rows);

    expect(setModelsHidden).toHaveBeenCalledWith([1], true);
    expect([...store.hiddenModelIds].sort()).toEqual([1, 2]);
  });

  it("reads the hide off the rows, and off the set payload for a row not read yet", async () => {
    modelsHiddenOnServer.add(2);
    const store = shelf();
    await store.fetchRows();
    expect(store.visibleRows.map((r) => r.id)).toEqual([1]);

    // The set payload names a model no fetched block holds: the grid must still
    // leave it out of a tray.
    modelsHiddenOnServer.add(9);
    await store.loadWorkflowSets();
    expect([...store.hiddenModelIds].sort()).toEqual([2, 9]);

    // And a later read that no longer flags it shows it again.
    modelsHiddenOnServer.delete(2);
    await store.fetchRows();
    expect(store.visibleRows.map((r) => r.id).sort()).toEqual([1, 2]);
  });

  it("keeps a hide that landed while a read was out", async () => {
    const store = shelf();
    await store.fetchRows();
    let release;
    listAdapters.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          // Served before the hide: the row says it is shown.
          release = () => resolve([adapter({ id: 2, hidden: false })]);
        }),
    );
    const read = store.fetchRows();
    await store.toggleHiddenModels([store.rows.find((r) => r.id === 2)]);
    release();
    await read;

    expect([...store.hiddenModelIds]).toEqual([2]);
  });

  it("says so and changes nothing when the write fails", async () => {
    const store = shelf();
    await store.fetchRows();
    setModelsHidden.mockRejectedValueOnce(new Error("offline"));
    useOperationStore().setLocalReceiptHost(true);

    await store.toggleHiddenModels([store.rows.find((r) => r.id === 2)]);

    expect(store.visibleRows.map((r) => r.id).sort()).toEqual([1, 2]);
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
    expect(useOperationStore().receipt).toBe(null);
  });

  it("raises a receipt whose Undo shows the model again", async () => {
    const store = shelf();
    await store.fetchRows();
    useOperationStore().setLocalReceiptHost(true);

    await store.toggleHiddenModels([store.rows.find((r) => r.id === 2)]);
    expect(useOperationStore().receipt.summary).toBe(
      'Hid "Style" in this library. No file was touched',
    );

    await useOperationStore().takeLocalReceiptAction();
    await settle();
    expect(setModelsHidden).toHaveBeenLastCalledWith([2], false);
    expect(store.visibleRows.map((r) => r.id).sort()).toEqual([1, 2]);
  });

  it("drops a hidden model from the selection, so it does not come back lit", async () => {
    const store = shelf();
    store.setView({ groupBy: "none" });
    await store.fetchRows();
    store.toggleSelected(1);
    store.toggleSelected(2);

    await store.toggleHiddenModels([store.rows.find((r) => r.id === 2)]);

    expect([...store.selectedIds]).toEqual([1]);
  });

  it("draws no hidden model among the ones removed from a set", async () => {
    const store = shelf({
      handMade: [
        {
          id: 7,
          name: "seven",
          declined: ["b".repeat(64)],
          members: [
            {
              ...checkpoint,
              sha256: "a".repeat(64),
              slot: "checkpoint",
              on_shelf: true,
            },
          ],
          removed: [{ id: 2, name: "style.st", slot: "lora", sha256: "b".repeat(64) }],
        },
      ],
    });
    await store.fetchRows();
    await store.loadWorkflowSets();
    expect(store.handMadeSets[0].removed.map((m) => m.id)).toEqual([2]);

    await store.toggleHiddenModels([store.rows.find((r) => r.id === 2)]);

    expect(store.handMadeSets[0].removed).toEqual([]);
  });
});
