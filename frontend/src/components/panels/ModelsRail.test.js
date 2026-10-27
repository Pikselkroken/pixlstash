// The Models screen's right rail (#2168): an inspector of the shelf's selection.
//
// The lines worth holding: with nothing picked it shows the view's figures and
// the sets missing a checkpoint; one set or one model gets its own sections;
// several say what they hold; the runs page has its own head; and Clear lets go
// of every selection at once.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const routeState = vi.hoisted(() => ({ name: "models", path: "/models" }));

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  const VMenu = { name: "VMenu", template: "<div><slot /></div>" };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

vi.mock("vue-router", () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: vi.fn() }),
}));

const fetchWorkflowSets = vi.fn();
const addWorkflowSetMembers = vi.fn();
const listAdapters = vi.fn();
const listCheckpoints = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  // The shelf reads every kind through one call; only the unfiltered read
  // answers, or each kind's rows would be counted again.
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
  removeWorkflowSetMembers: vi.fn(),
  renameWorkflowSet: vi.fn(),
  setWorkflowSetDeclines: vi.fn(),
  setModelsHidden: vi.fn(),
}));

const listFavourites = vi.fn();
vi.mock("../../api/favourites", () => ({
  listFavourites: (...args) => listFavourites(...args),
  setFavourites: vi.fn(),
}));

vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import { formatModelSize } from "../../utils/modelShelf";
import ModelsRail from "./ModelsRail.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

const stubs = {
  "v-icon": true,
  "v-menu": { template: "<div><slot /></div>" },
  AddToEntityControl: true,
  TasksPanel: true,
  Tooltip: { template: "<span><slot /></span>" },
  RouterLink: { props: ["to"], template: "<a><slot /></a>" },
  "router-link": { props: ["to"], template: "<a><slot /></a>" },
  ShelfRunLoss: true,
};

const shaOf = (id) => String(id).repeat(64).slice(0, 64);

/** A shelf row. Mixed case on purpose: a case-folding lookup passes all-lowercase. */
function row(id, filename, fileKind = "adapter", extra = {}) {
  return {
    id,
    sha256: shaOf(id),
    file_kind: fileKind,
    kind: fileKind === "adapter" ? "lora" : null,
    display_name: null,
    filename,
    base_model: "SDXL",
    file_size: 1024 * id,
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
    base_model: "SDXL",
  };
}

function handSet(id, name, members) {
  return {
    id,
    name,
    created_at: "2026-09-26T08:00:00Z",
    incomplete: !members.some((m) => m.slot === "checkpoint"),
    picture_count: 0,
    recipes: 0,
    covers: [],
    members,
  };
}

const ROWS = [
  row(1, "RealVisXL_v5", "checkpoint"),
  row(3, "FilmGrain_XL"),
  row(5, "Soft_Light"),
];

const mounted = [];

/** Load the shelf and the sets, then mount the rail on the Models tab. */
async function mountRail({
  handMade = [],
  rows = ROWS,
  combinations = [],
  attach = false,
} = {}) {
  listAdapters.mockResolvedValue(rows);
  fetchWorkflowSets.mockResolvedValue({
    combinations,
    no_set: [],
    hand_made: handMade,
    hidden_sets: [],
  });
  const store = useModelShelfStore();
  await store.fetchRows();
  await store.loadWorkflowSets();
  store.setPlace("all");
  useSidebarStore().setModelsRailOpen(true);
  const wrapper = mount(ModelsRail, {
    global: { stubs },
    ...(attach ? { attachTo: document.body } : {}),
  });
  mounted.push(wrapper);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

const title = (wrapper) => wrapper.get('[data-testid="mrail-title"]').text();

beforeEach(() => {
  setActivePinia(createPinia());
  routeState.name = "models";
  routeState.path = "/models";
  listAdapters.mockReset().mockResolvedValue([]);
  listCheckpoints.mockReset().mockResolvedValue([]);
  listFavourites.mockReset().mockResolvedValue({ model: [], workflow: [], set: [] });
  addWorkflowSetMembers.mockReset().mockResolvedValue({ added: [] });
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [], hand_made: [] });
});

afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.restoreAllMocks();
});

