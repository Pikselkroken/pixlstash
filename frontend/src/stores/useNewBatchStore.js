// useNewBatchStore.js - the pictures the last run made (#1868).
//
// Upscale… is for the keepers picked by eye after a run, so the run's pictures
// carry a "New" chip on the grid until the next run ends, and a notice offers
// to select them. Read off the Tasks tab's Finished batches (`finishedRuns`):
// a batch is listed once every run of it has ended.

import { defineStore } from "pinia";
import { onScopeDispose, ref, watch } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { useTasksStore } from "./useTasksStore";
import { useNoticeStore } from "./useNoticeStore";

export const useNewBatchStore = defineStore("newBatch", () => {
  const tasks = useTasksStore();
  const notices = useNoticeStore();
  /** The last run's picture ids. */
  const ids = ref(new Set());
  /** Bumped by "Select them"; the grid selects `ids` when it moves. */
  const selectAsk = ref(0);

  // The batches already told, and when this page started: a run that ended
  // before it (a reload, a second tab) is not new here. ponytail: `seen`
  // grows by one id per batch for the life of the page.
  const seen = new Set();
  const since = Date.now();

  function select() {
    selectAsk.value += 1;
  }

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
      const n = made.picture_ids.length;
      notices.info(
        `${n} new from ${made.label || "your run"}. Select ${n === 1 ? "it" : "them"} to pick the keepers.`,
        {
          key: "new-batch",
          action: { label: n === 1 ? "Select it" : "Select them", handler: select },
        },
      );
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

  return { ids, selectAsk, select, isNew };
});
