// The Models screen's rail (design B): Models | Tasks in place of the stats.
//
// The lines worth holding: the rail lists every model a slot takes, with the
// count on its tab; its selection is its own list and the one the shelf's
// verbs act on, reached from its menu under its own set verbs; two quick
// presses on Add file one model; a run of adds is one receipt whose Undo takes all of it
// back; and a pointer drag onto a hand-made card files the dragged rows.

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
const listCheckpoints = vi.fn();
const setModelsHidden = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) =>
    args[0]?.fileKind ? Promise.resolve([]) : listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
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
  setModelsHidden: (...args) => setModelsHidden(...args),
}));

vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelsRail from "./ModelsRail.vue";
import ModelSetGrid from "../views/ModelSetGrid.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { formatUserDate } from "../../utils/utils";

const stubs = {
  "v-icon": true,
  // The rail's mount of the shelf's bar resolves `v-menu` by name and draws
  // none; its Assign pickers read the entity lists as they mount.
  "v-menu": { template: "<div><slot /></div>" },
  AddToEntityControl: true,
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

async function mountRail({
  handMade = [],
  hidden = [],
  rows = ROWS,
  open = "",
  attach = false,
} = {}) {
  listAdapters.mockResolvedValue(rows);
  fetchWorkflowSets.mockResolvedValue({
    combinations: [],
    no_set: [],
    hand_made: handMade,
    hidden_sets: hidden,
  });
  const store = useModelShelfStore();
  store.setView({ groupBy: "workflow_set" });
  await store.fetchRows();
  await store.loadWorkflowSets();
  if (open) store.toggleSet(open);
  useSidebarStore().setModelsRailOpen(true);
  const wrapper = mount(ModelsRail, {
    global: { stubs },
    attachTo: attach ? document.body : undefined,
  });
  mounted.push(wrapper);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

/** Every mount, unmounted after its test: a live drag holds window listeners. */
const mounted = [];

function mountGrid() {
  const grid = mount(ModelSetGrid, { global: { stubs }, attachTo: document.body });
  mounted.push(grid);
  return grid;
}

/** A pointer event as the rail reads it; jsdom has no PointerEvent. */
function pointer(type, target, x, y, extra = {}) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    clientX: x,
    clientY: y,
    ...extra,
  });
  target.dispatchEvent(event);
  return event;
}

/** What is "under the pointer": jsdom has no layout, so the test says. */
function pointAt(element) {
  document.elementFromPoint = () => element;
}

/** Press a row and move far enough for the press to become a drag. */
async function startDrag(wrapper, name) {
  pointer("pointerdown", option(wrapper, name).element, 10, 10);
  pointer("pointermove", window, 40, 40);
  await wrapper.vm.$nextTick();
}

const ghost = () =>
  [...document.querySelectorAll('[data-testid="mrail-ghost"]')].at(-1);

const option = (wrapper, name) =>
  wrapper.findAll('[role="option"]').find((o) => o.text().includes(name));

beforeEach(() => {
  setActivePinia(createPinia());
  useOperationStore().setLocalReceiptHost(true);
  window.localStorage.clear();
  addWorkflowSetMembers.mockReset();
  removeWorkflowSetMembers.mockReset();
  setModelsHidden.mockReset();
  listAdapters.mockReset().mockResolvedValue([]);
  listCheckpoints.mockReset().mockResolvedValue([]);
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [], hand_made: [] });
});

