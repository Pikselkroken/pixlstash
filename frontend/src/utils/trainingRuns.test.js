// What a training-run card says about how far a run got (#2169).

import { describe, expect, it } from "vitest";

import { isBusyJob, jobStatus, progressOf, stepsOf } from "./trainingRuns.js";

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