describe("nothing picked", () => {
  it("heads the view by its name and figures what the view shows", async () => {
    const { wrapper } = await mountRail();
    expect(title(wrapper)).toBe("All models");
    expect(wrapper.get('[data-testid="mrail-stat-count"]').text()).toBe("3");
    expect(wrapper.get('[data-testid="mrail-stat-disk"]').text()).not.toBe("–");
    expect(wrapper.get('[data-testid="mrail-disk-bar"]').attributes("aria-label")).toContain(
      "Checkpoints",
    );
    expect(wrapper.findAll('[data-testid="mrail-largest"]')).toHaveLength(3);
  });

  it("figures the whole shelf on the Sets place, where no tray is open", async () => {
    const { wrapper, store } = await mountRail();
    store.setPlace("sets");
    await wrapper.vm.$nextTick();
    expect(title(wrapper)).toBe("Sets");
    expect(wrapper.get('[data-testid="mrail-stat-disk"]').text()).not.toBe("–");
    expect(wrapper.findAll('[data-testid="mrail-largest"]')).toHaveLength(3);
  });

  it("notes a set missing its checkpoint, and Select it picks that card on the Sets place", async () => {
    const orphan = {
      combo_key: "orphan",
      models: [{ id: 3, kind: "lora", filename: "FilmGrain_XL", on_shelf: true }],
      missing: [{ name: "flux1-dev.safetensors" }],
      covered_by: [],
      workflows: [],
      latest: [],
      last_used_at: null,
      picture_count: 0,
      recipes: 0,
      history_runs: 0,
    };
    const { wrapper, store } = await mountRail({ combinations: [orphan] });
    store.setPlace("all");
    await wrapper.vm.$nextTick();
    expect(store.missingSetKeys).toEqual(["missing:flux1-dev.safetensors"]);
    expect(wrapper.get('[data-testid="mrail-missing-note"]').text()).toContain(
      "1 set is missing its checkpoint.",
    );
    await wrapper.get('[data-testid="mrail-select-missing"]').trigger("click");
    expect(store.place).toBe("sets");
    expect([...store.selectedMissingKeys]).toEqual(["missing:flux1-dev.safetensors"]);
  });

  it("names the sidebar's type filter as the view", async () => {
    const { wrapper, store } = await mountRail();
    store.setSidebarFilter("type", "lora");
    await wrapper.vm.$nextTick();
    expect(title(wrapper)).toBe("LoRAs");
  });

  it("has no picker: the old list and its search are gone", async () => {
    const { wrapper } = await mountRail();
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="mrail-search"]').exists()).toBe(false);
  });

  it("offers no Clear when nothing is picked", async () => {
    const { wrapper } = await mountRail();
    expect(wrapper.find('[data-testid="mrail-clear"]').exists()).toBe(false);
  });
});

describe("one set", () => {
  const set = handSet(9, "Portraits", [
    member(1, "RealVisXL_v5", "checkpoint"),
    member(3, "FilmGrain_XL", "lora"),
  ]);

  it("heads the set by its name and shows its sections", async () => {
    const { wrapper, store } = await mountRail({ handMade: [set] });
    store.setPlace("sets");
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    expect(title(wrapper)).toBe("Portraits");
    for (const label of ["Models", "Works in", "Latest pictures", "About"]) {
      expect(wrapper.find(`section[aria-label="${label}"]`).exists()).toBe(true);
    }
    expect(wrapper.text()).toContain("Each of these has run with each other.");
    expect(wrapper.text()).toContain("You");
  });

  it("says a file that is not on the shelf, and asks for its replacement", async () => {
    const withGap = handSet(9, "Portraits", [
      member(1, "RealVisXL_v5", "checkpoint"),
      { ...member(7, "gone", "lora"), on_shelf: false },
    ]);
    const { wrapper, store } = await mountRail({ handMade: [withGap] });
    store.setPlace("sets");
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[data-testid="mrs-missing-member"]').text()).toContain(
      "(not on shelf)",
    );
    expect(wrapper.text()).toContain("replace it in the workflows that load it");
  });

  it("opens the set's tray from the pencil", async () => {
    const { wrapper, store } = await mountRail({ handMade: [set] });
    store.setPlace("sets");
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="mrs-edit-set"]').trigger("click");
    expect(store.openSetKey).toBe("hand:9");
  });

  it("shuts a section and keeps it shut when the reader moves to another set", async () => {
    const { wrapper, store } = await mountRail({ handMade: [set] });
    store.setPlace("sets");
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    const toggle = wrapper.get('[data-testid="mrs-toggle-members"]');
    expect(toggle.attributes("aria-expanded")).toBe("true");
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find('[data-testid="mrail-missing-note"]').exists()).toBe(false);
    expect(wrapper.find(".mrs-body#mrs-body-members").exists()).toBe(false);
    store.clearSetSelection();
    await wrapper.vm.$nextTick();
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[data-testid="mrs-toggle-members"]').attributes("aria-expanded")).toBe("false");
  });

  it("lets go of the set on Clear, back to the view", async () => {
    const { wrapper, store } = await mountRail({ handMade: [set] });
    store.setPlace("sets");
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="mrail-clear"]').trigger("click");
    expect(store.selectedSets).toEqual([]);
    expect(title(wrapper)).toBe("Sets");
  });
});