afterEach(() => {
  vi.useRealTimers();
  mounted.splice(0).forEach((wrapper) => wrapper.unmount());
  delete document.elementFromPoint;
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

  it("lists a stack as its leader alone, and hides it whole", async () => {
    setModelsHidden.mockImplementation(async (ids, hidden) => (hidden ? ids : []));
    // The leader comes last and has the highest id: only its position names it.
    const { wrapper } = await mountRail({
      rows: [
        ...ROWS,
        row(21, "Portrait-000004", "adapter", { stack_id: 9, stack_position: 2 }),
        row(22, "Portrait-000008", "adapter", { stack_id: 9, stack_position: 1 }),
        row(23, "Portrait", "adapter", { stack_id: 9, stack_position: 0 }),
      ],
    });
    const stacked = () =>
      wrapper.findAll('[role="option"]').filter((o) => o.text().includes("Portrait"));
    expect(stacked().map((o) => o.attributes("data-model-id"))).toEqual(["23"]);
    expect(wrapper.findAll(".inspector-tab")[0].text()).toContain("4");

    // A member's own filename finds the stack, still as its leader.
    const search = wrapper.find('[data-testid="mrail-search"] input');
    await search.setValue("Portrait-000004");
    expect(
      wrapper.findAll('[role="option"]').map((o) => o.attributes("data-model-id")),
    ).toEqual(["23"]);
    await search.setValue("");

    // The leader alone would only hand its place to the next member.
    await stacked()[0].trigger("contextmenu");
    const hideRow = wrapper.find('.mrail-menu [data-testid="mrail-hide"]');
    expect(hideRow.text()).toContain("Hide 3 models");
    await hideRow.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setModelsHidden).toHaveBeenCalledWith([23, 22, 21], true);
    expect(stacked()).toHaveLength(0);
  });

  it("orders newest first by file date, one heading per download, and remembers it", async () => {
    const HOUR_NS = 3600 * 1e9;
    const at = (hours) => ({ newest_file_mtime: 1.8e18 + hours * HOUR_NS });
    const { wrapper, store } = await mountRail({
      rows: [
        row(1, "RealVisXL_v5", "checkpoint", at(0)),
        row(3, "FilmGrain_XL", "adapter", at(-5)),
        row(5, "Soft_Light", "adapter", at(1)),
      ],
    });
    const order = wrapper.find('[data-testid="mrail-order"]');
    await order.findAll('[role="radio"]')[1].trigger("click");
    const ids = () =>
      wrapper.findAll('[role="option"]').map((o) => o.attributes("data-model-id"));
    // The checkpoint and the LoRA an hour after it are one download.
    expect(ids()).toEqual(["5", "1", "3"]);
    const headings = wrapper.findAll(".mrail-group");
    expect(headings).toHaveLength(2);
    // Named by the batch's first row's date and time, with its count.
    const first = new Date((1.8e18 + HOUR_NS) / 1e6).toISOString();
    expect(headings[0].text()).toBe(
      `${formatUserDate(first, useUserPrefsStore().dateFormat)} 2`,
    );
    expect(store.pickOrder).toBe("newest");
    expect(JSON.parse(window.localStorage.getItem("pixlstash:modelPickOrder"))).toEqual({
      order: "newest",
    });

    await order.findAll('[role="radio"]')[0].trigger("click");
    expect(ids()).toEqual(["1", "3", "5"]);
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

  it("names only the sets shown here, so a set hidden in this library is not named", async () => {
    const { wrapper } = await mountRail({
      handMade: [
        handSet(10, [member(3, "FilmGrain_XL", "lora")], { name: "Night city" }),
      ],
      hidden: ["hand:10"],
    });
    expect(option(wrapper, "FilmGrain").text()).toContain("In no set yet");
    expect(option(wrapper, "FilmGrain").text()).not.toContain("Night city");
  });

  it("offers only the shown sets when a model is added to one", async () => {
    const { wrapper } = await mountRail({
      handMade: [
        handSet(10, [member(1, "RealVisXL_v5", "checkpoint")], { name: "Night city" }),
        handSet(11, [member(1, "RealVisXL_v5", "checkpoint")], { name: "Day shift" }),
      ],
      hidden: ["hand:10"],
    });
    await option(wrapper, "Soft").trigger("contextmenu");
    await wrapper
      .findAll(".mrail-menu .ctx-item")
      .find((b) => b.text().includes("Add to set"))
      .trigger("click");
    const menu = wrapper.findAll(".mrail-menu .ctx-item").map((b) => b.text());
    expect(menu.join()).toContain("Day shift");
    expect(menu.join()).not.toContain("Night city");
  });

  it("turns Fits off when its set is hidden here, and says why", async () => {
    const night = handSet(10, [member(1, "RealVisXL_v5", "checkpoint")], {
      name: "Night city",
    });
    const { wrapper, store } = await mountRail({ handMade: [night] });
    store.railFitsSetId = 10;
    await wrapper.vm.$nextTick();
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [night],
      hidden_sets: ["hand:10"],
    });
    await store.loadWorkflowSets({ force: true });
    await wrapper.vm.$nextTick();
    expect(store.railFitsSetId).toBe(null);
    expect(wrapper.find('[role="status"]').text()).toContain(
      "Night city is hidden in this library",
    );
  });

  it("leaves out a model this library hides, until Show asks for the hidden ones", async () => {
    const { wrapper, store } = await mountRail({
      rows: ROWS.map((r) => (r.id === 5 ? { ...r, hidden: true } : r)),
    });
    const names = () =>
      wrapper.findAll('[role="option"]').map((o) => o.text()).join();
    expect(names()).not.toContain("Soft");
    expect(wrapper.findAll(".inspector-tab")[0].text()).toContain("2");

    store.setFilters({ showHidden: true });
    await wrapper.vm.$nextTick();
    // Drawn again, and saying why it was not there.
    expect(option(wrapper, "Soft").text()).toContain("Hidden");
    expect(option(wrapper, "FilmGrain").text()).not.toContain("Hidden");
  });

  it("hides the model under the menu, and shows a hidden one again from it", async () => {
    setModelsHidden.mockImplementation(async (ids, hidden) => (hidden ? ids : []));
    const { wrapper, store } = await mountRail();
    const hideRow = () =>
      wrapper.find('.mrail-menu [data-testid="mrail-hide"]');

    await option(wrapper, "Soft").trigger("contextmenu");
    expect(hideRow().text()).toContain("Hide model");
    await hideRow().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setModelsHidden).toHaveBeenCalledWith([5], true);
    expect(wrapper.findAll('[role="option"]')).toHaveLength(2);

    store.setFilters({ showHidden: true });
    await wrapper.vm.$nextTick();
    // It left the selection when it left the list, so it does not come back lit.
    expect(option(wrapper, "Soft").classes()).not.toContain("mrail-row--sel");
    await option(wrapper, "Soft").trigger("contextmenu");
    expect(hideRow().text()).toContain("Show model again");
    await hideRow().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setModelsHidden).toHaveBeenLastCalledWith([5], false);
  });

  it("keeps the selection when the hide does not land", async () => {
    setModelsHidden.mockRejectedValue(new Error("offline"));
    const { wrapper } = await mountRail();
    await option(wrapper, "Soft").trigger("contextmenu");
    await wrapper.find('.mrail-menu [data-testid="mrail-hide"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Still listed, so still selected: nothing happened to act on.
    expect(option(wrapper, "Soft").classes()).toContain("mrail-row--sel");
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

  it("counts a stack once on the set panel's Models fit, as the rail lists it", async () => {
    const { wrapper } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
      open: "hand:10",
      rows: [
        ...ROWS,
        row(21, "Portrait-000004", "adapter", { stack_id: 9, stack_position: 1 }),
        row(23, "Portrait", "adapter", { stack_id: 9, stack_position: 0 }),
      ],
    });
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    await wrapper.find('[data-testid="mrail-fits"]').trigger("click");
    const listedCount = wrapper.findAll('[role="option"]').length;
    expect(listedCount).toBe(3);
    expect(grid.find('[data-testid="models-fit"]').text()).toContain(
      `Models · ${listedCount} fit`,
    );
  });
});

