import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  apiClient: { get: vi.fn() },
  isReadOnly: { value: false },
}));
vi.mock("../api/workers", () => ({ getWorkerProgress: vi.fn() }));

import { getWorkerProgress } from "../api/workers";
import { useTasksStore } from "./useTasksStore";

const KEY = "DescriptionTask";
const START_MS = 1_700_000_000_000;

let calls = 0;

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  calls = 0;
  vi.spyOn(Date, "now").mockReturnValue(START_MS);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Drive one poll at `atSeconds` past the start, reporting `current` done. */
async function pollAt(store, atSeconds, current) {
  Date.now.mockReturnValue(START_MS + atSeconds * 1000);
  getWorkerProgress.mockResolvedValueOnce({
    workers: {
      [KEY]: {
        label: "descriptions_generated",
        current,
        total: 5000,
        remaining: 5000 - current,
        status: "running",
        running: true,
        active: true,
      },
    },
  });
  calls += 1;
  store.startPolling();
  await vi.waitFor(() =>
    expect(getWorkerProgress).toHaveBeenCalledTimes(calls),
  );
  store.stopPolling();
}

/** Poll every 2s while a batch of `size` runs for `seconds`, then commits. */
async function runBatch(store, { from, size, seconds, startedAt }) {
  for (let t = startedAt; t < startedAt + seconds; t += 2) {
    await pollAt(store, t, from);
  }
  await pollAt(store, startedAt + seconds, from + size);
}

describe("the displayed throughput", () => {
  // The regression. Descriptions are written once per 32-picture batch, so
  // `current` is flat for every poll the batch runs in and then jumps. The old
  // average over non-zero samples reported that jump divided by the 2s poll
  // interval - 16/s - for a batch that actually took half a minute.
  it("measures a batch over the time it took, not over one poll", async () => {
    const store = useTasksStore();
    await runBatch(store, { from: 0, size: 32, seconds: 30, startedAt: 0 });

    expect(store.getLatestRate(KEY)).toBeCloseTo(32 / 30, 2);
  });

  // Two captioners, same 32-picture batch, different speeds. This is what the
  // user saw: Moondream2 and JoyCaption both reading the same number.
  it("separates a slow worker from a fast one", async () => {
    const slow = useTasksStore();
    await runBatch(slow, { from: 0, size: 32, seconds: 30, startedAt: 0 });
    const slowRate = slow.getLatestRate(KEY);

    setActivePinia(createPinia());
    calls = 0;
    vi.clearAllMocks();
    const fast = useTasksStore();
    await runBatch(fast, { from: 0, size: 32, seconds: 8, startedAt: 0 });
    const fastRate = fast.getLatestRate(KEY);

    expect(fastRate).toBeGreaterThan(slowRate * 3);
    expect(slowRate).toBeCloseTo(32 / 30, 2);
    expect(fastRate).toBeCloseTo(32 / 8, 2);
  });

  it("holds the rate across the gap between two batches", async () => {
    const store = useTasksStore();
    await runBatch(store, { from: 0, size: 32, seconds: 20, startedAt: 0 });
    await runBatch(store, { from: 32, size: 32, seconds: 20, startedAt: 22 });

    // 64 pictures over the 42s both batches and the gap between them took.
    expect(store.getLatestRate(KEY)).toBeCloseTo(64 / 42, 2);
  });

  it("reads zero when nothing has been committed yet", async () => {
    const store = useTasksStore();
    for (let t = 0; t <= 10; t += 2) await pollAt(store, t, 0);

    expect(store.getLatestRate(KEY)).toBe(0);
  });

  it("reads zero rather than negative when pictures are deleted", async () => {
    const store = useTasksStore();
    await pollAt(store, 0, 500);
    await pollAt(store, 2, 400);

    expect(store.getLatestRate(KEY)).toBe(0);
  });

  it("reads zero for a worker with no samples", () => {
    expect(useTasksStore().getLatestRate("NeverSeenTask")).toBe(0);
  });
});
