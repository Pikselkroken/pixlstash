// What a training-run card and the rail say about one run (#2169).
//
// A card is `{ key, name, run, job, live }` from `useTrainingRunsStore`: `run`
// is the folder listing's entry (null while a queued run has no folder yet),
// `job` the PixlStash job that trained it (null for a run trained elsewhere).

/** A job in one of these is training, or about to. */
export const LIVE_STATUSES = ["queued", "running", "stopping"];

/**
 * True while the worker is not done with a job: it is training or about to,
 * or it has trained and is still drawing its deferred previews or being
 * imported. The backend stores that last state as `completed` with no
 * `completed_at`, which it writes once the import lands (#2291).
 */
export function isBusyJob(job) {
  if (!job) return false;
  if (LIVE_STATUSES.includes(job.status)) return true;
  return job.status === "completed" && !job.completed_at;
}

/** True when the run wrote the bare final file that confirms it finished. */
export function hasBareFinal(run) {
  return (run?.checkpoints || []).some((cp) => cp.step === null);
}

/** True when PixlStash trained the run and it failed. */
export function failed(card) {
  return card?.job?.status === "error";
}

/**
 * The shelf model a run made, for "Go to model": its stack's cover, which the
 * job listing names first. Null for a run that put nothing on the shelf, and
 * for one trained elsewhere (only a job knows its models).
 */
export function resultModelId(card) {
  return card?.job?.model_ids?.[0] ?? null;
}

/** The backend's own cap on a failure summary (`live_view._FAILURE_CHARS`). */
const FAILURE_CHARS = 200;

/**
 * Why a job failed, short enough to show, and the whole text when it says more.
 *
 * The backend writes `error` as one sentence and keeps the trainer's text in
 * `report`, but a run that failed before it summarised (or on another path)
 * carries the whole paragraph in `error`. That is cut at a word, like the
 * backend cuts, and its full text is offered instead.
 *
 * @returns {{short: string, full: string}} `full` is "" when `short` is all of it.
 */
