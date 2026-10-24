import { computed, onScopeDispose, ref, watch } from "vue";
import { defineStore } from "pinia";

import { addTemplates, listTemplates } from "../api/comfyui";
import { onSessionReset } from "../utils/apiClient";
import { errorMessage } from "../utils/apiError";

/**
 * The Workflows view's Templates tab (#2000): what the owner's ComfyUI serves,
 * read through `GET /comfyui/templates`, and the sort, filters and selection
 * the tab draws it with.
 *
 * **Nothing here is the owner's.** The list is read live and kept by the
 * server; a template becomes a workflow only through `add`, which stores it
 * and hands the Mine grid the new ids. The tab is also kept here (`shown`),
 * because the right rail is `App.vue`'s and has to know which inspector to
 * mount.
 */

export const TEMPLATE_SORT_KEYS = ["readiness", "popular", "newest", "name", "size"];

export const TEMPLATE_SORT_LABELS = {
  readiness: { label: "Readiness" },
  popular: { label: "Most used" },
  newest: { label: "Newest" },
  name: { label: "Name" },
  // ComfyUI's "Model size (low to high)"; a template that gives none goes last.
  size: { label: "Model size" },
};

/** Which state a template is in, best first: the readiness sort's order. */
export const READINESS = ["ready", "missing", "pack_not_loaded", "partner"];

export const READINESS_LABELS = {
  ready: "Ready to run",
  missing: "Missing files",
  pack_not_loaded: "Pack not loaded",
  partner: "Partner API",
};

/**
 * The filters ComfyUI's own template browser has, and as it has them: several
 * values each, a template kept when it matches any value of every filter in
 * use. Its category list (its categories, then Partner Nodes, then one per
 * node pack, with ComfyUI's own ids for the last two), Model, Use case (its
 * tags) and Runs on; Readiness is PixlStash's own, picked the same way.
 */
export const PARTNER_CATEGORY = "partner-nodes";
const PACK_CATEGORY_PREFIX = "extension-";

export const FILTER_KIND_LABELS = {
  category: "Category",
  model: "Model",
  useCase: "Use case",
  runsOn: "Runs on",
  readiness: "Readiness",
};

export const RUNS_ON_LABELS = {
  comfyui: "ComfyUI",
  api: "External or Remote API",
};

function inCategory(template, id) {
  if (id === PARTNER_CATEGORY) return template.source === "partner";
  if (id.startsWith(PACK_CATEGORY_PREFIX)) {
    return template.source === "pack" && template.module === id.slice(PACK_CATEGORY_PREFIX.length);
  }
  return (template.categories ?? []).includes(id);
}

function categoryLabel(id) {
  if (id === PARTNER_CATEGORY) return "Partner Nodes";
  return id.startsWith(PACK_CATEGORY_PREFIX) ? id.slice(PACK_CATEGORY_PREFIX.length) : id;
}

function runsOn(template) {
  return template.source === "partner" ? "api" : "comfyui";
}

/** Each value *values* gives over *list*, once, with how many it holds. */
function counted(list, values, label = (id) => id) {
  const counts = new Map();
  for (const template of list) {
    for (const id of new Set(values(template))) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }
  return [...counts].map(([id, count]) => ({ id, label: label(id), count }));
}

/**
 * Whether a template could run on this machine as it stands.
 *
 * `partner` runs on Comfy's paid API nodes and downloads nothing, so it is
 * neither ready nor missing; a pack whose nodes did not load cannot run
 * whatever the shelf holds. Otherwise it is `missing` while any file its
 * loaders name is not on the shelf - a template that names none is ready.
 *
 * @param {Object} template - one entry of `GET /comfyui/templates`.
 * @returns {"ready"|"missing"|"pack_not_loaded"|"partner"}
 */
export function templateReadiness(template) {
  if (template.source === "partner") return "partner";
  if (template.pack_loaded === false) return "pack_not_loaded";
  return (template.files ?? []).every((file) => file.present)
    ? "ready"
    : "missing";
}

// The folders a base model loads from: its mark is the model's own initial.
const BASE_DIRECTORIES = new Set(["diffusion_models", "checkpoints", "unet"]);
const DIRECTORY_LETTERS = {
  text_encoders: "T",
  clip: "T",
  vae: "V",
  loras: "L",
  upscale_models: "U",
};

/**
 * The one letter a template file's mark draws: the model's initial for a base
 * model (`F` for Flux), the kind's for the files around it (`T`, `V`, `L`).
 */
export function templateFileLetter(file) {
  const directory = String(file?.directory ?? "").toLowerCase();
  if (DIRECTORY_LETTERS[directory]) return DIRECTORY_LETTERS[directory];
  const source = BASE_DIRECTORIES.has(directory) || !directory ? file?.name : directory;
  const letter = String(source ?? "").match(/[a-z0-9]/i)?.[0];
  return letter ? letter.toUpperCase() : "?";
}

