// Starting downloads of missing models (`POST /model-downloads`), for the
// one-file button and the menus' *Download missing models*.
//
// Says so with one passing notice whose Show opens the task list, where each
// download's own bar and cancel are; `useTasksStore` tells how each ended.

import { startModelDownload } from "../api/modelDownloads";
import { preflightWorkflowRun } from "../api/workflows";
import { DEFAULT_TIMEOUTS, useNoticeStore } from "../stores/useNoticeStore";
import { useSidebarStore } from "../stores/useSidebarStore";
import { useTasksStore } from "../stores/useTasksStore";
import { errorDetail } from "../utils/apiError";
import { UNCHECKED_CODES } from "../utils/runReasons";

/** A loader's name without the subfolder it may put in front. */
export function modelBaseName(file) {
  return String(file).split(/[\\/]/).pop();
}

/**
 * The files a run pre-flight answer says can be downloaded: the missing
 * models marked `downloadable`, and the LoRAs the run leaves out because this
 * ComfyUI lacks them (not the owner's skips, nor the recipe's).
 *
 * @param {Object} answer - `POST /workflows/run/preflight`'s body.
 * @returns {string[]}
 */
export function downloadableInPreflight(answer) {
  const groups = answer?.groups ?? [];
  const models = groups
    .flatMap((group) => group.reasons ?? [])
    .filter((reason) => reason.code === "missing_models")
    .flatMap((reason) => reason.models ?? [])
    .filter((model) => model?.file && model.downloadable)
    .map((model) => String(model.file));
  const loras = groups
    .flatMap((group) => group.bypassed_loras ?? [])
    .filter((lora) => lora?.file && lora.downloadable)
    .filter((lora) => !lora.requested && !lora.recipe)
    .map((lora) => String(lora.file));
  return [...models, ...loras];
}

/**
 * What *cards* lack that PixlStash knows where to get, by asking each one's
 * run pre-flight (cached: ComfyUI's node list as last read).
 *
 * @param {Object[]} cards - workflow cards; not drafts, which have no graph.
 * @returns {Promise<{files: string[], unasked: number}>} the files, and how
 *   many cards could not be asked or ComfyUI did not answer for (logged).
 */
export async function downloadableInWorkflows(cards) {
  const files = [];
  let unasked = 0;
  for (const card of cards) {
    try {
      const answer = await preflightWorkflowRun(
        { workflow_id: card.id, values: [] },
        { cached: true },
      );
      // ComfyUI never answered: an empty list means unknown, not nothing.
      const unchecked = (answer?.groups ?? [])
        .flatMap((group) => group.reasons ?? [])
        .some((reason) => UNCHECKED_CODES.includes(reason.code));
      if (unchecked) {
        unasked += 1;
        console.warn(
          "Could not check what a workflow is missing: ComfyUI did not answer",
          card.id,
        );
        continue;
      }
      files.push(...downloadableInPreflight(answer));
    } catch (err) {
      unasked += 1;
      console.warn("Could not check what a workflow is missing", card.id, err);
    }
  }
  return { files, unasked };
}

/**
 * Start downloading *files*, once each by name, skipping any already running.
 *
 * @param {string[]} files - names as the workflows' loaders spell them.
 * @returns {Promise<number>} how many downloads started.
 */
export async function downloadModels(files) {
  const tasksStore = useTasksStore();
  const notices = useNoticeStore();
  // By basename: a download's `file` is the name it lands under, never the
  // loader's subfolder spelling.
  const byName = new Map();
  let running = 0;
  for (const file of files) {
    const name = modelBaseName(file);
    if (byName.has(name)) continue;
    if (tasksStore.runningDownloadFiles.has(name)) running += 1;
    else byName.set(name, file);
  }
  const started = [];
  for (const [name, file] of byName) {
    try {
      tasksStore.downloadStarted(await startModelDownload(file));
      started.push(name);
    } catch (err) {
      notices.error(errorDetail(err) || `Could not download ${name}.`, {
        key: `model-download-refused-${name}`,
      });
    }
  }
  if (started.length) {
    notices.push({
      level: "info",
      text:
        started.length === 1
          ? `Downloading ${started[0]}.`
          : `Downloading ${started.length} models.`,
      action: {
        label: "Show",
        handler: () => useSidebarStore().showTasksTab(),
      },
      // Passing: the Tasks tab keeps the bars, so this need not stay.
      timeout: DEFAULT_TIMEOUTS.info,
      key:
        started.length === 1
          ? `model-download-started-${started[0]}`
          : "model-downloads-started",
    });
  } else if (running && !byName.size) {
    // Everything asked for is on its way already, which is not nothing.
    notices.push({
      level: "info",
      text: "Already downloading: the Tasks list in the title bar shows how far.",
      action: {
        label: "Show",
        handler: () => useSidebarStore().showTasksTab(),
      },
      timeout: DEFAULT_TIMEOUTS.info,
      key: "model-downloads-already-running",
    });
  }
  return started.length;
}
