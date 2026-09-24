// The pull's lifecycle (#1440): start, poll until the server task ends, then
// re-read the grid once. The failure paths matter more than the happy one:
// each must end in a stated reason, never in a band that says "pulling"
// forever.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const startWorkflowPull = vi.fn();
const getWorkflowPull = vi.fn();
vi.mock("../api/comfyui", () => ({
  startWorkflowPull: (...args) => startWorkflowPull(...args),
  getWorkflowPull: (...args) => getWorkflowPull(...args),
}));
const fetchCards = vi.fn();
vi.mock("./useWorkflowsStore", () => ({
  useWorkflowsStore: () => ({ fetchCards }),
}));

import {
  PULL_POLL_MAX_MISSES,
  PULL_POLL_MS,
  useWorkflowPullStore,
} from "./useWorkflowPullStore";

const settle = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
  startWorkflowPull.mockReset().mockResolvedValue({
    status: "started",
    task_id: "t1",
  });
  getWorkflowPull.mockReset();
  fetchCards.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useWorkflowPullStore", () => {
  it("polls a running pull until it completes, then re-reads the grid once", async () => {
    getWorkflowPull
      .mockResolvedValueOnce({ status: "pending", task_id: "t1" })
      .mockResolvedValueOnce({ status: "running", task_id: "t1" })
      .mockResolvedValueOnce({
        status: "completed",
        task_id: "t1",
        comfyui_url: "http://127.0.0.1:8188",
        summary: { listed: 2, pulled: 2 },
      });
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    expect(pull.phase).toBe("pulling");
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    expect(pull.phase).toBe("pulling");
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    expect(pull.phase).toBe("done");
    expect(pull.summary).toEqual({ listed: 2, pulled: 2 });
    expect(pull.comfyuiUrl).toBe("http://127.0.0.1:8188");
    expect(fetchCards).toHaveBeenCalledTimes(1);
    expect(getWorkflowPull).toHaveBeenCalledTimes(3);
    // Stopped: no further asks once it is done.
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS * 5);
    expect(getWorkflowPull).toHaveBeenCalledTimes(3);
  });

  it("a second press while pulling starts nothing", async () => {
    getWorkflowPull.mockResolvedValue({ status: "running", task_id: "t1" });
    const pull = useWorkflowPullStore();
    pull.start();
    pull.start();
    await settle();
    expect(startWorkflowPull).toHaveBeenCalledTimes(1);
    pull.dismiss();
    expect(pull.phase).toBe("pulling");
  });

  it("a refused start is a stated failure", async () => {
    startWorkflowPull.mockRejectedValue(new Error("503"));
    const pull = useWorkflowPullStore();
    await pull.start();
    expect(pull.phase).toBe("failed");
    expect(pull.error).toBeTruthy();
    expect(getWorkflowPull).not.toHaveBeenCalled();
  });

  it("gives up with a reason after the server stops answering", async () => {
    getWorkflowPull.mockRejectedValue(new Error("offline"));
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS * PULL_POLL_MAX_MISSES);
    expect(pull.phase).toBe("failed");
    expect(getWorkflowPull).toHaveBeenCalledTimes(PULL_POLL_MAX_MISSES);
  });

  it("does not report a pull it did not start, or one a restart forgot", async () => {
    getWorkflowPull.mockResolvedValue({ status: "completed", task_id: "other" });
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    expect(pull.phase).toBe("failed");
    expect(pull.summary).toBeNull();
    expect(fetchCards).not.toHaveBeenCalled();

    getWorkflowPull.mockResolvedValue({ status: "idle" });
    pull.dismiss();
    pull.start();
    await settle();
    expect(pull.phase).toBe("failed");
    expect(pull.error).toContain("restarted");
  });

  it("dismissing a finished pull forgets it", async () => {
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t1",
      summary: { listed: 1 },
    });
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    pull.dismiss();
    expect(pull.phase).toBe("idle");
    expect(pull.summary).toBeNull();
  });
});

describe("a session change mid-pull", () => {
  it("drops an answer that arrives after the reset", async () => {
    let answer;
    getWorkflowPull.mockImplementation(
      () => new Promise((resolve) => (answer = resolve)),
    );
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    pull.reset();
    answer({
      status: "completed",
      task_id: "t1",
      comfyui_url: "http://owner-comfy:8188",
      summary: { listed: 1, missing_node_classes: ["OwnersPackNode"] },
    });
    await settle();
    expect(pull.phase).toBe("idle");
    expect(pull.summary).toBeNull();
    expect(pull.comfyuiUrl).toBeNull();
    expect(fetchCards).not.toHaveBeenCalled();
  });

  it("drops a start that answers after the reset", async () => {
    let started;
    startWorkflowPull.mockImplementation(
      () => new Promise((resolve) => (started = resolve)),
    );
    const pull = useWorkflowPullStore();
    pull.start();
    pull.reset();
    started({ status: "started", task_id: "t1" });
    await settle();
    expect(getWorkflowPull).not.toHaveBeenCalled();
    expect(pull.phase).toBe("idle");
  });
});

describe("the review's store findings", () => {
  it("a poll that fails after a reset asks nothing more", async () => {
    let fail;
    getWorkflowPull.mockImplementationOnce(
      () => new Promise((_resolve, reject) => (fail = reject)),
    );
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t1",
      comfyui_url: "http://owner-comfy:8188",
      summary: { listed: 1 },
    });
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    pull.reset();
    fail(new Error("offline"));
    await settle();
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS * 3);
    // Wrong if a second ask goes out: it would run under the new session and
    // write the owner's ComfyUI into it.
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);
    expect(pull.phase).toBe("idle");
    expect(pull.comfyuiUrl).toBeNull();
  });

  it("a grid re-read that fails leaves the summary standing", async () => {
    fetchCards.mockRejectedValue(new Error("500"));
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t1",
      summary: { listed: 2 },
    });
    const pull = useWorkflowPullStore();
    pull.start();
    await settle();
    expect(pull.phase).toBe("done");
    expect(pull.summary).toEqual({ listed: 2 });
  });

  it("resumes a pull already running on the server, and adopts no finished one", async () => {
    getWorkflowPull.mockResolvedValueOnce({ status: "running", task_id: "t9" });
    const pull = useWorkflowPullStore();
    await pull.resume();
    expect(pull.phase).toBe("pulling");
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t9",
      summary: { listed: 4 },
    });
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    expect(pull.phase).toBe("done");

    setActivePinia(createPinia());
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t9",
      summary: { listed: 4 },
    });
    const fresh = useWorkflowPullStore();
    await fresh.resume();
    expect(fresh.phase).toBe("idle");
    expect(fresh.summary).toBeNull();
    expect(startWorkflowPull).not.toHaveBeenCalled();
  });
});