/** How many of a template's files the shelf does not hold. */
export function missingCount(template) {
  return (template.files ?? []).filter((file) => !file.present).length;
}

const DEFAULT_FILTERS = Object.freeze({
  name: "",
  category: [],
  model: [],
  useCase: [],
  runsOn: [],
  readiness: [],
  showPartner: false,
});

/** The filters that pick several values, in the strip's order. */
export const LIST_FILTERS = ["category", "model", "useCase", "runsOn", "readiness"];

// The sort and filters, remembered per browser as the model shelf's view is.
const VIEW_KEY = "pixlstash:workflowTemplatesView";
const VIEW_SCHEMA_VERSION = 3;

/**
 * The remembered sort and filters, each one dropped that this build would not
 * draw. A blob that cannot be read (private mode, a disabled store, a corrupt
 * value) is the defaults.
 */
function storedView() {
  let parsed = null;
  try {
    parsed = JSON.parse(window.localStorage?.getItem(VIEW_KEY) || "null");
  } catch (err) {
    console.warn(`[templates] could not read ${VIEW_KEY}`, err);
  }
  const ok = parsed?.v === VIEW_SCHEMA_VERSION;
  // Category, model and use case are named by ComfyUI: whatever it lists next
  // time decides whether they still match anything.
  const allowed = {
    category: (c) => typeof c === "string",
    model: (m) => typeof m === "string",
    useCase: (u) => typeof u === "string",
    runsOn: (r) => r in RUNS_ON_LABELS,
    readiness: (r) => READINESS.includes(r),
  };
  const filters = { ...DEFAULT_FILTERS, showPartner: ok && parsed.showPartner === true };
  for (const key of LIST_FILTERS) {
    const value = ok && Array.isArray(parsed[key]) ? parsed[key] : [];
    filters[key] = [...new Set(value.filter(allowed[key]))];
  }
  return {
    sortKey: ok && TEMPLATE_SORT_KEYS.includes(parsed.sortKey) ? parsed.sortKey : "readiness",
    filters,
  };
}

const SORTERS = {
  // Within a state: fewest missing, then a template that names its files
  // (one that names none is ready only as far as anybody can tell), then
  // ComfyUI's own before a pack's, then the most used.
  readiness: (a, b) =>
    READINESS.indexOf(templateReadiness(a)) -
      READINESS.indexOf(templateReadiness(b)) ||
    missingCount(a) - missingCount(b) ||
    Number(Boolean(b.files?.length)) - Number(Boolean(a.files?.length)) ||
    Number(a.source === "pack") - Number(b.source === "pack") ||
    (b.usage ?? 0) - (a.usage ?? 0),
  popular: (a, b) => (b.usage ?? 0) - (a.usage ?? 0),
  newest: (a, b) => String(b.date ?? "").localeCompare(String(a.date ?? "")),
  name: () => 0,
  size: (a, b) => {
    const [x, y] = [a.size ?? Infinity, b.size ?? Infinity];
    return x === y ? 0 : x - y;
  },
};

