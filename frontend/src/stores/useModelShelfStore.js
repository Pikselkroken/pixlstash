import { computed, onScopeDispose, reactive, ref } from "vue";
import { defineStore } from "pinia";
import {
  BASE_MODEL_UNASSIGNED,
  listAdapters,
  listCheckpoints,
} from "../api/modelShelf";
import { onSessionReset } from "../utils/apiClient";
import { errorDetail } from "../utils/apiError";
import { locationState, modelName } from "../utils/modelShelf";

/** Where the `Show` selection is remembered between visits. */
const FILTERS_KEY = "pixlstash:modelShelfFilters";

/**
 * The default `Show` selection, and therefore what "no active filter" means.
 *
 * `unclassified` is off because a file we could not identify is not something
 * to put in front of someone who came to find a LoRA; it is a first-class
 * state with its own checkbox, never folded into either other bucket.
 * `adapterKinds: []` means *every* kind, not *no* kind — an empty multi-select
 * is unconstrained, the standard convention, and the only reading under which
 * a fresh install shows anything.
 */
function defaultFilters() {
  return {
    adapters: true,
    adapterKinds: [],
    checkpoints: true,
    unclassified: false,
    baseModels: [],
  };
}

/** Read the remembered selection, or null when there is none to trust. */
function storedFilters() {
  try {
    const raw = window.localStorage?.getItem(FILTERS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const filters = defaultFilters();
    for (const key of ["adapters", "checkpoints", "unclassified"]) {
      if (typeof parsed[key] === "boolean") filters[key] = parsed[key];
    }
    for (const key of ["adapterKinds", "baseModels"]) {
      if (Array.isArray(parsed[key])) {
        filters[key] = parsed[key].filter((v) => typeof v === "string");
      }
    }
    return filters;
  } catch (err) {
    // Private mode, or a corrupt blob. Showing the defaults is a fine outcome;
    // a throwing getter that takes the whole shelf with it is not.
    console.warn("[shelf] could not read the remembered filters", err);
    return null;
  }
}

export const useModelShelfStore = defineStore("modelShelf", () => {
  const filters = reactive(storedFilters() || defaultFilters());
  const rows = ref([]);
  const loading = ref(false);
  const error = ref("");
  /** True once a fetch has completed, so "empty" and "not asked yet" differ. */
  const loaded = ref(false);

  function remember() {
    try {
      window.localStorage?.setItem(FILTERS_KEY, JSON.stringify(filters));
    } catch (err) {
      // The selection still applies for this session; only the memory is lost.
      console.warn("[shelf] could not remember the filters for next time", err);
    }
  }

  /**
   * Load every row the top-level type checkboxes ask for.
   *
   * One request per selected block, never one per kind or per base model:
   * those two narrow the fetched set in {@link visibleRows} instead. The list
   * already carries locations and attachments, so a row costs no follow-up,
   * and a base-model multi-select would otherwise be one request per option
   * with the results merged client-side anyway.
   */
  async function fetchRows() {
    loading.value = true;
    error.value = "";
    try {
      const requests = [];
      if (filters.adapters) requests.push(listAdapters());
      if (filters.checkpoints) requests.push(listCheckpoints());
      if (filters.unclassified) {
        requests.push(listAdapters({ fileKind: "unknown" }));
      }
      const results = await Promise.all(requests);
      rows.value = results.flat();
      loaded.value = true;
    } catch (err) {
      error.value = errorDetail(err) || err?.message || String(err);
      rows.value = [];
    } finally {
      loading.value = false;
    }
  }

  /** Every adapter algorithm present, for the nested kind checkboxes. */
  const adapterKindOptions = computed(() =>
    [
      ...new Set(
        rows.value
          .filter((r) => r.file_kind === "adapter" && r.kind)
          .map((r) => String(r.kind)),
      ),
    ].sort(),
  );

  /**
   * Every base model present, with `UNASSIGNED` last.
   *
   * A null base model is explicit, not absent: it is a bulk state (37% of real
   * adapters record nothing), so it is an option in its own right rather than
   * a row the filter quietly drops.
   */
  const baseModelOptions = computed(() => {
    const named = [
      ...new Set(rows.value.map((r) => r.base_model).filter(Boolean)),
    ].sort();
    const hasUnset = rows.value.some((r) => !r.base_model);
    return hasUnset ? [...named, BASE_MODEL_UNASSIGNED] : named;
  });

  /** The rows the current selection actually shows, with display fields. */
  const visibleRows = computed(() => {
    const kinds = filters.adapterKinds;
    const bases = filters.baseModels;
    return rows.value
      .filter((row) => {
        if (row.file_kind === "adapter" && kinds.length) {
          if (!kinds.includes(String(row.kind))) return false;
        }
        if (bases.length) {
          const key = row.base_model || BASE_MODEL_UNASSIGNED;
          if (!bases.includes(key)) return false;
        }
        return true;
      })
      .map((row) => ({
        ...row,
        name: modelName(row),
        locState: locationState(row.locations),
      }));
  });

  /**
   * Active filters, counted by section rather than by box.
   *
   * A section contributes 1 when it deviates from its default, however many
   * boxes are ticked inside it. Counting boxes would report "9" for a mild
   * narrowing and the number would stop meaning anything.
   */
  const activeCount = computed(() => {
    let n = 0;
    if (!filters.adapters || filters.adapterKinds.length) n += 1;
    if (!filters.checkpoints) n += 1;
    if (filters.unclassified) n += 1;
    if (filters.baseModels.length) n += 1;
    return n;
  });

  /** True when the selection asks for no rows at all — a distinct empty state. */
  const nothingSelected = computed(
    () => !filters.adapters && !filters.checkpoints && !filters.unclassified,
  );

  /**
   * Drop everything the previous credential could see.
   *
   * The model rows themselves are hub-side facts about this machine, but every
   * row carries the characters and sets in the ACTIVE LIBRARY that use it, so
   * a library or credential change makes the whole page stale. The `Show`
   * selection survives: it is the user's own preference, holds no ids, and is
   * the same reasoning that exempts `useUserPrefsStore`.
   */
  function resetForSession() {
    rows.value = [];
    loaded.value = false;
    error.value = "";
    loading.value = false;
  }

  const unsubscribeSessionReset = onSessionReset(resetForSession);
  onScopeDispose(() => unsubscribeSessionReset());

  function resetFilters() {
    Object.assign(filters, defaultFilters());
    remember();
    return fetchRows();
  }

  /**
   * Apply a change to the `Show` selection.
   *
   * @param {Object} patch - the filter keys to change.
   * @param {Object} [options]
   * @param {boolean} [options.refetch=false] - true when the change alters
   *   which blocks are fetched rather than only which rows are shown.
   */
  function setFilters(patch, { refetch = false } = {}) {
    Object.assign(filters, patch);
    remember();
    return refetch ? fetchRows() : Promise.resolve();
  }

  return {
    filters,
    rows,
    loading,
    loaded,
    error,
    fetchRows,
    adapterKindOptions,
    baseModelOptions,
    visibleRows,
    activeCount,
    nothingSelected,
    resetFilters,
    resetForSession,
    setFilters,
  };
});