describe("what it knows before it says it", () => {
  it("lists a kind Show has unticked: the rail is every model, not the shown ones", async () => {
    // Checkpoints come from their own route, which Show's unticked box never
    // asks; only the rail does.
    listCheckpoints.mockResolvedValue([ROWS[0]]);
    const store = useModelShelfStore();
    await store.setFilters({ checkpoints: false });
    const { wrapper } = await mountRail({ rows: ROWS.slice(1) });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(option(wrapper, "RealVis")).toBeTruthy();
    // And the shelf's own shown rows still leave it out.
    expect(store.visibleRows.some((r) => r.id === 1)).toBe(false);
  });

  it("keeps an unticked kind fresh: a scan's new checkpoint reaches the rail", async () => {
    listCheckpoints.mockResolvedValue([ROWS[0]]);
    const store = useModelShelfStore();
    await store.setFilters({ checkpoints: false });
    const { wrapper } = await mountRail({ rows: ROWS.slice(1) });
    await new Promise((resolve) => setTimeout(resolve, 0));

    // A scan refetches the shelf; Show still leaves checkpoints unticked.
    listCheckpoints.mockResolvedValue([ROWS[0], row(9, "Juggernaut_XL", "checkpoint")]);
    await store.fetchRows({ markNew: true });
    await wrapper.vm.$nextTick();
    expect(option(wrapper, "Juggernaut")).toBeTruthy();

    // Unmounted, the rail stops costing the shelf an unticked block.
    wrapper.unmount();
    mounted.splice(mounted.indexOf(wrapper), 1);
    listCheckpoints.mockClear();
    await store.fetchRows();
    expect(listCheckpoints).not.toHaveBeenCalled();
  });

  it("says nothing about where a model is until the sets are read", async () => {
    listAdapters.mockResolvedValue(ROWS);
    fetchWorkflowSets.mockReturnValue(new Promise(() => {}));
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    useSidebarStore().setModelsRailOpen(true);
    const wrapper = mount(ModelsRail, { global: { stubs } });
    mounted.push(wrapper);
    await wrapper.vm.$nextTick();
    expect(option(wrapper, "Soft").text()).not.toContain("In no set yet");
    expect(wrapper.text()).toContain("Reading your sets");
    expect(
      wrapper.find('[data-testid="mrail-loose"]').attributes("disabled"),
    ).toBeDefined();
  });

  it("offers a retry when the sets cannot be read, rather than calling them empty", async () => {
    listAdapters.mockResolvedValue(ROWS);
    fetchWorkflowSets.mockRejectedValue(new Error("boom"));
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    useSidebarStore().setModelsRailOpen(true);
    const wrapper = mount(ModelsRail, { global: { stubs } });
    mounted.push(wrapper);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(option(wrapper, "Soft").text()).not.toContain("In no set yet");
    const error = wrapper.find('[data-testid="mrail-sets-error"]');
    expect(error.exists()).toBe(true);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [],
    });
    await error.find("button").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(option(wrapper, "Soft").text()).toContain("In no set yet");
  });
});

