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
import { cancelLease, freeComfyui, runLeaseAnyway } from "../api/resources";
import { useNoticeStore } from "./useNoticeStore";

vi.mock("../api/workers", () => ({ getWorkerProgress: vi.fn() }));
vi.mock("../api/comfyui", () => ({ abortRun: vi.fn(async () => ({})) }));
vi.mock("../api/modelDownloads", () => ({
  cancelModelDownload: vi.fn(async () => ({ cancelled: true })),
}));
vi.mock("../api/resources", () => ({
  runLeaseAnyway: vi.fn(async () => ({ status: "success" })),
  cancelLease: vi.fn(async () => ({ status: "success" })),
  freeComfyui: vi.fn(async () => ({ status: "success", freed_mb: 100 })),
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
          key: "run:p-1",
          runId: "p-1",
          figureMb: null,
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

// ── The GPU queue: what the poll's `resources` says, placed in groups ─────────

const GPU = {
  uuid: "GPU-1",
  name: "NVIDIA GeForce RTX 4090",
  total_mb: 24576,
  used_mb: 13000,
  free_mb: 11576,
  reserved_mb: 0,
  budget_mb: 10000,
  measurable: true,
  pixlstash: true,
};

function lease(over) {
  return {
    id: "lease-x",
    tenant: "pixlstash",
    label: "Tagging",
    kind: "light",
    state: "granted",
    device: "GPU-1",
    need_mb: 2000,
    ram_mb: 0,
    reserved_mb: 0,
    held_mb: 0,
    peak_mb: null,
    since: "2026-10-22T10:00:00Z",
    worker_type: null,
    run_id: null,
    job_id: null,
    on_cpu: false,
    warnings: [],
    waiting_reason: null,
    ...over,
  };
}

function resourcesAnswer(over = {}) {
  return {
    enforcing: true,
    devices: [GPU],
    ram: { total_mb: 65536, available_mb: 40000 },
    holders: [],
    leases: [],
    preempted: [],
    summary: { state: "idle", headline: "" },
    ...over,
  };
}

async function pollWith(progress) {
  const store = useTasksStore();
  getWorkerProgress.mockResolvedValue({ status: "success", workers: {}, process: {}, ...progress });
  store.startPolling();
  await flushPromises();
  return store;
}

describe("the GPU queue", () => {
  it("is null on a server that sends no resources, and nothing is folded or barred", async () => {
    const store = await pollWith({ runs: [] });
    try {
      expect(store.resources).toBeNull();
      expect(store.vramBars).toEqual([]);
      expect(store.pixlstashEntry).toBeNull();
      expect(store.waitingEntries).toEqual([]);
    } finally {
      store.stopPolling();
    }
  });

  it("puts a waiting ComfyUI run in Waiting, joined to its lease, in queue order", async () => {
    const waiting = {
      run_id: "run-b",
      prompt_id: null,
      label: "Klein inpaint",
      workflow_id: "auto:k",
      status: "waiting",
      percent: 0,
    };
    const held = {
      run_id: "run-a",
      prompt_id: "p-9",
      label: "Portrait upscale",
      workflow_id: "auto:p",
      status: "running",
      percent: 40,
    };
    const store = await pollWith({
      runs: [held, waiting],
      resources: resourcesAnswer({
        leases: [
          lease({ id: "lease-run-a", tenant: "comfyui", label: "ComfyUI 'Portrait upscale'", kind: "exclusive", run_id: "run-a", held_mb: 9800 }),
          lease({
            id: "lease-run-b",
            tenant: "comfyui",
            label: "ComfyUI 'Klein inpaint'",
            kind: "heavy",
            state: "waiting",
            device: null,
            need_mb: 14600,
            run_id: "run-b",
            waiting_reason: {
              code: "vram",
              text: "Needs 14.6 GB; 11.5 GB is free.",
              need_mb: 14600,
              free_mb: 11500,
              short_by_mb: 3100,
              position: 1,
              blocked_by: [],
              actions: ["run_anyway", "cancel"],
            },
          }),
        ],
      }),
    });
    try {
      // The running run is a row of its own, with the figure its lease holds.
      expect(store.runningEntries.filter((e) => e.kind === "comfyui")).toEqual([
        expect.objectContaining({ key: "run:run-a", figureMb: 9800 }),
      ]);
      // The waiting run is in Waiting and not in Running, once: through its lease.
      expect(store.waitingEntries).toEqual([
        expect.objectContaining({
          key: "lease:lease-run-b",
          runId: "run-b",
          tenant: "comfyui",
          label: "ComfyUI 'Klein inpaint'",
          needMb: 14600,
          text: "Needs 14.6 GB; 11.5 GB is free.",
          actions: ["run_anyway", "cancel"],
        }),
      ]);
      expect(store.activeEntries.map((e) => e.key)).toEqual([
        "run:run-a",
        "lease:lease-run-b",
      ]);
    } finally {
      store.stopPolling();
    }
  });

  it("gives a waiting run with no lease yet its own waiting row", async () => {
    const store = await pollWith({
      runs: [{ run_id: "run-z", prompt_id: null, label: "Upscale", status: "waiting", percent: 0 }],
      resources: resourcesAnswer(),
    });
    try {
      expect(store.waitingEntries).toEqual([
        expect.objectContaining({ key: "run:run-z", lease: null, actions: [] }),
      ]);
      expect(store.runningEntries.filter((e) => e.kind === "comfyui")).toEqual([]);
    } finally {
      store.stopPolling();
    }
  });

  it("orders waiting leases by their queue position, whatever order they came in", async () => {
    const waitingAt = (id, position) =>
      lease({
        id,
        tenant: "aitoolkit",
        label: id,
        state: "waiting",
        device: null,
        waiting_reason: { code: "aitoolkit_busy", text: "", need_mb: 1, free_mb: 0, short_by_mb: 1, position, blocked_by: [], actions: [] },
      });
    const store = await pollWith({
      resources: resourcesAnswer({ leases: [waitingAt("third", 3), waitingAt("first", 1), waitingAt("second", 2)] }),
    });
    try {
      expect(store.waitingEntries.map((e) => e.label)).toEqual(["first", "second", "third"]);
    } finally {
      store.stopPolling();
    }
  });

  it("folds PixlStash's granted leases into one row, summing what they hold", async () => {
    const store = await pollWith({
      workers: {
        TagTask: { label: "pictures_tagged", current: 3, total: 10, running: true, active: true },
      },
      resources: resourcesAnswer({
        leases: [
          lease({ id: "a", label: "Tagging", worker_type: "TagTask", held_mb: 2000 }),
          lease({ id: "b", label: "OCR", held_mb: 600 }),
          lease({ id: "c", label: "Quality scores", held_mb: 400, on_cpu: true }),
        ],
        preempted: [{ worker_type: "FaceTask", caused_by: "lease-z", since: "2026-10-22T10:01:00Z", text: "Stopped to make room" }],
      }),
    });
    try {
      const fold = store.pixlstashEntry;
      expect(fold.kind).toBe("pixlstash");
      // Three granted leases and one stopped task: four tasks, and only the VRAM of the leases.
      expect(fold.count).toBe(4);
      expect(fold.mb).toBe(3000);
      expect(fold.children.map((c) => c.label)).toEqual(["Tagging", "OCR", "Quality scores", "FaceTask"]);
      expect(fold.children[2].onCpu).toBe(true);
      expect(fold.children[3].stopped).toBe(true);
      expect(fold.children[0].progress).toEqual({ current: 3, total: 10 });
      // The folded worker is not listed a second time.
      expect(store.runningEntries.map((e) => e.key)).toEqual(["pixlstash"]);
      // Its tracker is still there for the taggingActive reader.
      expect(store.taggingActive).toBe(true);
    } finally {
      store.stopPolling();
    }
  });

  it("has no PixlStash row when none of its leases is granted", async () => {
    const store = await pollWith({
      resources: resourcesAnswer({ leases: [lease({ tenant: "comfyui", label: "x", run_id: "run-q" })] }),
    });
    try {
      expect(store.pixlstashEntry).toBeNull();
    } finally {
      store.stopPolling();
    }
  });

  it("splits each device's memory into the four segments, with the spare margin kept back", async () => {
    const store = await pollWith({
      resources: resourcesAnswer({
        holders: [
          { device: "GPU-1", tenant: "comfyui", label: "ComfyUI", used_mb: 10035, measured_by: "nvml", lease_id: null, outside_pixlstash: false },
          { device: "GPU-1", tenant: "pixlstash", label: "PixlStash", used_mb: 2150, measured_by: "torch_reserved", lease_id: null, outside_pixlstash: false },
        ],
      }),
    });
    try {
      const [bar] = store.vramBars;
      expect(bar.device.name).toBe("NVIDIA GeForce RTX 4090");
      expect(bar.total).toBe(24576);
      expect(bar.free).toBe(11576);
      const kinds = bar.segments.map((segment) => segment.kind);
      // Used: ComfyUI, PixlStash, and 815 MB no holder claims (other apps). Then
      // 1576 MB spare: free memory above the 10000 MB a request could get.
      expect(kinds).toEqual(["pixlstash", "comfyui", "other", "spare"]);
      expect(bar.segments.find((s) => s.kind === "other").mb).toBe(13000 - 10035 - 2150);
      expect(bar.segments.find((s) => s.kind === "spare").mb).toBe(1576);
    } finally {
      store.stopPolling();
    }
  });

  it("starts a waiting lease with a call, and nudges the poll afterwards", async () => {
    const store = await pollWith({ resources: resourcesAnswer() });
    try {
      await store.runWaitingAnyway("lease-run-b");
      expect(runLeaseAnyway).toHaveBeenCalledWith("lease-run-b");
      await store.cancelWaiting("lease-run-b");
      expect(cancelLease).toHaveBeenCalledWith("lease-run-b");
      await store.freeComfyuiCache();
      expect(freeComfyui).toHaveBeenCalled();
    } finally {
      store.stopPolling();
    }
  });

  it("reports a refused action as an error notice, not a silent failure", async () => {
    const store = await pollWith({ resources: resourcesAnswer() });
    try {
      runLeaseAnyway.mockRejectedValueOnce(new Error("409"));
      const notices = useNoticeStore();
      const errorSpy = vi.spyOn(notices, "error");
      const answer = await store.runWaitingAnyway("lease-gone");
      expect(answer).toBeNull();
      expect(errorSpy).toHaveBeenCalledWith("Could not start that run.", expect.anything());
    } finally {
      store.stopPolling();
    }
  });

  it("keeps two different refused actions as two notices", async () => {
    const store = await pollWith({ resources: resourcesAnswer() });
    try {
      runLeaseAnyway.mockRejectedValueOnce(new Error("409"));
      cancelLease.mockRejectedValueOnce(new Error("409"));
      await store.runWaitingAnyway("lease-gone");
      await store.cancelWaiting("lease-gone");
      expect(useNoticeStore().notices.map((n) => [n.text, n.count])).toEqual([
        ["Could not start that run.", 1],
        ["Could not cancel that run.", 1],
      ]);
    } finally {
      store.stopPolling();
    }
  });
});

