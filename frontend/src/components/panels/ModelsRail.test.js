// The Models screen's right rail (#2168): an inspector of the shelf's selection.
//
// The lines worth holding: with nothing picked it shows the view's figures and
// the sets missing a checkpoint; one set or one model gets its own sections;
// several say what they hold; the runs page has its own head; and Clear lets go
// of every selection at once.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const routeState = vi.hoisted(() => ({ name: "models", path: "/models", query: {} }));

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

const getTrainingJobLive = vi.fn();
const cancelTrainingJob = vi.fn();
vi.mock("../../api/training", () => ({
  getTrainingJobLive: (...args) => getTrainingJobLive(...args),
  cancelTrainingJob: (...args) => cancelTrainingJob(...args),
  listTrainingJobs: vi.fn(async () => []),
}));
vi.mock("../../api/modelImports", () => ({
  listRuns: vi.fn(async () => []),
  runSampleUrl: (folderId, runName, filename) =>
    `/runs/${folderId}/${runName}/${filename}`,
}));
vi.mock("../../api/characters", () => ({
  listCharacters: vi.fn(async () => [{ id: 3, name: "Aria", image_count: 64 }]),
}));
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn(async () => []),
}));

import { formatModelSize } from "../../utils/modelShelf";
import ModelsRail from "./ModelsRail.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
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

