// useRunDialogStore.js - which Run popup is open, and what it runs (v1.12 F5).
//
// The replacement for `useWorkflowRunStore`, which drove the run PANEL in the
// inspector rail. The design makes running a popup rather than a rail, and a
// popup is opened from four places that share no parent: the grid's two menus,
// the lightbox's Recipe tab and the Workflows view's Workflow tab. The dialogs
// themselves are mounted once, in App.vue.
//
// The grid still owns the live selection's view context (where a run's output
// is filed) and feeds it in. A run itself belongs to no view: the backend
// follows it and the Tasks tab reads it from the poll (`useTasksStore`).

import { useTasksStore } from "./useTasksStore";
import { normalizeRunId, runKey, runKeys, runMatches } from "../utils/gpuMemory";

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
   *                   "edit" is the Edit tab's run over its picture, reading
   *                   none of the picture's recipe.
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
   *   selection     - the Edit tab's Paint selection on its one picture
   *                   (`{version, steps, refine}`), sent as the run's
   *                   `selection`: where on the picture the edit goes.
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
  /** `{set_id, project_id, character_id}` from ImageGrid. */
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
   * interrupted. Here rather than in a view, because the card and the
   * Workflow tab must still say so when the owner comes back. Kept until
   * dismissed or the workflow runs again.
   */
  const failures = ref({});
  /**
   * What the last run of each workflow changed, by workflow id, as the Run
   * popup's band said it: `{name, count, text, promptIds, finished}` (#1850).
   * Here for the reason `failures` is: the popup is closed by the time the
   * pictures are back, and the Tasks tab offers to save those changes then.
   * Only what the offer is WORDED from; whether there is one is the server's
   * (`GET …/changes`, `ran`), which the tab asks before it draws. Kept until
   * the workflow runs again or the changes are saved.
   */
  const ranChanges = ref({});
  /**
   * The last save of a workflow's changes made in this tab, as
   * `{workflowId, savedAs, at}` (`savedAs` is the new workflow of a Save as
   * new, else ""). The server's own announcement skips the tab that wrote, so
   * the Workflow tab and the Tasks tab take it from here: what waited on the
   * workflow, and what its last run offered, are gone.
   */
  const saved = ref(null);
  /** Bumped on every session reset, so a pin write answered after one is dropped. */
  let session = 0;
  function sessionEpoch() {
    return session;
  }

  /**
   * The last run a popup started, `{prompts, pictureIds, pictureId}`, for the
   * grid to read: it shows the run's progress in the lightbox and moves the
   * lightbox on to the new picture. Nothing else follows a run from here.
   */
  const startedRun = ref(null);

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

  /** A run of this workflow ended without a picture (`plugin_progress`). */
  function runFailed(workflowId, failure) {
    if (!workflowId) return;
    failures.value = { ...failures.value, [workflowId]: failure };
  }

  /**
   * A run of *workflowId* was queued with these changes from the workflow
   * (`{name, count, text, promptIds}`), or with none (*record* null), which
   * takes down what its run before offered.
   */
  function ranWith(workflowId, record) {
    if (!workflowId) return;
    const next = { ...ranChanges.value };
    if (record) next[workflowId] = { ...record, finished: false };
    else delete next[workflowId];
    ranChanges.value = next;
  }

  /** The changes of *workflowId* were saved, over it or as *savedAs*. */
  function changesSaved(workflowId, savedAs = "") {
    if (!workflowId) return;
    ranWith(workflowId, null);
    saved.value = { workflowId, savedAs, at: Date.now() };
  }

  /**
   * Take a `plugin_progress` event. A ComfyUI run that failed is recorded on
   * the workflow it names and answered as `{promptId, message, stopped}`;
   * anything else is null. One that completed marks what that run changed as
   * ready to offer: the pictures are back.
   */
  /**
   * Whether a run event's prompt id is one of the run on record. Lifecycle
   * events key on the prompt id, and a run held at submit had none then, so
   * the record also holds its run ids. The ending names PixlStash's run id
   * (`pixlstash_run_id`), and the poll says which prompt each was sent as.
   * Only a record with neither (an older answer) takes any event of the
   * workflow to be the run on record.
   */
  function eventIsRecordedRun(ran, promptId, pixlstashRunId = null) {
    if (pixlstashRunId && ran.runIds?.includes(normalizeRunId(pixlstashRunId))) return true;
    if (ran.promptIds?.includes(promptId)) return true;
    if (!ran.promptIds?.length && !ran.runIds?.length) return true;
    return useTasksStore().comfyuiRuns.some(
      (run) =>
        runMatches(run, promptId) &&
        ran.runIds?.includes(runKey(run)),
    );
  }

  function runEvent(payload) {
    if (String(payload?.plugin || "").toLowerCase() !== "comfyui") return null;
    const ran = ranChanges.value[payload.workflow_id];
    const promptId = normalizeRunId(payload.run_id);
    if (payload.status === "completed" && ran && !ran.finished) {
      // Of the run on record, not of an earlier one still finishing.
      if (eventIsRecordedRun(ran, promptId, payload.pixlstash_run_id || null)) {
        ranChanges.value = {
          ...ranChanges.value,
          [payload.workflow_id]: { ...ran, finished: true },
        };
      }
    }
    if (payload.status !== "failed") return null;
    const failure = {
      promptId,
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

  /** A popup started these runs. */
  function started(prompts, pictureIds = []) {
    // Running a workflow again takes its last failure down. Not a failure OF
    // one of these prompts: a run can fail before `/workflows/run` answers.
    const startedIds = new Set(prompts.flatMap((prompt) => runKeys(prompt)));
    for (const prompt of prompts) {
      const failure = failures.value[prompt?.workflow_id];
      if (failure && !startedIds.has(failure.promptId)) {
        dismissFailure(prompt.workflow_id);
      }
    }
    startedRun.value = {
      prompts,
      pictureIds,
      pictureId: pictureIds.length === 1 ? pictureIds[0] : null,
    };
  }

  // A source names pictures and cards of the library the session could see.
  const unsubscribeSessionReset = onSessionReset(() => {
    close();
    context.value = {};
    pinsWritten.value = null;
    editRun.value = null;
    startedRun.value = null;
    failures.value = {};
    ranChanges.value = {};
    saved.value = null;
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
    ranChanges,
    ranWith,
    saved,
    changesSaved,
    runEvent,
    runFailed,
    dismissFailure,
    sessionEpoch,
    startedRun,
    openRun,
    openMakeMore,
    close,
    started,
  };
});
