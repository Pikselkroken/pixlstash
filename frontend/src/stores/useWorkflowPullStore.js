import { onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import { getWorkflowPull, startWorkflowPull } from "../api/comfyui";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";
import { useWorkflowsStore } from "./useWorkflowsStore";

/** How often a running pull is asked how it is going. */
export const PULL_POLL_MS = 1000;
/** Unanswered asks in a row before the pull is reported as lost. */
export const PULL_POLL_MAX_MISSES = 30;

/**
 * Pulling ComfyUI's saved workflows into the library (#1440).
 *
 * A store rather than component state because the pull outlives the screen
 * that started it: it is a server task, the Tasks tab draws its progress, and
 * somebody who leaves Workflows mid-pull should find the summary waiting when
 * they come back.
 *
 * **The summary belongs to THIS pull.** The server remembers the last pull
 * since it started, but a summary shown on every visit would be a stale report
 * about a ComfyUI that has since changed, so only a pull started here is
 * reported, and dismissing it forgets it.
 */
export const useWorkflowPullStore = defineStore("workflowPull", () => {
  // "idle" | "pulling" | "done" | "failed"
  const phase = ref("idle");
  const summary = ref(null);
  const comfyuiUrl = ref(null);
  const error = ref("");
  let taskId = null;
  let timer = null;
  let misses = 0;
  // Bumped by `reset()`. Every await re-checks it, because a request sent
  // with the owner's credential can answer AFTER the session changed, and
  // writing that answer would hand the new session the owner's ComfyUI URL
  // and the node and model names it lacks (the `useOperationStore` rule).
  let epoch = 0;

  function stopPolling() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  async function start() {
    if (phase.value === "pulling") return;
    stopPolling();
    misses = 0;
    phase.value = "pulling";
    summary.value = null;
    error.value = "";
    const mine = epoch;
    try {
      const started = await startWorkflowPull();
      if (mine !== epoch) return;
      taskId = started?.task_id ?? null;
    } catch (err) {
      if (mine !== epoch) return;
      phase.value = "failed";
      error.value = errorMessage(err, "The pull could not be started.");
      return;
    }
    poll();
  }

  async function poll() {
    const mine = epoch;
    let state;
    try {
      state = await getWorkflowPull();
      if (mine !== epoch) return;
    } catch (err) {
      if (mine !== epoch) return;
      console.warn("[workflows] could not read the pull's state", err);
      misses += 1;
      if (misses >= PULL_POLL_MAX_MISSES) {
        phase.value = "failed";
        error.value = errorMessage(
          err,
          "PixlStash stopped answering while the pull was running.",
        );
        return;
      }
      schedule();
      return;
    }
    misses = 0;
    // Another tab's pull, or one from before a restart: not ours to report.
    if (taskId && state?.task_id && state.task_id !== taskId) {
      phase.value = "failed";
      error.value = "Another pull took this one's place before it finished.";
      return;
    }
    if (state?.status === "completed") {
      phase.value = "done";
      summary.value = state.summary ?? null;
      comfyuiUrl.value = state.comfyui_url ?? null;
      try {
        await useWorkflowsStore().fetchCards();
      } catch (err) {
        // The summary still stands; the grid shows its own read error.
        console.warn("[workflows] could not re-read the grid after a pull", err);
      }
      return;
    }
    if (state?.status === "failed" || state?.status === "cancelled") {
      phase.value = "failed";
      comfyuiUrl.value = state.comfyui_url ?? null;
      error.value = state.error || "The pull failed.";
      return;
    }
    if (state?.status === "idle") {
      // The server forgot it (a restart): nothing more will come.
      phase.value = "failed";
      error.value = "The server restarted before the pull finished.";
      return;
    }
    schedule();
  }

  function schedule() {
    stopPolling();
    timer = setTimeout(poll, PULL_POLL_MS);
  }

  /**
   * Pick up a pull that is already running on the server - one started before
   * a reload, or from another tab - so the button says "Pulling…" rather than
   * offering to start what is already going. A finished pull is not adopted:
   * its summary was somebody else's to read.
   */
  async function resume() {
    if (phase.value !== "idle") return;
    const mine = epoch;
    let state;
    try {
      state = await getWorkflowPull();
    } catch (err) {
      console.warn("[workflows] could not ask whether a pull is running", err);
      return;
    }
    if (mine !== epoch || phase.value !== "idle") return;
    if (state?.status === "pending" || state?.status === "running") {
      taskId = state.task_id ?? null;
      misses = 0;
      phase.value = "pulling";
      schedule();
    }
  }

  function dismiss() {
    if (phase.value === "pulling") return;
    phase.value = "idle";
    summary.value = null;
    error.value = "";
    comfyuiUrl.value = null;
  }

  function reset() {
    epoch += 1;
    stopPolling();
    taskId = null;
    phase.value = "idle";
    summary.value = null;
    error.value = "";
    comfyuiUrl.value = null;
  }

  onScopeDispose(onSessionReset(reset));
  onScopeDispose(stopPolling);

  return { phase, summary, comfyuiUrl, error, start, resume, dismiss, reset };
});
