import { computed, reactive, ref } from "vue";
import { defineStore } from "pinia";
import { isReadOnly } from "../utils/apiClient";
import { getWorkerProgress } from "../api/workers";

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

/**
 * Cross-component "what is the app working on right now" store.
 *
 * This is the single source of truth for active background work, and the single
 * poller of GET /workers/progress. It exists so the Tasks tab, the animated
 * Tasks-tab indicator, and the app-wide stats-sidebar activity light all read
 * the same state, and so we never poll the endpoint from two places at once.
 *
 * Two kinds of work are merged into one `activeEntries` list:
 *  - backend workers (quality scoring, tagging, embeddings, faces, likeness…),
 *    fetched from /workers/progress;
 *  - ComfyUI runs, which are frontend-driven (ComfyUiRunner talks to ComfyUI's
 *    own WebSocket) and push their progress in via setComfyuiRun / clearComfyuiRun.
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

  // ── ComfyUI run state (frontend-driven) ───────────────────────────────────
  // runId → { status, percent, message, label }
  const comfyuiRuns = reactive({});
  // Abort callbacks, keyed by runId. Kept out of reactive state (they're plain
  // functions, never rendered) so the Tasks-tab row can abort a run that lives
  // in a different component (ComfyUiRunner inside ImageGrid / ImageOverlay).
  const comfyuiAbortHandlers = new Map();

  function setComfyuiRun(runId, run) {
    if (!runId) return;
    comfyuiRuns[runId] = {
      status: run?.status || "running",
      percent: Number(run?.percent) || 0,
      message: run?.message || "ComfyUI running…",
      label: run?.label || "ComfyUI",
    };
  }

  function clearComfyuiRun(runId) {
    if (runId && runId in comfyuiRuns) delete comfyuiRuns[runId];
  }

  function registerComfyuiAbort(runId, handler) {
    if (runId && typeof handler === "function") {
      comfyuiAbortHandlers.set(runId, handler);
    }
  }

  function unregisterComfyuiAbort(runId) {
    comfyuiAbortHandlers.delete(runId);
  }

  function abortComfyuiRun(runId) {
    const handler = comfyuiAbortHandlers.get(runId);
    if (handler) handler();
  }

  // ── Import run state (frontend-driven, #459) ──────────────────────────────
  // The async import (ImageImporter) surfaces its server-side Phase B here as a
  // determinate task row, mirroring the ComfyUI run pattern: the two-phase
  // dialog auto-hides at the safe transition and the import continues as just
  // another entry in the Tasks tab. runId → { status, percent, message, label,
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

  const comfyuiEntries = computed(() =>
    Object.entries(comfyuiRuns).map(([key, run]) => ({
      kind: "comfyui",
      key,
      run,
    })),
  );

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
  const activeEntries = computed(() => [
    ...comfyuiEntries.value,
    ...importEntries.value,
    ...activeWorkerEntries.value,
  ]);

  const activeCount = computed(() => activeEntries.value.length);
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
    const first = samples.find((s) => Number(s?.t || 0) >= cutoff);
    if (!first || first === last) return 0;
    const elapsed = lastTime - Number(first.t || 0);
    const done = Number(last.current || 0) - Number(first.current || 0);
    // `done` goes negative when pictures are deleted under a running worker,
    // and the window is briefly shorter than one batch when polling starts
    // mid-batch; both read as no measurement rather than as a wrong one.
    if (elapsed <= 0 || done <= 0) return 0;
    return done / elapsed;
  }

  // ── Polling ───────────────────────────────────────────────────────────────
  const tasksTabOpen = ref(false);
  function setTasksTabOpen(open) {
    tasksTabOpen.value = Boolean(open);
    // Opening the tab should switch to the fast cadence immediately rather than
    // waiting out the current idle interval.
    if (tasksTabOpen.value && polling) reschedule(true);
  }

  let polling = false;
  let fetchInFlight = false;
  let timer = null;

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
    try {
      const progress = await getWorkerProgress();
      const workers = progress?.workers || {};
      systemUsage.value = progress?.process || progress?.system || null;
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
    setComfyuiRun,
    clearComfyuiRun,
    registerComfyuiAbort,
    unregisterComfyuiAbort,
    abortComfyuiRun,
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
