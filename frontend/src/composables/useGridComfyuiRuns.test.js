// What the grid keeps of a ComfyUI run (#1862): the lightbox's progress for
// the runs started from it, and the step to the new picture. The run itself is
// the backend's, read from the tasks store's poll.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectScope, nextTick, ref } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";

import { useGridComfyuiRuns } from "./useGridComfyuiRuns";
import { useTasksStore } from "../stores/useTasksStore";
import { getPictureMetadata } from "../api/pictures";
import { listStackPictures } from "../api/stacks";
import { getRunOutcome } from "../api/workflows";

vi.mock("../api/pictures", () => ({ getPictureMetadata: vi.fn() }));
vi.mock("../api/stacks", () => ({ listStackPictures: vi.fn() }));
vi.mock("../api/workflows", () => ({ getRunOutcome: vi.fn() }));
vi.mock("../api/workers", () => ({ getWorkerProgress: vi.fn() }));
vi.mock("../api/comfyui", () => ({ abortRun: vi.fn() }));

let scope;
let tasks;

function listed(...runs) {
  tasks.comfyuiRuns = runs.map(([prompt_id, status = "queued", percent = 0]) => ({
    prompt_id,
    status,
    percent,
    label: "Flux",
  }));
  return flushPromises();
}

function mountRuns(overrides = {}) {
  const grid = {
    lightboxOpen: ref(false),
    allGridImages: ref([]),
    lastFetchedGridImages: ref([]),
    getPictureStackId: (img) => (img?.stack_id != null ? String(img.stack_id) : null),
    selectNewestStackMember: (members) => members[members.length - 1] ?? null,
    showRunResult: vi.fn(),
    refreshSidebar: vi.fn(),
    ...overrides,
  };
  scope = effectScope();
  const runs = scope.run(() => useGridComfyuiRuns(grid));
  return { grid, ...runs };
}

beforeEach(() => {
  setActivePinia(createPinia());
  tasks = useTasksStore();
  vi.clearAllMocks();
});

afterEach(() => {
  scope?.stop();
  vi.useRealTimers();
});

describe("the lightbox's progress", () => {
  it("is idle until a run is started from the grid", async () => {
    const { progress } = mountRuns();
    // Somebody else's run: in the Tasks tab, and none of the lightbox's.
    await listed(["other", "running", 60]);
    expect(progress.value).toMatchObject({ visible: false, status: "idle" });
    expect(getRunOutcome).not.toHaveBeenCalled();
  });

  it("follows the poll: queued, then the running run's percent", async () => {
    const { progress, follow } = mountRuns();
    follow({ prompts: [{ prompt_id: "p-1" }, { prompt_id: "p-2" }] });
    expect(progress.value).toEqual({
      visible: true,
      status: "queued",
      percent: 0,
      message: "ComfyUI queued...",
    });

    await listed(["other", "running", 90], ["p-1", "running", 40], ["p-2"]);
    expect(progress.value).toEqual({
      visible: true,
      status: "running",
      percent: 40,
      message: "ComfyUI running...",
    });
    expect(getRunOutcome).not.toHaveBeenCalled();
  });

  it("says complete once every run has left the poll, then clears", async () => {
    vi.useFakeTimers();
    getRunOutcome.mockResolvedValue({ status: "completed", picture_ids: [9] });
    const { progress, follow, grid } = mountRuns();
    follow({ prompts: [{ prompt_id: "p-1" }, { prompt_id: "p-2" }] });

    await listed(["p-2", "running", 10]);
    expect(getRunOutcome.mock.calls).toEqual([["p-1"]]);
    // One of two is over: the bar is still the batch's.
    expect(progress.value.status).toBe("running");

    await listed();
    expect(progress.value).toEqual({
      visible: true,
      status: "completed",
      percent: 100,
      message: "ComfyUI complete",
    });
    expect(grid.refreshSidebar).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(1500);
    await nextTick();
    expect(progress.value).toMatchObject({ visible: false, status: "idle" });
  });

  it("follows a held run by its run id: it is not asked after while listed, and held is not ended", async () => {
    getRunOutcome.mockResolvedValue({ run_id: "run-h", prompt_id: null, status: "waiting" });
    const { progress, follow } = mountRuns();
    // Held at submit: no prompt id, so it is followed by its run id alone.
    follow({ prompts: [{ run_id: "run-h", prompt_id: null }] });
    tasks.comfyuiRuns = [{ run_id: "run-h", prompt_id: null, label: "x", status: "waiting", percent: 0 }];
    await nextTick();
    expect(getRunOutcome).not.toHaveBeenCalled();
    expect(progress.value.status).toBe("queued");

    // Left the poll while still held: asked, and a held answer is not an ending.
    tasks.comfyuiRuns = [];
    await nextTick();
    await flushPromises();
    expect(getRunOutcome.mock.calls).toEqual([["run-h"]]);
    expect(progress.value.status).toBe("queued");
  });

  it("waits when the poll that missed a run was read before it was queued", async () => {
    getRunOutcome.mockResolvedValue({ status: "running" });
    const { progress, follow } = mountRuns();
    follow({ prompts: [{ prompt_id: "p-1" }] });
    await listed();
    expect(progress.value.status).toBe("queued");

    // Asked again on the next poll that does not list it.
    getRunOutcome.mockResolvedValue({ status: "completed" });
    await listed();
    expect(progress.value.status).toBe("completed");
  });

  it("gives a failure's sentence to the Edit tab, and shows no bar for it", async () => {
    getRunOutcome.mockResolvedValue({
      status: "failed",
      message: "KSampler failed: out of memory",
    });
    const { progress, follow } = mountRuns();
    follow({ prompts: [{ prompt_id: "p-1" }] });
    await listed();
    expect(progress.value).toEqual({
      visible: false,
      status: "failed",
      percent: 0,
      message: "KSampler failed: out of memory",
    });

    // The next run starts clean.
    follow({ prompts: [{ prompt_id: "p-2" }] });
    expect(progress.value.status).toBe("queued");
  });

  // The backend restarted mid-run: nothing imported the outputs, so "complete"
  // would be a guess. The Edit tab gets a sentence that says what happened.
  it("says a run the backend no longer knows was lost, not finished", async () => {
    getRunOutcome.mockRejectedValue({ response: { status: 404 } });
    const { progress, follow, grid } = mountRuns({ lightboxOpen: ref(true) });
    follow({ prompts: [{ prompt_id: "p-1" }], pictureId: 7 });
    await listed();
    expect(progress.value).toMatchObject({
      visible: false,
      status: "failed",
      message: "PixlStash lost track of this run. Check ComfyUI for its pictures.",
    });
    expect(listStackPictures).not.toHaveBeenCalled();
    expect(grid.showRunResult).not.toHaveBeenCalled();
  });

  it("keeps a failure when another run is started before the batch is over", async () => {
    getRunOutcome.mockImplementation(async (id) =>
      id === "p-1"
        ? { status: "failed", message: "KSampler failed" }
        : { status: "completed" },
    );
    const { progress, follow } = mountRuns();
    follow({ prompts: [{ prompt_id: "p-1" }, { prompt_id: "p-2" }] });
    await listed(["p-2", "running", 5]);
    follow({ prompts: [{ prompt_id: "p-3" }] });
    await listed();
    expect(progress.value).toMatchObject({
      status: "failed",
      message: "KSampler failed",
    });
  });

  it("keeps following a run whose ending could not be read", async () => {
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});
    getRunOutcome.mockRejectedValue({ response: { status: 503 } });
    const { progress, follow } = mountRuns();
    follow({ prompts: [{ prompt_id: "p-1" }] });
    await listed();
    expect(progress.value.status).toBe("queued");
    expect(warned).toHaveBeenCalled();
    warned.mockRestore();
  });
});

