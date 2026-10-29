// The Training settings section reads the jobs when the dialog mounts it already
// open, and polls while a job runs, including one started elsewhere while the
// section is open.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const training = vi.hoisted(() => ({ listTrainingJobs: vi.fn() }));
vi.mock("../../api/training", async (importOriginal) => ({
  ...(await importOriginal()),
  listTrainingJobs: training.listTrainingJobs,
}));
vi.mock("./AiToolkitSetup.vue", () => ({ default: { render: () => null } }));

import TrainingSection from "./TrainingSection.vue";
import { useTrainingStore } from "../../stores/useTrainingStore";

const RUNNING = {
  id: 1,
  uuid: "run-1",
  name: "Clementine",
  status: "running",
  step: 5,
  total_steps: 100,
};
const DONE = { ...RUNNING, id: 2, uuid: "run-2", status: "completed" };

const wrappers = [];

function mountSection(open) {
  const wrapper = mount(TrainingSection, { props: { open } });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  training.listTrainingJobs.mockReset();
  training.listTrainingJobs.mockResolvedValue([DONE]);
});

afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount());
  vi.useRealTimers();
});

describe("the Training settings section", () => {
  it("reads the jobs when it mounts already open", async () => {
    mountSection(true);
    await flushPromises();

    expect(training.listTrainingJobs).toHaveBeenCalledTimes(1);
  });

  it("says why a failed job failed", async () => {
    training.listTrainingJobs.mockResolvedValue([
      { ...DONE, status: "error", error: "Dataset build failed" },
    ]);
    const wrapper = mountSection(true);
    await flushPromises();

    expect(wrapper.find(".training-row-err").text()).toBe("Dataset build failed");
  });

  it("polls while a job started elsewhere is running", async () => {
    vi.useFakeTimers();
    mountSection(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(training.listTrainingJobs).toHaveBeenCalledTimes(1);

    // The toolbar panel starts a job: the store's jobs now hold a running one.
    training.listTrainingJobs.mockResolvedValue([RUNNING]);
    await useTrainingStore().fetchJobs();
    await vi.advanceTimersByTimeAsync(0);
    const before = training.listTrainingJobs.mock.calls.length;

    await vi.advanceTimersByTimeAsync(4000);
    expect(training.listTrainingJobs.mock.calls.length).toBeGreaterThan(before);
  });
});