/** The words the name filter is matched against: title, tags and models. */
function haystack(template) {
  return [
    template.title,
    template.description,
    template.module,
    ...(template.tags ?? []),
    ...(template.model_names ?? []),
    ...(template.files ?? []).map((file) => file.name),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
}

export const useWorkflowTemplatesStore = defineStore("workflowTemplates", () => {
  /** The Templates tab is the one on screen, rather than Mine. */
  const shown = ref(false);
  const templates = ref([]);
  const loaded = ref(false);
  const loading = ref(false);
  const reachable = ref(true);
  const comfyuiUrl = ref("");
  const detail = ref("");
  const error = ref("");
  const adding = ref(false);
  const initial = storedView();
  const sortKey = ref(initial.sortKey);
  const filters = ref(initial.filters);
  // The name text is left out, as the shelf leaves it out: it is never
  // restored, so writing it would only leave a search lying in storage.
  watch([sortKey, filters], ([sort, { name: _name, ...kept }]) => {
    try {
      window.localStorage?.setItem(
        VIEW_KEY,
        JSON.stringify({ v: VIEW_SCHEMA_VERSION, sortKey: sort, ...kept }),
      );
    } catch (err) {
      console.warn(`[templates] could not remember ${VIEW_KEY} for next time`, err);
    }
  });
  const selectedKeys = ref([]);
  // Bumped by the inspector's Add: the view carries it out, since it lands
  // on the Mine tab afterwards (the `requestUseSet` pattern).
  const addRequest = ref(0);

  let inflight = null;
  // Whether the read in flight asked ComfyUI again (`refresh`).
  let inflightRefresh = false;
  // Bumped by `reset`: a read that left before a session reset applies
  // nothing when it lands, so a previous owner's list cannot come back.
  let generation = 0;

  /**
   * Read the list. Once per session unless `refresh`, which also asks the
   * server to read ComfyUI again, or unless ComfyUI did not answer last time
   * (the server keeps no failed read); concurrent calls share one read, except
   * that a re-read asked for during a plain read is made once that one lands
   * rather than answered with a list ComfyUI was not asked for again.
   * `reread` reads it again without `refresh`, for the files' `present`, which
   * the server works out on every read: after any read in flight, since that
   * one may have left before the change, and keeping the list it has if
   * ComfyUI does not answer or the read fails.
   */
  async function fetchTemplates({ refresh = false, reread = false } = {}) {
    if (inflight) {
      if (!reread && (!refresh || inflightRefresh)) return inflight;
      await inflight;
      return fetchTemplates({ refresh, reread });
    }
    if (loaded.value && reachable.value && !refresh && !reread) return undefined;
    loading.value = true;
    error.value = "";
    const mine = generation;
    inflightRefresh = refresh;
    const read = (async () => {
      try {
        const body = await listTemplates({ refresh });
        if (mine !== generation) return;
        if (reread && body?.reachable === false) return;
        templates.value = Array.isArray(body?.templates) ? body.templates : [];
        reachable.value = body?.reachable !== false;
        comfyuiUrl.value = body?.comfyui_url ?? "";
        detail.value = body?.detail ?? "";
        loaded.value = true;
        const keys = new Set(templates.value.map((t) => t.key));
        selectedKeys.value = selectedKeys.value.filter((key) => keys.has(key));
      } catch (err) {
        console.warn("[templates] could not read the template list", err);
        if (mine === generation && !reread) {
          error.value = errorMessage(err, "Could not read the templates.");
        }
      } finally {
        if (mine === generation) loading.value = false;
        if (inflight === read) {
          inflight = null;
          inflightRefresh = false;
        }
      }
    })();
    inflight = read;
    return read;
  }

  /** Models reached the shelf: a list already read re-reads what is present. */
  function onModelsArrived() {
    return loaded.value ? fetchTemplates({ reread: true }) : undefined;
  }

  const partnerCount = computed(
    () => templates.value.filter((t) => t.source === "partner").length,
  );

  // Asking for them by Category, Runs on or Readiness shows them whatever the
  // toggle says: each would otherwise always come back empty.
  const partnersHidden = computed(() => {
    const f = filters.value;
    return (
      !f.showPartner &&
      !f.category.includes(PARTNER_CATEGORY) &&
      !f.runsOn.includes("api") &&
      !f.readiness.includes("partner")
    );
  });

  const filtered = computed(() => {
    const f = filters.value;
    const words = f.name.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return templates.value.filter((template) => {
      if (template.source === "partner" && partnersHidden.value) return false;
      const misses = (key, matches) => f[key].length && !f[key].some(matches);
      if (misses("category", (id) => inCategory(template, id))) return false;
      if (misses("model", (id) => (template.model_names ?? []).includes(id))) {
        return false;
      }
      if (misses("useCase", (id) => (template.tags ?? []).includes(id))) return false;
      if (misses("runsOn", (id) => runsOn(template) === id)) return false;
      if (misses("readiness", (id) => templateReadiness(template) === id)) {
        return false;
      }
      if (!words.length) return true;
      const text = haystack(template);
      return words.every((word) => text.includes(word));
    });
  });

  const sortedTemplates = computed(() =>
    [...filtered.value].sort(
      (a, b) =>
        SORTERS[sortKey.value](a, b) ||
        String(a.title).localeCompare(String(b.title)),
    ),
  );

  /**
   * Each filter's rows with how many templates it holds: categories in the
   * order ComfyUI lists them, then Partner Nodes and the packs by name, as
   * its own list has them; models and use cases by name.
   */
  const filterOptions = computed(() => {
    const list = templates.value;
    const byName = (a, b) => a.label.localeCompare(b.label);
    const packs = counted(
      list.filter((t) => t.source === "pack"),
      (t) => [PACK_CATEGORY_PREFIX + t.module],
      categoryLabel,
    ).sort(byName);
    return {
      category: [
        ...counted(list, (t) => t.categories ?? []),
        ...counted(
          list.filter((t) => t.source === "partner"),
          () => [PARTNER_CATEGORY],
          categoryLabel,
        ),
        ...packs,
      ],
      model: counted(list, (t) => t.model_names ?? []).sort(byName),
      useCase: counted(list, (t) => t.tags ?? []).sort(byName),
      runsOn: counted(list, (t) => [runsOn(t)], (id) => RUNS_ON_LABELS[id]),
    };
  });

  function setFilters(patch) {
    filters.value = { ...filters.value, ...patch };
  }

  /** Add *id* to one of the list filters, or take it out. */
  function toggleFilter(key, id) {
    const list = filters.value[key];
    setFilters({
      [key]: list.includes(id) ? list.filter((v) => v !== id) : [...list, id],
    });
  }

  function clearFilters() {
    filters.value = { ...DEFAULT_FILTERS };
  }

  /**
   * The strip's chips, in `FilterStrip`'s shape. Hiding partner templates is
   * the one default that is a chip too, so the count of what it hides is
   * always on screen and one × away (the design's "Partner API hidden (N)").
   */
  const filterChips = computed(() => {
    const f = filters.value;
    const chips = [];
    if (f.name.trim()) {
      chips.push({
        key: "name",
        kind: "Name",
        value: f.name.trim(),
        remove: () => setFilters({ name: "" }),
      });
    }
    // One chip per value, each taking out only its own.
    const labels = {
      category: categoryLabel,
      model: (id) => id,
      useCase: (id) => id,
      runsOn: (id) => RUNS_ON_LABELS[id],
      readiness: (id) => READINESS_LABELS[id] ?? id,
    };
    for (const key of LIST_FILTERS) {
      for (const id of f[key]) {
        chips.push({
          key: `${key}:${id}`,
          kind: FILTER_KIND_LABELS[key],
          value: labels[key](id),
          remove: () => toggleFilter(key, id),
        });
      }
    }
    if (partnersHidden.value && partnerCount.value) {
      chips.push({
        key: "partner",
        kind: "Partner API",
        value: `hidden (${partnerCount.value})`,
        remove: () => setFilters({ showPartner: true }),
      });
    }
    return chips;
  });

  /** The filters the owner turned on, for the Filters button's badge. */
  const activeFilterCount = computed(
    () => filterChips.value.filter((chip) => chip.key !== "partner").length,
  );

  const filterOfLabel = computed(
    () => `${sortedTemplates.value.length} of ${templates.value.length}`,
  );

  // ── Selection ──────────────────────────────────────────────────────────

  const selectedTemplates = computed(() => {
    const byKey = new Map(templates.value.map((t) => [t.key, t]));
    return selectedKeys.value.map((key) => byKey.get(key)).filter(Boolean);
  });

  function select(key, { additive = false } = {}) {
    if (!additive) {
      selectedKeys.value = [key];
      return;
    }
    selectedKeys.value = selectedKeys.value.includes(key)
      ? selectedKeys.value.filter((k) => k !== key)
      : [...selectedKeys.value, key];
  }

  function selectRange(keys) {
    selectedKeys.value = [...keys];
  }

  function clearSelection() {
    selectedKeys.value = [];
  }

  /**
   * Store the given templates (the selection by default) as workflows.
   * Answers the server's `{added, failed}`, or null with `error` set.
   */
  /**
   * Store `list` as workflows. An Add that a session reset outlived answers
   * null and leaves the new session's state alone: it neither holds that
   * session's Add back nor hands the view a result to land on.
   */
  async function add(list = selectedTemplates.value) {
    if (adding.value || !list.length) return null;
    adding.value = true;
    error.value = "";
    const mine = generation;
    try {
      const body = await addTemplates(list);
      return mine === generation ? body : null;
    } catch (err) {
      console.warn("[templates] could not add templates", err);
      if (mine === generation) {
        error.value = errorMessage(err, "Could not add those templates.");
      }
      return null;
    } finally {
      if (mine === generation) adding.value = false;
    }
  }

  function requestAdd() {
    if (selectedKeys.value.length) addRequest.value += 1;
  }

  function reset() {
    generation += 1;
    inflight = null;
    inflightRefresh = false;
    loading.value = false;
    adding.value = false;
    shown.value = false;
    templates.value = [];
    loaded.value = false;
    reachable.value = true;
    comfyuiUrl.value = "";
    detail.value = "";
    error.value = "";
    selectedKeys.value = [];
    // What this browser remembers, as a fresh start would read it.
    const view = storedView();
    sortKey.value = view.sortKey;
    filters.value = view.filters;
  }

  onScopeDispose(onSessionReset(reset));

  return {
    shown,
    templates,
    loaded,
    loading,
    reachable,
    comfyuiUrl,
    detail,
    error,
    adding,
    sortKey,
    filters,
    selectedKeys,
    selectedTemplates,
    sortedTemplates,
    filterOptions,
    partnerCount,
    filterChips,
    activeFilterCount,
    filterOfLabel,
    fetchTemplates,
    onModelsArrived,
    setFilters,
    toggleFilter,
    clearFilters,
    select,
    selectRange,
    clearSelection,
    add,
    addRequest,
    requestAdd,
    reset,
  };
});
