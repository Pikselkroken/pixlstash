// "Detect faces again" (Analyse menu): queue the run, then, when this tab's run
// lands having added no face, offer one more at a lower detector threshold.
// The answer arrives on the faces `pictures_changed` event (`new_faces`), since
// the run is a background task with no result to poll.
import { watch } from "vue";

import { detectFaces } from "../api/pictures";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useTasksStore } from "../stores/useTasksStore";
import { useWsStore } from "../stores/useWsStore";
import { errorDetail } from "../utils/apiError";

const NOTICE_KEY = "detect-faces";

export function useFaceRedetect() {
  const wsStore = useWsStore();
  const noticeStore = useNoticeStore();
  const tasksStore = useTasksStore();
  // The run this tab is waiting on: { ids: Set<number>, sensitive }. One at a
  // time; a newer click replaces it.
  let pending = null;

  async function detectFacesAgain(ids, { sensitive = false } = {}) {
    if (!ids.length) return;
    // Set before the request: a tiny run can finish before the POST answers.
    pending = { ids: new Set(ids), sensitive };
    try {
      await detectFaces(ids, { sensitive });
      tasksStore.nudge();
    } catch (err) {
      pending = null;
      console.error("Face detection request failed:", err);
      noticeStore.error(`Couldn't start face detection. ${errorDetail(err)}`, {
        key: NOTICE_KEY,
      });
    }
  }

  watch(
    () => wsStore.wsFacesUpdate,
    (update) => {
      if (!pending || !update || update.newFaces === null) return;
      if (update.originClientId !== wsStore.clientId) return;
      if (!update.pictureIds.some((id) => pending.ids.has(id))) return;
      const { ids, sensitive } = pending;
      pending = null;
      if (update.newFaces > 0) return;
      const count = ids.size;
      const where = count === 1 ? "this picture" : `these ${count} pictures`;
      if (sensitive) {
        noticeStore.info(
          `No new faces in ${where}, even on the more sensitive setting.`,
          { key: NOTICE_KEY },
        );
        return;
      }
      noticeStore.info(`No new faces found in ${where}.`, {
        key: NOTICE_KEY,
        action: {
          label: "Try more sensitive",
          handler: () => {
            void detectFacesAgain([...ids], { sensitive: true });
          },
        },
      });
    },
  );

  return { detectFacesAgain };
}
