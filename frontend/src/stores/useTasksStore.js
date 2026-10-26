import { computed, onScopeDispose, reactive, ref } from "vue";
import { defineStore } from "pinia";
import { isReadOnly, onSessionReset } from "../utils/apiClient";
import { getWorkerProgress } from "../api/workers";
import { abortRun } from "../api/comfyui";
import { cancelModelDownload } from "../api/modelDownloads";
import { formatModelSize } from "../utils/modelShelf";
import { cancelLease, freeComfyui, runLeaseAnyway } from "../api/resources";
import { deviceVramBar, runKey, runMatches } from "../utils/gpuMemory";
import { useNoticeStore } from "./useNoticeStore";

// Adaptive poll cadence (ms). Fast while the Tasks tab is open or something is
// actively running; slow when the app is merely idle-watching for new work.
const POLL_INTERVAL_ACTIVE_MS = 2000;
const POLL_INTERVAL_IDLE_MS = 5000;

// A backend worker lingers in the active list for this long after its last
// observed activity, so brief gaps between batches don't make a row flicker out.
const WORKER_REMOVE_GRACE_SECONDS = 10;
// Window the displayed "/s" is measured over. It has to be long enough to span
// at least one commit of the coarsest worker, because progress lands in whole
// batches: descriptions commit 32 pictures at once and a slow captioner takes
// half a minute over them.
const RATE_AVERAGE_WINDOW_SECONDS = 60;
// How much sparkline history to retain per worker.
const SERIES_WINDOW_SECONDS = 120;

/** "Downloading… 1.2 GB of 6.5 GB", or the bytes alone while the size is unknown. */
export function downloadMessage(download) {
  const received = formatModelSize(Number(download?.received) || 0);
  const total = Number(download?.total) || 0;
  return total
    ? `Downloading… ${received} of ${formatModelSize(total)}`
    : `Downloading… ${received}`;
}

/**
 * Cross-component "what is the app working on right now" store.
 *
 * This is the single source of truth for active background work, and the single
 * poller of GET /workers/progress. It exists so the Tasks tab, the animated
 * Tasks-tab indicator, and the app-wide stats-sidebar activity light all read
 * the same state, and so we never poll the endpoint from two places at once.
 *
 * Three kinds of work are merged into one `activeEntries` list:
 *  - backend workers (quality scoring, tagging, embeddings, faces, likeness…),
 *    fetched from /workers/progress;
 *  - ComfyUI runs in flight, which the backend follows and reports in the same
 *    poll (`runs`), so a run's row is there on every view and in every tab,
 *    whoever started it;
 *  - model downloads (`downloads`, POST /model-downloads), one row each;
 *  - the async import, which is frontend-driven (ImageImporter).
 */
