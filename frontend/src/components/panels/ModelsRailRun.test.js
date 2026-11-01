// The Models rail's card for one training run (#2169): its Stop asks before it
// stops the run, as Settings' Cancel does (#2295); with the run's page open it
// says only what the run was made of.

import { describe, it, expect, vi, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const getTrainingJobLive = vi.fn();
const cancelTrainingJob = vi.fn();
const exportTrainingSnapshot = vi.fn();
const listTrainingJobs = vi.fn(async () => []);
vi.mock("../../api/training", () => ({
  getTrainingJobLive: (...args) => getTrainingJobLive(...args),
  cancelTrainingJob: (...args) => cancelTrainingJob(...args),
  exportTrainingSnapshot: (...args) => exportTrainingSnapshot(...args),
  listTrainingJobs: (...args) => listTrainingJobs(...args),
}));
const importRun = vi.fn();
vi.mock("../../api/modelImports", () => ({
  runSampleUrl: (folderId, runName, filename) =>
    `/samples/${folderId}/${runName}/${filename}`,
  listRuns: async () => [],
  importRun: (...args) => importRun(...args),
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}`,
}));
const saveFileAs = vi.fn();
vi.mock("../../utils/downloadFile", () => ({
  saveFileAs: (...args) => saveFileAs(...args),
}));
const routerPush = vi.fn();
vi.mock("vue-router", () => ({
  useRoute: () => ({ fullPath: "/models/runs?run=abcd" }),
  useRouter: () => ({ push: routerPush }),
}));
vi.mock("../../stores/useEntityListsStore", () => ({
  useEntityListsStore: () => ({
    characters: [],
    pictureSets: [{ id: 5, name: "Old sailor" }],
    refresh: vi.fn(),
  }),
}));
const folders = {
  sourceFolder: { id: 2, path: "/home/me/runs", delete_after_import: false },
  folders: [
    { id: 2, kind: "source", path: "/home/me/runs" },
    { id: 9, kind: "managed", path: "/home/me/models" },
  ],
  refresh: vi.fn(async () => {}),
};
vi.mock("../../stores/useModelFoldersStore", () => ({
  useModelFoldersStore: () => folders,
}));
const fetchRows = vi.fn(async () => {});
vi.mock("../../stores/useModelShelfStore", () => ({
  useModelShelfStore: () => ({ fetchRows }),
}));

import ModelsRailRun from "./ModelsRailRun.vue";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";

const stubs = {
  ModelsRailSection: {
    props: ["sectionKey", "title"],
    template:
      "<div :data-section='sectionKey'><slot name='end' /><slot /></div>",
  },
  // The chart owns the smoothing; the rail must show its figure, not the raw
  // last loss (0.100 in these fixtures).
  LoraLossChart: {
    setup(_props, { expose }) {
      expose({ latestSmoothed: 0.244 });
    },
    template: "<div />",
  },
  AppButton: {
    props: ["loading"],
    template: "<button class='app-btn'><slot /></button>",
  },
  TrainingSampleDialog: {
    props: ["sample"],
    template: "<div class='tsd-stub' :data-sample='JSON.stringify(sample)' />",
  },
};

const card = {
  key: "job:7",
  name: "ohwx-portrait-v2",
  run: null,
  live: true,
  job: {
    id: 7,
    uuid: "abcd",
    name: "ohwx-portrait-v2",
    status: "running",
    step: 1000,
    total_steps: 1500,
    trigger_word: "ohwx",
    set_id: null,
    character_id: null,
  },
};

const live = {
  status: "running",
  phase: "training",
  step: 1000,
  total_steps: 1500,
  speed: { seconds_per_step: 1.9, remaining_low_s: null, remaining_high_s: null },
  loss_curve: [],
  columns: [],
  rows: [],
  dataset_picture_ids: [],
  run_folder: null,
};

const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  vi.restoreAllMocks();
  cancelTrainingJob.mockClear();
  exportTrainingSnapshot.mockReset();
  importRun.mockReset();
  saveFileAs.mockReset();
});

/** A run that failed at step 500 of 1,500, as its page opens it. */
const failedCard = {
  key: "run:old-sailor",
  name: "old-sailor",
  live: false,
  run: {
    name: "old-sailor",
    rank: 16,
    learning_rate: 0.0001,
    checkpoints: [
      { step: 250, filename: "old-sailor_000000250.safetensors", size: 1 },
      { step: 500, filename: "old-sailor_000000500.safetensors", size: 1 },
    ],
  },
  job: { ...card.job, status: "error", step: 500, set_id: 5, trigger_word: "oldsailor" },
};

const failedLive = {
  ...live,
  status: "error",
  phase: null,
  step: 500,
  columns: [0, 250, 500, 750, 1000, 1250, 1500],
  dataset_picture_ids: Array.from({ length: 12 }, (_, i) => i + 1),
  dataset_snapshot_id: 4,
  imported: false,
  loss_curve: [{ step: 10, loss: 0.2 }, { step: 20, loss: 0.1 }],
};

async function openRail(props, body = failedLive) {
  setActivePinia(createPinia());
  useTrainingRunsStore().jobsLoaded = true;
  getTrainingJobLive.mockResolvedValue(body);
  const wrapper = mount(ModelsRailRun, { props, global: { stubs } });
  mounted.push(wrapper);
  await flushPromises();
  return wrapper;
}

const sections = (wrapper) =>
  wrapper.findAll("[data-section]").map((s) => s.attributes("data-section"));

describe("the rail's training card", () => {
  it("asks before it stops the run, and stops nothing when the owner says no", async () => {
    setActivePinia(createPinia());
    getTrainingJobLive.mockResolvedValue(live);
    cancelTrainingJob.mockResolvedValue({});
    const wrapper = mount(ModelsRailRun, { props: { card }, global: { stubs } });
    mounted.push(wrapper);
    await flushPromises();

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await wrapper.find('[data-testid="mrr-stop"]').trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledWith(
      'Stop training "ohwx-portrait-v2"? The run will stop and cannot be resumed.',
    );
    expect(cancelTrainingJob).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    await wrapper.find('[data-testid="mrr-stop"]').trigger("click");
    await flushPromises();
    expect(cancelTrainingJob).toHaveBeenCalledWith(7);
  });

  it("skips a trained run's previews without asking: the model is kept", async () => {
    setActivePinia(createPinia());
    const trained = { ...card, job: { ...card.job, status: "completed", phase: "previews" } };
    getTrainingJobLive.mockResolvedValue({ ...live, status: "completed", phase: "previews" });
    cancelTrainingJob.mockResolvedValue({});
    const wrapper = mount(ModelsRailRun, { props: { card: trained }, global: { stubs } });
    mounted.push(wrapper);
    await flushPromises();

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const skip = wrapper.find('[data-testid="mrr-stop"]');
    expect(skip.text()).toBe("Skip previews");
    await skip.trigger("click");
    await flushPromises();
    expect(confirm).not.toHaveBeenCalled();
    expect(cancelTrainingJob).toHaveBeenCalledWith(7);
  });

  it("opens a sample full size on a click", async () => {
    setActivePinia(createPinia());
    getTrainingJobLive.mockResolvedValue({
      ...live,
      columns: [250, 500],
      rows: [
        {
          index: 0,
          prompt: "photo of ohwx woman",
          with_trigger: true,
          cells: [
            { state: "sample", filename: "a.jpg" },
            { state: "sample", filename: "b.jpg" },
          ],
        },
      ],
      run_folder: { folder_id: 3, run_name: "ohwx_abcd" },
    });
    const wrapper = mount(ModelsRailRun, { props: { card }, global: { stubs } });
    mounted.push(wrapper);
    await flushPromises();

    const sample = () => JSON.parse(wrapper.find(".tsd-stub").attributes("data-sample"));
    expect(sample()).toBe(null);
    await wrapper.findAll(".mrr-cell img")[1].trigger("click");
    expect(sample()).toEqual({
      src: "/samples/3/ohwx_abcd/b.jpg",
      step: 500,
      prompt: "photo of ohwx woman",
    });
  });

  it("says what the run was made of while its page is open, and nothing the page says", async () => {
    const wrapper = await openRail({ card: failedCard, open: true });
    // Training (progress, loss, the failure) and Samples are the page's now.
    expect(sections(wrapper)).toEqual(["setup", "trained", "ckpts"]);
    expect(wrapper.text()).not.toContain("Loss");
    const setup = wrapper.find("[data-section='setup']").text();
    expect(setup).toContain("oldsailor");
    expect(setup).toContain("16 · 0.0001");
    expect(setup).toContain("1,500 steps, a sample every 250");

    // Every column a multiple of the first gap is not an even pitch.
    const uneven = await openRail(
      { card: failedCard, open: true },
      { ...failedLive, total_steps: 1500, columns: [0, 100, 500, 1500] },
    );
    const plan = uneven.find("[data-section='setup']").text();
    expect(plan).toContain("1,500 steps");
    expect(plan).not.toContain("a sample every");
    expect(setup).not.toContain("Trained on");
    // Twelve pictures: eight shown and a cell counting the other four.
    const trained = wrapper.find("[data-section='trained']");
    expect(trained.text()).toContain("Old sailor");
    expect(trained.findAll("img")).toHaveLength(8);
    expect(trained.text()).toContain("+4 more");
  });

  it("opens a training picture in the lightbox, coming back to this run", async () => {
    routerPush.mockReset();
    const wrapper = await openRail({ card: failedCard, open: true });
    await wrapper.findAll("[data-testid='mrr-pictures'] img")[1].trigger("click");
    expect(routerPush).toHaveBeenCalledWith({
      name: "all-pictures",
      query: { overlay: "2", from: "/models/runs?run=abcd" },
    });
  });

  it("goes to the model the run put on the shelf, in the grid and on its page", async () => {
    const onShelf = { ...failedCard, job: { ...failedCard.job, model_ids: [31, 30] } };
    for (const open of [false, true]) {
      routerPush.mockReset();
      const wrapper = await openRail({ card: onShelf, open });
      await wrapper.find("[data-section='ckpts'] [data-testid='mrr-model']").trigger("click");
      expect(routerPush).toHaveBeenCalledWith({ name: "models", query: { model: "31" } });
    }
  });

  it("offers no Go to model for a run with nothing on the shelf", async () => {
    const wrapper = await openRail({ card: failedCard });
    expect(wrapper.find("[data-testid='mrr-model']").exists()).toBe(false);
  });

  it("keeps the whole summary while the run is only picked in the grid", async () => {
    const wrapper = await openRail({ card: failedCard });
    expect(sections(wrapper)).toEqual(["progress", "samples", "ckpts", "setup"]);
    // One loss, said to be smoothed, as the page says it.
    expect(wrapper.find("[data-section='progress']").text()).toMatch(/Loss\s*0\.244\s*smoothed/);
    expect(wrapper.find("[data-section='setup']").text()).toContain("Old sailor · 12 pictures");
    expect(wrapper.find('[data-testid="mrr-shelve"]').exists()).toBe(false);
  });

  it("cuts a trainer's whole paragraph and keeps it behind Full message", async () => {
    const dump = `CUDA out of memory. Tried to allocate 5.06 GiB. ${"Process 1 has 498.00 MiB memory in use. ".repeat(12)}`.trim();
    const report = `${dump}\nTraceback (most recent call last): …`;
    const wrapper = await openRail({ card: failedCard }, { ...failedLive, error: dump, report });
    const reason = wrapper.find('[data-testid="mrr-reason"]').text();
    expect(reason.length).toBeLessThanOrEqual(200);
    expect(reason.endsWith("…")).toBe(true);
    expect(dump.startsWith(reason.slice(0, -1))).toBe(true);
    expect(wrapper.find('[data-testid="mrr-full"]').exists()).toBe(false);

    const toggle = wrapper.find('[data-testid="mrr-full-toggle"]');
    expect(toggle.attributes("aria-expanded")).toBe("false");
    await toggle.trigger("click");
    expect(wrapper.find('[data-testid="mrr-full"]').text()).toBe(report);
    expect(toggle.attributes("aria-expanded")).toBe("true");
  });

  it("shows a short reason whole, with nothing more to open", async () => {
    const wrapper = await openRail(
      { card: failedCard },
      { ...failedLive, error: "Training failed.", report: null },
    );
    expect(wrapper.find('[data-testid="mrr-reason"]').text()).toBe("Training failed.");
    expect(wrapper.find('[data-testid="mrr-full-toggle"]').exists()).toBe(false);
  });

  it("exports the frozen dataset from the rail", async () => {
    const blob = new Blob(["zip"]);
    exportTrainingSnapshot.mockResolvedValue({ blob, filename: "run_dataset.zip" });
    saveFileAs.mockResolvedValue(true);
    const wrapper = await openRail({ card: failedCard, open: true });
    await wrapper.find('[data-testid="mrr-export"]').trigger("click");
    await flushPromises();
    expect(exportTrainingSnapshot).toHaveBeenCalledWith(7);
    expect(saveFileAs).toHaveBeenCalledWith(blob, "run_dataset.zip");

    const none = await openRail(
      { card: failedCard, open: true },
      { ...failedLive, dataset_snapshot_id: null },
    );
    expect(none.find('[data-testid="mrr-export"]').exists()).toBe(false);
  });

  it("puts the last checkpoint of a run that never reached the shelf on it", async () => {
    importRun.mockResolvedValue({ files: [{ status: "imported" }] });
    const wrapper = await openRail({ card: failedCard, open: true });
    const shelve = wrapper.find('[data-testid="mrr-shelve"]');
    expect(shelve.text()).toBe("Put step 500 on the shelf");
    await shelve.trigger("click");
    await flushPromises();
    expect(importRun).toHaveBeenCalledWith({
      sourceFolderId: 2,
      runName: "old-sailor",
      destinationFolderId: 9,
      steps: [500],
    });
    expect(fetchRows).toHaveBeenCalled();
    // Landed: not offered a second time.
    expect(wrapper.find('[data-testid="mrr-shelve"]').exists()).toBe(false);
  });

  it("offers no checkpoint of a run already on the shelf, or still training", async () => {
    const imported = await openRail(
      { card: failedCard, open: true },
      {
        ...failedLive,
        status: "completed",
        completed_at: "2026-10-30T10:00:00Z",
        imported: true,
        coverable: [250, 500],
      },
    );
    expect(imported.find('[data-testid="mrr-shelve"]').exists()).toBe(false);

    // Step 250 taken from the grid leaves step 500 still to take.
    const partly = await openRail(
      { card: failedCard, open: true },
      { ...failedLive, imported: true, coverable: [250] },
    );
    expect(partly.find('[data-testid="mrr-shelve"]').text()).toBe("Put step 500 on the shelf");

    const training = await openRail(
      { card: { ...failedCard, live: true, job: { ...failedCard.job, status: "running" } }, open: true },
      { ...failedLive, status: "running", phase: "training" },
    );
    expect(training.find('[data-testid="mrr-shelve"]').exists()).toBe(false);
  });

  it("asks first when the folder removes what it imports, and takes nothing on no", async () => {
    folders.sourceFolder.delete_after_import = true;
    try {
      const wrapper = await openRail({ card: failedCard, open: true });
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      await wrapper.find('[data-testid="mrr-shelve"]').trigger("click");
      await flushPromises();
      expect(confirm).toHaveBeenCalled();
      expect(importRun).not.toHaveBeenCalled();
    } finally {
      folders.sourceFolder.delete_after_import = false;
    }
  });
});
