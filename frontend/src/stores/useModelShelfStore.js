import { computed, onScopeDispose, reactive, ref } from "vue";
import { defineStore } from "pinia";
import {
  BASE_MODEL_UNASSIGNED,
  editModels,
  forgetModels,
  listAdapters,
  listCheckpoints,
} from "../api/modelShelf";
import { onSessionReset } from "../utils/apiClient";
import { useNoticeStore } from "./useNoticeStore";
import { errorDetail } from "../utils/apiError";
import { locationState, modelName } from "../utils/modelShelf";

/** Where the `Show` selection is remembered between visits. */
const FILTERS_KEY = "pixlstash:modelShelfFilters";

/**
 * Where the view axes are remembered: grouping, sort, and what is collapsed.
 *
 * A second key rather than more fields in {@link FILTERS_KEY}: `resetFilters`
 * clears everything under that one, and losing your sort order because you
 * cleared a filter is a different promise than the button makes.
 */
const VIEW_KEY = "pixlstash:modelShelfView";

/** Bumped when the shape below changes; a blob from another `v` is discarded. */
const VIEW_SCHEMA_VERSION = 1;

/**
 * Ceiling on remembered collapsed groups, per axis, oldest dropped.
 *
 * Base models come from file metadata and folders from a registry, so neither
 * set is truly unbounded, but both are user-supplied strings and the blob must
 * not grow forever. Losing one only means a group opens.
 */
const MAX_COLLAPSED_KEYS = 200;

/** The axes the shelf can group by. `none` is the flat F1 list. */
export const GROUP_BY_KEYS = ["none", "base_model", "folder"];

/**
 * How folder groups are laid out, which is a sub-choice of `Folder` rather than
 * a fourth axis: `drive` bands them by the disk they sit on, `alpha` runs them
 * A to Z. It was once offered as `Sort: Drive | Folder`, which was never a sort
 * and is why the absence of real sorting went unnoticed for so long.
 */
export const FOLDER_LAYOUTS = ["drive", "alpha"];

/**
 * The five ruled sort keys, mirroring `SortKey` in `routes/model_shelf.py`.
 *
 * Applied CLIENT-SIDE, and that is not a shortcut. `fetchRows` issues one
 * request per selected block and concatenates the results, so three
 * server-sorted lists would arrive correctly ordered and be destroyed by the
 * merge. Every field these keys read is already on the list payload, so sorting
 * here costs no request and no refetch when the user flips a direction.
 */
export const SORT_KEYS = [
  "added_at",
  "file_mtime",
  "name",
  "size",
  "base_model",
];

/** What each sort key reads off a row. `null` means the row cannot answer. */
const SORT_VALUE = {
  // A stack's date is its newest member's, never its cover's.
  added_at: (row) => row.newest_member_at || row.added_at || null,
  file_mtime: (row) => row.newest_file_mtime ?? null,
  name: (row) => row.name.text,
  // The cover alone understates a six-step run by about six times, in the
  // column the shelf exists to answer.
  size: (row) => row.total_size ?? row.file_size ?? null,
  base_model: (row) => row.base_model || null,
};

/**
 * Order two rows on one key.
 *
 * A row with no value for the key sorts LAST IN BOTH DIRECTIONS, which is the
 * API's own contract for these keys. It is not "smallest": "this file records
 * no base model" is an unanswered question, and letting 37% of the shelf pile
 * up at whichever end the direction points is how a sort stops being one.
 */
function compareOn(a, b, key, direction) {
  const left = SORT_VALUE[key](a);
  const right = SORT_VALUE[key](b);
  if (left === null || left === "")
    return right === null || right === "" ? 0 : 1;
  if (right === null || right === "") return -1;
  const sign = direction === "asc" ? 1 : -1;
  if (typeof left === "number" && typeof right === "number") {
    return (left - right) * sign;
  }
  return (
    String(left).localeCompare(String(right), undefined, {
      numeric: true,
      sensitivity: "base",
    }) * sign
  );
}