describe("the step to the new picture", () => {
  const finishRunOn = async (runs, pictureId) => {
    getRunOutcome.mockResolvedValue({ status: "completed" });
    runs.follow({ prompts: [{ prompt_id: "p-1" }], pictureId });
    await listed();
    await flushPromises();
  };

  it("offers an open lightbox the newest picture of the source's stack", async () => {
    // The grid's copy of the source predates the run: it is in no stack yet.
    getPictureMetadata.mockResolvedValue({ stack_id: 5 });
    listStackPictures.mockResolvedValue([{ id: 7 }, { id: 31 }]);
    const runs = mountRuns({
      lightboxOpen: ref(true),
      lastFetchedGridImages: ref([{ id: 7 }]),
    });
    await finishRunOn(runs, 7);
    expect(getPictureMetadata).toHaveBeenCalledWith(7);
    expect(listStackPictures).toHaveBeenCalledWith("5");
    // The lightbox decides whether the reader is still there to be moved.
    expect(runs.grid.showRunResult.mock.calls).toEqual([[31, 7, "5"]]);
  });

  it("reads nothing for a closed lightbox", async () => {
    listStackPictures.mockResolvedValue([{ id: 7 }, { id: 31 }]);
    const closed = mountRuns({
      lastFetchedGridImages: ref([{ id: 7, stack_id: 5 }]),
    });
    await finishRunOn(closed, 7);
    expect(closed.grid.showRunResult).not.toHaveBeenCalled();
    expect(listStackPictures).not.toHaveBeenCalled();
  });

  it("offers nothing when the stack has no newer member, or the run was not stacked", async () => {
    const grid = {
      lightboxOpen: ref(true),
      lastFetchedGridImages: ref([{ id: 7, stack_id: 5 }]),
    };
    listStackPictures.mockResolvedValue([{ id: 3 }, { id: 7 }]);
    const same = mountRuns(grid);
    await finishRunOn(same, 7);
    expect(same.grid.showRunResult).not.toHaveBeenCalled();
    scope.stop();

    getPictureMetadata.mockResolvedValue({ stack_id: null });
    const unstacked = mountRuns({ lightboxOpen: ref(true) });
    await finishRunOn(unstacked, 7);
    expect(unstacked.grid.showRunResult).not.toHaveBeenCalled();
  });

  it("stays put for a run with no source picture, and for one that failed", async () => {
    listStackPictures.mockResolvedValue([{ id: 7 }, { id: 31 }]);
    const grid = {
      lightboxOpen: ref(true),
      lastFetchedGridImages: ref([{ id: 7, stack_id: 5 }]),
    };
    // The Edit tab's run: handed on without a picture, so the lightbox stays.
    const unsourced = mountRuns(grid);
    await finishRunOn(unsourced, null);
    expect(unsourced.grid.showRunResult).not.toHaveBeenCalled();
    scope.stop();

    const failed = mountRuns(grid);
    getRunOutcome.mockResolvedValue({ status: "failed", message: "boom" });
    failed.follow({ prompts: [{ prompt_id: "p-9" }], pictureId: 7 });
    await listed();
    await flushPromises();
    expect(failed.grid.showRunResult).not.toHaveBeenCalled();
  });
});
