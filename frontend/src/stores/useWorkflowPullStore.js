import { onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";

import { getWorkflowPull } from "../api/comfyui";
import { onSessionReset } from "../utils/apiClient";
import { useWorkflowsStore } from "./useWorkflowsStore";

/** How often the server is asked whether ComfyUI is being checked. */
export const PULL_WATCH_MS = 5000;

/**
 * Watching ComfyUI's saved workflows come into the library (#1440).
 *
 * The server pulls on its own every minute (and after Link), so this store
 * starts nothing: while the Workflows screen is mounted it asks how the latest
 * pull is going and reports two things. `phase === "pulling"` while one runs,
 * and, when one that finished while somebody was looking brought something
 * new or changed, `phase === "done"` with its summary. A pull that changed
 * nothing, or that failed, reports nothing: it happens every minute, and a
 * band per minute would be noise. Failures are logged; the ComfyUI settings
 * section is where connection trouble is shown.
 *
 * A pull already finished when watching starts is the baseline, not news;
 * one still running then is reported when it ends.
 */
export const useWorkflowPullStore = defineStore("workflowPull", () => {
  // "idle" | "pulling" | "done"
  const phase = ref("idle");
  const summary = ref(null);
  const comfyuiUrl = ref(null);
  let timer = null;
  let watching = false;
  // The last finished pull's task id, so each one is reported once. Undefined
  // until the first answer, which only sets the baseline.
  let lastSeen;
  // Bumped by `reset()`. Every await re-checks it, because a request sent
  // with the owner's credential can answer AFTER the session changed, and
  // writing that answer would hand the new session the owner's ComfyUI URL
  // and the node and model names it lacks (the `useOperationStore` rule).
  let epoch = 0;

  function stopTimer() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  }

  async function poll() {
    const mine = epoch;
    let state;
    try {
      state = await getWorkflowPull();
    } catch (err) {
      if (mine !== epoch || !watching) return;
      console.warn("[workflows] could not read the pull's state", err);
      schedule();
      return;
    }
    if (mine !== epoch || !watching) return;
    try {
      await apply(state);
    } catch (err) {
      // One answer this cannot read must not end the watch.
      console.warn("[workflows] could not apply the pull's state", state, err);
    }
    if (mine === epoch && watching) schedule();
  }

  async function apply(state) {
    const status = state?.status;
    if (status === "pending" || status === "running") {
      // Running when watching starts: its end is news, so the baseline is
      // "nothing finished yet", as for `idle` below.
      if (lastSeen === undefined) lastSeen = null;
      phase.value = "pulling";
      return;
    }
    if (phase.value === "pulling") phase.value = "idle";
    // Nothing pulled yet is a baseline too: the first pull after it is news.
    if (status === "idle" && lastSeen === undefined) lastSeen = null;
    if (status !== "completed" && status !== "failed") return;
    const id = state.task_id ?? null;
    const first = lastSeen === undefined;
    const isNew = id !== lastSeen;
    lastSeen = id;
    if (first || !isNew) return;
    if (status === "failed") {
      console.warn("[workflows] the ComfyUI pull failed", state.error);
      return;
    }
    const found = state.summary ?? {};
    if (!found.pulled && !found.changed) return;
    summary.value = found;
    comfyuiUrl.value = state.comfyui_url ?? null;
    phase.value = "done";
    try {
      await useWorkflowsStore().fetchCards();
    } catch (err) {
      // The summary still stands; the grid shows its own read error.
      console.warn("[workflows] could not re-read the grid after a pull", err);
    }
  }

  function schedule() {
    stopTimer();
    timer = setTimeout(poll, PULL_WATCH_MS);
  }

  /** Start asking, now and every {@link PULL_WATCH_MS}. Idempotent. */
  function watch() {
    if (watching) return;
    watching = true;
    poll();
  }

  /** Stop asking (the Workflows screen unmounted). */
  function unwatch() {
    watching = false;
    stopTimer();
  }

  function dismiss() {
    if (phase.value !== "done") return;
    phase.value = "idle";
    summary.value = null;
    comfyuiUrl.value = null;
  }

  function reset() {
    epoch += 1;
    unwatch();
    lastSeen = undefined;
    phase.value = "idle";
    summary.value = null;
    comfyuiUrl.value = null;
  }

  onScopeDispose(onSessionReset(reset));
  onScopeDispose(unwatch);

  return { phase, summary, comfyuiUrl, watch, unwatch, dismiss, reset };
});