/** "1 model" / "12 models", so no receipt ever reads "1 models". */
function modelCount(n) {
  return `${Number(n).toLocaleString()} ${n === 1 ? "model" : "models"}`;
}

/** What each curated column is called in a receipt. */
const FIELD_WORDS = {
  display_name: "name",
  base_model: "base model",
  kind: "algorithm",
  file_kind: "type",
};

/**
 * Say what an edit did, naming the columns rather than the request.
 *
 * There is no undo here, so the receipt is the only record: it has to be
 * specific enough that a wrong bulk write is recognised as wrong immediately,
 * while the previous values are still in the reader's head.
 */
export function editReceipt(count, changes) {
  const fields = Object.keys(changes)
    .map((key) => FIELD_WORDS[key] || key)
    .join(" and ");
  if (changes.display_name !== undefined && count === 1) {
    return changes.display_name
      ? `Renamed to ${changes.display_name}.`
      : "Cleared the name. The shelf shows one derived from the filename.";
  }
  return `Set the ${fields} on ${modelCount(count)}.`;
}

/**
 * Say what a forget destroyed and what it left, in that order.
 *
 * The refusals are named rather than swallowed: "3 forgotten, 2 still on disk"
 * is the normal outcome of a selection made a minute ago, and a receipt that
 * reported only the 3 would read as a silent partial failure.
 */
export function forgetReceipt(gone, kept) {
  if (!gone && !kept) return "Nothing to forget.";
  if (!gone) {
    return `${modelCount(kept)} still ${kept === 1 ? "has a copy" : "have copies"} on this machine, so nothing was forgotten.`;
  }
  const forgotten = `Forgot ${modelCount(gone)}.`;
  return kept
    ? `${forgotten} ${modelCount(kept)} still ${kept === 1 ? "has a copy" : "have copies"} and ${kept === 1 ? "was" : "were"} kept.`
    : forgotten;
}

/**
 * The default view: newest added first, ungrouped, exactly what F1 showed.
 *
 * `folderLayout` is carried at all times but only read under `groupBy:
 * 'folder'`. Remembering it while another axis is chosen is the point: flipping
 * to Base model and back must not silently reset how the folders were laid out.
 * It seeds to `drive`, because the question a shelf of 438 GB is asked first is
 * which disk is filling up.
 */
function defaultView() {
  return {
    groupBy: "none",
    sortKey: "added_at",
    sortDirection: "desc",
    folderLayout: "drive",
  };
}

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

/**
 * Read one remembered blob, or null when there is none to trust.
 *
 * Private mode, a disabled store or a corrupt blob all land here. Falling back
 * to the defaults is a fine outcome; a throwing getter that takes the whole
 * shelf with it is not.
 */