describe("the rail's own selection", () => {
  it("selects in the rail and never on the shelf", async () => {
    const { wrapper, store } = await mountRail();
    await option(wrapper, "Soft").trigger("click");
    expect(option(wrapper, "Soft").attributes("aria-selected")).toBe("true");
    expect(store.selectedIds.size).toBe(0);
  });

  it("is the one selection on screen: it drops the shelf's, and a shelf pick drops it", async () => {
    const { wrapper, store } = await mountRail();
    store.toggleSelected(3);
    await option(wrapper, "Soft").trigger("click");
    expect(store.selectedIds.size).toBe(0);
    expect(store.selectedRows.map((r) => r.id)).toEqual([5]);

    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    expect(option(wrapper, "Soft").attributes("aria-selected")).toBe("false");
    expect(store.railSelection).toBeNull();
    expect([...store.selectedIds]).toEqual([3]);
  });

  it("lets go of its selection when the list leaves the screen", async () => {
    // Delete pressed on the shelf must not act on rows behind the Tasks tab.
    const { wrapper, store } = await mountRail();
    await option(wrapper, "Soft").trigger("click");
    expect(store.selectedRows).toHaveLength(1);
    useSidebarStore().showTasksTab();
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(0);
  });
});

describe("a set card's double-click", () => {
  it("brings a shut rail back on its Models tab", async () => {
    const { wrapper } = await mountRail();
    const sidebar = useSidebarStore();
    sidebar.showTasksTab();
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);

    sidebar.modelsRailOpen = false;
    sidebar.showCardInInspector("models");
    await wrapper.vm.$nextTick();
    expect(sidebar.modelsRailOpen).toBe(true);
    expect(wrapper.find('[role="listbox"]').exists()).toBe(true);
  });
});