export function failureText(job) {
  const error = String(job?.error ?? "").trim();
  const report = String(job?.report ?? "").trim();
  let short = error;
  if (short.length > FAILURE_CHARS) {
    const cut = short.slice(0, FAILURE_CHARS - 1);
    const space = cut.lastIndexOf(" ");
    short = `${(space > FAILURE_CHARS / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
  }
  const whole = report || error;
  return { short, full: whole && whole !== short ? whole : "" };
}

/** The highest step a run saved a checkpoint at, or 0. */
export function lastSavedStep(run) {
  return Math.max(
    0,
    ...(run?.checkpoints || []).map((cp) => cp.step ?? 0),
  );
}

/** The checkpoint a run's last save left: the bare final, else the highest step. */
export function lastCheckpoint(run) {
  const checkpoints = run?.checkpoints || [];
  return (
    checkpoints.find((cp) => cp.step == null) ??
    checkpoints.reduce(
      (best, cp) => (best == null || cp.step > best.step ? cp : best),
      null,
    )
  );
}

/**
 * The folders an import may land in: the same two exclusions a move applies,
 * for the same reasons. A `source` folder is taken from rather than written
 * into (the server refuses it), and an `external` folder is shared with other
 * software.
 */
export function importDestinations(folders) {
  return (folders || []).filter(
    (folder) => folder.kind !== "source" && folder.movable !== "external",
  );
}

/**
 * Where an import lands unless the owner picks: the default folder from the
 * Model folders dialog, which is the managed store until the owner picks
 * another.
 */
export function defaultDestination(destinations) {
  return (
    destinations.find((f) => f.is_default) ??
    destinations.find((f) => f.kind === "managed") ??
    destinations[0] ??
    null
  );
}

/** A queued run that is held by another run ahead of it, not by the GPU. */
export const WAITING_BEHIND_RUN = "Waiting for the run ahead of it";

/**
 * True when the live view's `waiting` says the lease is queued behind another
 * run (`fifo`), so the wait is for that run, not for the GPU. Only `/live`
 * carries `waiting`; the job list does not, so its cards keep the plain text.
 */
export function isQueuedBehindRun(waiting) {
  return waiting?.code === "fifo";
}

/** The run's state in words, from its job. */
export function jobStatus(job) {
  switch (job?.status) {
    case "queued":
      // Only the live view knows a probe is running, as it does a phase.
      return job.phase === "checking" ? "Checking memory and speed" : "Waiting for the GPU";
    case "running":
      return job.total_steps
        ? `Training ${(job.step ?? 0).toLocaleString()} / ${job.total_steps.toLocaleString()}`
        : "Setting up";
    case "stopping":
      return "Stopping";
    case "stopped":
      // A run stopped before it reported a step has none to name.
      return job.step != null
        ? `Stopped at step ${job.step.toLocaleString()}`
        : "Stopped";
    case "error":
      return "Failed";
    case "completed":
      // Only the live view knows which: a job summary carries no phase.
      if (job.completed_at) return "Trained by PixlStash";
      if (job.phase === "previews") return "Generating previews";
      if (job.phase === "importing") return "Importing to the shelf";
      return "Finishing";
    default:
      return "Trained by PixlStash";
  }
}

/** The steps asked for, from the job or else the run's config. */
function totalOf(card) {
  return card.job?.total_steps ?? card.run?.total_steps ?? null;
}

/** How far it got: the job's step while it reports one, else the last save. */
function doneOf(card) {
  if (card.job && (card.live || card.job.status === "stopped")) {
    return card.job.step ?? 0;
  }
  return lastSavedStep(card.run);
}

/** "1,200 / 3,000 steps", "3,000 steps", "800 / 2,000 steps · stopped". */
export function stepsOf(card) {
  const total = totalOf(card);
  const done = doneOf(card);
  const finished = !card.live && hasBareFinal(card.run);
  const stopped = card.job?.status === "stopped" ? " · stopped" : "";
  if (finished) return `${(total ?? done).toLocaleString()} steps${stopped}`;
  if (total) {
    return `${done.toLocaleString()} / ${total.toLocaleString()} steps${stopped}`;
  }
  return `${done.toLocaleString()} steps${stopped}`;
}

/** The bar's fill in percent, or null when how far is not known. */
export function progressOf(card) {
  if (!card.live && hasBareFinal(card.run)) return 100;
  const total = totalOf(card);
  if (!total) return null;
  return Math.min(100, Math.round((doneOf(card) / total) * 100));
}

/** The base model as the run's config or job names it. */
export function baseOf(card) {
  return card.run?.base_model || card.job?.base_model || "Base model not recorded";
}

/**
 * The row `ModelMark` draws a run's base model from, or null with none
 * recorded. `source` is a job, a folder run or a live view: anything with
 * `base_model` and the server's `base_tile` (null for a base it does not
 * know, which then wears the generated mark, named by its last segment less
 * a model file's extension).
 */
export function baseMarkOf(source) {
  const raw = String(source?.base_model || "").trim();
  if (!raw) return null;
  const tile = source.base_tile || {};
  const name =
    tile.label ||
    raw
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .pop()
      .replace(/\.(safetensors|sft|ckpt|pt|pth|bin|gguf)$/i, "");
  return { ...tile, display_name: name, base_model: tile.label || raw };
}

/** The base model's mark for a card: the same source `baseOf` names. */
export function cardBaseMark(card) {
  return baseMarkOf(card.run?.base_model ? card.run : card.job);
}

/** The run's day: its newest checkpoint, else when its job was asked for. */
export function dateOf(card) {
  const seconds = card.run?.modified_at;
  const at =
    seconds != null
      ? new Date(seconds * 1000)
      : card.job?.created_at
        ? new Date(card.job.created_at)
        : null;
  if (!at || Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function minutes(seconds) {
  const m = Math.max(1, Math.round(seconds / 60));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

/** "about 1 h 10 min" from the live view's remaining range, or "". */
export function timeLeft(speed) {
  const low = speed?.remaining_low_s;
  const high = speed?.remaining_high_s;
  if (low == null || high == null) return "";
  return `about ${minutes((low + high) / 2)}`;
}

/** Status of a page drawn from a run's folder alone: no job ever trained it. */
export const FOLDER_RUN_STATUS = "folder";

/**
 * A run trained outside PixlStash, in the shape `GET /training/jobs/{id}/live`
 * answers, so the run page can draw it: the columns are the steps it saved or
 * sampled at, the cells its previews. Everything only a job records (loss,
 * memory, speed, the pictures it was trained on) is empty, never 0.
 */
export function folderRunLive(run, folderId) {
  const samples = run.samples || [];
  const columns = [
    ...new Set([
      ...samples.map((s) => s.step),
      ...(run.checkpoints || []).map((cp) => cp.step).filter((step) => step != null),
    ]),
  ].sort((a, b) => a - b);
  const last = columns[columns.length - 1] ?? 0;
  const words = (run.trigger_words || []).map((w) => w.toLowerCase());
  const indexes = [...new Set(samples.map((s) => s.index))].sort((a, b) => a - b);
  return {
    name: run.name,
    status: FOLDER_RUN_STATUS,
    phase: null,
    step: hasBareFinal(run) ? Math.max(last, run.total_steps ?? 0) : last,
    total_steps: run.total_steps ?? null,
    base_model: run.base_model ?? null,
    base_tile: run.base_tile ?? null,
    trigger_word: run.trigger_words?.[0] || "",
    columns,
    rows: indexes.map((index) => {
      const prompt = run.sample_prompts?.[index] || "";
      return {
        index,
        prompt,
        // Unknown without a prompt or a trigger word to look for.
        with_trigger:
          prompt && words.length
            ? words.some((w) => prompt.toLowerCase().includes(w))
            : null,
        cells: columns.map((step) => {
          const filename =
            samples.find((s) => s.step === step && s.index === index)?.filename || null;
          return { state: filename ? "sample" : "missing", filename };
        }),
      };
    }),
    run_folder: { folder_id: folderId, run_name: run.name },
    coverable: [],
    cover_step: null,
    loss_curve: [],
    phases: [],
    dataset_picture_ids: [],
    dataset_snapshot_id: null,
  };
}

/**
 * The samples as prompts × steps, at most the last `columns` sampled steps.
 *
 * From the live view's rows when PixlStash trained the run (it knows which
 * prompts carry the trigger word); otherwise from the folder's samples, named
 * by the config's prompts. Returns `{ steps, rows: [{ prompt, noTrigger,
 * cells: [filename|""] }] }`; `noTrigger` is true only when it is known.
 */
export function sampleGrid({ live, run, columns = 4 }) {
  if (live?.rows?.length) {
    const drawn = (live.columns || [])
      .map((step, i) => ({ step, i }))
      .filter(({ i }) => live.rows.some((row) => row.cells[i]?.state === "sample"))
      .slice(-columns);
    return {
      steps: drawn.map(({ step }) => step),
      rows: live.rows.map((row) => ({
        prompt: row.prompt || `Prompt ${row.index + 1}`,
        noTrigger: row.with_trigger === false,
        cells: drawn.map(({ i }) =>
          row.cells[i]?.state === "sample" ? row.cells[i].filename : "",
        ),
      })),
    };
  }
  const samples = run?.samples || [];
  const steps = [...new Set(samples.map((s) => s.step))]
    .sort((a, b) => a - b)
    .slice(-columns);
  const indexes = [...new Set(samples.map((s) => s.index))].sort((a, b) => a - b);
  const words = (run?.trigger_words || []).map((w) => w.toLowerCase());
  return {
    steps,
    rows: indexes.map((index) => {
      const prompt = run?.sample_prompts?.[index] || "";
      return {
        prompt: prompt || `Prompt ${index + 1}`,
        noTrigger:
          Boolean(prompt) &&
          words.length > 0 &&
          !words.some((w) => prompt.toLowerCase().includes(w)),
        cells: steps.map(
          (step) =>
            samples.find((s) => s.step === step && s.index === index)?.filename ||
            "",
        ),
      };
    }),
  };
}
