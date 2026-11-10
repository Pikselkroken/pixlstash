// Opening a workflow in the owner's ComfyUI: the Workflow tab's button and More
// menu, and the Workflows grid's verb menus, which are the same two verbs.
//
// ComfyUI takes no workflow from a URL, so the id rides along as
// `?pixlstash_workflow=` and the ComfyUI-PixlStash node fetches the graph
// (`GET /workflows/{id}/graph`) and loads it. The node asks for the graph with
// its own savers and Adapter Loader in place of the stock nodes, so a picture
// queued in ComfyUI comes back here; `pixlstash_original=1` tells it to ask
// for the workflow's own nodes instead.

import { computed, onScopeDispose, ref, watch } from "vue";

import { getPixlstashNode } from "../api/comfyui";
import { useFilterStore } from "../stores/useFilterStore";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorMessage } from "../utils/apiError";
import { comfyuiLink } from "../utils/comfyuiLink";

/**
 * The desktop shell's bridge, when there is one. Its window opens only `https:`
 * outside the app and ComfyUI is plain `http:`, so on the desktop the link goes
 * through `desktop:openComfyui` instead of `window.open`.
 */
function desktopBridge() {
  return typeof window !== "undefined" ? window.pixlstashDesktop : null;
}

/**
 * Open workflow `id` in ComfyUI, in a new tab.
 *
 * Synchronous on purpose: a `window.open` after an await is what popup
 * blockers refuse. The caller gates it (`useComfyuiOpen`).
 *
 * @param {string} id
 * @param {boolean} [original] - the workflow's own nodes, not PixlStash's.
 */