describe("the shelf's verbs, from the rail", () => {
  const menuItems = (wrapper) => wrapper.findAll(".mrail-menu .ctx-item");
  const menuItem = (wrapper, label) =>
    menuItems(wrapper).find((b) => b.text().includes(label));

  it("lists them under the rail's own, which lead and are marked", async () => {
    const { wrapper } = await mountRail({
      handMade: [handSet(10, [], { name: "Night city" })],
    });
    await option(wrapper, "Soft").trigger("contextmenu");
    const items = menuItems(wrapper);
    const labels = items.map((b) => b.text());
    expect(labels.slice(0, 2).join()).toContain("Add to set");
    expect(labels[1]).toContain("New workflow set with this");
    expect(items.filter((b) => b.classes("ctx-item--key"))).toHaveLength(2);
    // What the shelf's row menu does to a model, and was out of reach here.
    for (const label of [
      "Rename",
      "Set thumbnail",
      "Set base model",
      "Set kind",
      "Set trigger words",
      "Move to…",
      "Keep one copy",
      "Open in file manager",
      "Works with",
      "Copy filename",
      "Copy SHA-256",
      "Remove from shelf",
    ]) {
      expect(menuItem(wrapper, label), label).toBeDefined();
    }
    expect(wrapper.find(".mrail-menu .ctx-item--danger").exists()).toBe(true);
    // Never twice: the rail's own New workflow set and Hide stand for the
    // shelf's.
    expect(labels.filter((l) => l.includes("New workflow set"))).toHaveLength(1);
    expect(labels.filter((l) => l.includes("Hide"))).toHaveLength(1);
    expect(wrapper.find('[data-testid="mrail-hide"]').exists()).toBe(true);
  });

  it("asks the shelf to run the one picked, on the rail's selection", async () => {
    const { wrapper, store } = await mountRail();
    await option(wrapper, "Soft").trigger("click");
    await option(wrapper, "FilmGrain").trigger("click", { ctrlKey: true });
    await option(wrapper, "Soft").trigger("contextmenu");
    expect(wrapper.vm.menuOpen).toBe(true);
    await menuItem(wrapper, "Set base model").trigger("click");
    expect(store.verbRequest).toEqual({ verb: "set-base-model", args: [] });
    expect(store.selectedRows.map((r) => r.id).sort()).toEqual([3, 5]);
    // The menu stays open on a click, for its two pickers, so a verb has to
    // close it: left up, it would sit over the dialog the shelf opens.
    expect(wrapper.vm.menuOpen).toBe(false);
  });

  it("right-clicks a row outside the selection onto that row alone", async () => {
    const { wrapper, store } = await mountRail();
    await option(wrapper, "Soft").trigger("click");
    await option(wrapper, "FilmGrain").trigger("contextmenu");
    expect(store.selectedRows.map((r) => r.id)).toEqual([3]);
  });

  it("makes the cursor's row the selection when the footer's menu opens on none", async () => {
    // A verb with nothing selected would have nothing to confirm against.
    const { wrapper, store } = await mountRail();
    await wrapper.find('[data-testid="mrail-list"]').trigger("focus");
    expect(store.selectedRows).toHaveLength(0);
    await wrapper.find('[data-testid="mrail-more"]').trigger("click");
    expect(store.selectedRows).toHaveLength(1);
  });

  it("answers Delete and F2 as the shelf does, and only with a selection", async () => {
    const { wrapper, store } = await mountRail();
    const list = wrapper.find('[data-testid="mrail-list"]');
    await list.trigger("keydown", { key: "Delete" });
    expect(store.verbRequest).toBeNull();

    await option(wrapper, "Soft").trigger("click");
    await list.trigger("keydown", { key: "Delete", shiftKey: true });
    expect(store.verbRequest).toEqual({ verb: "delete", args: [true] });
    await list.trigger("keydown", { key: "F2" });
    expect(store.verbRequest).toEqual({ verb: "rename", args: [] });

    // A name is one file's: two selected, and F2 asks for nothing. A marker
    // stands in the request first, so a rename would be seen replacing it.
    await option(wrapper, "FilmGrain").trigger("click", { ctrlKey: true });
    store.requestVerb("none");
    await list.trigger("keydown", { key: "F2" });
    expect(store.verbRequest.verb).toBe("none");
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

  it("drops a run still waiting for its pause when the session changes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const held = [member(1, "RealVisXL_v5", "checkpoint"), member(5, "Soft", "lora")];
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, held),
      added: [shaOf(5)],
    });
    // No set open: a reset leaves `openSetKey` as it was, so nothing else ends
    // the run. Add to set… is how a run starts without one.
    const { wrapper, store } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
    });
    // The next library happens to hold a set with the same id.
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, held)],
    });
    const spy = vi.spyOn(store, "announceAdded");
    await option(wrapper, "Soft").trigger("contextmenu");
    const menuItems = () => wrapper.findAll(".mrail-menu .ctx-item");
    await menuItems()
      .find((b) => b.text().includes("Add to set"))
      .trigger("click");
    await menuItems()
      .find((b) => b.text().includes("RealVisXL"))
      .trigger("click");
    await vi.advanceTimersByTimeAsync(10);
    expect(addWorkflowSetMembers).toHaveBeenCalledTimes(1);

    store.resetForSession();
    await wrapper.vm.$nextTick();
    await store.loadWorkflowSets();
    await vi.advanceTimersByTimeAsync(2000);
    // No receipt, so no Undo that would reach into the other library's set.
    expect(spy).not.toHaveBeenCalled();
  });

  it.each([
    ["raises again for a new add after some of the run is gone", false],
    ["never re-raises what is left of a run when it ends", true],
  ])("%s", async (_, endRunAfterRemoval) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const ckpt = member(1, "RealVisXL_v5", "checkpoint");
    let held = [ckpt];
    addWorkflowSetMembers.mockImplementation((id, members) => {
      held = [
        ...held,
        ...members.map((m) => member(m.model_id, `m${m.model_id}`, m.slot)),
      ];
      return Promise.resolve({
        set: handSet(10, held),
        added: members.map((m) => shaOf(m.model_id)),
      });
    });
    const serve = () =>
      Promise.resolve({
        combinations: [],
        no_set: [],
        hand_made: [handSet(10, held)],
      });
    const { wrapper, store } = await mountRail({
      rows: [...ROWS, row(13, "Rim_Light")],
      handMade: [handSet(10, held)],
      open: "hand:10",
    });
    fetchWorkflowSets.mockImplementation(serve);
    const spy = vi.spyOn(store, "announceAdded");
    const add = async (name) => {
      await option(wrapper, name)
        .find('[data-testid="mrail-add"]')
        .trigger("click");
      await vi.advanceTimersByTimeAsync(10);
    };
    await add("Soft");
    await add("FilmGrain");
    await vi.advanceTimersByTimeAsync(2000);
    expect(spy).toHaveBeenCalledTimes(1);
    // One of the run is taken off the set elsewhere (the tray's ✕).
    held = held.filter((m) => m.id !== 3);
    await store.loadWorkflowSets({ force: true });
    await vi.advanceTimersByTimeAsync(2000);
    if (endRunAfterRemoval) {
      // Ending the run now must not re-announce the one that is left.
      store.toggleSet("hand:10");
      await vi.advanceTimersByTimeAsync(10);
      expect(spy).toHaveBeenCalledTimes(1);
      return;
    }
    // A new add in the same run is announced, with what the set still holds.
    await add("Rim");
    await vi.advanceTimersByTimeAsync(2000);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[1][1].map((m) => m.model_id)).toEqual([5, 13]);
    store.toggleSet("hand:10");
    await vi.advanceTimersByTimeAsync(10);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("reports a failed add as an error notice and frees the row for another try", async () => {
    addWorkflowSetMembers.mockRejectedValue(new Error("boom"));
    const { wrapper } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
      open: "hand:10",
    });
    await option(wrapper, "Soft").find('[data-testid="mrail-add"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(useNoticeStore().notices.some((n) => n.level === "error")).toBe(true);
    const add = option(wrapper, "Soft").find('[data-testid="mrail-add"]');
    expect(add.attributes("disabled")).toBeUndefined();
    expect(useOperationStore().receipt).toBe(null);
  });

  it("covers every add in some Undo when two sets' writes land out of order", async () => {
    const settle = {};
    addWorkflowSetMembers.mockImplementation(
      (id, members) =>
        new Promise((resolve) => {
          settle[id] = () =>
            resolve({
              set: handSet(id, []),
              added: members.map((m) => shaOf(m.model_id)),
            });
        }),
    );
    const held = {
      10: [member(1, "RealVisXL_v5", "checkpoint")],
      11: [member(9, "Other_ckpt", "checkpoint")],
    };
    const { wrapper, store } = await mountRail({
      handMade: [handSet(10, held[10]), handSet(11, held[11])],
      open: "hand:10",
    });
    // Every refetch serves both sets holding everything asked for so far.
    fetchWorkflowSets.mockImplementation(() =>
      Promise.resolve({
        combinations: [],
        no_set: [],
        hand_made: [
          handSet(10, [...held[10], member(5, "Soft_Light", "lora")]),
          handSet(11, [...held[11], member(3, "FilmGrain_XL", "lora")]),
        ],
      }),
    );
    const spy = vi.spyOn(store, "announceAdded");
    // Set 10 through the row's Add, set 11 through Add to set….
    await option(wrapper, "Soft").find('[data-testid="mrail-add"]').trigger("click");
    await option(wrapper, "FilmGrain").trigger("contextmenu");
    const menuItems = () => wrapper.findAll(".mrail-menu .ctx-item");
    await menuItems()
      .find((b) => b.text().includes("Add to set"))
      .trigger("click");
    await menuItems()
      .find((b) => b.text().includes("Other"))
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Set 11 lands first, then set 10.
    settle[11]();
    await new Promise((resolve) => setTimeout(resolve, 0));
    settle[10]();
    await new Promise((resolve) => setTimeout(resolve, 0));
    store.toggleSet("hand:10");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const announced = spy.mock.calls.map(([set, added]) => [
      set.id,
      added.map((m) => m.model_id),
    ]);
    expect(announced).toEqual(
      expect.arrayContaining([
        [11, [3]],
        [10, [5]],
      ]),
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
    // Named to a screen reader, not printed on every checkpoint's second line,
    // which keeps where the row already is and its size.
    expect(juggernaut.attributes("aria-description")).toContain(
      "This set already has",
    );
    expect(juggernaut.find(".mrail-why").text()).not.toContain(
      "This set already has",
    );
  });

  it("lists a model still being hashed but will not add or drag it", async () => {
    const { wrapper } = await mountRail({
      rows: [
        ...ROWS,
        row(11, "Anime_Detail", "adapter", { sha256: null, hash_pending: true }),
      ],
      handMade: [set()],
      open: "hand:10",
    });
    const hashing = option(wrapper, "Anime");
    expect(hashing.text()).toContain("Still hashing");
    const add = hashing.find('[data-testid="mrail-add"]');
    expect(add.text()).toBe("Hashing…");
    expect(add.attributes("disabled")).toBeDefined();
    // Its grip is hidden and a press on it never starts a drag.
    expect(hashing.find(".mrail-grip").classes()).toContain("mrail-grip--off");
    await startDrag(wrapper, "Anime");
    expect(useModelShelfStore().railDrag).toBe(null);
  });

  it("does not offer a model that never gets a hash", async () => {
    // A cached Hugging Face repo is a directory: nothing hashes it, so a set
    // can never keep it and "Still hashing" would be said of it for good.
    const { wrapper } = await mountRail({
      rows: [
        ...ROWS,
        row(11, "Wan-AI/Wan2.1-T2V-14B", "unknown", {
          sha256: null,
          hash_pending: false,
        }),
      ],
      handMade: [set()],
      open: "hand:10",
    });
    expect(wrapper.text()).not.toContain("Wan-AI");
    expect(wrapper.text()).not.toContain("Still hashing");
    // The rest of the list is still there.
    expect(option(wrapper, "Soft").exists()).toBe(true);
  });
});

