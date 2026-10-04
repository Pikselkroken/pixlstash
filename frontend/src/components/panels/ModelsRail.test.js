// The Models screen's rail (design B): Models | Tasks in place of the stats.
//
// The lines worth holding: the rail lists every model a slot takes, with the
// count on its tab; its selection is its own, so a click there never reaches
// the shelf's (and with it Delete, Move and Forget); two quick presses on Add
// file one model; a run of adds is one receipt whose Undo takes all of it
// back; and a drop on a hand-made card files the dragged rows.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  const VMenu = { name: "VMenu", template: "<div><slot /></div>" };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

vi.mock("vue-router", () => ({ useRoute: () => ({ name: "models" }) }));

const fetchWorkflowSets = vi.fn();
const addWorkflowSetMembers = vi.fn();
const removeWorkflowSetMembers = vi.fn();
const listAdapters = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) =>
    args[0]?.fileKind ? Promise.resolve([]) : listAdapters(...args),
  listCheckpoints: vi.fn().mockResolvedValue([]),
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
  editModels: vi.fn(),
  forgetModels: vi.fn(),
  deleteModels: vi.fn(),
  setAdapterAttachments: vi.fn(),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
  createWorkflowSet: vi.fn(),
  deleteWorkflowSet: vi.fn(),
  addWorkflowSetMembers: (...args) => addWorkflowSetMembers(...args),
  removeWorkflowSetMembers: (...args) => removeWorkflowSetMembers(...args),
  renameWorkflowSet: vi.fn(),
  setWorkflowSetDeclines: vi.fn(),
}));

vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelsRail from "./ModelsRail.vue";
import ModelSetGrid from "../views/ModelSetGrid.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

const stubs = {
  "v-icon": true,
  TasksPanel: true,
  Tooltip: { template: "<span><slot /></span>" },
};

const shaOf = (id) => String(id).repeat(64).slice(0, 64);

function row(id, filename, fileKind = "adapter", extra = {}) {
  return {
    id,
    sha256: shaOf(id),
    file_kind: fileKind,
    kind: fileKind === "adapter" ? "lora" : null,
    display_name: null,
    filename,
    base_model: null,
    locations: [
      { state: "present", folder_id: 1, folder_path: "/m", relpath: filename },
    ],
    attachments: [],
    ...extra,
  };
}

function member(id, name, slot) {
  return {
    sha256: shaOf(id),
    slot,
    label: name,
    on_shelf: true,
    id,
    name,
    filename: name,
    kind: slot === "checkpoint" ? "checkpoint" : "adapter",
    base_model: null,
  };
}

function handSet(id, members, extra = {}) {
  return {
    id,
    name: null,
    created_at: "2026-09-26T08:00:00Z",
    incomplete: !members.some((m) => m.slot === "checkpoint"),
    picture_count: 0,
    recipes: 0,
    covers: [],
    members,
    ...extra,
  };
}

// Mixed case on purpose: a lookup that folds case wrongly passes on an
// all-lowercase fixture.
const ROWS = [
  row(1, "RealVisXL_v5", "checkpoint"),
  row(3, "FilmGrain_XL"),
  row(5, "Soft_Light"),
  row(7, "engine_x", "engine"),
];

