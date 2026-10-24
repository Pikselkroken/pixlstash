// The last run's pictures (#1868): marked New when their batch ends, without a
// notice, and never a batch that ended before the page opened.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";

vi.mock("./useTasksStore", async () => {
  const { defineStore } = await import("pinia");
  const { ref } = await import("vue");
  return {
    useTasksStore: defineStore("tasks", () => ({ finishedRuns: ref([]) })),
  };
});

import { useNewBatchStore } from "./useNewBatchStore";
import { useTasksStore } from "./useTasksStore";
import { useNoticeStore } from "./useNoticeStore";

function batch(id, overrides = {}) {
  return {
    id,
    workflow_id: "auto:abc",
    label: "SDXL: Text to Image",
    status: "completed",
    ended_at: new Date(Date.now() + 1000).toISOString(),
    picture_ids: [11, 12],
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("useNewBatchStore", () => {
  it("marks a batch that ends without raising a notice", async () => {
    const store = useNewBatchStore();
    const tasks = useTasksStore();
    tasks.finishedRuns = [batch("b1")];
    await nextTick();
    expect(store.isNew(11)).toBe(true);
    expect(store.isNew(99)).toBe(false);
    expect(useNoticeStore().notices).toHaveLength(0);
  });

  it("replaces the last batch with the next", async () => {
    const store = useNewBatchStore();
    const tasks = useTasksStore();
    tasks.finishedRuns = [batch("b1")];
    await nextTick();
    tasks.finishedRuns = [batch("b2", { picture_ids: [21] }), batch("b1")];
    await nextTick();
    expect(store.isNew(11)).toBe(false);
    expect(store.isNew(21)).toBe(true);
  });

  it("leaves out an upscale, a failed run and one that ended before", async () => {
    const store = useNewBatchStore();
    const tasks = useTasksStore();
    tasks.finishedRuns = [
      batch("up", { workflow_id: null, picture_ids: [31] }),
      batch("bad", { status: "failed", picture_ids: [32] }),
      batch("old", {
        ended_at: new Date(Date.now() - 60_000).toISOString(),
        picture_ids: [33],
      }),
    ];
    await nextTick();
    expect([31, 32, 33].some((id) => store.isNew(id))).toBe(false);
  });
});
