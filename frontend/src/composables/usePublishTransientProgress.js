import { onUnmounted, toRaw, watch } from "vue";
import { useTasksStore } from "../stores/useTasksStore";

/**
 * Publish a component's short, un-abortable wait to the title bar.
 *
 * `source` returns ProgressOverlay's `{ visible, status, message, percent }`;
 * each change lands in `tasksStore.transientProgress`, which `TitleBar` draws
 * beside the tasks pill. On unmount the bar is cleared, unless another
 * publisher has written since: a grid mounting before the old one leaves
 * (a transition, a keep-alive swap) keeps its own bar.
 *
 * @param {() => {visible: boolean, status: string, message: string, percent: number}} source
 */
export function usePublishTransientProgress(source) {
  const tasksStore = useTasksStore();
  let written = null;
  watch(
    source,
    (progress) => {
      written = progress;
      tasksStore.transientProgress = progress;
    },
    { immediate: true },
  );
  onUnmounted(() => {
    if (toRaw(tasksStore.transientProgress) !== written) return;
    tasksStore.clearTransientProgress();
  });
}