/** Load the shelf and the sets, then mount the rail. */
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
  routeState.query = {};
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

  it("has no tab band, and a Tasks deep link opens the title bar's pill", async () => {
    const { wrapper } = await mountRail();
    expect(wrapper.find(".inspector-tabs").exists()).toBe(false);
    const sidebar = useSidebarStore();
    sidebar.setModelsRailOpen(false);
    const before = sidebar.tasksPillRequest;
    sidebar.showTasksTab();
    await wrapper.vm.$nextTick();
    expect(sidebar.tasksPillRequest).toBe(before + 1);
    // Nothing for the rail to show, so the link does not open it.
    expect(sidebar.modelsRailOpen).toBe(false);
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
    // The shared header's chevron is the button that carries the state.
    const toggle = wrapper.get('[data-testid="mrs-toggle-members"] button');
    expect(toggle.attributes("aria-expanded")).toBe("true");
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find('[data-testid="mrail-missing-note"]').exists()).toBe(false);
    expect(wrapper.find(".mrs-body#mrs-body-members").exists()).toBe(false);
    store.clearSetSelection();
    await wrapper.vm.$nextTick();
    store.selectSet(9);
    await wrapper.vm.$nextTick();
    expect(
      wrapper.get('[data-testid="mrs-toggle-members"] button').attributes("aria-expanded"),
    ).toBe("false");
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

describe("a picked training run (#2169)", () => {
  const finished = {
    name: "walter_v2",
    checkpoints: [
      { filename: "walter_v2_000001000.safetensors", step: 1000, size: 2e8 },
      { filename: "walter_v2.safetensors", step: null, size: 2e8 },
    ],
    samples: [
      { filename: "a_1000_0.jpg", step: 1000, index: 0 },
      { filename: "a_1000_1.jpg", step: 1000, index: 1 },
      { filename: "a_500_0.jpg", step: 500, index: 0 },
    ],
    base_model: "SDXL",
    trigger_words: ["w4lt3r"],
    sample_prompts: ["w4lt3r, portrait", "a red bicycle"],
    rank: 16,
    learning_rate: 0.0001,
    total_steps: 2000,
  };
  const liveJob = {
    id: 7,
    uuid: "u7",
    name: "aria_v4",
    status: "running",
    step: 1200,
    total_steps: 3000,
    base_model: "Flux.1 dev",
    trigger_word: "ar1a",
    character_id: 3,
    run_name: null,
  };

  async function mountOnRuns({ runs = [], jobs = [], pick = [] } = {}) {
    routeState.name = "models-runs";
    const folders = useModelFoldersStore();
    folders.folders = [{ id: 1, path: "/home/me/ai-toolkit/output", kind: "source" }];
    folders.loaded = true;
    const runsStore = useTrainingRunsStore();
    runsStore.runs = runs;
    runsStore.jobs = jobs;
    runsStore.jobsLoaded = true;
    runsStore.chosen = pick;
    const mountedRail = await mountRail();
    return { ...mountedRail, runsStore };
  }

  it("describes the run whose page is open, picked or not, by what it was made of", async () => {
    const job = {
      ...liveJob,
      status: "error",
      run_name: "walter_v2",
      uuid: "u8",
      started_at: "2026-10-30T10:00:00Z",
    };
    routeState.query = { run: "u8" };
    const { wrapper } = await mountOnRuns({ runs: [finished], jobs: [job] });
    expect(title(wrapper)).toBe("aria_v4");
    // The page shows how it went; the rail does not say it again.
    expect(wrapper.find('[data-testid="mrr-steps"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="mrr-samples"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("ar1a");
    expect(wrapper.text()).toContain("walter_v2_000001000.safetensors");
    // What the grid ticked is not on screen to clear, even when it is ticked.
    expect(wrapper.find('[data-testid="mrail-clear"]').exists()).toBe(false);
    const ticked = await mountOnRuns({
      runs: [finished],
      jobs: [job],
      pick: ["run:walter_v2"],
    });
    expect(ticked.wrapper.find('[data-testid="mrail-clear"]').exists()).toBe(false);
  });

  it("describes a run trained elsewhere whose page is open, from its folder", async () => {
    routeState.query = { "folder-run": "walter_v2" };
    const { wrapper } = await mountOnRuns({ runs: [finished] });
    expect(title(wrapper)).toBe("walter_v2");
    expect(wrapper.find('[data-testid="mrr-samples"]').exists()).toBe(false);
    expect(wrapper.text()).toContain("w4lt3r");
    expect(wrapper.text()).toContain("The pictures were not recorded.");
  });

  it("with nothing picked names the folder and counts the runs", async () => {
    const { wrapper } = await mountOnRuns({ runs: [finished], jobs: [liveJob] });
    const pane = wrapper.get('[data-testid="mrail-runs-hint"]');
    expect(pane.text()).toContain("/home/me/ai-toolkit/output");
    expect(pane.text()).toContain("2 · 1 training now");
  });

  it("describes one finished run: samples, checkpoints and set-up", async () => {
    const { wrapper } = await mountOnRuns({
      runs: [finished],
      pick: ["run:walter_v2"],
    });
    expect(title(wrapper)).toBe("walter_v2");
    expect(wrapper.get('[data-testid="mrr-steps"]').text()).toBe("2,000 steps");
    expect(wrapper.find('[data-testid="mrr-stop"]').exists()).toBe(false);
    // Trained elsewhere: its page is drawn from the folder.
    expect(wrapper.find('[data-testid="mrr-open"]').exists()).toBe(true);
    const grid = wrapper.get('[data-testid="mrr-samples"]');
    expect(grid.text()).toContain("a red bicycle · no trigger");
    expect(grid.findAll("img").map((img) => img.attributes("src"))).toEqual([
      "/runs/1/walter_v2/a_500_0.jpg",
      "/runs/1/walter_v2/a_1000_0.jpg",
      "/runs/1/walter_v2/a_1000_1.jpg",
    ]);
    expect(wrapper.text()).toContain("walter_v2.safetensors");
    expect(wrapper.text()).toContain("16 · 0.0001");
    expect(wrapper.text()).toContain("/home/me/ai-toolkit/output/walter_v2");
  });

  it("follows a live run and stops it from its Training section", async () => {
    getTrainingJobLive.mockResolvedValue({
      ...liveJob,
      columns: [0, 500, 1000],
      rows: [
        {
          index: 0,
          prompt: "ar1a, portrait",
          with_trigger: true,
          cells: [
            { state: "sample", filename: "s0.jpg" },
            { state: "sample", filename: "s500.jpg" },
            { state: "todo", filename: null },
          ],
        },
      ],
      run_folder: { folder_id: 1, run_name: "aria_v4" },
      loss_curve: [
        { step: 1, loss: 0.5 },
        { step: 2, loss: 0.31 },
      ],
      speed: { remaining_low_s: 3600, remaining_high_s: 4800 },
      dataset_picture_ids: [1, 2, 3],
    });
    cancelTrainingJob.mockResolvedValue({});
    const { wrapper } = await mountOnRuns({ jobs: [liveJob], pick: ["job:u7"] });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(title(wrapper)).toBe("aria_v4");
    expect(wrapper.text()).toContain("1,200 / 3,000 steps");
    expect(wrapper.text()).toContain("about 1 h 10 min");
    expect(wrapper.text()).toContain("0.310");
    expect(wrapper.text()).toContain("Aria · 3 pictures");
    // Only the steps that have drawn a sample are columns.
    expect(wrapper.findAll(".mrr-col").map((c) => c.text())).toEqual(["0", "500"]);
    expect(wrapper.find('[data-testid="mrr-open"]').exists()).toBe(true);

    // Stopping asks first (#2295); the owner says yes.
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get('[data-testid="mrr-stop"]').trigger("click");
    expect(cancelTrainingJob).toHaveBeenCalledWith(7);
  });

  it("skips a trained run's previews from the rail, and offers nothing while it imports", async () => {
    // Trained, not imported yet: `completed` with no completed_at (#2291).
    const trained = { ...liveJob, status: "completed", completed_at: null };
    getTrainingJobLive.mockResolvedValue({ ...trained, phase: "previews" });
    cancelTrainingJob.mockResolvedValue({});
    const { wrapper } = await mountOnRuns({ jobs: [trained], pick: ["job:u7"] });
    await new Promise((resolve) => setTimeout(resolve, 0));

    const skip = wrapper.get('[data-testid="mrr-stop"]');
    expect(skip.text()).toBe("Skip previews");
    await skip.trigger("click");
    expect(cancelTrainingJob).toHaveBeenCalledWith(7);

    getTrainingJobLive.mockResolvedValue({ ...trained, phase: "importing" });
    const importing = await mountOnRuns({ jobs: [trained], pick: ["job:u7"] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(importing.wrapper.find('[data-testid="mrr-stop"]').exists()).toBe(false);
  });

  it("draws no picture for a sample whose folder it cannot name, and keeps the grid", async () => {
    // A live job can report a drawn sample before its run folder resolves to a
    // model folder, and with no run on disk yet there is nothing else to
    // address it by: an empty `src` would show a broken picture.
    getTrainingJobLive.mockResolvedValue({
      ...liveJob,
      columns: [0, 500],
      rows: [
        {
          index: 0,
          prompt: "ar1a, portrait",
          with_trigger: true,
          cells: [
            { state: "sample", filename: "s0.jpg" },
            { state: "sample", filename: "s500.jpg" },
          ],
        },
      ],
      run_folder: null,
    });
    const { wrapper } = await mountOnRuns({ jobs: [liveJob], pick: ["job:u7"] });
    await new Promise((resolve) => setTimeout(resolve, 0));

    const grid = wrapper.get('[data-testid="mrr-samples"]');
    expect(grid.findAll(".mrr-cell")).toHaveLength(2);
    expect(grid.findAll("img")).toHaveLength(0);
  });

  it("says why a run failed before it had a folder", async () => {
    const failed = { ...liveJob, status: "error", error: "Dataset build failed" };
    getTrainingJobLive.mockResolvedValue({ ...failed, rows: [] });
    const { wrapper } = await mountOnRuns({ jobs: [failed], pick: ["job:u7"] });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.get('[data-testid="mrr-reason"]').text()).toBe(
      "Dataset build failed",
    );
    expect(wrapper.find('[data-testid="mrr-stop"]').exists()).toBe(false);
  });

  it("offers no second Stop while the run is already stopping", async () => {
    getTrainingJobLive.mockResolvedValue({ ...liveJob, status: "stopping", rows: [] });
    const { wrapper } = await mountOnRuns({
      jobs: [{ ...liveJob, status: "stopping" }],
      pick: ["job:u7"],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.find('[data-testid="mrr-stop"]').exists()).toBe(false);
  });

  it("polls again when the tab is looked at after being hidden", async () => {
    vi.useFakeTimers();
    let state = "visible";
    const spy = vi
      .spyOn(document, "visibilityState", "get")
      .mockImplementation(() => state);
    try {
      getTrainingJobLive.mockResolvedValue({ ...liveJob, rows: [] });
      routeState.name = "models-runs";
      const runsStore = useTrainingRunsStore();
      runsStore.jobs = [liveJob];
      runsStore.chosen = ["job:u7"];
      const wrapper = mount(ModelsRail, { global: { stubs } });
      mounted.push(wrapper);
      await vi.advanceTimersByTimeAsync(0);
      state = "hidden";
      await vi.advanceTimersByTimeAsync(4000);
      const whileHidden = getTrainingJobLive.mock.calls.length;
      await vi.advanceTimersByTimeAsync(10000);
      expect(getTrainingJobLive.mock.calls.length).toBe(whileHidden);

      state = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
      expect(getTrainingJobLive.mock.calls.length).toBe(whileHidden + 1);
      await vi.advanceTimersByTimeAsync(2000);
      expect(getTrainingJobLive.mock.calls.length).toBe(whileHidden + 2);
    } finally {
      spy.mockRestore();
      vi.useRealTimers();
    }
  });

  it("leaves no poll behind when it goes while a read is out", async () => {
    vi.useFakeTimers();
    try {
      let release;
      getTrainingJobLive.mockImplementation(
        () => new Promise((resolve) => (release = resolve)),
      );
      routeState.name = "models-runs";
      const runsStore = useTrainingRunsStore();
      runsStore.jobs = [liveJob];
      runsStore.chosen = ["job:u7"];
      const wrapper = mount(ModelsRail, { global: { stubs } });
      await wrapper.vm.$nextTick();
      expect(getTrainingJobLive).toHaveBeenCalledTimes(1);

      wrapper.unmount();
      release({ ...liveJob, rows: [] });
      await vi.advanceTimersByTimeAsync(10000);
      expect(getTrainingJobLive).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says the stacking rule for several runs, and Clear lets go of them", async () => {
    const other = { ...finished, name: "walter_v3", base_model: "Flux.1 dev" };
    const { wrapper, runsStore } = await mountOnRuns({
      runs: [finished, other],
      pick: ["run:walter_v2", "run:walter_v3"],
    });
    expect(title(wrapper)).toBe("2 runs");
    const pane = wrapper.get('[data-testid="mrail-runs-many"]');
    expect(pane.text()).toContain("4 checkpoints");
    expect(pane.text()).toContain("SDXL, Flux.1 dev");
    expect(pane.text()).toContain("each run lands as one stack");

    await wrapper.get('[data-testid="mrail-clear"]').trigger("click");
    expect(runsStore.chosen).toEqual([]);
  });
});
