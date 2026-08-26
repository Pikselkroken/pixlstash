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

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

/** The shape of one /workers/progress row for a face pass mid-library. */
function snapshot({ active, current }) {
  return {
    workers: {
      FaceExtractionTask: {
        label: "faces_extracted",
        current,
        total: 12085,
        remaining: 12085 - current,
        status: active ? "running" : "idle",
        running: active,
        active,
      },
    },
  };
}

/** Drive exactly one poll through the store's own fetch path. */
async function poll(store, state) {
  getWorkerProgress.mockResolvedValueOnce(snapshot(state));
  store.startPolling();
  await vi.waitFor(() =>
    expect(getWorkerProgress).toHaveBeenCalledTimes(state.call),
  );
  store.stopPolling();
}

afterEach(() => {
  vi.useRealTimers();
});

// A worker grinding a whole library is idle between every batch: the planner
// submits, the batch runs, inflight drops to zero, the next batch arrives up to
// a backoff later. The store has always carried a grace window for exactly that
// — and it was unreachable, because the filter returned `snapshot.active` for
// both values and the backend always sends the field.
describe("a worker row between batches", () => {
  it("stays in the active list across the gap", async () => {
    const store = useTasksStore();

    await poll(store, { active: true, current: 100, call: 1 });
    expect(store.activeCount).toBe(1);

    // That batch finished. Nothing is in flight; the work is far from done.
    await poll(store, { active: false, current: 200, call: 2 });

    expect(store.activeCount).toBe(1);
    expect(store.hasActiveTasks).toBe(true);
  });

  it("still drops a worker that has genuinely stopped", async () => {
    const store = useTasksStore();

    await poll(store, { active: true, current: 100, call: 1 });
    await poll(store, { active: false, current: 100, call: 2 });
    expect(store.activeCount).toBe(1);

    // Past the grace window: never active again, and no progress since.
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 60_000);
    await poll(store, { active: false, current: 100, call: 3 });

    expect(store.activeCount).toBe(0);
  });
});