function readStored(key) {
  try {
    const raw = window.localStorage?.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (err) {
    console.warn(`[shelf] could not read ${key}`, err);
    return null;
  }
}

/** Persist one blob. The choice still applies this session if this fails. */
function writeStored(key, value) {
  try {
    window.localStorage?.setItem(key, JSON.stringify(value));
  } catch (err) {
    console.warn(`[shelf] could not remember ${key} for next time`, err);
  }
}

/** Read the remembered selection, or null when there is none to trust. */
function storedFilters() {
  const parsed = readStored(FILTERS_KEY);
  if (!parsed) return null;
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
}

/** Read the remembered view axes, falling back to the defaults per field. */
function storedView() {
  const parsed = readStored(VIEW_KEY);
  const view = defaultView();
  // A blob an older build wrote is discarded whole rather than half-applied.
  if (!parsed || parsed.v !== VIEW_SCHEMA_VERSION) return view;
  if (GROUP_BY_KEYS.includes(parsed.groupBy)) view.groupBy = parsed.groupBy;
  // Read per field rather than gated behind a schema bump: a blob written
  // before the layout choice existed is still a valid remembered sort, and
  // bumping the version to add one field would throw that away for everyone.
  if (FOLDER_LAYOUTS.includes(parsed.folderLayout)) {
    view.folderLayout = parsed.folderLayout;
  }
  if (SORT_KEYS.includes(parsed.sortKey)) view.sortKey = parsed.sortKey;
  if (parsed.sortDirection === "asc" || parsed.sortDirection === "desc") {
    view.sortDirection = parsed.sortDirection;
  }
  return view;
}

/**
 * Read the remembered collapsed groups, keyed by the axis they belong to.
 *
 * Namespaced per axis so collapsing `Not set` under `Base model` does not also
 * collapse a folder that happens to be called the same thing, and so switching
 * axis and back restores what you had. Only the COLLAPSED set is stored: groups
 * default to expanded, so a base model that appears after this was written
 * still opens.
 */
function storedCollapsed() {
  const parsed = readStored(VIEW_KEY);
  const collapsed = {};
  for (const axis of GROUP_BY_KEYS) collapsed[axis] = new Set();
  if (!parsed || parsed.v !== VIEW_SCHEMA_VERSION) return collapsed;
  for (const axis of GROUP_BY_KEYS) {
    const keys = parsed.collapsed?.[axis];
    if (!Array.isArray(keys)) continue;
    collapsed[axis] = new Set(
      keys
        .filter((k) => typeof k === "string" && k !== "")
        .slice(-MAX_COLLAPSED_KEYS),
    );
  }
  return collapsed;
}

/** The group a row with no value on the current axis falls into. */
const UNSET_GROUP_KEY = "\u0000unset";

/**
 * Every group a row belongs to on one axis, as `{key, label, labelKind}`.
 *
 * A row belongs to exactly one base model but to EVERY folder holding a copy of
 * it, so this returns a list rather than a key. The alternative was a "primary
 * location", which is a fiction the shelf would then have to explain, and which
 * makes the storage answer wrong: a file copied into two folders occupies both.
 * `labelKind` is `path` for a literal filesystem path, which is set in the mono
 * face and never uppercased, because uppercasing a path misstates the string.
 */
function groupsOf(row, axis) {
  if (axis === "base_model") {
    const base = row.base_model || "";
    return base
      ? [{ key: base, label: base, labelKind: "name" }]
      : [
          {
            key: UNSET_GROUP_KEY,
            label: "Base model not set",
            labelKind: "name",
          },
        ];
  }
  const locations = Array.isArray(row.locations) ? row.locations : [];
  if (!locations.length) {
    return [
      {
        key: UNSET_GROUP_KEY,
        label: "No registered copy",
        labelKind: "name",
      },
    ];
  }
  return locations.map((loc) => ({
    key: String(loc.folder_path || loc.folder_id || ""),
    label: String(loc.folder_path || `Folder ${loc.folder_id}`),
    labelKind: "path",
    location: loc,
  }));
}

/**
 * Order two groups.
 *
 * Alphabetical by label, with the "not set" group ALWAYS last, in both sort
 * directions. It is the absence of a value rather than a value, so it never
 * joins the alphabetical run and never swaps ends when the direction flips.
 * That is the same rule `baseModelOptions` already applies to the filter's
 * `UNASSIGNED` option, and it matters here because "not set" is not a tail: 37%
 * of real adapters record no base model, so it is one of the largest groups on
 * the shelf and putting it first would bury everything identifiable under it.
 *
 * The sort keys never reorder groups, only rows inside them. Switching to
 * "Largest first" moving every header out from under the reader would be a
 * different view, not a sorted one.
 */
function compareGroups(a, b) {
  if (a.key === UNSET_GROUP_KEY) return b.key === UNSET_GROUP_KEY ? 0 : 1;
  if (b.key === UNSET_GROUP_KEY) return -1;
  return a.label.localeCompare(b.label, undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

/** The three top-level type checkboxes, each one request and one row bucket. */
const BLOCKS = ["adapters", "checkpoints", "unclassified"];

/** Which block a row came from, so a fetch only replaces what it asked for. */
function blockOf(row) {
  if (row.file_kind === "checkpoint") return "checkpoints";
  if (row.file_kind === "unknown") return "unclassified";
  return "adapters";
}

export const useModelShelfStore = defineStore("modelShelf", () => {
  const filters = reactive(storedFilters() || defaultFilters());
  /** Grouping and sort. A view preference, not part of the `Show` selection. */
  const view = reactive(storedView());
  /** Collapsed group keys, per axis. Replaced wholesale so templates react. */
  const collapsed = ref(storedCollapsed());
  /** Every row fetched so far, across blocks. Not the shown set. */
  const rows = ref([]);
  const loading = ref(false);
  const error = ref("");
  /** True once a fetch has completed, so "empty" and "not asked yet" differ. */
  const loaded = ref(false);
  // Discards a list read the user has already overtaken, and one that was on
  // the wire when the credential changed. Every fetch takes the next number,
  // so only the newest one may write.
  let epoch = 0;

  function remember() {
    writeStored(FILTERS_KEY, filters);
  }

  /** Persist the view axes and the collapsed sets as one versioned blob. */
  function rememberView() {
    const blob = { v: VIEW_SCHEMA_VERSION, ...view, collapsed: {} };
    for (const axis of GROUP_BY_KEYS) {
      blob.collapsed[axis] = [...collapsed.value[axis]].slice(
        -MAX_COLLAPSED_KEYS,
      );
    }
    writeStored(VIEW_KEY, blob);
  }

  /**
   * Load every row the top-level type checkboxes ask for.
   *
   * One request per selected block, never one per kind or per base model:
   * those two narrow the fetched set in {@link visibleRows} instead. The list
   * already carries locations and attachments, so a row costs no follow-up,
   * and a base-model multi-select would otherwise be one request per option
   * with the results merged client-side anyway.
   *
   * A fetch REPLACES the blocks it asked for and LEAVES THE REST STANDING.
   * `rows` is therefore everything known, not the shown set: the type
   * checkboxes narrow in {@link visibleRows} like the other two. Overwriting
   * the whole array with a narrowed fetch is what used to delete the option
   * vocabularies, because both are derived from it: unticking Adapters
   * unmounted the kind checkboxes it is documented to grey, and unticking
   * Checkpoints dropped base models that stayed selected and persisted with
   * no box left to untick them.
   *
   * `epoch` discards a flight the user has already overtaken: three
   * checkboxes each refetch, so a slower earlier request could otherwise land
   * last and show adapters only while Checkpoints is ticked. Same shape as
   * `useLibrariesStore.refresh`.
   */
  async function fetchRows() {
    const startedAt = (epoch += 1);
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
      if (startedAt !== epoch) return;
      const refreshed = new Set(BLOCKS.filter((block) => filters[block]));
      rows.value = [
        ...rows.value.filter((row) => !refreshed.has(blockOf(row))),
        ...results.flat(),
      ];
      loaded.value = true;
      pruneSelection();
    } catch (err) {
      if (startedAt !== epoch) return;
      error.value = errorDetail(err) || err?.message || String(err);
      // `rows` is left standing. Clearing it was consistent while a fetch
      // replaced the whole array, but under the contract above it throws away
      // blocks the failed request never asked for, which empties
      // `adapterKindOptions` and `baseModelOptions` and unmounts the Show
      // panel's nested checkboxes: the bug this store was just fixed for,
      // reached down the error path instead. The error branch renders ahead of
      // the row list, so nothing stale is shown, and the next successful fetch
      // re-requests every ticked block anyway.
    } finally {
      if (startedAt === epoch) loading.value = false;
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
        // The type checkboxes narrow here as well as choosing what to fetch:
        // a block already fetched stays in `rows` so its options survive.
        if (!filters[blockOf(row)]) return false;
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
   * The shown rows, sorted and cut into groups.
   *
   * Always at least one group, so the list has ONE shape to render: with
   * `groupBy: 'none'` it is a single unlabelled group and the header is not
   * drawn. That is what keeps the flat F1 list and the grouped list from
   * becoming two copies of the row markup.
   *
   * ONE level of headers, deliberately, though the plan allows two. Folder is
   * a grouping VALUE rather than a permanent outer band: a band per folder
   * crossed with a group per base model fragments "what do I have for SDXL"
   * into one answer per disk, which is the question the shelf exists to answer.
   * The second level stays unspent for F5, where a stack genuinely nests inside
   * a group.
   */
  const groups = computed(() => {
    const axis = view.groupBy;
    // A tiebreak on id, because a refetch can reorder equal-valued rows and a
    // list that reshuffles under an unchanged sort reads as a rendering fault.
    const sorted = [...visibleRows.value].sort(
      (a, b) =>
        compareOn(a, b, view.sortKey, view.sortDirection) || a.id - b.id,
    );
    if (axis === "none") {
      return [{ key: "", label: "", labelKind: "name", rows: sorted }];
    }

    const byKey = new Map();
    for (const row of sorted) {
      for (const group of groupsOf(row, axis)) {
        let bucket = byKey.get(group.key);
        if (!bucket) {
          bucket = {
            key: group.key,
            label: group.label,
            labelKind: group.labelKind,
            // Which registered folder this group IS, when it is one. The drive
            // bands need it to look the group's disk up, and the group is the
            // only place that survives the flattening: `location` belongs to a
            // copy, and a bucket outlives the copy that opened it.
            folderId: group.location ? Number(group.location.folder_id) : null,
            rows: [],
          };
          byKey.set(group.key, bucket);
        }
        // Under `folder` a row is listed once per copy and reports THAT copy's
        // state rather than the merged one, or a file present here and missing
        // there would claim to be fine in the folder it is absent from.
        bucket.rows.push(
          group.location
            ? {
                ...row,
                rowKey: `${row.id}:${group.key}`,
                locState: locationState([group.location]),
              }
            : { ...row, rowKey: String(row.id) },
        );
      }
    }
    return [...byKey.values()].sort(compareGroups);
  });

  /**
   * How many rows the list actually draws.
   *
   * Higher than `visibleRows.length` under folder grouping, because a model
   * with copies in two folders is drawn under both. The toolbar states both
   * numbers when they differ rather than picking one and being wrong about the
   * other.
   */
  const renderedCount = computed(() =>
    groups.value.reduce((total, group) => total + group.rows.length, 0),
  );

  /** True when this group is collapsed on the axis currently in use. */
  function isCollapsed(key) {
    return collapsed.value[view.groupBy]?.has(key) ?? false;
  }

  /**
   * Collapse or expand one group on the current axis.
   *
   * Namespaced by axis: collapsing `Not set` under `Base model` must not also
   * collapse a folder of the same name, and switching axis and back restores
   * what was there.
   */
  function toggleGroup(key) {
    const axis = view.groupBy;
    const next = new Set(collapsed.value[axis] || []);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    collapsed.value = { ...collapsed.value, [axis]: next };
    rememberView();
  }

  /**
   * Change the grouping or the sort.
   *
   * Never refetches: every field the five sort keys read is already on the list
   * payload, so a direction flip is a resort of what is in hand.
   *
   * @param {Object} patch - any of `groupBy`, `folderLayout`, `sortKey`,
   *   `sortDirection`.
   */
  function setView(patch) {
    Object.assign(view, patch);
    rememberView();
  }

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
  // ── Selection and the verbs (F3) ────────────────────────────────────────

  /**
   * The models the verbs will act on, by hub `model.id`.
   *
   * By MODEL, not by rendered row. Under folder grouping one model is drawn
   * once per folder holding a copy of it, and the verbs write the model: a
   * per-row selection would let the same file be "half selected" and would ask
   * the reader to understand a distinction the data does not have.
   *
   * Not persisted, and dropped by a session reset with the rows: a selection is
   * a gesture made against a list that is on screen, not a preference.
   */
  const selectedIds = ref(new Set());

  /**
   * The selected models as rows, in the order the list draws them.
   *
   * Derived from `visibleRows` and NOT from `rows`, which is load-bearing: a
   * verb may only ever act on something the reader can see. Narrowing the
   * `Show` selection therefore drops rows out of the selection, and an
   * unclassified file has to have its box ticked before it can be corrected at
   * all. With no undo behind any of this, "you cannot act on what is off
   * screen" is the safer half of the trade.
   */
  const selectedRows = computed(() =>
    visibleRows.value.filter((row) => selectedIds.value.has(row.id)),
  );

  function isSelected(id) {
    return selectedIds.value.has(id);
  }

  /**
   * Add or remove one model.
   *
   * A new Set rather than a mutation: Vue does not track `Set.add`, so the
   * bar's count and every row's tick would go stale until something else
   * happened to re-render.
   */
  function toggleSelected(id) {
    const next = new Set(selectedIds.value);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedIds.value = next;
  }

  /** Select every model the current filters show, ungrouped duplicates and all. */
  function selectVisible() {
    selectedIds.value = new Set(visibleRows.value.map((row) => row.id));
  }

  function clearSelection() {
    if (selectedIds.value.size) selectedIds.value = new Set();
  }

  /**
   * Drop ids the shelf no longer holds.
   *
   * Run after every fetch. Without it a forgotten model stays in the selection
   * for the life of the tab, so the bar counts rows that are not on screen and
   * the next verb posts an id the server has to refuse.
   */
  function pruneSelection() {
    if (!selectedIds.value.size) return;
    const known = new Set(rows.value.map((row) => row.id));
    const kept = [...selectedIds.value].filter((id) => known.has(id));
    if (kept.length !== selectedIds.value.size) {
      selectedIds.value = new Set(kept);
    }
  }

  /**
   * Write curated columns onto the selection, then say what happened.
   *
   * The caller owns the confirmation: a bulk base-model overwrite is one of the
   * shelf's two prompts and this is not the layer that knows the selection was
   * made deliberately.
   *
   * @param {Object} changes - any of `display_name`, `base_model`, `kind`,
   *   `file_kind`. Only the keys present are sent.
   * @returns {Promise<boolean>} true when the write landed.
   */
  async function editSelected(changes) {
    const notices = useNoticeStore();
    const ids = selectedRows.value.map((row) => row.id);
    if (!ids.length) return false;
    try {
      const body = await editModels(ids, changes);
      await fetchRows();
      notices.push({
        level: "success",
        text: editReceipt(body?.updated?.length ?? ids.length, changes),
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not write that change.",
      });
      return false;
    }
  }

  /**
   * Forget the selection, then say what was forgotten and what was kept.
   *
   * The refusals are the interesting half of the receipt: the server gates on
   * each row's state, so "3 forgotten, 2 still on disk" is the normal outcome
   * of a selection made a minute ago, not an error.
   *
   * @returns {Promise<boolean>} true when the call was made at all.
   */
  async function forgetSelected() {
    const notices = useNoticeStore();
    const ids = selectedRows.value.map((row) => row.id);
    if (!ids.length) return false;
    try {
      const body = await forgetModels(ids);
      clearSelection();
      await fetchRows();
      const gone = body?.forgotten?.length ?? 0;
      const kept = body?.refused?.length ?? 0;
      notices.push({
        level: gone ? "success" : "info",
        text: forgetReceipt(gone, kept),
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not forget those models.",
      });
      return false;
    }
  }

  function resetForSession() {
    epoch += 1;
    rows.value = [];
    selectedIds.value = new Set();
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
    view,
    collapsed,
    selectedIds,
    selectedRows,
    isSelected,
    toggleSelected,
    selectVisible,
    clearSelection,
    editSelected,
    forgetSelected,
    rows,
    loading,
    loaded,
    error,
    fetchRows,
    adapterKindOptions,
    baseModelOptions,
    visibleRows,
    groups,
    renderedCount,
    activeCount,
    nothingSelected,
    isCollapsed,
    toggleGroup,
    resetFilters,
    resetForSession,
    setFilters,
    setView,
  };
});
