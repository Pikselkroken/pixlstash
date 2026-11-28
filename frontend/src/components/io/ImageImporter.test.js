// A batch whose last byte has gone out is the server's to finish. Unpacking a
// 12 GB zip takes minutes with no upload progress, and re-sending it staged
// every file in it once per attempt.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../../api/pictureImport", () => ({
  openStagingSession: vi.fn(),
  stageFiles: vi.fn(),
  commitStaging: vi.fn(),
  cancelStaging: vi.fn(),
  fetchStagingStatus: vi.fn(),
}));

import ImageImporter from "./ImageImporter.vue";
import * as api from "../../api/pictureImport";
import { useImportArrivalsStore } from "../../stores/useImportArrivalsStore";
import { useTasksStore } from "../../stores/useTasksStore";

let wrapper = null;

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  URL.createObjectURL = vi.fn(() => null);
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(console, "info").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  api.openStagingSession.mockResolvedValue({ stagingId: "s1" });
  // Never reached: the tests stop at the staging request.
  api.commitStaging.mockReturnValue(new Promise(() => {}));
  api.cancelStaging.mockResolvedValue({});
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllMocks();
  vi.useRealTimers();
});

/** A request that never answers, rejecting as axios does when aborted. */
function hanging(signal) {
  return new Promise((_, reject) => {
    signal.addEventListener("abort", () => {
      const err = new Error("canceled");
      err.code = "ERR_CANCELED";
      reject(err);
    });
  });
}

/** A staging request that reports progress up to `sent` bytes, then hangs. */
function hangAfter(sent, total = 100) {
  api.stageFiles.mockImplementation(({ onUploadProgress, signal }) => {
    onUploadProgress({ loaded: sent, total });
    return hanging(signal);
  });
}

async function startImport() {
  wrapper = mount(ImageImporter);
  const file = new File([new Uint8Array(100)], "big.zip");
  wrapper.vm.startImport([file]);
  await vi.advanceTimersByTimeAsync(0);
}

describe("ImageImporter staging", () => {
  it("waits for the server once the body is sent, with no re-send", async () => {
    hangAfter(100);
    await startImport();

    // Far past the 15 s stall window and every retry's sleep.
    await vi.advanceTimersByTimeAsync(50_000);
    expect(api.stageFiles).toHaveBeenCalledTimes(1);
    expect(api.stageFiles.mock.calls[0][0].signal.aborted).toBe(false);
  });

  it("does not re-send a batch the server failed after receiving", async () => {
    api.stageFiles.mockImplementation(({ onUploadProgress }) => {
      onUploadProgress({ loaded: 100, total: 100 });
      return Promise.reject(new Error("Network Error"));
    });
    await startImport();

    await vi.advanceTimersByTimeAsync(10_000);
    expect(api.stageFiles).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("import-error")?.[0]?.[0].message).toBe(
      "Network Error",
    );
    expect(api.cancelStaging).toHaveBeenCalledWith(
      expect.objectContaining({ stagingId: "s1" }),
    );
  });

  it("restarts the deadline for the server once the body is sent", async () => {
    // A slow upload: the last byte goes out at 55 s of a 60 s deadline.
    api.stageFiles.mockImplementation(({ onUploadProgress, signal }) => {
      for (let t = 0; t <= 50; t += 10) {
        setTimeout(() => onUploadProgress({ loaded: t, total: 100 }), t * 1000);
      }
      setTimeout(() => onUploadProgress({ loaded: 100, total: 100 }), 55_000);
      return hanging(signal);
    });
    await startImport();

    await vi.advanceTimersByTimeAsync(100_000);
    expect(api.stageFiles).toHaveBeenCalledTimes(1);
    expect(api.stageFiles.mock.calls[0][0].signal.aborted).toBe(false);
  });

  it("knows the body is sent when the browser reports no total", async () => {
    hangAfter(100, null);
    await startImport();

    await vi.advanceTimersByTimeAsync(50_000);
    expect(api.stageFiles).toHaveBeenCalledTimes(1);
    expect(api.stageFiles.mock.calls[0][0].signal.aborted).toBe(false);
  });

  it("waits out a server import that runs past ten minutes", async () => {
    api.stageFiles.mockImplementation(({ onUploadProgress }) => {
      onUploadProgress({ loaded: 100, total: 100 });
      return Promise.resolve({ staged: 1, received: [], skipped: [] });
    });
    api.commitStaging.mockResolvedValue({ taskId: "t1", stagedCount: 1 });
    let polls = 0;
    api.fetchStagingStatus.mockImplementation(async () => ({
      stage: ++polls > 900 ? "completed" : "importing",
      total: 1,
      processed: 0,
      importedCount: 1,
      outcomes: [],
    }));
    await startImport();

    await vi.advanceTimersByTimeAsync(950_000);
    expect(wrapper.emitted("import-error")).toBeUndefined();
    expect(wrapper.emitted("import-finished")?.[0]?.[0].importedCount).toBe(1);
  });

  it("still retries an upload that stalls before its last byte", async () => {
    hangAfter(50);
    await startImport();

    await vi.advanceTimersByTimeAsync(20_000);
    expect(api.stageFiles.mock.calls[0][0].signal.aborted).toBe(true);
    expect(api.stageFiles).toHaveBeenCalledTimes(2);
  });

  it("cancel while checking keeps what arrived and drops the rest", async () => {
    let keys = [];
    api.stageFiles.mockImplementation(async (args) => {
      keys = args.keys;
      args.onUploadProgress({ loaded: 200, total: 200 });
      return { staged: 2, received: [], skipped: [] };
    });
    api.commitStaging.mockResolvedValue({ taskId: "t1", stagedCount: 2 });
    let stopped = false;
    api.cancelStaging.mockImplementation(async () => {
      stopped = true;
      return {};
    });
    api.fetchStagingStatus.mockImplementation(async ({ outcomesFrom }) => ({
      stage: stopped ? "completed" : "importing",
      total: 2,
      processed: 1,
      importedCount: 1,
      cancelledCount: stopped ? 1 : null,
      outcomes:
        outcomesFrom === 0 ? [{ key: keys[0], outcome: "new" }] : [],
    }));
    wrapper = mount(ImageImporter);
    wrapper.vm.startImport([
      new File([new Uint8Array(100)], "a.png"),
      new File([new Uint8Array(100)], "b.png"),
    ]);
    await vi.advanceTimersByTimeAsync(1500);

    const arrivals = useImportArrivalsStore();
    const tasks = useTasksStore();
    const run = arrivals.runs[0];
    expect(run.stage).toBe("checking");
    expect(tasks.importRuns[run.id].abortable).toBe(true);

    tasks.abortImportRun(run.id);
    expect(run.stopping).toBe(true);
    expect(api.cancelStaging).toHaveBeenCalledWith(
      expect.objectContaining({ stagingId: "s1" }),
    );
    await vi.advanceTimersByTimeAsync(2000);

    expect(run.stage).toBe("landed");
    expect(run.tiles.map((t) => [t.name, t.state])).toEqual([
      ["a.png", "landed"],
    ]);
    expect(run.counts.cancelled).toBe(1);
    expect(wrapper.emitted("import-cancelled")).toBeUndefined();
    expect(wrapper.emitted("import-finished")?.[0]?.[0].importedCount).toBe(1);
  });
});
