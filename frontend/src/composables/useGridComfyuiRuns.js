// useGridComfyuiRuns.js - what the grid does with the runs started from it.
//
// A ComfyUI run belongs to the app, not to a view: the backend follows it and
// the Tasks tab reads it from the poll (`useTasksStore`). What is the grid's is
// the lightbox: the progress it shows for the runs started here, and moving on
// to the new picture when one of them lands in the stack on screen.
//
// A run's ending is read, never guessed. A run that has left the poll is asked
// after (`GET /workflows/runs/{id}`), which also covers one that ended before
// `/workflows/run` had answered. One the backend no longer knows is said to be
// lost, not finished.

import { computed, onScopeDispose, reactive, ref, unref, watch } from "vue";
import { getPictureMetadata } from "../api/pictures";
import { listStackPictures } from "../api/stacks";
import { getRunOutcome } from "../api/workflows";
import { runKey, runKeys } from "../utils/gpuMemory";
import { useTasksStore } from "../stores/useTasksStore";

// What a run PixlStash stopped following is said to have done.
const LOST_RUN =
  "PixlStash lost track of this run. Check ComfyUI for its pictures.";
// How long the lightbox's bar says "complete" after the last run ends.
const COMPLETE_TAIL_MS = 1200;

/**
 * @param {Object} grid
 * @param {import("vue").Ref<boolean>} grid.overlayOpen
 * @param {import("vue").Ref<Array>} grid.allGridImages
 * @param {import("vue").Ref<Array>} grid.lastFetchedGridImages
 * @param {(img: Object) => ?string} grid.getPictureStackId
 * @param {(members: Array) => ?Object} grid.selectNewestStackMember
 * @param {(id: number, sourceId: number, stackId: string) => void} grid.showRunResult
 *   the lightbox's own step to a run's result, which it takes only while it
 *   is still on the source picture or its stack
 * @param {() => void} grid.refreshSidebar
 * @returns {{progress: import("vue").ComputedRef<Object>, follow: Function}}
 *   `progress` is `{visible, status, percent, message}` with `status` one of
 *   `idle`, `queued`, `running`, `completed` and `failed`; `follow` takes a
 *   started run, `{prompts, pictureId}`.
 */
