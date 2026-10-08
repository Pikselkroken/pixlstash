// Watching the server's automatic pulls (#1440): ask while watched, report
// only what changed, stop when told to. The failure paths matter more than
// the happy one: nothing may keep asking, or keep a band, after the screen or
// the session is gone.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const getWorkflowPull = vi.fn();
vi.mock("../api/comfyui", () => ({
  getWorkflowPull: (...args) => getWorkflowPull(...args),
}));
const fetchCards = vi.fn();
vi.mock("./useWorkflowsStore", () => ({
  useWorkflowsStore: () => ({ fetchCards }),
}));

import { PULL_WATCH_MS, useWorkflowPullStore } from "./useWorkflowPullStore";

const settle = () => vi.advanceTimersByTimeAsync(0);
const done = (task_id, summary) => ({
  status: "completed",
  task_id,
  comfyui_url: "http://127.0.0.1:8188",
  summary,
});

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  getWorkflowPull.mockReset();
  fetchCards.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useWorkflowPullStore", () => {
  it("is pulling while the server runs one, idle once it ends", async () => {
    getWorkflowPull.mockResolvedValueOnce({ status: "running", task_id: "t1" });
    getWorkflowPull.mockResolvedValue({ status: "idle" });
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    expect(pull.phase).toBe("pulling");
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("idle");
  });

  it("asks every PULL_WATCH_MS and stops on unwatch", async () => {
    getWorkflowPull.mockResolvedValue({ status: "idle" });
    const pull = useWorkflowPullStore();
    pull.watch();
    pull.watch();
    await settle();
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 2);
    expect(getWorkflowPull).toHaveBeenCalledTimes(3);
    pull.unwatch();
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 5);
    expect(getWorkflowPull).toHaveBeenCalledTimes(3);
  });

  it("an answer that lands after unwatch is dropped and schedules nothing", async () => {
    let answer;
    getWorkflowPull.mockReturnValue(new Promise((r) => (answer = r)));
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    pull.unwatch();
    answer({ status: "running", task_id: "t1" });
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 3);
    expect(pull.phase).toBe("idle");
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);
  });

  it("reports a new pull that changed something, once, and re-reads the grid", async () => {
    getWorkflowPull.mockResolvedValueOnce(done("old", { listed: 3, pulled: 3 }));
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    // Finished before we looked: the baseline, not news.
    expect(pull.phase).toBe("idle");
    expect(fetchCards).not.toHaveBeenCalled();

    getWorkflowPull.mockResolvedValue(done("t2", { listed: 5, changed: 2 }));
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("done");
    expect(pull.summary).toEqual({ listed: 5, changed: 2 });
    expect(pull.comfyuiUrl).toBe("http://127.0.0.1:8188");
    expect(fetchCards).toHaveBeenCalledTimes(1);

    // The same pull answered again is not reported again.
    pull.dismiss();
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 2);
    expect(pull.phase).toBe("idle");
    expect(fetchCards).toHaveBeenCalledTimes(1);
  });

  it("reports nothing for a pull that changed nothing", async () => {
    getWorkflowPull.mockResolvedValueOnce({ status: "idle" });
    getWorkflowPull.mockResolvedValue(
      done("t1", { listed: 5, matched: 5, unchanged: 5 }),
    );
    const pull = useWorkflowPullStore();
    pull.watch();
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("idle");
    expect(pull.summary).toBeNull();
    expect(fetchCards).not.toHaveBeenCalled();
  });

  it("logs, and shows nothing, for a failed pull", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    getWorkflowPull.mockResolvedValueOnce({ status: "idle" });
    getWorkflowPull.mockResolvedValue({
      status: "failed",
      task_id: "t1",
      error: "ComfyUI runs with --multi-user",
    });
    const pull = useWorkflowPullStore();
    pull.watch();
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("idle");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("keeps asking after a read fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    getWorkflowPull.mockRejectedValueOnce(new Error("down"));
    getWorkflowPull.mockResolvedValue({ status: "running", task_id: "t1" });
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    expect(pull.phase).toBe("idle");
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("pulling");
    warn.mockRestore();
  });

  it("a read that fails after unwatch schedules nothing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    let fail;
    getWorkflowPull.mockReturnValueOnce(new Promise((_r, j) => (fail = j)));
    getWorkflowPull.mockResolvedValue({ status: "idle" });
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    pull.unwatch();
    fail(new Error("down"));
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 3);
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });

  it("keeps asking after an answer it cannot apply", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    getWorkflowPull.mockResolvedValueOnce({ status: "idle" });
    getWorkflowPull.mockResolvedValueOnce({
      status: "completed",
      task_id: "t1",
      get summary() {
        throw new Error("unreadable");
      },
    });
    getWorkflowPull.mockResolvedValue({ status: "running", task_id: "t2" });
    const pull = useWorkflowPullStore();
    pull.watch();
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 2);
    expect(getWorkflowPull).toHaveBeenCalledTimes(3);
    expect(pull.phase).toBe("pulling");
    warn.mockRestore();
  });

  it("reports a pull that was already running when watching started", async () => {
    getWorkflowPull.mockResolvedValueOnce({ status: "running", task_id: "t1" });
    getWorkflowPull.mockResolvedValue(done("t1", { listed: 2, pulled: 2 }));
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    expect(pull.phase).toBe("pulling");
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("done");
    expect(pull.summary).toEqual({ listed: 2, pulled: 2 });
    expect(fetchCards).toHaveBeenCalledTimes(1);
  });

  it("a grid re-read that fails leaves the summary standing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchCards.mockRejectedValue(new Error("grid down"));
    getWorkflowPull.mockResolvedValueOnce({ status: "idle" });
    getWorkflowPull.mockResolvedValue(done("t1", { listed: 1, pulled: 1 }));
    const pull = useWorkflowPullStore();
    pull.watch();
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    expect(pull.phase).toBe("done");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("drops an answer that arrives after the reset", async () => {
    let answer;
    getWorkflowPull.mockReturnValue(new Promise((r) => (answer = r)));
    const pull = useWorkflowPullStore();
    pull.watch();
    await settle();
    pull.reset();
    answer(done("t1", { listed: 1, pulled: 1, missing_node_classes: ["X"] }));
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 2);
    expect(pull.phase).toBe("idle");
    expect(pull.summary).toBeNull();
    expect(pull.comfyuiUrl).toBeNull();
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);
  });
});
