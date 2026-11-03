import { computed, onBeforeUnmount, ref } from "vue";
import { getPixlstashNode, installComfyuiPack } from "../api/comfyui";
import { errorMessage } from "../utils/apiError";

const POLL_EVERY_MS = 3000;
const POLL_FOR_MS = 120000;

/**
 * Update the ComfyUI-PixlStash nodes in the current ComfyUI to the copy this
 * PixlStash ships, and wait for ComfyUI to come back with them: the fix for a
 * `pack_outdated` reason whose `can_update` is true.
 *
 * `POST /comfyui/pack/install` copies the nodes and asks ComfyUI-Manager to
 * restart ComfyUI; then `GET /comfyui/pixlstash-node` is polled until its
 * `shelf_loader_current` is true. The old nodes answer false, so a poll that
 * lands before the restart cannot finish early. A restart left to the person
 * (`restart: "manual"`) is polled for too, so their restart finishes it.
 *
 * `phase`: `idle`, `updating`, `restarting`, `manual`, `done`, `timeout` or
 * `failed`; `note` says it in words, "" for `idle` and `done`.
 *
 * @param {() => void} onCurrent called once ComfyUI runs the new nodes.
 */
export function usePixlstashPackUpdate(onCurrent) {
  const phase = ref("idle");
  const detail = ref("");
  let timer = null;
  // Bumped on every stop, so an answer still in flight when the caller
  // unmounts (or a new update begins) cannot act.
  let run = 0;

  function stop() {
    clearTimeout(timer);
    timer = null;
    run += 1;
  }

  function poll(mine) {
    const deadline = Date.now() + POLL_FOR_MS;
    const tick = async () => {
      let current = false;
      try {
        current = (await getPixlstashNode())?.shelf_loader_current === true;
      } catch (err) {
        console.debug("ComfyUI not answering yet while it restarts:", err);
      }
      if (mine !== run) return;
      if (current) {
        phase.value = "done";
        onCurrent?.();
      } else if (Date.now() >= deadline) {
        phase.value = "timeout";
      } else {
        timer = setTimeout(tick, POLL_EVERY_MS);
      }
    };
    timer = setTimeout(tick, POLL_EVERY_MS);
  }

  async function update() {
    stop();
    const mine = run;
    phase.value = "updating";
    detail.value = "";
    try {
      const got = await installComfyuiPack();
      if (mine !== run) return;
      phase.value = got?.restart === "manual" ? "manual" : "restarting";
      detail.value = got?.detail || "";
      poll(mine);
    } catch (err) {
      if (mine !== run) return;
      phase.value = "failed";
      detail.value = errorMessage(err, "Could not update ComfyUI-PixlStash.");
    }
  }

  const note = computed(() => {
    switch (phase.value) {
      case "updating":
        return "Updating ComfyUI-PixlStash…";
      case "restarting":
        return "Updated. Restarting ComfyUI…";
      case "manual":
        return `Updated. ${detail.value || "Restart ComfyUI to load it."}`;
      case "timeout":
        return "ComfyUI has not come back with the update yet. If it did not restart, restart it yourself.";
      case "failed":
        return detail.value;
      default:
        return "";
    }
  });

  /** Whether the update button is offered: before an update, or after one failed. */
  const offering = computed(() => ["idle", "failed"].includes(phase.value));

  onBeforeUnmount(stop);
  return { phase, note, offering, update, stop };
}
