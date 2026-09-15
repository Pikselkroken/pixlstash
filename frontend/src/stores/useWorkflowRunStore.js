// useWorkflowRunStore.js - the run panel in the inspector rail (#1307).
//
// The panel lives in App.vue's rail, outside the grid, while the selection and
// the ComfyUI progress runner belong to ImageGrid. The grid therefore feeds this
// store what a run needs (the live selection, the view a run with no selection
// files into, and the runner that tracks progress), and the panel reads it. The
// selection is read live rather than captured at open, because the panel does
// not block browsing: selecting more pictures while it is open changes the run.

import { defineStore } from "pinia";
import { onScopeDispose, ref } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { useSidebarStore } from "./useSidebarStore";

/** Opened from the selection pill: workflows with a Selection input. */
export const FROM_SELECTION = "selection";
/** Opened from the toolbar: workflows that take no selection. */
export const FROM_TOOLBAR = "toolbar";

export const useWorkflowRunStore = defineStore("workflowRun", () => {
  const open = ref(false);
  const origin = ref(FROM_SELECTION);
  /** The grid's selected picture ids, kept current by ImageGrid. */
  const selectionIds = ref([]);
  /** `{client_id, set_id, project_id, character_id}` from ImageGrid. */
  const context = ref({});

  let runner = null;

  function openFor(from) {
    origin.value = from === FROM_TOOLBAR ? FROM_TOOLBAR : FROM_SELECTION;
    open.value = true;
    // The panel is the rail, so a collapsed rail is opened with it.
    const sidebar = useSidebarStore();
    if (!sidebar.statsOpen) sidebar.toggleStats();
  }

  function close() {
    open.value = false;
  }

  /**
   * Register the grid's progress runner. Returns the function that detaches it,
   * which only detaches this one, so a remounted grid is not unhooked by the
   * old one's teardown.
   */
  function attachRunner(handler) {
    runner = handler;
    return () => {
      if (runner === handler) runner = null;
    };
  }

  /** Hand a started run's prompts to the runner, for progress. */
  function started(prompts) {
    runner?.({ prompts });
  }

  // The selection names pictures of the library the session could see.
  const unsubscribeSessionReset = onSessionReset(() => {
    open.value = false;
    selectionIds.value = [];
    context.value = {};
  });
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    open,
    origin,
    selectionIds,
    context,
    openFor,
    close,
    attachRunner,
    started,
  };
});
