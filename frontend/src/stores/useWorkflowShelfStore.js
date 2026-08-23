import { computed, onScopeDispose, reactive, ref } from "vue";
import { defineStore } from "pinia";

import {
  listWorkflowPictures,
  listWorkflowVariants,
  listWorkflows,
} from "../api/workflows";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import {
  filterWorkflows,
  GROUP_BY_KEYS,
  groupWorkflows,
  libraryState,
  SHOW_KEYS,
  SORT_KEYS,
  sortWorkflows,
} from "../utils/workflowShelf";

/**
 * Where the view axes are remembered between visits.
 *
 * The shelf keeps its `Show` selection and its view axes under two keys because
 * "clear the filters" must not lose your sort order. This list has no clear
 * button, so one key is enough — and if one is ever added, that is the moment
 * to split it, not now.
 */
const VIEW_KEY = "pixlstash:workflowShelfView";

/** Bumped when a default below changes; a blob from another `v` is discarded. */
const VIEW_SCHEMA_VERSION = 1;

/** Ceiling on remembered collapsed bands, oldest dropped. */
const MAX_COLLAPSED_KEYS = 200;

function defaultView() {
  return { groupBy: "none", sortKey: "used", descending: true, show: "all" };
}

function readStored(key) {
  try {
    const raw = window.localStorage?.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (err) {
    console.warn(`[workflows] could not read ${key}`, err);
    return null;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage?.setItem(key, JSON.stringify(value));
  } catch (err) {
    console.warn(`[workflows] could not remember ${key} for next time`, err);
  }
}

/** The remembered axes, or the defaults when there is nothing to trust. */
function storedView() {
  const parsed = readStored(VIEW_KEY);
  if (!parsed || parsed.v !== VIEW_SCHEMA_VERSION) {
    return { view: defaultView(), collapsed: [] };
  }
  const view = defaultView();
  if (GROUP_BY_KEYS.includes(parsed.groupBy)) view.groupBy = parsed.groupBy;
  if (SORT_KEYS.includes(parsed.sortKey)) view.sortKey = parsed.sortKey;
  if (SHOW_KEYS.includes(parsed.show)) view.show = parsed.show;
  if (typeof parsed.descending === "boolean") {
    view.descending = parsed.descending;
  }
  const collapsed = Array.isArray(parsed.collapsed) ? parsed.collapsed : [];
  return { view, collapsed: collapsed.filter((k) => typeof k === "string") };
}

/**
 * The Workflows view's state: the list, the axes, the selection, the expansion.
 *
 * **The list is fetched whole and shaped here.** ~192 rows with their asset
 * names is one small response, and every axis the toolbar offers is a re-read
 * of the same array — so re-grouping or re-sorting costs no request, and the
 * only thing that goes back to the server is opening a row.
 *
 * **A row's variants are fetched once and kept.** The worst family in the
 * owner's library holds 159 of them; fetching that on every toggle would make
 * the one row most worth exploring the one row least pleasant to explore.
 */
export const useWorkflowShelfStore = defineStore("workflowShelf", () => {
  const initial = storedView();

  const rows = ref([]);
  const scan = ref({ pictures: 0, scanned: 0 });
  const loading = ref(false);
  const loaded = ref(false);
  const error = ref("");

  const view = reactive(initial.view);
  const collapsed = ref(new Set(initial.collapsed));

  /** The selected row's topology hash — single-select, like the design's rail. */
  const selectedHash = ref(null);

  /** `topology_hash -> variant rows`, filled the first time a row is opened. */
  const variants = reactive({});
  const variantsLoading = ref(new Set());
  const openHashes = ref(new Set());

  /** `topology_hash -> picture ids`, for the inspector's tiles. */
  const samples = reactive({});
  const samplesLoading = ref(new Set());
  /** Workflows whose tile ids were asked for and did not come back. */
  const samplesFailed = ref(new Set());

  // A stamp, not a boolean: an in-flight fetch that resolves after a session
  // reset must not write its rows into the new session's store.
  let epoch = 0;

  const visibleRows = computed(() =>
    sortWorkflows(
      filterWorkflows(rows.value, view.show),
      view.sortKey,
      view.descending,
    ),
  );

  const groups = computed(() =>
    groupWorkflows(visibleRows.value, view.groupBy),
  );

  const state = computed(() =>
    libraryState(scan.value, visibleRows.value.length),
  );

  const selectedRow = computed(
    () =>
      rows.value.find((row) => row.topology_hash === selectedHash.value) ||
      null,
  );

  /** The variant count across what is shown, for the toolbar's subtitle. */
  const shownVariantCount = computed(() =>
    visibleRows.value.reduce(
      (total, row) => total + (Number(row.variants) || 0),
      0,
    ),
  );

  function rememberView() {
    writeStored(VIEW_KEY, {
      v: VIEW_SCHEMA_VERSION,
      ...view,
      collapsed: [...collapsed.value].slice(-MAX_COLLAPSED_KEYS),
    });
  }

  /**
   * Change one or more view axes.
   *
   * No refetch: every axis is a re-read of rows already in hand.
   *
   * @param {Object} patch - `groupBy`, `sortKey`, `descending` or `show`.
   */
  function setView(patch) {
    Object.assign(view, patch || {});
    rememberView();
  }

  function isCollapsed(key) {
    return key != null && collapsed.value.has(key);
  }

  function toggleCollapsed(key) {
    if (key == null) return;
    const next = new Set(collapsed.value);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    collapsed.value = next;
    rememberView();
  }

  /** Load the list. Safe to call on every visit; it replaces what it holds. */
  async function fetchRows() {
    const mine = epoch;
    loading.value = true;
    error.value = "";
    try {
      const body = await listWorkflows();
      if (mine !== epoch) return;
      rows.value = body.workflows;
      scan.value = body.scan;
      loaded.value = true;
      // A row that is no longer in the list cannot stay selected, or the rail
      // describes a workflow the list does not show.
      if (
        selectedHash.value &&
        !body.workflows.some((row) => row.topology_hash === selectedHash.value)
      ) {
        selectedHash.value = null;
      }
    } catch (err) {
      if (mine !== epoch) return;
      error.value = errorDetail(err) || "Could not read the workflow library.";
    } finally {
      if (mine === epoch) loading.value = false;
    }
  }

  // Selecting does NOT fetch the tiles. The inspector asks for them when it
  // draws the workflow, which is the only place they are shown — and asking
  // here as well meant two concurrent requests for the same ids on every
  // selection, because `samples[hash]` is not set until the first one lands.
  function select(topologyHash) {
    selectedHash.value = topologyHash || null;
  }

  function clearSelection() {
    selectedHash.value = null;
  }

  function isOpen(topologyHash) {
    return openHashes.value.has(topologyHash);
  }

  /** Open or close a row's variants, fetching them the first time only. */
  async function toggleOpen(topologyHash) {
    const next = new Set(openHashes.value);
    if (next.has(topologyHash)) {
      next.delete(topologyHash);
      openHashes.value = next;
      return;
    }
    next.add(topologyHash);
    openHashes.value = next;
    if (variants[topologyHash]) return;
    const mine = epoch;
    const busy = new Set(variantsLoading.value);
    busy.add(topologyHash);
    variantsLoading.value = busy;
    try {
      const list = await listWorkflowVariants(topologyHash);
      if (mine === epoch) variants[topologyHash] = list;
    } catch (err) {
      if (mine === epoch) {
        error.value =
          errorDetail(err) || "Could not read this workflow's variants.";
      }
    } finally {
      const done = new Set(variantsLoading.value);
      done.delete(topologyHash);
      variantsLoading.value = done;
    }
  }

  function isVariantsLoading(topologyHash) {
    return variantsLoading.value.has(topologyHash);
  }

  /**
   * The inspector's tiles for one workflow, fetched once.
   *
   * **A failure is recorded, not cached as an answer, and those are different
   * things.** Writing an empty array on the error path made the "already have
   * them" guard permanent, so one dropped request meant the rail said "nothing
   * this workflow made is still in the library" — the one thing it must not say
   * wrongly — until a session reset. Leaving the key simply unset fixed that
   * and broke the other half: absent then meant BOTH "not asked yet" and
   * "asked and failed", so the panel showed "Reading its pictures…" for ever
   * and the sentence written for the failure could never render.
   *
   * So there are three states and three places to read them from: in
   * ``samplesLoading`` is in flight, a key in ``samples`` is the answer, and a
   * hash in ``samplesFailed`` is a request that came back empty-handed. A
   * retry clears the third and asks again.
   */
  async function loadSamples(topologyHash, limit = 6) {
    if (samples[topologyHash] || samplesLoading.value.has(topologyHash)) return;
    const mine = epoch;
    const busy = new Set(samplesLoading.value);
    busy.add(topologyHash);
    samplesLoading.value = busy;
    // Cleared as the attempt starts, not as it ends: a retry must not render
    // as failed while it is in flight.
    if (samplesFailed.value.has(topologyHash)) {
      const cleared = new Set(samplesFailed.value);
      cleared.delete(topologyHash);
      samplesFailed.value = cleared;
    }
    try {
      const ids = await listWorkflowPictures(topologyHash, limit);
      if (mine === epoch) samples[topologyHash] = ids;
    } catch (err) {
      // Not an error banner over the list: the rail says it could not read
      // them, which is a different sentence from "there are none", and the
      // console carries the reason.
      console.warn("[workflows] could not read sample pictures", err);
      if (mine === epoch) {
        const failed = new Set(samplesFailed.value);
        failed.add(topologyHash);
        samplesFailed.value = failed;
      }
    } finally {
      const done = new Set(samplesLoading.value);
      done.delete(topologyHash);
      samplesLoading.value = done;
    }
  }

  function isSamplesLoading(topologyHash) {
    return samplesLoading.value.has(topologyHash);
  }

  function samplesDidFail(topologyHash) {
    return samplesFailed.value.has(topologyHash);
  }

  function resetForSession() {
    epoch += 1;
    rows.value = [];
    scan.value = { pictures: 0, scanned: 0 };
    loaded.value = false;
    loading.value = false;
    error.value = "";
    selectedHash.value = null;
    openHashes.value = new Set();
    variantsLoading.value = new Set();
    samplesLoading.value = new Set();
    samplesFailed.value = new Set();
    for (const key of Object.keys(variants)) delete variants[key];
    for (const key of Object.keys(samples)) delete samples[key];
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    rows,
    scan,
    loading,
    loaded,
    error,
    view,
    visibleRows,
    groups,
    state,
    selectedHash,
    selectedRow,
    shownVariantCount,
    variants,
    samples,
    setView,
    isCollapsed,
    toggleCollapsed,
    fetchRows,
    select,
    clearSelection,
    isOpen,
    toggleOpen,
    isVariantsLoading,
    isSamplesLoading,
    samplesDidFail,
    loadSamples,
    resetForSession,
  };
});
