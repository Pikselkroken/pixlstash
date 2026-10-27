// useTrainingRunsStore: what a session reset leaves behind for the next pick.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../api/modelImports", () => ({ listRuns: vi.fn() }));
vi.mock("../api/training", () => ({ listTrainingJobs: vi.fn() }));

import { notifySessionReset } from "../utils/apiClient";
import { useTrainingRunsStore } from "./useTrainingRunsStore";

function walter() {
  return {
    name: "walter_v2",
    checkpoints: [
      { step: 500, filename: "walter_v2_000000500.safetensors" },
      { step: 1000, filename: "walter_v2.safetensors" },
    ],
  };
}

/** One finished run, read and picked, as the grid leaves it. */
async function pickWalter(store) {
  store.runs = [walter()];
  store.jobs = [];
  store.jobsLoaded = true;
  store.chosen = ["run:walter_v2"];
  await nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("a session reset and the per-checkpoint picks", () => {
  // The single run's steps fill once per run name, so a reload does not re-tick
  // a checkpoint the reader excluded. A reset must not leave that name behind:
  // the next pick of the same run, in whatever tick it lands, starts full.
  it.each([
    ["after the reset is seen", true],
    ["in the same tick as the reset", false],
  ])("ticks every checkpoint of a run picked again %s", async (_, settle) => {
    const store = useTrainingRunsStore();
    await pickWalter(store);
    expect(store.chosenSteps).toEqual([500, 1000]);
    store.chosenSteps = [1000];

    notifySessionReset("test");
    if (settle) await nextTick();
    await pickWalter(store);

    expect(store.chosenSteps).toEqual([500, 1000]);
  });
});
