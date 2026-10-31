// What a training-run card says about how far a run got (#2169).

import { describe, expect, it } from "vitest";

import {
  folderRunLive,
  isBusyJob,
  jobStatus,
  progressOf,
  stepsOf,
} from "./trainingRuns.js";

const saved = (...steps) => ({
  checkpoints: steps.map((step) => ({ filename: `r_${step}`, step })),
});

describe("a run's steps", () => {
  it("counts a live run against what it was asked for", () => {
    const card = { live: true, job: { status: "running", step: 1200, total_steps: 3000 }, run: null };
    expect(stepsOf(card)).toBe("1,200 / 3,000 steps");
    expect(progressOf(card)).toBe(40);
  });

  it("calls a run with its final file finished, at the steps asked for", () => {
    const card = { live: false, job: null, run: { ...saved(500, null), total_steps: 1000 } };
    expect(stepsOf(card)).toBe("1,000 steps");
    expect(progressOf(card)).toBe(100);
  });

  it("says a stopped run got part of the way", () => {
    const card = {
      live: false,
      job: { status: "stopped", step: 800, total_steps: 2000 },
      run: saved(500),
    };
    expect(stepsOf(card)).toBe("800 / 2,000 steps · stopped");
    expect(progressOf(card)).toBe(40);
  });

  it("draws no bar when how far it got is not known", () => {
    const card = { live: false, job: null, run: saved(500) };
    expect(stepsOf(card)).toBe("500 steps");
    expect(progressOf(card)).toBeNull();
  });
});

describe("a run's status", () => {
  it("says a run stopped before its first step is stopped, without a step", () => {
    expect(jobStatus({ status: "stopped", step: null })).toBe("Stopped");
    expect(jobStatus({ status: "stopped", step: 800 })).toBe("Stopped at step 800");
    expect(jobStatus({ status: "running", step: null, total_steps: 10 })).toBe(
      "Training 0 / 10",
    );
  });
});

describe("whether the worker is done with a job (#2291)", () => {
  const DONE_AT = "2026-10-26T09:00:00+00:00";

  it("is busy while it trains, and while a trained run is not yet imported", () => {
    for (const status of ["queued", "running", "stopping"]) {
      expect(isBusyJob({ status })).toBe(true);
    }
    expect(isBusyJob({ status: "completed", completed_at: null })).toBe(true);
  });

  it("is done once it has a finish time, or ended any other way", () => {
    expect(isBusyJob({ status: "completed", completed_at: DONE_AT })).toBe(false);
    expect(isBusyJob({ status: "stopped", completed_at: null })).toBe(false);
    expect(isBusyJob({ status: "error", completed_at: null })).toBe(false);
    expect(isBusyJob(null)).toBe(false);
  });

  it("names what a trained run is still doing", () => {
    const job = { status: "completed", completed_at: null };
    expect(jobStatus(job)).toBe("Finishing");
    expect(jobStatus({ ...job, phase: "previews" })).toBe("Generating previews");
    expect(jobStatus({ ...job, phase: "importing" })).toBe("Importing to the shelf");
    expect(jobStatus({ ...job, completed_at: DONE_AT })).toBe("Trained by PixlStash");
  });
});

describe("a run trained elsewhere, as the run page draws it", () => {
  const run = {
    name: "walter_v2",
    checkpoints: [
      { filename: "walter_v2_000000500.safetensors", step: 500 },
      { filename: "walter_v2_000001000.safetensors", step: 1000 },
      { filename: "walter_v2.safetensors", step: null },
    ],
    samples: [
      { filename: "a_0_0.jpg", step: 0, index: 0 },
      { filename: "a_500_0.jpg", step: 500, index: 0 },
      { filename: "a_500_1.jpg", step: 500, index: 1 },
      { filename: "a_0_2.jpg", step: 0, index: 2 },
    ],
    trigger_words: ["W4lt"],
    sample_prompts: ["w4lt on a bike", "a red bicycle"],
    total_steps: 1200,
  };

  it("lays its previews on the steps it saved or sampled at", () => {
    const live = folderRunLive(run, 1);
    expect(live.columns).toEqual([0, 500, 1000]);
    // The bare final says it reached the steps it was asked for.
    expect(live.step).toBe(1200);
    expect(live.run_folder).toEqual({ folder_id: 1, run_name: "walter_v2" });
    expect(live.rows.map((row) => row.cells.map((c) => c.filename))).toEqual([
      ["a_0_0.jpg", "a_500_0.jpg", null],
      [null, "a_500_1.jpg", null],
      ["a_0_2.jpg", null, null],
    ]);
    expect(live.rows[0].cells[2].state).toBe("missing");
  });

  it("claims the trigger word only where a prompt says either way", () => {
    const rows = folderRunLive(run, 1).rows;
    expect(rows.map((row) => row.with_trigger)).toEqual([true, false, null]);
    const untriggered = folderRunLive({ ...run, trigger_words: [] }, 1).rows;
    expect(untriggered.map((row) => row.with_trigger)).toEqual([null, null, null]);
  });

  it("stops at its last save without a final file, and records nothing it did not see", () => {
    const live = folderRunLive({ ...run, checkpoints: run.checkpoints.slice(0, 2) }, 1);
    expect(live.step).toBe(1000);
    expect(live.loss_curve).toEqual([]);
    expect(live.dataset_picture_ids).toEqual([]);
    expect(isBusyJob(live)).toBe(false);
  });
});