describe("one model", () => {
  it("heads the model by its name and reads its file", async () => {
    const trained = row(3, "FilmGrain_XL", "adapter", {
      trigger_words: ["fg3"],
      added_at: "2026-09-01T10:00:00Z",
    });
    const { wrapper, store } = await mountRail({ rows: [row(1, "RealVisXL_v5", "checkpoint"), trained] });
    store.toggleSelected(trained.id);
    await wrapper.vm.$nextTick();
    // The shelf reads the filename as its name: underscores become spaces.
    expect(title(wrapper)).toBe("FilmGrain XL");
    // An adapter is a LoRA, in the sidebar's Type word.
    expect(wrapper.find(".mrs-facts dd").text()).toBe("LoRA");
    expect(wrapper.text()).toContain("Trigger word");
    expect(wrapper.text()).toContain("fg3");
    expect(wrapper.text()).toContain("/m/FilmGrain_XL");
    expect(wrapper.find('[data-testid="mrm-copy-trigger"]').exists()).toBe(true);
  });

  it("opens the shelf's rename verb from the pencil", async () => {
    const { wrapper, store } = await mountRail();
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="mrm-rename"]').trigger("click");
    expect(store.verbRequest?.verb).toBe("rename");
  });

  it("says a model in no set is in no set yet, and files it from Add to set…", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(9, "Portraits", [member(1, "RealVisXL_v5", "checkpoint")])],
    });
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("In no set yet.");
    await wrapper.get('[data-testid="mrm-add-to-set"]').trigger("click");
    const choice = wrapper
      .findAll('[role="menuitem"]')
      .find((b) => b.text() === "Portraits");
    await choice.trigger("click");
    expect(addWorkflowSetMembers).toHaveBeenCalledWith(9, [
      { model_id: 3, slot: "lora" },
    ]);
  });
});

describe("the Add to set… menu", () => {
  it("gives focus back to its button when Escape closes it", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(9, "Portraits", [member(1, "RealVisXL_v5", "checkpoint")])],
      attach: true,
    });
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    const trigger = wrapper.get('[data-testid="mrm-add-to-set"]');
    await trigger.trigger("click");
    const item = wrapper.get('[data-testid="mrm-set-menu"] [role="menuitem"]');
    item.element.focus();
    await wrapper.get('[data-testid="mrm-set-menu"]').trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[data-testid="mrm-set-menu"]').exists()).toBe(false);
    expect(document.activeElement).toBe(trigger.element);
  });
});

describe("the Add to set… menu, pressed outside", () => {
  it("closes on a press outside it, and stays open for one inside", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(9, "Portraits", [member(1, "RealVisXL_v5", "checkpoint")])],
      attach: true,
    });
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="mrm-add-to-set"]').trigger("click");
    const menu = wrapper.get('[data-testid="mrm-set-menu"]');
    menu.element.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="mrm-set-menu"]').exists()).toBe(true);
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="mrm-set-menu"]').exists()).toBe(false);
  });
});

describe("several picked", () => {
  it("counts the disk and the base models of what is picked", async () => {
    const { wrapper, store } = await mountRail();
    store.toggleSelected(1);
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    expect(title(wrapper)).toBe("2 models");
    const many = wrapper.get('[data-testid="mrail-many"]');
    expect(many.text()).toContain("On disk");
    expect(many.text()).toContain("SDXL");
  });

  it("counts a file two picked sets share once", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [
        handSet(9, "Portraits", [member(1, "RealVisXL_v5", "checkpoint")]),
        handSet(10, "Street", [
          member(1, "RealVisXL_v5", "checkpoint"),
          member(5, "Soft_Light", "lora"),
        ]),
      ],
    });
    store.setPlace("sets");
    store.selectSet(9);
    store.selectSet(10, { ctrl: true });
    await wrapper.vm.$nextTick();
    // Rows 1 and 5 weigh 1 KB and 5 KB: 6 KB, not the 7 KB of counting 1 twice.
    expect(wrapper.get('[data-testid="mrail-many"]').text()).toContain(
      formatModelSize(6 * 1024),
    );
  });

  it("heads two sets by their count, not as one set", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [
        handSet(9, "Portraits", [member(1, "RealVisXL_v5", "checkpoint")]),
        handSet(10, "Street", [member(5, "Soft_Light", "lora")]),
      ],
    });
    store.setPlace("sets");
    store.selectSet(9);
    store.selectSet(10, { ctrl: true });
    await wrapper.vm.$nextTick();
    expect(title(wrapper)).toBe("2 sets");
    expect(wrapper.find('[data-testid="mrail-many"]').exists()).toBe(true);
    expect(wrapper.find('section[aria-label="Works in"]').exists()).toBe(false);
  });

  it("heads a mix as selected, and Clear lets go of all of it", async () => {
    const { wrapper, store } = await mountRail({
      handMade: [handSet(9, "Portraits", [member(1, "RealVisXL_v5", "checkpoint")])],
    });
    store.selectSet(9);
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    expect(title(wrapper)).toBe("2 selected");
    await wrapper.get('[data-testid="mrail-clear"]').trigger("click");
    expect(store.selectedRows).toEqual([]);
    expect(store.selectedSets).toEqual([]);
    expect(store.selectedMissingKeys.size).toBe(0);
  });
});

describe("the training runs page", () => {
  it("heads itself Training runs, with a hint and no inspector body", async () => {
    routeState.name = "models-runs";
    const { wrapper } = await mountRail();
    expect(title(wrapper)).toBe("Training runs");
    expect(wrapper.find('[data-testid="mrail-runs-hint"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="mrail-stat-count"]').exists()).toBe(false);
  });
});
