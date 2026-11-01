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

describe("the cards of jobs with no folder", () => {
  const job = (uuid, status, run_name = null, started_at = null) => ({
    uuid,
    name: uuid,
    status,
    run_name,
    started_at,
    created_at: "2026-10-01T00:00:00Z",
  });
  const STARTED = "2026-10-01T00:01:00Z";

  // A run that failed or was cancelled before ai-toolkit made a folder has
  // nothing on disk to draw, so its job is its only card (#2293); one that
  // finished, or ended after it had a folder, is drawn from the folder or not
  // at all.
  it("keeps a card for a run that ended before it had a folder", () => {
    const store = useTrainingRunsStore();
    store.runs = [walter()];
    store.jobs = [
      job("queued", "queued"),
      job("dataset-failed", "error"),
      job("cancelled", "stopped"),
      // Finished: imported, so it has its finish time (#2291).
      { ...job("finished", "completed"), completed_at: STARTED },
      job("imported-away", "error", "gone_v1", STARTED),
      // A started run under an output root since moved names no run.
      job("root-moved", "stopped", null, STARTED),
      job("walter", "error", "walter_v2", STARTED),
    ];

    const keys = store.cards.map((card) => card.key);
    expect(keys).toEqual([
      "job:queued",
      "job:dataset-failed",
      "job:cancelled",
      "run:walter_v2",
    ]);
    const failed = store.cards.find((card) => card.key === "job:dataset-failed");
    expect(failed.live).toBe(false);
    expect(failed.run).toBe(null);
  });
});

describe("a card's name", () => {
  it("is its job's name, and the folder's for a run trained elsewhere", () => {
    const store = useTrainingRunsStore();
    store.runs = [walter(), { name: "elsewhere_v1", checkpoints: [] }];
    store.jobs = [
      {
        uuid: "abcd1234",
        name: "Walter Z-Image Turbo #2",
        status: "completed",
        run_name: "walter_v2",
        started_at: "2026-10-01T00:01:00Z",
        completed_at: "2026-10-01T01:00:00Z",
        created_at: "2026-10-01T00:00:00Z",
      },
    ];

    const names = Object.fromEntries(store.cards.map((c) => [c.key, c.name]));
    expect(names).toEqual({
      "run:walter_v2": "Walter Z-Image Turbo #2",
      "run:elsewhere_v1": "elsewhere_v1",
    });
  });
});