export function openWorkflowInComfyui(id, original = false) {
  const filterStore = useFilterStore();
  const notices = useNoticeStore();
  if (!id || !filterStore.comfyuiUrl) return;
  const url = comfyuiLink(filterStore.comfyuiUrl, {
    pixlstash_workflow: id,
    ...(original ? { pixlstash_original: "1" } : {}),
  });
  if (!url) {
    notices.push({
      level: "error",
      text: "The ComfyUI address in Settings is not a web address.",
    });
    return;
  }
  const desktop = desktopBridge();
  if (desktop?.openComfyui) {
    desktop
      .openComfyui(url)
      .then((opened) => {
        if (!opened) throw new Error("the shell refused the link");
      })
      .catch((err) => {
        console.warn("[workflows] the desktop shell would not open ComfyUI", err);
        notices.push({ level: "error", text: "Could not open ComfyUI." });
      });
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/**
 * Open ComfyUI on a graph the server holds for it: `hold()` asks for it and
 * answers `{opening, workflow_id}` (an earlier version, #1792), which ComfyUI
 * reads at `?pixlstash_run=&pixlstash_of=` as it reads the Run popup's.
 *
 * In a browser the tab is opened BEFORE the request and sent on after it, as
 * the Run popup does: a `window.open` after an await is what popup blockers
 * refuse. Failures are said as notices.
 *
 * @param {() => Promise<{opening: string, workflow_id: string}>} hold
 * @param {string} failText
 */
export async function openHeldInComfyui(hold, failText) {
  const filterStore = useFilterStore();
  const notices = useNoticeStore();
  const address = filterStore.comfyuiUrl;
  if (!comfyuiLink(address, {})) {
    notices.push({
      level: "error",
      text: "The ComfyUI address in Settings is not a web address.",
    });
    return;
  }
  const desktop = desktopBridge();
  const tab = desktop?.openComfyui ? null : window.open("", "_blank");
  if (!tab && !desktop?.openComfyui) {
    notices.push({
      level: "error",
      text: "The browser blocked the new tab. Allow pop-ups for PixlStash, then try again.",
    });
    return;
  }
  if (tab) tab.opener = null;
  try {
    const held = await hold();
    const link = comfyuiLink(address, {
      pixlstash_run: held.opening,
      pixlstash_of: held.workflow_id,
    });
    if (tab) tab.location.replace(link);
    else if (!(await desktop.openComfyui(link))) {
      throw new Error("the shell refused the link");
    }
  } catch (err) {
    tab?.close();
    console.warn("[workflows] could not open a held graph in ComfyUI", err);
    notices.push({ level: "error", text: errorMessage(err, failText) });
  }
}

/**
 * How soon a ComfyUI that did not answer is asked again, in ms: doubling from
 * the first to the most while it stays down, since each ask costs the server
 * a request to ComfyUI and a log line.
 */
export const COMFYUI_RECHECK_MS = 5000;
export const COMFYUI_RECHECK_MAX_MS = 30000;

/**
 * Whether a workflow can be opened in ComfyUI from here, and what opens.
 *
 * Asks ComfyUI for the PixlStash node once per mount and again when the
 * address in Settings changes. A ComfyUI that does not answer is asked again
 * until it does, backing off from `COMFYUI_RECHECK_MS` to
 * `COMFYUI_RECHECK_MAX_MS`, so starting it wakes the buttons without leaving
 * the screen; and once more whenever the window regains focus, which is when
 * the owner comes back from starting or stopping it, and which starts the
 * back-off over, since ComfyUI is then likely still loading.
 */
export function useComfyuiOpen() {
  const filterStore = useFilterStore();
  const desktop = desktopBridge();

  /** Whether there is a ComfyUI to open, and a way to open it from here. */
  const canOpenComfyui = computed(
    () => Boolean(filterStore.comfyuiUrl) && (!desktop || !!desktop.openComfyui),
  );

  /**
   * ComfyUI answered that it lacks the ComfyUI-PixlStash node's
   * `open_workflow.js`, so a link would open on whatever it had last. Only a
   * definite no refuses.
   */
  const comfyuiLacksNode = ref(false);
  /**
   * The nodes ComfyUI has are from before a workflow opened there saved back
   * (#1852): what opens is a copy. Only a definite no.
   */
  const comfyuiOpensACopy = ref(false);
  /**
   * The nodes ComfyUI has cannot open a held graph (no `open_run.js`), so
   * `openHeldInComfyui` would open on nothing. Only a definite no.
   */
  const comfyuiOpensNoRuns = ref(false);
  /**
   * The server could not reach ComfyUI at the address in Settings (the node
   * check answers `null`): a link would open a tab on nothing. A failed ask of
   * PixlStash's own server is not this, and leaves the verbs to try.
   */
  const comfyuiDown = ref(false);
  /** Why the verbs are refused while `comfyuiDown`, without a full stop. */
  const comfyuiDownReason = computed(
    () =>
      `ComfyUI is not running at ${filterStore.comfyuiUrl}. ` +
      "Start ComfyUI to open it there",
  );

  let nodeCheck = 0;
  let recheck = null;
  let delay = COMFYUI_RECHECK_MS;
  function askLater(url) {
    recheck = setTimeout(() => ask(url), delay);
    delay = Math.min(delay * 2, COMFYUI_RECHECK_MAX_MS);
  }
  async function ask(url) {
    const check = ++nodeCheck;
    clearTimeout(recheck);
    recheck = null;
    if (!url) return;
    try {
      const {
        can_open_workflows: canOpen,
        saves_back: savesBack,
        opens_runs: opensRuns,
      } = await getPixlstashNode();
      if (check !== nodeCheck) return;
      comfyuiDown.value = canOpen === null;
      comfyuiLacksNode.value = canOpen === false;
      comfyuiOpensACopy.value = canOpen === true && savesBack === false;
      comfyuiOpensNoRuns.value = opensRuns === false;
      if (comfyuiDown.value) askLater(url);
      else delay = COMFYUI_RECHECK_MS;
    } catch (err) {
      console.warn("[workflows] could not ask ComfyUI for the PixlStash node", err);
      // A refusal already standing keeps being re-asked, or it would stick.
      if (check === nodeCheck && comfyuiDown.value) askLater(url);
    }
  }

  const currentUrl = () => canOpenComfyui.value && filterStore.comfyuiUrl;
  // A new address starts from nothing: the answer about one ComfyUI says
  // nothing of the next. A re-ask of the same one keeps its answer until the
  // new one lands, so the buttons do not flicker on every poll.
  watch(
    currentUrl,
    (url) => {
      comfyuiDown.value = false;
      comfyuiLacksNode.value = false;
      comfyuiOpensACopy.value = false;
      comfyuiOpensNoRuns.value = false;
      delay = COMFYUI_RECHECK_MS;
      void ask(url);
    },
    { immediate: true },
  );
  const onFocus = () => {
    delay = COMFYUI_RECHECK_MS;
    void ask(currentUrl());
  };
  if (typeof window !== "undefined") window.addEventListener("focus", onFocus);
  onScopeDispose(() => {
    nodeCheck += 1;
    clearTimeout(recheck);
    if (typeof window !== "undefined") {
      window.removeEventListener("focus", onFocus);
    }
  });

  return {
    canOpenComfyui,
    comfyuiLacksNode,
    comfyuiOpensACopy,
    comfyuiOpensNoRuns,
    comfyuiDown,
    comfyuiDownReason,
  };
}
