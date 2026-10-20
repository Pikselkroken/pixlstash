import { computed, onScopeDispose, ref } from "vue";
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

export const TEMPLATE_SORT_KEYS = ["readiness", "popular", "newest", "name"];

export const TEMPLATE_SORT_LABELS = {
  readiness: { label: "Readiness" },
  popular: { label: "Most used" },
  newest: { label: "Newest" },
  name: { label: "Name" },
};

/** Which state a template is in, best first: the readiness sort's order. */
export const READINESS = ["ready", "missing", "pack_not_loaded", "partner"];

export const READINESS_LABELS = {
  ready: "Ready to run",
  missing: "Missing files",
  pack_not_loaded: "Pack not loaded",
  partner: "Partner API",
};

export const SOURCE_LABELS = {
  builtin: "Built-in",
  pack: "Node pack",
  partner: "Partner API",
};

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

const DEFAULT_FILTERS = {
  name: "",
  media: null,
  source: null,
  readiness: null,
  showPartner: false,
};

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
  const sortKey = ref("readiness");
  const filters = ref({ ...DEFAULT_FILTERS });
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
   * server to read ComfyUI again; concurrent calls share one read, except
   * that a re-read asked for during a plain read is made once that one lands
   * rather than answered with a list ComfyUI was not asked for again.
   */
  async function fetchTemplates({ refresh = false } = {}) {
    if (inflight) {
      if (!refresh || inflightRefresh) return inflight;
      await inflight;
      return fetchTemplates({ refresh: true });
    }
    if (loaded.value && !refresh) return undefined;
    loading.value = true;
    error.value = "";
    const mine = generation;
    inflightRefresh = refresh;
    const read = (async () => {
      try {
        const body = await listTemplates({ refresh });
        if (mine !== generation) return;
        templates.value = Array.isArray(body?.templates) ? body.templates : [];
        reachable.value = body?.reachable !== false;
        comfyuiUrl.value = body?.comfyui_url ?? "";
        detail.value = body?.detail ?? "";
        loaded.value = true;
        const keys = new Set(templates.value.map((t) => t.key));
        selectedKeys.value = selectedKeys.value.filter((key) => keys.has(key));
      } catch (err) {
        console.warn("[templates] could not read the template list", err);
        if (mine === generation) {
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

  const partnerCount = computed(
    () => templates.value.filter((t) => t.source === "partner").length,
  );

  const filtered = computed(() => {
    const f = filters.value;
    const words = f.name.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return templates.value.filter((template) => {
      // An explicit Source: Partner API shows them whatever the toggle says.
      if (
        template.source === "partner" &&
        !f.showPartner &&
        f.source !== "partner"
      ) {
        return false;
      }
      if (f.media && template.media_type !== f.media) return false;
      if (f.source && template.source !== f.source) return false;
      if (f.readiness && templateReadiness(template) !== f.readiness) {
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

  /** The media types the list holds, for the Media filter's rows. */
  const mediaTypes = computed(() =>
    [...new Set(templates.value.map((t) => t.media_type).filter(Boolean))].sort(),
  );

  function setFilters(patch) {
    filters.value = { ...filters.value, ...patch };
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
    if (f.media) {
      chips.push({
        key: "media",
        kind: "Media",
        value: f.media,
        remove: () => setFilters({ media: null }),
      });
    }
    if (f.source) {
      chips.push({
        key: "source",
        kind: "Source",
        value: SOURCE_LABELS[f.source] ?? f.source,
        remove: () => setFilters({ source: null }),
      });
    }
    if (f.readiness) {
      chips.push({
        key: "readiness",
        kind: "Readiness",
        value: READINESS_LABELS[f.readiness] ?? f.readiness,
        remove: () => setFilters({ readiness: null }),
      });
    }
    if (!f.showPartner && f.source !== "partner" && partnerCount.value) {
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
  async function add(list = selectedTemplates.value) {
    if (adding.value || !list.length) return null;
    adding.value = true;
    error.value = "";
    try {
      return await addTemplates(list);
    } catch (err) {
      console.warn("[templates] could not add templates", err);
      error.value = errorMessage(err, "Could not add those templates.");
      return null;
    } finally {
      adding.value = false;
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
    shown.value = false;
    templates.value = [];
    loaded.value = false;
    reachable.value = true;
    comfyuiUrl.value = "";
    detail.value = "";
    error.value = "";
    selectedKeys.value = [];
    filters.value = { ...DEFAULT_FILTERS };
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
    mediaTypes,
    partnerCount,
    filterChips,
    activeFilterCount,
    filterOfLabel,
    fetchTemplates,
    setFilters,
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
