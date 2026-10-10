// useRunDialogStore.test.js - App.vue keys the Run popup on `runOpened`, so a
// popup opened over an open one mounts fresh instead of keeping its edits.

import { beforeEach, describe, it, expect } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useRunDialogStore } from "./useRunDialogStore";

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
});
