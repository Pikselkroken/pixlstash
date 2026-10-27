import { computed, onScopeDispose, ref, watch } from "vue";
import { defineStore } from "pinia";

import { listRuns } from "../api/modelImports";
import { listTrainingJobs } from "../api/training";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import { useModelFoldersStore } from "./useModelFoldersStore";

/** A job in one of these is training, or about to: it selects alone. */
export const LIVE_STATUSES = ["queued", "running", "stopping"];

/**
 * The Models workspace's training runs (#2169): the runs in the ai-toolkit
 * output root, the runs PixlStash trains, and which of them are picked.
 *
 * A store because three surfaces read one list: the runs grid draws and picks
 * them, the rail describes what is picked, and the sidebar counts them and
 * lights the amber dot while one trains. Held per component they disagreed.
 *
 * Listing a run costs nothing and changes nothing (`api/modelImports.js`), so
 * the list is read whenever it is looked at again; every read is checked
 * against the generation before it writes, because mount, a folder change, a
 * visibility change and a window focus each start one and none cancels the
 * last.
 */
export const useTrainingRunsStore = defineStore("trainingRuns", () => {
  const folders = useModelFoldersStore();

  const runs = ref([]);
  const loading = ref(false);
  const error = ref("");
  /** True once a listing has been asked for, so the sidebar asks only once. */
  const asked = ref(false);

  const jobs = ref([]);
  const jobsLoaded = ref(false);
  /** The job list could not be read, so "not in this library" is not known. */
  const jobsFailed = ref(false);

  /**
   * Card KEYS, not indices: a reload reorders, keys survive it. A run with a
   * folder is keyed by its name whether or not its job has been read, so a
   * tick survives the job list landing or failing; a queued job with no folder
   * yet is keyed by its job, and `reconcile` carries that tick over to its
   * folder card when the folder appears.
   */
  const chosen = ref([]);
  const chosenSteps = ref([]);

  /** The registered ai-toolkit output root. One, by the folders store's rule. */
  const source = computed(() => folders.sourceFolder);

  function isLive(job) {
    return Boolean(job) && LIVE_STATUSES.includes(job.status);
  }

  /**
   * One card per run: every folder run, plus each live job that has no folder
   * yet (queued, setting up). Live runs first, then the newest.
   */
  const cards = computed(() => {
    // Jobs arrive newest first; on a shared run name the newest one names it.
    const byRunName = new Map();
    for (const job of jobs.value) {
      if (job.run_name && !byRunName.has(job.run_name)) {
        byRunName.set(job.run_name, job);
      }
    }
    const names = new Set(runs.value.map((run) => run.name));
    const fromFolder = runs.value.map((run) => {
      const job = byRunName.get(run.name) ?? null;
      return {
        key: `run:${run.name}`,
        name: run.name,
        run,
        job,
        live: isLive(job),
      };
    });
    const pending = jobs.value
      .filter((job) => isLive(job) && !names.has(job.run_name))
      .map((job) => ({
        key: `job:${job.uuid}`,
        name: job.name,
        run: null,
        job,
        live: true,
      }));
    const when = (card) =>
      card.run?.modified_at ??
      (Date.parse(card.job?.created_at ?? "") / 1000 || 0);
    // Stable: runs with no date keep the listing's name order.
    return [...pending, ...fromFolder].sort(
      (a, b) => Number(b.live) - Number(a.live) || when(b) - when(a),
    );
  });

  const liveCount = computed(() => cards.value.filter((c) => c.live).length);

  /** How many runs, for the sidebar and the toolbar; null while unknown. */
  const count = computed(() => {
    if (error.value) return null;
    if (!source.value && !cards.value.length) return null;
    return cards.value.length;
  });

  const chosenCards = computed(() =>
    cards.value.filter((card) => chosen.value.includes(card.key)),
  );

  /**
   * Whether every card's live state is known. Until the job list is read, or
   * when it could not be, a run still training reads as finished, and
   * importing it (with a folder set to delete what it imports) would take the
   * folder out from under the trainer.
   */
  const liveKnown = computed(() => jobsLoaded.value && !jobsFailed.value);

  /** The picked runs an import can take: finished, with a folder. */
  const importable = computed(() =>
    liveKnown.value
      ? chosenCards.value.filter((card) => !card.live && card.run)
      : [],
  );

  /** The one importable run, or null: what gates the per-step picker. */
  const single = computed(() =>
    importable.value.length === 1 ? importable.value[0].run : null,
  );

  function isChosen(card) {
    return chosen.value.includes(card.key);
  }

  /**
   * Pick or unpick a card. Finished runs gather into a batch; a live run is
   * picked on its own, because there is nothing to import from it yet and its
   * rail is the one with a Stop.
   */
  function toggle(card) {
    if (isChosen(card)) {
      chosen.value = chosen.value.filter((key) => key !== card.key);
      return;
    }
    if (card.live) {
      chosen.value = [card.key];
      return;
    }
    const live = new Set(cards.value.filter((c) => c.live).map((c) => c.key));
    chosen.value = [...chosen.value.filter((key) => !live.has(key)), card.key];
  }

  /** "Select all shown": every finished run there is something to import of. */
  function selectAll() {
    chosen.value = cards.value
      .filter((card) => !card.live && card.run)
      .map((card) => card.key);
  }

  function clearSelection() {
    chosen.value = [];
  }

  // Whenever the importable selection narrows to exactly one run, the step
  // list starts filled with all of its checkpoints, because importing part of
  // a run is the exception: the steps become one stack.
  //
  // Keyed on the run's NAME and not on the computed's identity: `single`
  // recomputes whenever `runs` is replaced, which a reload does on every
  // window focus, and refilling there would silently re-tick a checkpoint the
  // reader had just excluded.
  let stepsFilledFor = "";
  watch(single, (run) => {
    const name = run?.name || "";
    if (name === stepsFilledFor) return;
    stepsFilledFor = name;
    chosenSteps.value = run
      ? (run.checkpoints || []).map((cp) => cp.step ?? null)
      : [];
  });

  /**
   * Re-read the runs under the output root.
   *
   * Keeps the reader's place: every still-present card keeps its tick, and the
   * ticked checkpoints of a single run survive. Cards that have VANISHED drop
   * out of the selection rather than leaving it pointing at nothing. The folder
   * id is captured and the request made against THAT, so a read is always
   * asking about the folder it was started for.
   */
  let generation = 0;
  async function loadRuns() {
    asked.value = true;
    const sourceId = source.value?.id ?? null;
    const gen = ++generation;
    const current = () => gen === generation;
    if (sourceId == null) {
      runs.value = [];
      // A failure was about the root that is gone.
      error.value = "";
      // This read is over, and it owns the flag as much as any other: an
      // in-flight one is now stale and will decline to clear it.
      loading.value = false;
      return;
    }
    const keepSteps = [...chosenSteps.value];
    const keptSingle = single.value?.name ?? "";
    loading.value = true;
    error.value = "";
    try {
      const found = await listRuns(sourceId);
      if (!current()) return;
      runs.value = found;
      reconcile();
      if (keptSingle && single.value?.name === keptSingle) {
        chosenSteps.value = keepSteps;
      }
    } catch (err) {
      if (!current()) return;
      error.value = errorDetail(err) || "Could not read that folder.";
      runs.value = [];
      clearSelection();
    } finally {
      // Only the current read owns the spinner: an older one clearing it would
      // report the newer, still-running read as finished.
      if (current()) loading.value = false;
    }
  }

  /**
   * Keep each tick on its run: a queued job's `job:<uuid>` moves to the folder
   * card that job now names, and ticks on cards no longer listed are dropped.
   */
  function reconcile() {
    const keys = new Set(cards.value.map((card) => card.key));
    const byJob = new Map(
      cards.value.filter((card) => card.job).map((card) => [`job:${card.job.uuid}`, card.key]),
    );
    chosen.value = [
      ...new Set(chosen.value.map((key) => byJob.get(key) ?? key)),
    ].filter((key) => keys.has(key));
  }

  let jobsGeneration = 0;
  async function loadJobs() {
    const gen = ++jobsGeneration;
    try {
      const found = await listTrainingJobs();
      if (gen !== jobsGeneration) return;
      jobs.value = Array.isArray(found) ? found : [];
      jobsFailed.value = false;
    } catch (err) {
      if (gen !== jobsGeneration) return;
      // The grid still lists the folder's runs; only the live pages are lost.
      console.warn("Could not list the training jobs:", err);
      jobs.value = [];
      jobsFailed.value = true;
    } finally {
      if (gen === jobsGeneration) jobsLoaded.value = true;
    }
    reconcile();
  }

  function reload() {
    return Promise.all([loadRuns(), loadJobs()]);
  }

  /** Read once, for a surface that only counts (the sidebar). */
  function ensureLoaded() {
    if (!asked.value) reload();
  }

  /**
   * Read the runs whenever the output root changes - including from nothing.
   *
   * The old root's rows and its selection are dropped FIRST, before the new
   * read is even started: a run name means nothing outside the root it was
   * read under, so a tick surviving the walk would send the new root's id with
   * the old root's run name (#1019).
   */
  watch(
    () => source.value?.id,
    () => {
      runs.value = [];
      clearSelection();
      // Explicitly, not left to the `single` watcher: a run of the same name
      // under the new root must start with every checkpoint, not the old
      // run's picks.
      chosenSteps.value = [];
      stepsFilledFor = "";
      if (asked.value) loadRuns();
    },
  );

  function reset() {
    generation += 1;
    jobsGeneration += 1;
    runs.value = [];
    jobs.value = [];
    error.value = "";
    loading.value = false;
    asked.value = false;
    jobsLoaded.value = false;
    jobsFailed.value = false;
    chosen.value = [];
    chosenSteps.value = [];
  }

  // Owner-only host data read under one credential: a change of credential
  // empties it.
  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    runs,
    loading,
    error,
    jobs,
    jobsLoaded,
    jobsFailed,
    chosen,
    chosenSteps,
    source,
    cards,
    liveCount,
    liveKnown,
    count,
    chosenCards,
    importable,
    single,
    isLive,
    isChosen,
    toggle,
    selectAll,
    clearSelection,
    loadRuns,
    loadJobs,
    reload,
    ensureLoaded,
  };
});