async function mountRail({ handMade = [], rows = ROWS, open = "" } = {}) {
  listAdapters.mockResolvedValue(rows);
  fetchWorkflowSets.mockResolvedValue({
    combinations: [],
    no_set: [],
    hand_made: handMade,
  });
  const store = useModelShelfStore();
  store.setView({ groupBy: "workflow_set" });
  await store.fetchRows();
  await store.loadWorkflowSets();
  if (open) store.toggleSet(open);
  useSidebarStore().setModelsRailOpen(true);
  const wrapper = mount(ModelsRail, { global: { stubs } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

const option = (wrapper, name) =>
  wrapper.findAll('[role="option"]').find((o) => o.text().includes(name));

beforeEach(() => {
  setActivePinia(createPinia());
  useOperationStore().setLocalReceiptHost(true);
  window.localStorage.clear();
  addWorkflowSetMembers.mockReset();
  removeWorkflowSetMembers.mockReset();
  listAdapters.mockReset().mockResolvedValue([]);
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [], hand_made: [] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("what the rail lists", () => {
  it("lists every model a slot takes, counted on its tab, and no engine", async () => {
    const { wrapper } = await mountRail();
    const names = wrapper.findAll('[role="option"]').map((o) => o.text());
    expect(names).toHaveLength(3);
    expect(names.join()).not.toMatch(/engine/i);
    const modelsTab = wrapper.findAll(".inspector-tab")[0];
    expect(modelsTab.text()).toContain("Models");
    expect(modelsTab.text()).toContain("3");
  });

  it("says where each model already is", async () => {
    const { wrapper } = await mountRail({
      handMade: [
        handSet(10, [member(3, "FilmGrain_XL", "lora")], { name: "Night city" }),
      ],
    });
    expect(option(wrapper, "FilmGrain").text()).toContain("In Night city");
    expect(option(wrapper, "Soft").text()).toContain("In no set yet");
  });

  it("narrows to the models in no set when asked, and only then", async () => {
    const { wrapper } = await mountRail({
      handMade: [handSet(10, [member(3, "FilmGrain_XL", "lora")])],
    });
    expect(option(wrapper, "FilmGrain")).toBeTruthy();
    await wrapper.find('[data-testid="mrail-loose"]').trigger("click");
    expect(option(wrapper, "FilmGrain")).toBeFalsy();
    expect(option(wrapper, "Soft")).toBeTruthy();
  });

  it("never turns Fits on because a set was opened", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
      open: "hand:10",
    });
    expect(store.railFitsSetId).toBe(null);
    const fits = wrapper.find('[data-testid="mrail-fits"]');
    expect(fits.attributes("aria-pressed")).toBe("false");
    await fits.trigger("click");
    expect(store.railFitsSetId).toBe(10);
    // The set's own checkpoint is held, so Fits leaves it out.
    expect(option(wrapper, "RealVis")).toBeFalsy();
  });
});

describe("the rail's own selection", () => {
  it("selects in the rail and never on the shelf", async () => {
    const { wrapper, store } = await mountRail();
    await option(wrapper, "Soft").trigger("click");
    expect(option(wrapper, "Soft").attributes("aria-selected")).toBe("true");
    expect(store.selectedIds.size).toBe(0);
  });
});

describe("adding", () => {
  const set = () => handSet(10, [member(1, "RealVisXL_v5", "checkpoint")]);

  it("files one model for two quick presses", async () => {
    let settle;
    addWorkflowSetMembers.mockImplementation(
      (id, members) =>
        new Promise((resolve) => {
          settle = () =>
            resolve({
              set: set(),
              added: members.map((m) => shaOf(m.model_id)),
            });
        }),
    );
    const { wrapper } = await mountRail({ handMade: [set()], open: "hand:10" });
    const add = option(wrapper, "Soft").find('[data-testid="mrail-add"]');
    await add.trigger("click");
    await add.trigger("click");
    expect(addWorkflowSetMembers).toHaveBeenCalledTimes(1);
    expect(addWorkflowSetMembers.mock.calls[0]).toEqual([
      10,
      [{ model_id: 5, slot: "lora" }],
    ]);
    settle();
  });

  it("gives a run of adds one receipt, and its Undo takes all of it back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let held = [member(1, "RealVisXL_v5", "checkpoint")];
    addWorkflowSetMembers.mockImplementation((id, members) => {
      held = [
        ...held,
        ...members.map((m) =>
          member(m.model_id, `m${m.model_id}`, m.slot),
        ),
      ];
      return Promise.resolve({
        set: handSet(10, held),
        added: members.map((m) => shaOf(m.model_id)),
      });
    });
    const { wrapper } = await mountRail({ handMade: [set()], open: "hand:10" });
    // Each write refetches: serve the set as it now is.
    fetchWorkflowSets.mockImplementation(() =>
      Promise.resolve({
        combinations: [],
        no_set: [],
        hand_made: [handSet(10, held)],
      }),
    );
    const operations = useOperationStore();
    await option(wrapper, "Soft").find('[data-testid="mrail-add"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    await option(wrapper, "FilmGrain")
      .find('[data-testid="mrail-add"]')
      .trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    // Quiet while the run goes on.
    expect(operations.receipt).toBe(null);
    await vi.advanceTimersByTimeAsync(2000);
    expect(operations.receipt?.summary).toMatch(
      /^Added 2 LoRAs to "RealVisXL v5"/,
    );
    // Added rows stay where they were, with a check.
    expect(
      option(wrapper, "Soft").find('[data-testid="mrail-in-set"]').exists(),
    ).toBe(true);
    // Ending the run after its receipt is up raises no second one.
    const shown = operations.receipt;
    useModelShelfStore().toggleSet("hand:10");
    await vi.advanceTimersByTimeAsync(10);
    expect(operations.receipt).toBe(shown);
    removeWorkflowSetMembers.mockResolvedValue({ removed: [] });
    await operations.takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledTimes(1);
    expect(new Set(removeWorkflowSetMembers.mock.calls[0][1])).toEqual(
      new Set([shaOf(5), shaOf(3)]),
    );
  });

  it("offers a checkpoint into a set that has one as a second, by name", async () => {
    const { wrapper } = await mountRail({
      rows: [...ROWS, row(9, "Juggernaut_XL", "checkpoint")],
      handMade: [set()],
      open: "hand:10",
    });
    const juggernaut = option(wrapper, "Juggernaut");
    expect(juggernaut.find('[data-testid="mrail-add"]').text()).toBe(
      "Add as 2nd",
    );
    expect(juggernaut.text()).toContain("This set already has");
  });

  it("lists a model still being hashed but will not add or drag it", async () => {
    const { wrapper } = await mountRail({
      rows: [...ROWS, row(11, "Anime_Detail", "adapter", { sha256: null })],
      handMade: [set()],
      open: "hand:10",
    });
    const hashing = option(wrapper, "Anime");
    expect(hashing.text()).toContain("Still hashing");
    const add = hashing.find('[data-testid="mrail-add"]');
    expect(add.text()).toBe("Hashing…");
    expect(add.attributes("disabled")).toBeDefined();
    expect(hashing.attributes("draggable")).toBe("false");
  });
});

describe("dropping on the set grid", () => {
  it("files the dragged rows into a hand-made card, each in its kind's slot", async () => {
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, []),
      added: [shaOf(5)],
    });
    listAdapters.mockResolvedValue(ROWS);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, [])],
    });
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    const grid = mount(ModelSetGrid, { global: { stubs } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();

    store.railDrag = [store.rows.find((r) => r.id === 5)];
    await grid.vm.$nextTick();
    const card = grid.find('[data-key="hand:10"]');
    const dataTransfer = { dropEffect: "" };
    await card.trigger("dragover", { dataTransfer });
    expect(dataTransfer.dropEffect).toBe("copy");
    await card.trigger("drop", { dataTransfer });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ model_id: 5, slot: "lora" }]],
    ]);
    expect(store.railDrag).toBe(null);
  });

  it("never takes a drop on an evidence card, and dims it for the drag", async () => {
    const ckpt = { id: 1, name: "RealVisXL_v5", kind: "checkpoint" };
    const lora = { id: 3, name: "FilmGrain_XL", kind: "adapter" };
    listAdapters.mockResolvedValue(ROWS);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [
        {
          key: "1+3",
          models: [ckpt, lora],
          recipes: 1,
          history_runs: 0,
          picture_count: 1,
          covers: [],
        },
      ],
      no_set: [],
      hand_made: [],
    });
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    const grid = mount(ModelSetGrid, { global: { stubs } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    store.railDrag = [store.rows.find((r) => r.id === 5)];
    await grid.vm.$nextTick();
    const card = grid.find(".msg__row");
    expect(card.classes()).toContain("msg__row--dim");
    const dataTransfer = { dropEffect: "" };
    await card.trigger("dragover", { dataTransfer });
    expect(dataTransfer.dropEffect).toBe("");
    await card.trigger("drop", { dataTransfer });
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
  });

  it("refuses a drop on a set of another base model, as the menu does", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      row(5, "Soft_Light", "adapter", { base_model: "SD 1.5" }),
    ]);
    await store.fetchRows();
    const sdxl = handSet(10, [
      { ...member(1, "RealVisXL_v5", "checkpoint"), base_model: "SDXL" },
    ]);
    await store.addRowsToHandMadeSet(sdxl, store.rows);
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
  });
});
