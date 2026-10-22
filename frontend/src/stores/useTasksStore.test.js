// The ComfyUI runs in the tasks store (#1862): they are whatever the backend's
// poll last reported, so a run's row is there for a tab that did not start it
// and survives every view switch, and its abort asks the backend.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises } from "@vue/test-utils";

import { useTasksStore } from "./useTasksStore";
import { getWorkerProgress } from "../api/workers";
import { abortRun } from "../api/comfyui";
import { cancelModelDownload } from "../api/modelDownloads";
import { useNoticeStore } from "./useNoticeStore";

vi.mock("../api/workers", () => ({ getWorkerProgress: vi.fn() }));
vi.mock("../api/comfyui", () => ({ abortRun: vi.fn(async () => ({})) }));
vi.mock("../api/modelDownloads", () => ({
  cancelModelDownload: vi.fn(async () => ({ cancelled: true })),
}));

const RUN = {
  prompt_id: "p-1",
  label: "Night market",
  workflow_id: "auto:a",
  status: "running",
  percent: 25,
};

function answer(runs) {
  return { status: "success", workers: {}, process: {}, ...(runs ? { runs } : {}) };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe("the runs in flight", () => {
  it("are read from the poll, and gone when the poll stops listing them", async () => {
    const store = useTasksStore();
    getWorkerProgress.mockResolvedValue(answer([RUN]));
    store.startPolling();
    await flushPromises();
    try {
      expect(store.comfyuiRuns).toEqual([RUN]);
      expect(store.activeEntries).toEqual([
        {
          kind: "comfyui",
          key: "comfyui",
          run: {
            status: "running",
            percent: 25,
            label: "Night market",
            message: "ComfyUI running…",
          },
        },
      ]);
      expect(store.hasActiveTasks).toBe(true);

      // The run ended: the backend no longer lists it.
      getWorkerProgress.mockResolvedValue(answer([]));
      store.nudge();
      await vi.waitFor(() => expect(store.comfyuiRuns).toEqual([]));
      expect(store.activeEntries).toEqual([]);

      // A backend that says nothing of runs (an older one) lists none.
      getWorkerProgress.mockResolvedValue(answer([RUN]));
      store.nudge();
      await vi.waitFor(() => expect(store.comfyuiRuns).toEqual([RUN]));
      getWorkerProgress.mockResolvedValue(answer(null));
      store.nudge();
      await vi.waitFor(() => expect(store.comfyuiRuns).toEqual([]));
    } finally {
      store.stopPolling();
    }
  });

  it("are stopped by the backend, and read again at once", async () => {
    const store = useTasksStore();
    getWorkerProgress.mockResolvedValue(answer([RUN]));
    store.startPolling();
    await flushPromises();
    try {
      const polls = getWorkerProgress.mock.calls.length;
      getWorkerProgress.mockResolvedValue(answer([]));
      await store.abortComfyuiRuns();
      expect(abortRun).toHaveBeenCalledTimes(1);
      await vi.waitFor(() =>
        expect(getWorkerProgress.mock.calls.length).toBeGreaterThan(polls),
      );
      await vi.waitFor(() => expect(store.comfyuiRuns).toEqual([]));
      expect(useNoticeStore().notices).toEqual([]);
    } finally {
      store.stopPolling();
    }
  });

  it("say so when the backend could not stop them", async () => {
    const warned = vi.spyOn(console, "warn").mockImplementation(() => {});
    abortRun.mockRejectedValueOnce(new Error("ComfyUI unreachable"));
    await useTasksStore().abortComfyuiRuns();
    expect(useNoticeStore().notices.map((notice) => notice.text)).toEqual([
      "ComfyUI could not be told to stop.",
    ]);
    warned.mockRestore();
  });
});

describe("model downloads", () => {
  const RUNNING = {
    id: "d-1",
    file: "flux1-dev.safetensors",
    status: "running",
    received: 1024 ** 3,
    total: 4 * 1024 ** 3,
    percent: 25,
    error: null,
  };

  async function poll(store, downloads) {
    getWorkerProgress.mockResolvedValue({ ...answer([]), downloads });
    const asked = getWorkerProgress.mock.calls.length;
    store.nudge();
    await vi.waitFor(() =>
      expect(getWorkerProgress.mock.calls.length).toBeGreaterThan(asked),
    );
    await flushPromises();
  }

  it("are a row each while running, with a cancel that asks the backend", async () => {
    const store = useTasksStore();
    getWorkerProgress.mockResolvedValue({ ...answer([]), downloads: [RUNNING] });
    store.startPolling();
    await flushPromises();
    try {
      expect(store.activeEntries).toEqual([
        {
          kind: "download",
          key: "download-d-1",
          id: "d-1",
          run: {
            status: "running",
            percent: 25,
            label: "flux1-dev.safetensors",
            message: "Downloading… 1.0 GB of 4.0 GB",
          },
        },
      ]);
      expect(store.runningDownloadFiles.has("flux1-dev.safetensors")).toBe(true);
      await store.cancelDownload("d-1");
      expect(cancelModelDownload).toHaveBeenCalledWith("d-1");
    } finally {
      store.stopPolling();
    }
  });

  it("tell how they ended once, and only an ending this tab saw", async () => {
    const store = useTasksStore();
    const notices = useNoticeStore();
    getWorkerProgress.mockResolvedValue({
      ...answer([]),
      // Ended before this tab first looked: old news, not told.
      downloads: [RUNNING, { ...RUNNING, id: "d-0", status: "failed" }],
    });
    store.startPolling();
    await flushPromises();
    try {
      expect(notices.notices).toEqual([]);
      const done = { ...RUNNING, status: "done", received: RUNNING.total };
      await poll(store, [done]);
      expect(store.activeEntries).toEqual([]);
      expect(store.downloadsLanded).toBe(1);
      expect(notices.notices.map((n) => n.text)).toEqual([
        "Downloaded flux1-dev.safetensors.",
      ]);
      // Still listed on the next poll: not told twice.
      await poll(store, [done]);
      expect(notices.notices).toHaveLength(1);
      expect(store.downloadsLanded).toBe(1);

      store.downloadStarted({ ...RUNNING, id: "d-2" });
      await poll(store, [{ ...RUNNING, id: "d-2", status: "failed", error: "404" }]);
      expect(notices.notices.at(-1).text).toBe(
        "Could not download flux1-dev.safetensors: 404",
      );
      expect(store.downloadsLanded).toBe(1);
    } finally {
      store.stopPolling();
    }
  });
});