export function useGridComfyuiRuns({
  overlayOpen,
  allGridImages,
  lastFetchedGridImages,
  getPictureStackId,
  selectNewestStackMember,
  showRunResult,
  refreshSidebar,
}) {
  const tasksStore = useTasksStore();
  // The runs started here that have not ended: run id (a held run has no
  // prompt id yet) -> the one picture the run was made from, or null.
  const active = ref(new Map());
  // How the last of them ended, for the lightbox and its Edit tab.
  const ended = reactive({ status: "idle", message: "" });
  // The first failure among the runs being followed.
  let failure = "";
  const asking = new Set();
  let hideTimer = null;

  const progress = computed(() => {
    if (active.value.size) {
      const front = tasksStore.comfyuiRuns.find(
        (run) =>
          run.status === "running" &&
          runKeys(run).some((key) => active.value.has(key)),
      );
      return {
        visible: true,
        status: front ? "running" : "queued",
        percent: front ? Number(front.percent) || 0 : 0,
        message: front ? "ComfyUI running..." : "ComfyUI queued...",
      };
    }
    const completed = ended.status === "completed";
    return {
      // A failure is told by the app's notice, once; the bar does not repeat it.
      visible: completed,
      status: ended.status,
      percent: completed ? 100 : 0,
      message: ended.message,
    };
  });

  function follow({ prompts = [], pictureId = null } = {}) {
    // Keyed by run id: every run has one, and the poll lists a held run by it.
    const ids = prompts.map((prompt) => runKey(prompt)).filter(Boolean);
    if (!ids.length) return;
    const next = new Map(active.value);
    for (const id of ids) next.set(id, pictureId ?? null);
    // A failure among runs still being followed is theirs to tell at the end.
    if (!active.value.size) failure = "";
    active.value = next;
    clearTimeout(hideTimer);
    ended.status = "idle";
    ended.message = "";
    // Sooner than the idle cadence: the run is listed from the moment it is queued.
    tasksStore.nudge();
  }

  function findImage(imageId) {
    if (imageId == null) return null;
    const id = String(imageId);
    for (const list of [unref(lastFetchedGridImages), unref(allGridImages)]) {
      const found = (Array.isArray(list) ? list : []).find(
        (item) => item?.id != null && String(item.id) === id,
      );
      if (found) return found;
    }
    return null;
  }

  /**
   * Offer an open lightbox the newest picture of *sourceId*'s stack. Read from
   * the backend: a run is only said to be over once its pictures are imported
   * and stacked, and the grid's own copy does not have them while the
   * lightbox is open (inserts wait for it to close). The lightbox takes the
   * step itself, since only it knows what the reader is on now.
   */
  async function moveOverlayToResult(sourceId) {
    if (sourceId == null || !unref(overlayOpen)) return;
    try {
      let stackId = getPictureStackId(findImage(sourceId));
      if (!stackId) {
        const data = await getPictureMetadata(sourceId);
        const read = data?.stack_id ?? data?.stackId ?? null;
        stackId = read != null ? String(read) : null;
      }
      // Not stacked with its source: the new picture is one of its own.
      if (!stackId) return;
      const members = await listStackPictures(stackId);
      if (!unref(overlayOpen)) return;
      const newest = selectNewestStackMember(
        Array.isArray(members) ? members : [],
      );
      if (newest?.id != null && String(newest.id) !== String(sourceId)) {
        showRunResult(newest.id, sourceId, stackId);
      }
    } catch (err) {
      console.warn(
        `Could not move the lightbox to the result of a run on picture ${sourceId}:`,
        err,
      );
    }
  }

  function end(id, outcome) {
    const pictureId = active.value.get(id);
    const next = new Map(active.value);
    next.delete(id);
    active.value = next;
    if (outcome?.status === "failed") {
      failure ||= String(outcome.message || "").trim() || "ComfyUI failed";
    } else {
      void moveOverlayToResult(pictureId);
    }
    refreshSidebar?.();
    if (next.size) return;
    ended.status = failure ? "failed" : "completed";
    ended.message = failure || "ComfyUI complete";
    if (!failure) {
      hideTimer = setTimeout(() => {
        ended.status = "idle";
        ended.message = "";
      }, COMPLETE_TAIL_MS);
    }
  }

  async function ask(id) {
    if (asking.has(id)) return;
    asking.add(id);
    let outcome;
    try {
      outcome = await getRunOutcome(id);
    } catch (err) {
      if (err?.response?.status !== 404) {
        // Asked again when the next poll still does not list it.
        console.warn(`Could not read how run ${id} ended:`, err);
        return;
      }
      // The backend no longer knows the run (it restarted mid-run), so
      // nothing more will be heard of it: lost, which is not finished.
      outcome = { status: "failed", message: LOST_RUN };
    } finally {
      asking.delete(id);
    }
    // Still running, or still held: the poll that missed it was read before it
    // was queued. Neither is an ending.
    if (
      outcome?.status === "running" ||
      outcome?.status === "waiting" ||
      !active.value.has(id)
    ) {
      return;
    }
    end(id, outcome);
  }

  const stopWatching = watch(
    () => tasksStore.comfyuiRuns,
    (runs) => {
      if (!active.value.size) return;
      // Listed by either id: a held run is listed by its run id alone.
      const listed = new Set(runs.flatMap((run) => runKeys(run)));
      for (const id of active.value.keys()) {
        if (!listed.has(id)) void ask(id);
      }
    },
  );

  onScopeDispose(() => {
    stopWatching();
    clearTimeout(hideTimer);
  });

  return { progress, follow };
}