export const useTasksStore = defineStore("tasks", () => {
  // ── Backend worker state ──────────────────────────────────────────────────
  const workerSnapshots = ref({});
  const systemUsage = ref(null);
  const series = ref({}); // key → [{ t, rate, current }]
  const nowSeconds = ref(Date.now() / 1000);

  // Non-reactive accumulation bookkeeping (closure-local, never rendered).
  const lastSnapshot = new Map(); // key → { current, t }
  const lastActiveAtByWorker = new Map();
  const lastProgressAtByWorker = new Map();

  // ── ComfyUI runs (backend-reported) ───────────────────────────────────────
  // The runs in flight as the last poll listed them, oldest first:
  // [{ run_id, prompt_id, label, workflow_id, status: "waiting" | "queued" |
  // "running", percent }]. A `waiting` run has no prompt_id yet.
  const comfyuiRuns = ref([]);
  // The runs that ended, newest first, as the last poll listed them
  // (`finished`, #2050): [{ id, workflow_id, label, ended_at, status,
  // message, made, picture_ids, offer }]. `offer` marks the one whose changes
  // from the workflow can still be saved.
  const finishedRuns = ref([]);
  // Which Finished rows are open, by id. A run with changes opens by itself,
  // once, when its offer arrives; one the first poll already listed with it
  // (a reload, a second tab) comes back folded. An offer can arrive a poll
  // after its run (a run that ended before its request was answered), so it
  // is the offer that is remembered. In the store so a tab switch keeps it.
  const finishedOpen = ref({});
  // ponytail: grows by one id per offer for the life of the page.
  const offersSeen = new Set();
  let finishedPolled = false;

  /**
   * Take the poll's `finished`, opening the offers that arrived since the
   * last. `null` (the server could not read them) keeps what is shown.
   */
  function noteFinished(list) {
    if (!Array.isArray(list)) return;
    const opened = { ...finishedOpen.value };
    for (const run of list) {
      if (!run.offer || offersSeen.has(run.id)) continue;
      offersSeen.add(run.id);
      if (finishedPolled) opened[run.id] = true;
    }
    finishedPolled = true;
    finishedOpen.value = opened;
    finishedRuns.value = list;
  }

  function toggleFinished(id) {
    finishedOpen.value = { ...finishedOpen.value, [id]: !finishedOpen.value[id] };
  }

  /** A workflow's run set was saved: no run of it offers it any more. */
  function dropOffer(workflowId) {
    finishedRuns.value = finishedRuns.value.map((run) =>
      run.workflow_id === workflowId ? { ...run, offer: false } : run,
    );
  }

  /** Take dismissed or cleared runs off the list before the next poll does. */
  function forgetFinished(ids) {
    const gone = new Set(ids);
    finishedRuns.value = finishedRuns.value.filter((run) => !gone.has(run.id));
  }
  // The GPU's state as the poll reports it (`resources`), or null when the
  // backend does not send it (an older server, or a read-only session).
  const resources = ref(null);

  /**
   * Stop ComfyUI: the run in progress is interrupted and its queue emptied,
   * which is every run in flight, whichever tab or tool started it. Each
   * then ends as stopped, and the next poll drops it.
   */
  async function abortComfyuiRuns() {
    try {
      await abortRun();
    } catch (e) {
      console.warn("tasks: ComfyUI could not be told to stop", e);
      useNoticeStore().error("ComfyUI could not be told to stop.", {
        key: "comfyui-abort-failed",
      });
    }
    nudge();
  }

  // ── Model downloads (backend-reported) ────────────────────────────────────
  // As the last poll listed them: [{ id, file, status, received, total,
  // percent, error }]. A finished one stays listed for a few seconds, which is
  // how its ending is heard here and told once (`endedDownloads`).
  const modelDownloads = ref([]);
  const seenDownloadStatus = new Map();
  // Bumped when a download lands, so a view showing what is missing asks again.
  const downloadsLanded = ref(0);

  const runningDownloadFiles = computed(
    () =>
      new Set(
        modelDownloads.value
          .filter((d) => d.status === "running")
          .map((d) => d.file),
      ),
  );

  /** The downloads still on their way, as the last poll listed them. */
  const runningDownloads = computed(() =>
    modelDownloads.value.filter((d) => d.status === "running"),
  );

  /**
   * The running download of *file* (by its own name, any case, as the server
   * matches it) or of *sha256*, or null. What a workflow row or a set member
   * asks to say it is on its way rather than missing.
   *
   * *slot*, the set slot the asker's kind fills, keeps a name match to a
   * download of that kind, as the server's `running_download` does with a
   * folder: a VAE named like a checkpoint being fetched is not on its way.
   * Nor is a file asked for by a digest the download does not have.
   */
  function runningDownload({ file = "", sha256 = "", slot = "" } = {}) {
    const name = String(file).split(/[\\/]/).pop().toLowerCase();
    const digest = String(sha256 || "").toLowerCase();
    return (
      runningDownloads.value.find(
        (d) =>
          (digest && d.sha256 === digest) ||
          (name &&
            (!slot || !d.slot || d.slot === slot) &&
            // Two known digests that differ are two files, named alike or not.
            !(digest && d.sha256) &&
            String(d.file).toLowerCase() === name),
      ) ?? null
    );
  }

  function noteDownloads(list) {
    modelDownloads.value = list;
    const notices = useNoticeStore();
    for (const download of list) {
      const before = seenDownloadStatus.get(download.id);
      seenDownloadStatus.set(download.id, download.status);
      // Only an ending this tab saw happen: one that ended before the first
      // poll is old news.
      if (before !== "running" || download.status === "running") continue;
      if (download.status === "done") {
        downloadsLanded.value += 1;
        notices.push({
          level: "success",
          text: `Downloaded ${download.file}.`,
          key: `model-download-${download.id}`,
        });
      } else if (download.status === "failed") {
        notices.error(
          `Could not download ${download.file}: ${download.error || "it failed."}`,
          { key: `model-download-${download.id}` },
        );
      }
    }
    for (const id of Array.from(seenDownloadStatus.keys())) {
      if (!list.some((download) => download.id === id)) {
        seenDownloadStatus.delete(id);
      }
    }
  }

  /** Record a download this tab just started, so its ending is told here. */
  function downloadStarted(download) {
    if (!download?.id) return;
    seenDownloadStatus.set(download.id, "running");
    modelDownloads.value = [
      ...modelDownloads.value.filter((d) => d.id !== download.id),
      download,
    ];
    nudge();
  }

  async function cancelDownload(id) {
    try {
      await cancelModelDownload(id);
    } catch (e) {
      console.warn("tasks: the download could not be cancelled", e);
      useNoticeStore().error("The download could not be cancelled.", {
        key: "model-download-cancel-failed",
      });
    }
    nudge();
  }

  const downloadEntries = computed(() =>
    modelDownloads.value
      .filter((download) => download.status === "running")
      .map((download) => ({
        kind: "download",
        key: `download-${download.id}`,
        id: download.id,
        run: {
          status: "running",
          percent: Number(download.percent) || 0,
          label: download.file,
          message: downloadMessage(download),
        },
      })),
  );

  /**
   * Start a waiting lease now, past the memory it is short of ("Run anyway").
   * A refusal (409: no longer waiting) is an error notice; the poll then shows
   * what is true now.
   */
  async function runWaitingAnyway(leaseId) {
    return waitingAction(() => runLeaseAnyway(leaseId), "Could not start that run.");
  }

  /** Cancel a waiting lease, and the ComfyUI run held behind it. */
  async function cancelWaiting(leaseId) {
    return waitingAction(() => cancelLease(leaseId), "Could not cancel that run.");
  }

  /** Ask ComfyUI to free its cache for a non-ComfyUI job that is short of memory. */
  async function freeComfyuiCache() {
    return waitingAction(
      () => freeComfyui(),
      "ComfyUI could not free its cache.",
    );
  }

  async function waitingAction(call, failure) {
    let answer = null;
    try {
      answer = await call();
    } catch (e) {
      console.warn(`tasks: ${failure}`, e);
      useNoticeStore().error(failure, { key: `gpu-action-failed:${failure}` });
    }
    nudge();
    return answer;
  }

  // ── Import run state (frontend-driven, #459) ──────────────────────────────
  // The async import (ImageImporter) surfaces its server-side Phase B here as a
  // determinate task row: the two-phase dialog auto-hides at the safe
  // transition and the import continues as just another entry in the Tasks
  // tab. runId → { status, percent, message, label,
  // current, total, abortable }.
  //
  // `abortable` is the honest gate for the Tasks-tab cancel affordance: it is
  // true ONLY while a client-side abort can genuinely act (the pre-commit upload
  // window, where aborting the in-flight request stops sending remaining bytes).
  // Once the staging session is committed the import is a background server task
  // that the client cannot stop without a backend cancel endpoint, so the run is
  // marked non-abortable and no cancel control is offered. See ImageImporter.vue.
  const importRuns = reactive({});
  const importAbortHandlers = new Map();

  function setImportRun(runId, run) {
    if (!runId) return;
    const current = Number(run?.current);
    const total = Number(run?.total);
    importRuns[runId] = {
      status: run?.status || "running",
      percent: Number(run?.percent) || 0,
      message: run?.message || "Importing on the server…",
      label: run?.label || "Importing pictures",
      current: Number.isFinite(current) ? current : 0,
      total: Number.isFinite(total) ? total : 0,
      abortable: Boolean(run?.abortable),
    };
  }

  function clearImportRun(runId) {
    if (runId && runId in importRuns) delete importRuns[runId];
  }

  function registerImportAbort(runId, handler) {
    if (runId && typeof handler === "function") {
      importAbortHandlers.set(runId, handler);
    }
  }

  function unregisterImportAbort(runId) {
    importAbortHandlers.delete(runId);
  }

  function abortImportRun(runId) {
    const handler = importAbortHandlers.get(runId);
    if (handler) handler();
  }

  // The backend surfaces the async import (#459) through the SAME generic
  // worker-progress snapshot as detection/watch-folder rows, under this key
  // (TaskType.PICTURE_IMPORT.value). When our own frontend-driven import run is
  // active it IS the single import row (driven by /status polling across both
  // stages), so we suppress the generic snapshot entry to avoid a double row.
  // With no active import run (e.g. after a mid-import tab refresh) it is NOT
  // suppressed, so the import still shows as a fallback worker row.
  const IMPORT_WORKER_KEY = "PictureImportTask";

  // ── Derived: active work ──────────────────────────────────────────────────
  const activeWorkerEntries = computed(() => {
    const suppressImportWorker = Object.keys(importRuns).length > 0;
    return Object.entries(workerSnapshots.value || {})
      .filter(([key, snapshot]) => {
        if (!snapshot) return false;
        if (suppressImportWorker && key === IMPORT_WORKER_KEY) return false;
        // `active: true` is decisive. `active: false` is NOT - it only means
        // nothing is in flight *this instant*, and a worker chewing through a
        // library is idle between every batch: the planner submits, the batch
        // runs, inflight drops to 0, and the next batch arrives up to a
        // backoff later. This used to `return snapshot.active` for either
        // value, which made the grace window below unreachable - the backend
        // always sends the field - and the row vanished in every gap. Watching
        // a face pass grind through twelve thousand pictures, the Tasks tab
        // read "nothing running" most of the time.
        if (snapshot.active === true) return true;
        // The grace below exists for a worker between batches of a pass. A
        // worker whose whole job is zero rows ("File cleanup 0, 0.00/s") has
        // no batches to be between - it ran, found nothing, and lingering for
        // ten seconds only reads as a row that never does anything.
        if (!(Number(snapshot.total) > 0)) return false;
        const lastActiveAt = Number(lastActiveAtByWorker.get(key) || 0);
        const lastProgressAt = Number(lastProgressAtByWorker.get(key) || 0);
        const latestActivityAt = Math.max(lastActiveAt, lastProgressAt);
        return (
          latestActivityAt > 0 &&
          nowSeconds.value - latestActivityAt <= WORKER_REMOVE_GRACE_SECONDS
        );
      })
      .map(([key, snapshot]) => ({ kind: "worker", key, snapshot }));
  });

  // ── GPU leases (backend-reported, `resources`) ────────────────────────────
  const leases = computed(() => resources.value?.leases ?? []);

  /** The lease a ComfyUI run holds, joined by run id; null when it has none. */
  function leaseOfRun(run) {
    return (
      leases.value.find(
        (lease) => lease.run_id && runMatches(run, lease.run_id),
      ) ?? null
    );
  }

  // The AI Toolkit run that holds the card: one row, with the step, total and
  // latest loss the run reports on its lease. A waiting one is listed by
  // waitingEntries, through the same lease.
  const aitoolkitEntries = computed(() =>
    leases.value
      .filter(
        (lease) => lease.tenant === "aitoolkit" && lease.state === "granted",
      )
      .map((lease) => ({
        kind: "aitoolkit",
        key: `lease:${lease.id}`,
        label: lease.label || "AI Toolkit",
        figureMb: lease.held_mb ?? null,
        progress: lease.progress ?? null,
        // The training run's uuid: its page is `/models/runs?run=<uuid>`.
        jobId: lease.job_id ?? null,
      })),
  );

  // One row per ComfyUI run that is not held: the run's own label, its percent
  // and the VRAM its lease holds. ComfyUI works through them one at a time, and
  // the abort on a row stops every run in flight.
  const comfyuiEntries = computed(() =>
    comfyuiRuns.value
      .filter((run) => run.status !== "waiting")
      .map((run) => ({
        kind: "comfyui",
        key: `run:${runKey(run)}`,
        runId: runKey(run),
        run: {
          status: run.status,
          percent: Number(run.percent) || 0,
          label: run.label || "ComfyUI",
          message:
            run.status === "running" ? "ComfyUI running…" : "Queued in ComfyUI",
        },
        figureMb: leaseOfRun(run)?.held_mb ?? null,
      })),
  );

  // "Waiting for the GPU": a lease in the wait, in the order it was asked for
  // (`waiting_reason.position`). A ComfyUI run that fits may start before
  // ComfyUI runs listed above it. A ComfyUI run held by PixlStash is
  // listed once, through its lease; one with no lease yet still gets a row.
  const waitingEntries = computed(() => {
    const out = [];
    const coveredRuns = new Set();
    for (const lease of leases.value) {
      if (lease.state !== "waiting") continue;
      const run = comfyuiRuns.value.find(
        (entry) =>
          entry.status === "waiting" &&
          lease.run_id &&
          runMatches(entry, lease.run_id),
      );
      if (run) coveredRuns.add(runKey(run));
      const reason = lease.waiting_reason ?? null;
      out.push({
        kind: "waiting",
        key: `lease:${lease.id}`,
        lease,
        runId: lease.run_id || "",
        tenant: lease.tenant,
        label: lease.label || run?.label || "Waiting",
        needMb: lease.need_mb ?? null,
        text: reason?.text || "",
        position: reason?.position ?? Number.POSITIVE_INFINITY,
        actions: reason?.actions ?? [],
      });
    }
    for (const run of comfyuiRuns.value) {
      if (run.status !== "waiting" || coveredRuns.has(runKey(run))) continue;
      out.push({
        kind: "waiting",
        key: `run:${runKey(run)}`,
        lease: null,
        runId: runKey(run),
        tenant: "comfyui",
        label: run.label || "ComfyUI",
        needMb: null,
        text: "",
        position: Number.POSITIVE_INFINITY,
        actions: [],
      });
    }
    // Stable, so rows with no position keep the order they came in.
    return out.sort((a, b) =>
      a.position === b.position ? 0 : a.position < b.position ? -1 : 1,
    );
  });

  // PixlStash's own background work, folded into one entry: one child per
  // active backend worker, plus every granted PixlStash light lease and every
  // task the backend stopped to make room (`preempted`). A lease is taken per
  // task, so a worker between batches holds none; folding by lease alone left
  // it, and every CPU-only worker, as a flat row beside the fold. A lease or a
  // stopped task naming a worker in the fold lands on that worker's child.
  // Null when there is none, so no fold row is drawn.
  const pixlstashEntry = computed(() => {
    const children = [];
    const byWorker = new Map();
    for (const entry of activeWorkerEntries.value) {
      const child = {
        key: `worker:${entry.key}`,
        label: entry.snapshot.label || entry.key,
        workerKey: entry.key,
        snapshot: entry.snapshot,
        mb: 0,
        onCpu: false,
        stopped: false,
        text: "",
        progress: null,
      };
      children.push(child);
      byWorker.set(entry.key, child);
    }
    for (const lease of leases.value) {
      if (lease.tenant !== "pixlstash" || lease.state !== "granted") continue;
      const own = lease.worker_type ? byWorker.get(lease.worker_type) : null;
      if (own) {
        own.mb += Number(lease.held_mb) || 0;
        own.onCpu = own.onCpu || Boolean(lease.on_cpu);
        continue;
      }
      const snapshot = lease.worker_type
        ? workerSnapshots.value?.[lease.worker_type]
        : null;
      children.push({
        key: `lease:${lease.id}`,
        label: lease.label || "PixlStash task",
        workerKey: lease.worker_type || null,
        snapshot: null,
        mb: Number(lease.held_mb) || 0,
        onCpu: Boolean(lease.on_cpu),
        stopped: false,
        text: "",
        progress: snapshot
          ? { current: Number(snapshot.current) || 0, total: Number(snapshot.total) || 0 }
          : null,
      });
    }
    for (const [index, stopped] of (resources.value?.preempted ?? []).entries()) {
      // Each waiting run that paused a worker names it once, so two runs can
      // name one worker: still one task, paused for both reasons.
      const own = stopped.worker_type ? byWorker.get(stopped.worker_type) : null;
      if (own) {
        own.stopped = true;
        if (stopped.text && !own.text.includes(stopped.text)) {
          own.text = own.text ? `${own.text} · ${stopped.text}` : stopped.text;
        }
        continue;
      }
      const child = {
        key: `preempted:${index}`,
        label: stopped.worker_type || "PixlStash task",
        workerKey: stopped.worker_type || null,
        snapshot: null,
        mb: 0,
        onCpu: false,
        stopped: true,
        text: stopped.text || "",
        progress: null,
      };
      children.push(child);
      if (stopped.worker_type) byWorker.set(stopped.worker_type, child);
    }
    if (!children.length) return null;
    return {
      kind: "pixlstash",
      key: "pixlstash",
      count: children.length,
      mb: children.reduce((sum, child) => sum + child.mb, 0),
      children,
    };
  });

  // Per-GPU bars: each card's split of what is on it, from `devices` and
  // `holders`. Empty when the backend sends no resources.
  const vramBars = computed(() => {
    const holders = resources.value?.holders ?? [];
    return (resources.value?.devices ?? []).map((device) => ({
      device,
      ...deviceVramBar(device, holders),
    }));
  });

  const importEntries = computed(() =>
    Object.entries(importRuns).map(([key, run]) => ({
      kind: "import",
      key,
      run,
    })),
  );

  // ComfyUI runs and imports lead: they are the work the user just kicked off
  // and is waiting on, so they read first in the Tasks tab, above the ambient
  // backend workers.
  const runningEntries = computed(() => [
    ...comfyuiEntries.value,
    ...downloadEntries.value,
    ...aitoolkitEntries.value,
    ...(pixlstashEntry.value ? [pixlstashEntry.value] : []),
    ...importEntries.value,
  ]);

  const activeEntries = computed(() => [
    ...runningEntries.value,
    ...waitingEntries.value,
  ]);

  // The PixlStash fold is one row but counts each task in it.
  const activeCount = computed(() =>
    activeEntries.value.reduce(
      (sum, entry) => sum + (entry.kind === "pixlstash" ? entry.count : 1),
      0,
    ),
  );
  const hasActiveTasks = computed(() => activeCount.value > 0);

  // The tagger's worker key is its TaskType value. While it is running, a
  // tag-filtered grid would otherwise be offered a "View changed externally"
  // pill after every eight-picture batch; readers hold theirs until this
  // goes false. Same grace window as the Tasks tab row, so a pass idling
  // between batches still counts as running.
  const TAGGER_WORKER_KEY = "TagTask";
  const taggingActive = computed(() =>
    activeWorkerEntries.value.some((entry) => entry.key === TAGGER_WORKER_KEY),
  );

  // ── Rate helpers (read by the Tasks tab for sparklines / "/s" labels) ──────
  // Progress made across the window divided by the time it took, which is the
  // throughput the label claims to show.
  //
  // The per-sample `rate` this used to average cannot answer that. A worker
  // commits a whole batch at once, so `current` sits still for every poll the
  // batch is running and then jumps: one sample of batch-size-over-poll-
  // interval, surrounded by zeroes. Averaging only the non-zero samples - the
  // old behaviour, meant to stop a gap between batches dragging the number
  // down - threw away exactly the ticks the work happened in and reported
  // 32 pictures over one 2-second poll no matter how long the batch took.
  // Moondream2 at one picture a second and JoyCaption at four both read "13/s".
  //
  // Counting the flat ticks is the fix: they are the batch running, not a
  // stall. A worker that has genuinely stopped falls to zero once its last
  // commit slides out of the window, and the row is dropped by
  // WORKER_REMOVE_GRACE_SECONDS long before that.
  function getLatestRate(key) {
    const samples = series.value[key] || [];
    if (samples.length < 2) return 0;
    const last = samples[samples.length - 1];
    const lastTime = Number(last?.t || 0);
    if (!lastTime) return 0;
    const cutoff = lastTime - RATE_AVERAGE_WINDOW_SECONDS;
    // `current` is total minus what is still to do, so queuing work (a retag
    // puts N pictures back in the queue) or deleting pictures drops it. Measure
    // from the most recent drop: a window straddling it nets out negative and
    // read 0.00/s for a full minute while the worker was busy.
    let start = 0;
    for (let i = samples.length - 1; i > 0; i -= 1) {
      if (Number(samples[i].current || 0) < Number(samples[i - 1].current || 0)) {
        start = i;
        break;
      }
    }
    const first = samples
      .slice(start)
      .find((s) => Number(s?.t || 0) >= cutoff);
    if (!first || first === last) return 0;
    const elapsed = lastTime - Number(first.t || 0);
    const done = Number(last.current || 0) - Number(first.current || 0);
    // The window is briefly shorter than one batch when polling starts
    // mid-batch, or right after a drop; that reads as no measurement rather
    // than as a wrong one.
    if (elapsed <= 0 || done <= 0) return 0;
    return done / elapsed;
  }

  // ── Polling ───────────────────────────────────────────────────────────────
  const tasksTabOpen = ref(false);
  // A count, not a flag: more than one inspector can host the Tasks panel, so
  // a boolean would let one panel's unmount drop the cadence back to idle
  // while another is still on screen. Today the two hosts are `v-if`/`v-else`
  // siblings and cannot overlap, but the panel is shared on purpose and the
  // model-detail and duplicates-evidence panes are next to take it.
  let openPanels = 0;
  function setTasksTabOpen(open) {
    openPanels = Math.max(0, openPanels + (open ? 1 : -1));
    tasksTabOpen.value = openPanels > 0;
    // Opening the tab should switch to the fast cadence immediately rather than
    // waiting out the current idle interval.
    if (tasksTabOpen.value && polling) reschedule(true);
  }

  let polling = false;
  let fetchInFlight = false;
  let timer = null;
  // Bumped on a credential change: a poll already on the wire answers for the
  // session that sent it, so its answer is dropped.
  let generation = 0;

  function desiredInterval() {
    return tasksTabOpen.value || hasActiveTasks.value
      ? POLL_INTERVAL_ACTIVE_MS
      : POLL_INTERVAL_IDLE_MS;
  }

  async function fetchProgress() {
    if (fetchInFlight) return;
    // Share / read-only sessions are not owners; the endpoint 403s for them, so
    // skip the request entirely rather than poll a guaranteed failure.
    if (isReadOnly.value) return;
    fetchInFlight = true;
    const asked = generation;
    try {
      const progress = await getWorkerProgress();
      if (asked !== generation) return;
      const workers = progress?.workers || {};
      systemUsage.value = progress?.process || progress?.system || null;
      comfyuiRuns.value = Array.isArray(progress?.runs) ? progress.runs : [];
      noteFinished(progress?.finished);
      noteDownloads(
        Array.isArray(progress?.downloads) ? progress.downloads : [],
      );
      resources.value = progress?.resources ?? null;
      const now = Date.now() / 1000;
      nowSeconds.value = now;
      const nextSeries = { ...series.value };
      workerSnapshots.value = workers;
      for (const [key, snapshot] of Object.entries(workers)) {
        const current = Number(snapshot.current || 0);
        const prev = lastSnapshot.get(key);
        let rate = 0;
        if (prev && current > prev.current && now > prev.t) {
          rate = (current - prev.current) / (now - prev.t);
        }
        if (rate > 0) lastProgressAtByWorker.set(key, now);
        const hasExplicitActive = typeof snapshot?.active === "boolean";
        const isActive = hasExplicitActive
          ? snapshot.active
          : Boolean(snapshot?.running) && rate > 0;
        if (isActive) lastActiveAtByWorker.set(key, now);
        lastSnapshot.set(key, { current, t: now });
        const existing = nextSeries[key] ? [...nextSeries[key]] : [];
        existing.push({ t: now, rate, current });
        nextSeries[key] = existing.filter(
          (e) => e.t >= now - SERIES_WINDOW_SECONDS,
        );
      }
      for (const key of Array.from(lastActiveAtByWorker.keys())) {
        if (!(key in workers)) lastActiveAtByWorker.delete(key);
      }
      for (const key of Array.from(lastProgressAtByWorker.keys())) {
        if (!(key in workers)) lastProgressAtByWorker.delete(key);
      }
      series.value = nextSeries;
    } catch (e) {
      // Best-effort background poll: a transient failure is expected (server
      // restart, brief network drop). Log at debug so it isn't silent, but
      // don't spam the console on every tick.
      console.debug("tasks: /workers/progress poll failed", e);
    } finally {
      fetchInFlight = false;
    }
  }

  function reschedule(immediate = false) {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (!polling) return;
    if (typeof document !== "undefined" && document.hidden) return;
    timer = setTimeout(tick, immediate ? 0 : desiredInterval());
  }

  async function tick() {
    await fetchProgress();
    reschedule();
  }

  function onVisibilityChange() {
    if (!polling) return;
    if (document.hidden) {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    } else {
      reschedule(true); // catch up immediately on refocus
    }
  }

  function startPolling() {
    if (polling) return;
    polling = true;
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    tick();
  }

  // Kick an immediate progress fetch and switch to the fast cadence right away,
  // instead of waiting out the current (possibly 5 s idle) interval. Used when
  // the user explicitly starts background work (e.g. Segment) so the activity
  // light / Tasks-tab pulse appear within one poll RTT rather than up to 5 s
  // later. No-op-safe: fetchProgress() guards against concurrent fetches via
  // fetchInFlight, and reschedule(true) fires tick() immediately.
  function nudge() {
    if (!polling) return;
    if (typeof document !== "undefined" && document.hidden) return;
    reschedule(true);
  }

  // The owner's runs, downloads and card readings are not the next session's
  // to see. A tab's own imports (importRuns) are its uploads, not server data.
  function reset() {
    generation += 1;
    workerSnapshots.value = {};
    systemUsage.value = null;
    series.value = {};
    lastSnapshot.clear();
    lastActiveAtByWorker.clear();
    lastProgressAtByWorker.clear();
    comfyuiRuns.value = [];
    finishedRuns.value = [];
    finishedOpen.value = {};
    offersSeen.clear();
    finishedPolled = false;
    resources.value = null;
    modelDownloads.value = [];
    seenDownloadStatus.clear();
  }

  onScopeDispose(onSessionReset(reset));

  function stopPolling() {
    polling = false;
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    }
  }

  return {
    // backend worker state
    workerSnapshots,
    systemUsage,
    series,
    // comfyui runs
    comfyuiRuns,
    abortComfyuiRuns,
    finishedRuns,
    finishedOpen,
    noteFinished,
    toggleFinished,
    forgetFinished,
    dropOffer,
    // model downloads
    modelDownloads,
    runningDownloadFiles,
    runningDownloads,
    runningDownload,
    downloadsLanded,
    downloadStarted,
    cancelDownload,
    // gpu resources (VRAM orchestration)
    resources,
    vramBars,
    runningEntries,
    waitingEntries,
    pixlstashEntry,
    runWaitingAnyway,
    cancelWaiting,
    freeComfyuiCache,
    // import runs (#459)
    importRuns,
    setImportRun,
    clearImportRun,
    registerImportAbort,
    unregisterImportAbort,
    abortImportRun,
    // derived
    activeEntries,
    importEntries,
    activeWorkerEntries,
    activeCount,
    hasActiveTasks,
    taggingActive,
    // rate helpers
    getLatestRate,
    // polling lifecycle
    tasksTabOpen,
    setTasksTabOpen,
    startPolling,
    stopPolling,
    nudge,
  };
});
