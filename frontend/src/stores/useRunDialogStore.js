// useRunDialogStore.js - which Run popup is open, and what it runs (v1.12 F5).
//
// The replacement for `useWorkflowRunStore`, which drove the run PANEL in the
// inspector rail. The design makes running a popup rather than a rail, and a
// popup is opened from four places that share no parent: the grid's two menus,
// the lightbox's Recipe tab and the Workflows view's Workflow tab. The dialogs
// themselves are mounted once, in App.vue.
//
// The grid still owns the live selection's view context (where a run's output
// is filed) and feeds it in. The ComfyUI progress runner is attached by
// whichever view is mounted: the grid's, or the Workflows view's.

import { defineStore } from "pinia";
import { onScopeDispose, ref } from "vue";
import { onSessionReset } from "../utils/apiClient";

export const useRunDialogStore = defineStore("runDialog", () => {
  /**
   * The single Run popup's source, or null when it is closed.
   *
   * `{kind, pictureIds, workflowId, pickWorkflow, name, coverUrl}`:
   *   kind          - "picture" | "selection" | "card" | "edit", for the left
   *                   column's wording and for which body the run sends.
   *                   "edit" is Edit with ComfyUI: the built-in edit card over
   *                   the selection, reading none of the pictures' recipes.
   *   pictureIds    - what the run is made from; empty for a card.
   *   workflowId    - the workflow to run, when it is already known.
   *   pickWorkflow  - open with the workflow picker unset ("Run a workflow on
   *                   these…"), so nothing runs until one is chosen.
   *   savedRecipe   - the saved recipe row to run (v1.12 F6). It is a SOURCE,
   *                   not a prefill: the run body carries `saved_recipe_id`
   *                   and the route fills the row's own prompt, LoRAs,
   *                   overrides and seed in underneath the form.
   *   prompt        - the prompt box's starting text, when the caller already
   *                   shows one; still an editable prefill, not a lock.
   *   lora          - "Create with LoRA…" from a person's or a set's menu:
   *                   `{entityType: "character" | "set", entityId, name}`.
   *                   The picker narrows to the workflows that fit the LoRA
   *                   attached to it, that LoRA starts as an added row, and
   *                   the results go to the person or set.
   *   stack         - the stack checkbox's starting value, when the caller
   *                   already showed one; unset, the popup decides.
   *   fromEditTab   - opened from the lightbox's Edit tab, which stays open
   *                   and follows the run itself (see `editRun`).
   */
  const source = ref(null);
  /**
   * The Make more popup's source, or null.
   *
   * `{pictureIds, preflight}` - and `preflight` is the answer the caller
   * ALREADY has. Deciding which popup to open means pre-flighting the
   * selection, and each pre-flight costs the server a ComfyUI `/object_info`
   * read, so handing the answer over saves asking the same question twice for
   * one gesture.
   */
  const makeMore = ref(null);
  /** `{client_id, set_id, project_id, character_id}` from ImageGrid. */
  const context = ref({});
  /**
   * The last pin list the popup wrote, as `{workflowId, pins}`: "Set each
   * run" frees a fixed parameter on the workflow, and the Workflow tab, which
   * writes the whole list from its own copy, takes this so it never writes
   * the freed parameter back.
   */
  const pinsWritten = ref(null);
  /**
   * The last run a popup opened from the Edit tab started, for the tab to
   * follow: `{prompts, pictureId, workflowName, instruction, stack}`.
   */
  const editRun = ref(null);
  /**
   * The run of each workflow that last ended without a picture, by workflow
   * id: `{promptId, message, stopped}`. `message` is the backend's sentence
   * (the node that failed and why) and `stopped` marks a run somebody
   * interrupted. Here rather than in a progress runner because a runner dies
   * with its view, and the card and the Workflow tab must still say so when
   * the owner comes back. Kept until dismissed or the workflow runs again.
   */
  const failures = ref({});
  /** Bumped on every session reset, so a pin write answered after one is dropped. */
  let session = 0;
  function sessionEpoch() {
    return session;
  }

  let runner = null;
  /**
   * Whether a progress runner is mounted to follow a run.
   *
   * The grid attaches one, and so does the Workflows view (`App.vue` mounts
   * `ImageGrid` under `v-else`, so the grid's is gone there). Only the grid's
   * comes with a `client_id` in `context`.
   */
  const hasRunner = ref(false);

  /** Bumped per `openRun`: App.vue keys the popup on it, so each opens fresh. */
  const runOpened = ref(0);

  function openRun(next) {
    makeMore.value = null;
    source.value = { pictureIds: [], ...next };
    runOpened.value += 1;
  }

  function openMakeMore(next) {
    source.value = null;
    makeMore.value = { pictureIds: [], ...next };
  }

  function close() {
    source.value = null;
    makeMore.value = null;
  }

  /**
   * Register a progress runner (the grid's or the Workflows view's). Returns
   * the function that detaches it, which only detaches this one, so a late
   * teardown does not unhook the runner that replaced it.
   */
  function attachRunner(handler) {
    runner = handler;
    hasRunner.value = true;
    return () => {
      if (runner === handler) {
        runner = null;
        hasRunner.value = false;
      }
    };
  }

  /** A run of this workflow ended without a picture (`plugin_progress`). */
  function runFailed(workflowId, failure) {
    if (!workflowId) return;
    failures.value = { ...failures.value, [workflowId]: failure };
  }

  /**
   * Take a `plugin_progress` event. A ComfyUI run that failed is recorded on
   * the workflow it names and answered as `{promptId, message, stopped}`;
   * anything else is null.
   */
  function runEvent(payload) {
    if (String(payload?.plugin || "").toLowerCase() !== "comfyui") return null;
    if (payload.status !== "failed") return null;
    const failure = {
      promptId: String(payload.run_id || "").replace(/^comfyui-/i, ""),
      message: String(payload.message || "").trim() || "ComfyUI failed",
      stopped: Boolean(payload.stopped),
    };
    runFailed(payload.workflow_id, failure);
    return failure;
  }

  function dismissFailure(workflowId) {
    if (!(workflowId in failures.value)) return;
    const next = { ...failures.value };
    delete next[workflowId];
    failures.value = next;
  }

  /** Hand a started run's prompts to the runner, for progress. */
  function started(prompts, pictureIds = []) {
    // Running a workflow again takes its last failure down. Not a failure OF
    // one of these prompts: a run can fail before `/workflows/run` answers.
    const startedIds = new Set(prompts.map((prompt) => String(prompt?.prompt_id)));
    for (const prompt of prompts) {
      const failure = failures.value[prompt?.workflow_id];
      if (failure && !startedIds.has(String(failure.promptId))) {
        dismissFailure(prompt.workflow_id);
      }
    }
    runner?.({
      prompts,
      pictureIds,
      pictureId: pictureIds.length === 1 ? pictureIds[0] : null,
    });
  }

  // A source names pictures and cards of the library the session could see.
  const unsubscribeSessionReset = onSessionReset(() => {
    close();
    context.value = {};
    pinsWritten.value = null;
    editRun.value = null;
    failures.value = {};
    session += 1;
  });
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    source,
    makeMore,
    runOpened,
    context,
    pinsWritten,
    editRun,
    failures,
    runEvent,
    runFailed,
    dismissFailure,
    sessionEpoch,
    hasRunner,
    openRun,
    openMakeMore,
    close,
    attachRunner,
    started,
  };
});
