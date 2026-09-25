// useRunDialogStore.js - which Run popup is open, and what it runs (v1.12 F5).
//
// The replacement for `useWorkflowRunStore`, which drove the run PANEL in the
// inspector rail. The design makes running a popup rather than a rail, and a
// popup is opened from four places that share no parent: the grid's two menus,
// the lightbox's Recipe tab and the Workflows view's Workflow tab. The dialogs
// themselves are mounted once, in App.vue.
//
// The grid still owns two things this cannot: the live selection's view context
// (where a run's output is filed) and the ComfyUI progress runner. It feeds
// both in, exactly as it fed the panel.

import { defineStore } from "pinia";
import { onScopeDispose, ref } from "vue";
import { onSessionReset } from "../utils/apiClient";

export const useRunDialogStore = defineStore("runDialog", () => {
  /**
   * The single Run popup's source, or null when it is closed.
   *
   * `{kind, pictureIds, workflowKey, pickWorkflow, name, coverUrl}`:
   *   kind          - "picture" | "selection" | "card", for the left column's
   *                   wording and for which body the run sends.
   *   pictureIds    - what the run is made from; empty for a card.
   *   workflowKey   - the card to run, when it is already known.
   *   pickWorkflow  - open with the workflow picker unset ("Run a workflow on
   *                   these…"), so nothing runs until one is chosen.
   *   savedRecipe   - the saved recipe row to run (v1.12 F6). It is a SOURCE,
   *                   not a prefill: the run body carries `saved_recipe_id`
   *                   and the route fills the row's own prompt, LoRAs,
   *                   overrides and seed in underneath the form.
   *   prompt        - the prompt box's starting text, when the caller already
   *                   shows one; still an editable prefill, not a lock.
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

  let runner = null;
  /**
   * Whether a grid is mounted to follow a run's progress.
   *
   * `App.vue` mounts `ImageGrid` under `v-else`, so on the Workflows view there
   * is none: a run started from the Workflow tab has no progress overlay and no
   * `client_id` worth sending, and the toast is the whole of its feedback.
   */
  const hasRunner = ref(false);

  function openRun(next) {
    makeMore.value = null;
    source.value = { pictureIds: [], ...next };
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
   * Register the grid's progress runner. Returns the function that detaches it,
   * which only detaches this one, so a remounted grid is not unhooked by the
   * old one's teardown.
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

  /** Hand a started run's prompts to the runner, for progress. */
  function started(prompts, pictureIds = []) {
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
  });
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    source,
    makeMore,
    context,
    hasRunner,
    openRun,
    openMakeMore,
    close,
    attachRunner,
    started,
  };
});
