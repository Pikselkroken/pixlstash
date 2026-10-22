// useRunDialogStore.test.js - App.vue keys the Run popup on `runOpened`, so a
// popup opened over an open one mounts fresh instead of keeping its edits.

import { beforeEach, describe, it, expect } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useRunDialogStore } from "./useRunDialogStore";
import { notifySessionReset } from "../utils/apiClient";

describe("useRunDialogStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("gives every open a new key, even over an open popup", () => {
    const store = useRunDialogStore();
    store.openRun({ kind: "picture", pictureIds: [1] });
    const first = store.runOpened;
    store.openRun({ kind: "picture", pictureIds: [2] });
    expect(store.runOpened).not.toBe(first);
    expect(store.source.pictureIds).toEqual([2]);
  });

  // What the grid reads for its lightbox (#1862). One source picture is what
  // lets the lightbox step to the result; several, or none, leave it put.
  it("holds the run a popup started, with its one source picture", () => {
    const store = useRunDialogStore();
    expect(store.startedRun).toBe(null);
    const prompts = [{ workflow_id: "wf-a", prompt_id: "p-1" }];
    store.started(prompts, [7]);
    expect(store.startedRun).toEqual({ prompts, pictureIds: [7], pictureId: 7 });
    store.started(prompts, [7, 8]);
    expect(store.startedRun.pictureId).toBe(null);
    store.started(prompts);
    expect(store.startedRun).toEqual({ prompts, pictureIds: [], pictureId: null });
  });

  // A run that ended without a picture is shown on its workflow, and the
  // popup that started it is closed by then, so the store holds it.
  describe("a workflow's failed run", () => {
    const failure = { promptId: "p-1", message: "KSampler failed", stopped: false };

    it("is kept until it is dismissed", () => {
      const store = useRunDialogStore();
      store.runFailed("wf-a", failure);
      store.runFailed("wf-b", { ...failure, promptId: "p-2" });
      expect(store.failures["wf-a"]).toEqual(failure);
      store.dismissFailure("wf-a");
      expect(store.failures["wf-a"]).toBeUndefined();
      expect(store.failures["wf-b"]).toBeDefined();
    });

    it("is taken down when that workflow runs again, and only that one", () => {
      const store = useRunDialogStore();
      store.runFailed("wf-a", failure);
      store.runFailed("wf-b", { ...failure, promptId: "p-2" });
      store.started([{ workflow_id: "wf-a", prompt_id: "p-3" }]);
      expect(store.failures["wf-a"]).toBeUndefined();
      expect(store.failures["wf-b"]).toBeDefined();
    });

    // A cached graph can fail before `/workflows/run` has answered, so the
    // failure is on record when its own run is registered.
    it("survives the registration of the very run that failed", () => {
      const store = useRunDialogStore();
      store.runFailed("wf-a", failure);
      store.started([{ workflow_id: "wf-a", prompt_id: "p-1" }]);
      expect(store.failures["wf-a"]).toEqual(failure);
    });

    // What `App.vue` hands over: the socket's `plugin_progress` payload.
    it("is read off the backend's event", () => {
      const store = useRunDialogStore();
      const event = {
        plugin: "ComfyUI",
        status: "failed",
        run_id: "comfyui-p-9",
        workflow_id: "wf-a",
        message: " KSampler failed: RuntimeError boom ",
        stopped: true,
      };
      const read = {
        promptId: "p-9",
        message: "KSampler failed: RuntimeError boom",
        stopped: true,
      };
      expect(store.runEvent(event)).toEqual(read);
      expect(store.failures["wf-a"]).toEqual(read);
    });

    it.each([
      ["a run that completed", { plugin: "ComfyUI", status: "completed", workflow_id: "wf-a" }],
      ["another plugin's failure", { plugin: "Upscale", status: "failed", workflow_id: "wf-a" }],
      ["no event at all", null],
    ])("records nothing for %s", (_name, event) => {
      const store = useRunDialogStore();
      expect(store.runEvent(event)).toBe(null);
      expect(store.failures).toEqual({});
    });

    it("is not recorded for a run that names no workflow", () => {
      const store = useRunDialogStore();
      store.runFailed(null, failure);
      expect(store.failures).toEqual({});
    });
  });

  // What the Tasks tab words its offer from (#1850): the popup that knew the
  // labels is closed by the time the pictures are back.
  describe("what a workflow's last run changed", () => {
    const record = {
      name: "Portrait",
      count: 2,
      text: "Checkpoint, Steps.",
      promptIds: ["p-1", "p-2"],
    };
    const completed = (workflowId, promptId) => ({
      plugin: "ComfyUI",
      status: "completed",
      run_id: `comfyui-${promptId}`,
      workflow_id: workflowId,
    });

    it("is kept per workflow, and is not ready to offer while the run is out", () => {
      const store = useRunDialogStore();
      store.ranWith("wf-a", record);
      expect(store.ranChanges["wf-a"]).toEqual({ ...record, finished: false });
      expect(store.ranChanges["wf-b"]).toBeUndefined();
    });

    it("is ready once one of that run's pictures is back", () => {
      const store = useRunDialogStore();
      store.ranWith("wf-a", record);
      // Another workflow's run, and an earlier run of this one, are not it.
      store.runEvent(completed("wf-b", "p-1"));
      store.runEvent(completed("wf-a", "p-0"));
      expect(store.ranChanges["wf-a"].finished).toBe(false);
      // A completed run is still not a failure to report.
      expect(store.runEvent(completed("wf-a", "p-2"))).toBe(null);
      expect(store.ranChanges["wf-a"].finished).toBe(true);
    });

    it("is ready when the run it waited for was held, and sent under a prompt the poll names", async () => {
      const { useTasksStore } = await import("./useTasksStore");
      const tasks = useTasksStore();
      const store = useRunDialogStore();
      // A run held at submit has no prompt id; one prompt is already known.
      store.ranWith("wf-a", { ...record, runIds: ["run-held", "run-sent"], promptIds: ["p-sent"] });
      // The poll says the held run was sent as p-held, which is not on record yet.
      tasks.comfyuiRuns = [{ run_id: "run-held", prompt_id: "p-held", status: "queued" }];
      // A prompt the poll does not connect to this run is not it.
      store.runEvent(completed("wf-a", "p-stranger"));
      expect(store.ranChanges["wf-a"].finished).toBe(false);
      // The held run's own prompt is the run on record.
      store.runEvent(completed("wf-a", "p-held"));
      expect(store.ranChanges["wf-a"].finished).toBe(true);
    });

    it("is not made ready by a run that failed", () => {
      const store = useRunDialogStore();
      store.ranWith("wf-a", record);
      store.runEvent({ ...completed("wf-a", "p-1"), status: "failed" });
      expect(store.ranChanges["wf-a"].finished).toBe(false);
    });

    it("is replaced by the workflow's next run, and taken down by one that changed nothing", () => {
      const store = useRunDialogStore();
      store.ranWith("wf-a", record);
      store.runEvent(completed("wf-a", "p-1"));
      store.ranWith("wf-a", { ...record, count: 1, promptIds: ["p-3"] });
      expect(store.ranChanges["wf-a"]).toMatchObject({ count: 1, finished: false });
      store.ranWith("wf-a", null);
      expect(store.ranChanges["wf-a"]).toBeUndefined();
    });

    it("is taken down by a save, which is also said to whoever shows the workflow", () => {
      const store = useRunDialogStore();
      store.ranWith("wf-a", record);
      store.ranWith("wf-b", record);
      expect(store.saved).toBeNull();
      store.changesSaved("wf-a", "wf-new");
      expect(store.ranChanges["wf-a"]).toBeUndefined();
      expect(store.ranChanges["wf-b"]).toBeDefined();
      expect(store.saved).toMatchObject({ workflowId: "wf-a", savedAs: "wf-new" });
    });

    it("records nothing for a run that names no workflow", () => {
      const store = useRunDialogStore();
      store.ranWith("", record);
      store.changesSaved("");
      expect(store.ranChanges).toEqual({});
      expect(store.saved).toBeNull();
    });

    // Both name workflows of the library the session could see: the next
    // session must be offered nothing from them, and told of no save.
    it("is forgotten with the session, and so is the last save", () => {
      const store = useRunDialogStore();
      store.ranWith("wf-a", record);
      store.runEvent(completed("wf-a", "p-1"));
      store.ranWith("wf-b", record);
      store.changesSaved("wf-b");
      expect(store.ranChanges["wf-a"]).toBeDefined();
      expect(store.saved).not.toBeNull();
      notifySessionReset("test");
      expect(store.ranChanges).toEqual({});
      expect(store.saved).toBeNull();
    });
  });
});
