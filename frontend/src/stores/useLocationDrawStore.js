import { defineStore } from "pinia";
import { onScopeDispose, ref } from "vue";
import { onSessionReset } from "../utils/apiClient";
import { useNoticeStore } from "./useNoticeStore";

/**
 * The one location draw (#2819): New location's wide pictures and More
 * angles' video, frames and restores. The dialog that orders the runs is
 * mounted once, in App.vue, so a draw goes on after it closes; this store is
 * how the rest of the Studio opens it and shows how far the draw has got.
 *
 * One draw at a time: ComfyUI runs them one after another anyway, and a
 * second would only queue behind the first. Opening the dialog for another
 * location while one is under way opens the one under way.
 *
 * ponytail: in memory, so a reload forgets a draw (its runs finish on the
 * server, unpicked); keep it in the hub if that ever matters.
 */
export const useLocationDrawStore = defineStore("locationDraw", () => {
  const dialogOpen = ref(false);
  // The location the dialog is for: a Studio location, or null for a new one.
  const dialogLocation = ref(null);
  // The draw under way or waiting for its views to be picked, else null:
  // `{ locationId, name, status, fraction, ready }`, `locationId` null for a
  // location not made yet, `fraction` 0-1.
  const job = ref(null);
  // Bumped when the session changes: the dialog drops the draw it holds,
  // whose pictures and runs belong to the library that was left.
  const epoch = ref(0);

  const sameLocation = (location) =>
    (job.value?.locationId ?? null) === (location?.id ?? null);

  /** Open the dialog for *location* (null: a new one), or on the draw under
   * way when that is another location's. */
  function openFor(location = null) {
    if (job.value && !sameLocation(location)) {
      useNoticeStore().push({
        level: "info",
        text: `${job.value.name} is still being drawn. One location draws at a time.`,
      });
    } else {
      dialogLocation.value = location;
    }
    dialogOpen.value = true;
  }

  /** Open the dialog on the draw under way, from its progress bar. */
  function reopen() {
    if (job.value) dialogOpen.value = true;
  }

  function closeDialog() {
    dialogOpen.value = false;
  }

  /** The draw's summary, as the dialog reports it; null when it has ended. */
  function report(summary) {
    job.value = summary ? { ...job.value, ...summary } : null;
  }

  /** The draw of the location with *id*, if it has one. */
  function jobFor(id) {
    return job.value && job.value.locationId === id ? job.value : null;
  }

  function reset() {
    epoch.value += 1;
    job.value = null;
    dialogOpen.value = false;
    dialogLocation.value = null;
  }

  const unsubscribe = onSessionReset(reset);
  onScopeDispose(() => unsubscribe());

  return {
    dialogOpen,
    dialogLocation,
    job,
    epoch,
    openFor,
    reopen,
    closeDialog,
    report,
    jobFor,
    reset,
  };
});