describe("dragging", () => {
  it("turns a press into a drag only past a few pixels, and carries the row", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
    });
    pointer("pointerdown", option(wrapper, "Soft").element, 10, 10);
    pointer("pointermove", window, 12, 11);
    await wrapper.vm.$nextTick();
    expect(store.railDrag).toBe(null);
    expect(ghost().style.display).toBe("none");

    pointer("pointermove", window, 40, 40);
    await wrapper.vm.$nextTick();
    expect(store.railDrag.map((r) => r.id)).toEqual([5]);
    // The page's own pill, under the pointer, naming what it carries.
    expect(ghost().style.display).not.toBe("none");
    expect(ghost().textContent).toContain("Soft");
    // Held at the pointer (40, 40): just right of it, centred on it.
    expect(ghost().style.transform).toBe("translate(44px, 40px) translateY(-50%)");
    expect(option(wrapper, "Soft").classes()).toContain("mrail-row--lifted");
    expect(wrapper.find('[data-testid="mrail-drag-hint"]').text()).toMatch(
      /^Drop Soft.* on a set with a dashed rim\.$/,
    );

    pointer("pointerup", window, 40, 40);
    await wrapper.vm.$nextTick();
    expect(store.railDrag).toBe(null);
    expect(ghost().style.display).toBe("none");
    expect(option(wrapper, "Soft").classes()).not.toContain("mrail-row--lifted");
    expect(wrapper.find('[data-testid="mrail-drag-hint"]').exists()).toBe(false);
  });

  it("drags the whole selection, and the release's click does not narrow it", async () => {
    // Attached: the swallowed click is caught on its way through `window`.
    const { wrapper, store } = await mountRail({ attach: true });
    await option(wrapper, "Soft").trigger("click");
    await option(wrapper, "Real").trigger("click", { ctrlKey: true });
    await startDrag(wrapper, "Soft");
    expect(store.railDrag.map((r) => r.id).sort()).toEqual([1, 5]);
    expect(ghost().textContent).toContain("2 models");
    pointer("pointerup", window, 40, 40);
    // The click the browser fires after a release on the row it began on.
    await option(wrapper, "Soft").trigger("click");
    expect(wrapper.find(".mrail-foot-count").text()).toBe("2 selected");
  });

  it("leaves a model still being hashed behind, and says so on the pill", async () => {
    const { wrapper, store } = await mountRail({
      rows: [
        ...ROWS,
        row(11, "Anime_Detail", "adapter", { sha256: null, hash_pending: true }),
      ],
    });
    await option(wrapper, "Soft").trigger("click");
    await option(wrapper, "Anime").trigger("click", { ctrlKey: true });
    await startDrag(wrapper, "Soft");
    expect(store.railDrag.map((r) => r.id)).toEqual([5]);
    expect(ghost().textContent).toContain("1 still hashing, left out");
    pointer("pointerup", window, 40, 40);
  });

  it("hands the drop to the target under the pointer, never to one that refuses", async () => {
    const { wrapper } = await mountRail();
    const target = document.createElement("div");
    target.dataset.railDrop = "somewhere";
    document.body.append(target);
    const drops = [];
    target.addEventListener("rail-drop", (event) => drops.push(event));
    pointAt(target);
    await startDrag(wrapper, "Soft");
    pointer("pointerup", window, 40, 40);
    expect(drops).toHaveLength(1);

    target.dataset.railRefused = "Not here";
    await startDrag(wrapper, "Soft");
    pointer("pointerup", window, 40, 40);
    expect(drops).toHaveLength(1);
    target.remove();
  });

  it("puts the models back on Escape, dropping nothing", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(10, [])],
    });
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    pointAt(grid.find('[data-key="hand:10"] .msg__cell').element);
    await startDrag(wrapper, "Soft");
    expect(store.railOver).toBe("hand:10");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(store.railDrag).toBe(null);
    pointer("pointerup", window, 40, 40);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
  });
});

