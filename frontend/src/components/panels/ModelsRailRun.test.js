// The Models rail's card for one training run (#2169): its Stop asks before it
// stops the run, as Settings' Cancel does (#2295).

import { describe, it, expect, vi, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const getTrainingJobLive = vi.fn();
const cancelTrainingJob = vi.fn();
const listTrainingJobs = vi.fn(async () => []);
vi.mock("../../api/training", () => ({
  getTrainingJobLive: (...args) => getTrainingJobLive(...args),
  cancelTrainingJob: (...args) => cancelTrainingJob(...args),
  listTrainingJobs: (...args) => listTrainingJobs(...args),
}));
vi.mock("../../api/modelImports", () => ({ runSampleUrl: () => "" }));
vi.mock("vue-router", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../../stores/useEntityListsStore", () => ({
  useEntityListsStore: () => ({ characters: [], pictureSets: [], refresh: vi.fn() }),
}));

import ModelsRailRun from "./ModelsRailRun.vue";

const stubs = {
  ModelsRailSection: {
    template: "<div><slot name='end' /><slot /></div>",
  },
  LoraLossChart: { template: "<div />" },
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
});

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
});
