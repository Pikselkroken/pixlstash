// useNewBatchStore.js - the pictures the last run made (#1868).
//
// Upscale… is for the keepers picked by eye after a run, so the run's pictures
// carry a "New" chip on the grid until the next run ends. Read off the Tasks
// tab's Finished batches (`finishedRuns`): a batch is listed once every run of
// it has ended.

import { defineStore } from "pinia";
import { onScopeDispose, ref, watch } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { useTasksStore } from "./useTasksStore";

export const useNewBatchStore = defineStore("newBatch", () => {
  const tasks = useTasksStore();
  /** The last run's picture ids. */
  const ids = ref(new Set());

  // The batches already seen, and when this page started: a run that ended
  // before it (a reload, a second tab) is not new here. ponytail: `seen`
  // grows by one id per batch for the life of the page.
  const seen = new Set();
  const since = Date.now();

  watch(
    () => tasks.finishedRuns,
    (list) => {
      const fresh = (list || []).filter(
        (run) => !seen.has(run.id) && Date.parse(run.ended_at) >= since,
      );
      for (const run of fresh) seen.add(run.id);
      // A workflow's run, not an upscale of pictures already picked (those
      // have no workflow): newest first, as the poll lists them.
      const made = fresh.find(
        (run) =>
          run.status === "completed" &&
          run.workflow_id &&
          run.picture_ids?.length,
      );
      if (!made) return;
      ids.value = new Set(made.picture_ids);
    },
    { immediate: true },
  );

  // Picture ids of the library the session could see.
  const unsubscribeSessionReset = onSessionReset(() => {
    ids.value = new Set();
  });
  onScopeDispose(() => unsubscribeSessionReset());

  function isNew(id) {
    return ids.value.has(id);
  }

  return { ids, isNew };
});