describe("dropping on the set grid", () => {
  it("files the dragged rows into the hand-made card under the pointer", async () => {
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, []),
      added: [shaOf(5)],
    });
    const { wrapper, store } = await mountRail({ handMade: [handSet(10, [])] });
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();

    // Under the pointer: something inside the card, as a real hit would be.
    pointAt(grid.find('[data-key="hand:10"] .msg__cell').element);
    await startDrag(wrapper, "Soft");
    const mark = grid.find('[data-key="hand:10"] [data-testid="rail-drop-mark"]');
    expect(mark.classes()).toContain("msg__drop--over");
    expect(mark.text()).toMatch(/^Add Soft/);

    pointer("pointerup", window, 40, 40);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ model_id: 5, slot: "lora" }]],
    ]);
    expect(store.railDrag).toBe(null);
  });

  it("never takes a drop on an evidence card, and dims it for the drag", async () => {
    const ckpt = { id: 1, name: "RealVisXL_v5", kind: "checkpoint" };
    const lora = { id: 3, name: "FilmGrain_XL", kind: "adapter" };
    const { wrapper, store } = await mountRail();
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
    await store.loadWorkflowSets({ force: true });
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    const card = grid.find(".msg__row");
    pointAt(card.find(".msg__cell").element);
    await startDrag(wrapper, "Soft");
    expect(card.classes()).toContain("msg__row--dim");
    expect(card.attributes("data-rail-drop")).toBeUndefined();
    expect(store.railOver).toBe("");
    pointer("pointerup", window, 40, 40);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
  });

  it("lets nothing take a drop on a slot that refuses it", async () => {
    const { wrapper } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
      open: "hand:10",
    });
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    // A LoRA over the Checkpoint slot: refused there, and the pill says so.
    const slot = grid.find('[data-slot="checkpoint"]');
    pointAt(slot.element);
    await startDrag(wrapper, "Soft");
    expect(slot.classes()).toContain("mss__slot--refuse");
    expect(ghost().classList).toContain("mrail-ghost--refused");
    expect(document.documentElement.classList).toContain("rail-dragging--refused");
    pointer("pointerup", window, 40, 40);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
    expect(document.documentElement.classList).not.toContain("rail-dragging");
  });

  it("files into the slot under the pointer when every model fits it", async () => {
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, []),
      added: [shaOf(5)],
    });
    const { wrapper } = await mountRail({
      handMade: [handSet(10, [member(1, "RealVisXL_v5", "checkpoint")])],
      open: "hand:10",
    });
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    const slot = grid.find('[data-slot="lora"]');
    pointAt(slot.element);
    await startDrag(wrapper, "Soft");
    expect(slot.classes()).toContain("mss__slot--drop");
    pointer("pointerup", window, 40, 40);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ model_id: 5, slot: "lora" }]],
    ]);
  });

  it("marks every target from the start of the drag, not only under the pointer", async () => {
    listAdapters.mockResolvedValue(ROWS);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, [])],
    });
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    expect(grid.findAll('[data-testid="rail-drop-mark"]')).toHaveLength(0);

    store.railDrag = [store.rows.find((r) => r.id === 5)];
    await grid.vm.$nextTick();
    const marks = () => grid.findAll('[data-testid="rail-drop-mark"]');
    // The hand-made card and New workflow set, before the pointer reaches one.
    expect(marks()).toHaveLength(2);
    expect(marks().some((m) => m.classes("msg__drop--over"))).toBe(false);

    store.railOver = "new";
    await grid.vm.$nextTick();
    const tile = grid.find('[data-key="new"] [data-testid="rail-drop-mark"]');
    expect(tile.classes()).toContain("msg__drop--over");
    expect(tile.text()).toMatch(/^New set with Soft/);

    store.railDrag = null;
    await grid.vm.$nextTick();
    expect(marks()).toHaveLength(0);
  });

  it("refuses a set of another base model during the drag, and says why", async () => {
    listAdapters.mockResolvedValue([
      row(5, "Soft_Light", "adapter", { base_model: "SD 1.5" }),
    ]);
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [
        handSet(10, [
          { ...member(1, "RealVisXL_v5", "checkpoint"), base_model: "SDXL" },
        ]),
      ],
    });
    const store = useModelShelfStore();
    store.setView({ groupBy: "workflow_set" });
    await store.fetchRows();
    const grid = mountGrid();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await grid.vm.$nextTick();
    store.railDrag = store.rows.filter((r) => r.id === 5);
    store.railOver = "hand:10";
    await grid.vm.$nextTick();

    const card = grid.find('[data-key="hand:10"]');
    // The rail reads this to refuse the drop and grey its pill.
    expect(card.attributes("data-rail-refused")).toBe("SDXL, not SD 1.5");
    const mark = card.find('[data-testid="rail-drop-mark"]');
    expect(mark.classes()).toEqual(
      expect.arrayContaining(["msg__drop--refused", "msg__drop--over"]),
    );
    expect(mark.text()).toBe("SDXL, not SD 1.5");
    // Handed a drop anyway, the card still files nothing.
    card.element.dispatchEvent(new CustomEvent("rail-drop", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();

    // Its open tray agrees with its card: the LoRA slot refuses too.
    store.toggleSet("hand:10");
    store.railDrag = store.rows.filter((r) => r.id === 5);
    await grid.vm.$nextTick();
    const slot = grid.find('[data-slot="lora"]');
    expect(slot.classes()).toContain("mss__slot--refuse");
    expect(slot.classes()).not.toContain("mss__slot--target");
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

  it("refuses a mixed selection when any model is on another base model", async () => {
    const store = useModelShelfStore();
    listAdapters.mockResolvedValue([
      row(5, "Soft_Light", "adapter", { base_model: "SD 1.5" }),
      row(6, "Film_XL", "adapter", { base_model: "SDXL" }),
    ]);
    await store.fetchRows();
    const sdxl = handSet(10, [
      { ...member(1, "RealVisXL_v5", "checkpoint"), base_model: "SDXL" },
    ]);
    // One of them fits; the other must not ride in on its back.
    expect(store.railDropRefusal(sdxl, store.rows)).toBe("SDXL, not SD 1.5");
    await store.addRowsToHandMadeSet(sdxl, store.rows);
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
    // A model whose base is unknown is no conflict.
    expect(
      store.railDropRefusal(sdxl, [store.rows[1], row(8, "Mystery", "adapter")]),
    ).toBe("");
  });
});
