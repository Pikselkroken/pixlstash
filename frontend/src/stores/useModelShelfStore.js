import { computed, onScopeDispose, reactive, ref } from "vue";
import { defineStore } from "pinia";
import { clearModelIcons, setModelIcon } from "../api/modelIcons";
import {
  BASE_MODEL_UNASSIGNED,
  editModels,
  forgetModels,
  listAdapters,
  listCheckpoints,
  setAdapterAttachments,
} from "../api/modelShelf";
import { onSessionReset } from "../utils/apiClient";
import { useNoticeStore } from "./useNoticeStore";
import { errorDetail } from "../utils/apiError";
import {
  baseModelKey,
  collapseStacks,
  compareGroups,
  locationState,
  modelName,
  UNSET_GROUP_KEY,
} from "../utils/modelShelf";

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
  // Sorted by the folded value so the run is by base rather than by spelling.
  base_model: (row) => baseModelKey(row) || null,
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
 *
 * The two refusal reasons stay apart. "Still has a copy" is the gate doing its
 * job and the file is fine; "already gone" means the row had been forgotten
 * before this call reached it, which is not the same news and must not be
 * reported as though the file were still on the disk.
 *
 * @param {number} gone - rows the call destroyed.
 * @param {number} kept - rows refused because a copy is still present or
 *   unreachable.
 * @param {number} [vanished=0] - rows that no longer existed to forget.
 */
export function forgetReceipt(gone, kept, vanished = 0) {
  const notes = [];
  if (kept) {
    notes.push(
      `${modelCount(kept)} still ${kept === 1 ? "has a copy" : "have copies"} and ${kept === 1 ? "was" : "were"} kept.`,
    );
  }
  if (vanished) {
    notes.push(
      `${modelCount(vanished)} ${vanished === 1 ? "was" : "were"} already gone.`,
    );
  }
  if (!gone) {
    return notes.length
      ? `Nothing was forgotten. ${notes.join(" ")}`
      : "Nothing to forget.";
  }
  return [`Forgot ${modelCount(gone)}.`, ...notes].join(" ");
}

/**
 * Say what an Assign wrote, and name what it could not write.
 *
 * Assign is the one shelf verb that is N calls rather than one, because the
 * route replaces a single adapter's whole attachment set. A partial failure is
 * therefore a real outcome and not an error case: four adapters attached and
 * one refused has to read as four attached, or the reader re-runs the verb on
 * the four that already landed.
 *
 * @param {number} done - adapters the call wrote.
 * @param {number} failed - adapters whose write was refused or never landed.
 * @param {string} entityName - the character or set, named rather than typed:
 *   "Assigned to Alice" is checkable against what the reader meant, and
 *   "Assigned to a character" is not.
 * @param {boolean} attaching - false when the verb was a detach.
 */
export function assignReceipt(done, failed, entityName, attaching) {
  const target = entityName || "that entity";
  const verb = attaching ? "Assigned" : "Removed";
  const preposition = attaching ? "to" : "from";
  const notes = failed ? ` ${modelCount(failed)} could not be written.` : "";
  if (!done) {
    return failed
      ? `Nothing was ${attaching ? "assigned" : "removed"}.${notes}`
      : `Nothing to ${attaching ? "assign" : "remove"}.`;
  }
  return `${verb} ${modelCount(done)} ${preposition} ${target}.${notes}`;
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
 * `engines` is on for the opposite reason: they are the answer to "where did
 * my disk go", and on a measured machine they are 118 GB against the adapters'
 * few — invisible by default is how they came to be missing from the shelf for
 * three releases while the architecture note claimed they were on it.
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
    engines: true,
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
  for (const key of ["adapters", "checkpoints", "unclassified", "engines"]) {
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
    // Grouped by the FOLDED value, so four spellings of one base make one
    // header. The label is that canonical string: a header reading
    // `sdxl_base_v1-0` over rows that say `SDXL` would be the fold leaking.
    const base = baseModelKey(row);
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

/** The four top-level type checkboxes, each one request and one row bucket. */
const BLOCKS = ["adapters", "checkpoints", "unclassified", "engines"];

/** Which block a row came from, so a fetch only replaces what it asked for. */
function blockOf(row) {
  if (row.file_kind === "checkpoint") return "checkpoints";
  if (row.file_kind === "unknown") return "unclassified";
  if (row.file_kind === "engine") return "engines";
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
      // The engines block: PixlStash's own taggers and scorers, the
      // InsightFace packs and every HuggingFace repo in the cache. Same
      // route, same shape, one more `file_kind`.
      if (filters.engines) requests.push(listAdapters({ fileKind: "engine" }));
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
    // Faceted on the folded value too, or the filter offers four boxes that
    // each tick a quarter of one base — and ticking "SDXL" would hide the rows
    // whose file happens to spell it `sdxl base`.
    const named = [
      ...new Set(rows.value.map(baseModelKey).filter(Boolean)),
    ].sort();
    const hasUnset = rows.value.some((r) => !baseModelKey(r));
    return hasUnset ? [...named, BASE_MODEL_UNASSIGNED] : named;
  });

  /** The rows the current selection actually shows, with display fields. */
  const visibleRows = computed(() => {
    const kinds = filters.adapterKinds;
    const bases = filters.baseModels;
    const shown = rows.value
      .filter((row) => {
        // The type checkboxes narrow here as well as choosing what to fetch:
        // a block already fetched stays in `rows` so its options survive.
        if (!filters[blockOf(row)]) return false;
        if (row.file_kind === "adapter" && kinds.length) {
          if (!kinds.includes(String(row.kind))) return false;
        }
        if (bases.length) {
          // Matched against the same key the facet list was built from.
          const key = baseModelKey(row) || BASE_MODEL_UNASSIGNED;
          if (!bases.includes(key)) return false;
        }
        return true;
      })
      .map((row) => ({
        ...row,
        name: modelName(row),
        locState: locationState(row.locations),
      }));
    // Folded LAST, so the filters narrow individual models and the stack is
    // then built from what survived. Folding first would let a stack whose
    // cover matches drag hidden members back into view.
    return collapseStacks(shown);
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
      // `rowKey` on this branch too. It was only set where a model can be drawn
      // more than once, which left every row in the DEFAULT view without one:
      // the list's `v-for` key was `undefined` for all of them, and so was
      // anything else keyed per drawn row.
      return [
        {
          key: "",
          label: "",
          labelKind: "name",
          rows: sorted.map((row) => ({ ...row, rowKey: String(row.id) })),
        },
      ];
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

  /**
   * Every selected model, stack members included.
   *
   * `selectedRows` is one row per *shown* row, so a collapsed stack appears
   * once — right for counting and for what the bar says, wrong for what a verb
   * writes. A verb must act on the whole run or a Forget would destroy a run's
   * cover and leave its five steps on the shelf, which is precisely the partial
   * state `services/stack_membership` exists to forbid.
   */
  const selectedModelIds = computed(() =>
    selectedRows.value.flatMap((row) => row.memberIds ?? [row.id]),
  );

  /**
   * The model a Shift-range measures from: the last one picked deliberately.
   *
   * Held apart from the selection itself, exactly as `lastSelectedImageId` is
   * in `useMultiSelect`: a range replaces the selection, so the anchor cannot
   * be recovered from what is selected afterwards.
   */
  const anchorId = ref(null);

  function isSelected(id) {
    return selectedIds.value.has(id);
  }

  /**
   * Add or remove one model, and make it the anchor.
   *
   * A new Set rather than a mutation: Vue does not track `Set.add`, so the
   * bar's count and every row's mark would go stale until something else
   * happened to re-render.
   */
  function toggleSelected(id) {
    const next = new Set(selectedIds.value);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedIds.value = next;
    anchorId.value = id;
  }

  /**
   * Select from a click, the way a file manager does.
   *
   * The same three gestures `ImageGrid.handleImageCardClick` already teaches,
   * and deliberately the same rules rather than a shelf dialect: a plain click
   * REPLACES the selection with the row clicked, Ctrl/Cmd+click toggles one
   * without disturbing the rest, and Shift+click takes the contiguous run from
   * the anchor to the row clicked and replaces the selection with it (it does
   * not merge, which is what makes a mis-aimed range one click to correct).
   *
   * @param {number} id - the model clicked.
   * @param {Object} [modifiers] - `ctrl` and `shift` off the event.
   * @param {Array<number>} [order] - model ids in the order the list DRAWS
   *   them, which is the caller's business: banding re-orders groups, so the
   *   store's own `groups` order is not what the reader sees. Omitted, a range
   *   falls back to selecting the one row, which is what a range with nothing
   *   to measure against means.
   */
  /**
   * Every model one clicked row stands for.
   *
   * A collapsed stack stands for its whole run, and stacks are **atomic** here
   * exactly as they are for pictures: `services/stack_membership` applies a
   * grouping mutation to every member "so state can never go partial". Selecting
   * the cover alone would let Move take one step of six and leave the rest, and
   * Forget destroy a run's cover while its steps stayed on the shelf.
   */
  function modelsBehind(id) {
    const row = visibleRows.value.find((candidate) => candidate.id === id);
    return row?.memberIds?.length ? row.memberIds : [id];
  }

  function selectFromClick(id, { ctrl = false, shift = false } = {}, order) {
    const behind = modelsBehind(id);
    if (ctrl) {
      // Toggled as a unit: a run is in the selection or it is not.
      const next = new Set(selectedIds.value);
      const present = behind.every((member) => next.has(member));
      for (const member of behind) {
        if (present) next.delete(member);
        else next.add(member);
      }
      selectedIds.value = next;
      anchorId.value = id;
      return;
    }
    const sequence = Array.isArray(order) ? order : [];
    const from = sequence.indexOf(anchorId.value);
    const to = sequence.indexOf(id);
    if (shift && from >= 0 && to >= 0) {
      const [start, end] = from <= to ? [from, to] : [to, from];
      // The anchor stays where it was: dragging a range out and back with
      // repeated Shift+clicks has to measure from the same end each time.
      selectedIds.value = new Set(
        sequence.slice(start, end + 1).flatMap(modelsBehind),
      );
      return;
    }
    selectedIds.value = new Set(behind);
    anchorId.value = id;
  }

  /** Select every model the current filters show, ungrouped duplicates and all. */
  function selectVisible() {
    selectedIds.value = new Set(
      visibleRows.value.flatMap((row) => row.memberIds ?? [row.id]),
    );
    anchorId.value = visibleRows.value[0]?.id ?? null;
  }

  function clearSelection() {
    if (selectedIds.value.size) selectedIds.value = new Set();
    anchorId.value = null;
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
    return editModelIds(selectedModelIds.value, changes);
  }

  /**
   * The same write, against ids the caller names rather than the selection.
   *
   * The row's inline rename needs it: naming a model is a gesture on ONE row
   * and must not disturb, or depend on, whatever is selected elsewhere.
   *
   * @param {number[]} ids - model ids. A stack cover passes its members.
   * @param {Object} changes - as {@link editSelected}.
   * @returns {Promise<boolean>} true when the write landed.
   */
  async function editModelIds(ids, changes) {
    const notices = useNoticeStore();
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
    const ids = selectedModelIds.value;
    if (!ids.length) return false;
    try {
      const body = await forgetModels(ids);
      clearSelection();
      await fetchRows();
      const refused = body?.refused ?? [];
      const gone = body?.forgotten?.length ?? 0;
      // The two refusal reasons are different news and must not be conflated:
      // `still_has_a_copy` means the file turned up, `no_such_model` means the
      // row was already gone (another tab forgot it, or this list is stale).
      // Anything the server may add later counts as "kept", which is the
      // conservative reading — not forgotten, and possibly still there.
      const vanished = refused.filter(
        (r) => r.reason === "no_such_model",
      ).length;
      const kept = refused.length - vanished;
      notices.push({
        level: gone ? "success" : "info",
        text: forgetReceipt(gone, kept, vanished),
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

  /**
   * Attach or detach one character/set across the selected adapters.
   *
   * `PUT /adapters/{sha256}/attachments` REPLACES one adapter's whole set, so
   * this is N calls with the union computed here — never one call, and never a
   * blind write of just the new entity, which would silently detach every other
   * character already using the model.
   *
   * The rows are re-read from `selectedRows` rather than trusted from the
   * payload: the picker emits ids it was handed, and between the menu opening
   * and the click landing the selection may have moved. Anything no longer
   * selected, or without a hash to address, is dropped rather than written.
   *
   * No confirmation, deliberately, though the shelf has no undo: an assignment
   * is fully reconstructable from what is on screen, so the prompt would cost a
   * click on every use and prevent nothing. The receipt is the record.
   *
   * @param {Object} payload - as emitted by `AddToEntityControl`.
   * @param {string} payload.entityType - `character` or `set`.
   * @param {number} payload.entityId
   * @param {string} [payload.entityName] - for the receipt.
   * @param {Array<string|number>} payload.subjectIds - hub `model.id` values.
   * @param {boolean} [payload.attach=true] - false detaches.
   * @returns {Promise<boolean>} true when at least one write landed.
   */
  async function setAttachment({
    entityType,
    entityId,
    entityName = "",
    subjectIds = [],
    attach = true,
  }) {
    const notices = useNoticeStore();
    const wanted = new Set(subjectIds.map((id) => String(id)));
    const targets = selectedRows.value.filter(
      (row) => wanted.has(String(row.id)) && row.sha256,
    );
    if (!targets.length) return false;

    const results = await Promise.allSettled(
      targets.map((row) => {
        // Drop any existing entry for this entity first, so an attach cannot
        // duplicate one and a detach removes it however it was recorded.
        const rest = (row.attachments ?? []).filter(
          (att) =>
            !(att.entity_type === entityType && att.entity_id === entityId),
        );
        const next = attach
          ? [...rest, { entity_type: entityType, entity_id: entityId }]
          : rest;
        return setAdapterAttachments(row.sha256, next);
      }),
    );

    const failures = results.filter((r) => r.status === "rejected");
    const done = results.length - failures.length;
    if (failures.length) {
      // Logged as well as counted: the receipt says how many failed, and this
      // says why, which is the only place the reason survives.
      console.warn(
        `[modelShelf] ${failures.length} attachment write(s) failed:`,
        failures.map((f) => errorDetail(f.reason) || f.reason),
      );
    }
    await fetchRows();
    notices.push({
      level: done ? "success" : "error",
      text: assignReceipt(done, failures.length, entityName, attach),
    });
    return done > 0;
  }

  /**
   * Give one model an icon.
   *
   * Single-row by nature, and gated that way in the bar: an icon answers
   * "which one is this?", so giving forty rows the same mark would remove the
   * only thing telling them apart. (Two models legitimately SHARING a logo is
   * different — that is the owner setting each one, and the content-addressed
   * store then keeps one file.)
   *
   * No confirmation: setting an icon is reconstructable by setting it again.
   *
   * **Refuses a selection that is not exactly one**, rather than taking the
   * first row. The bar disables the button, but a UI state is not an invariant:
   * any other caller would silently mark whichever row happened to sort first,
   * which is the surprise this verb can least afford.
   *
   * @param {File|Blob} file
   * @returns {Promise<boolean>} true when the icon landed; false if it was
   *   refused or the request failed.
   */
  async function setIconOnSelected(file) {
    const notices = useNoticeStore();
    if (selectedRows.value.length !== 1 || !file) return false;
    const row = selectedRows.value[0];
    try {
      await setModelIcon(row.id, file);
      await fetchRows();
      notices.push({
        level: "success",
        // A row in the `needs-a-name` state has no name to say, by design —
        // its `text` is empty so the shelf can draw it as a field. The receipt
        // still has to name something the reader can recognise.
        text: `Set the icon on ${row.name.text || row.filename || "the model"}.`,
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not set that icon.",
      });
      return false;
    }
  }

  /**
   * Clear the icon on the selection.
   *
   * The caller confirms a BULK clear: one row is reconstructable by setting it
   * again, and a selection is not — the same test the bulk base-model overwrite
   * falls on. The server reports which rows actually had one, so the receipt
   * says what changed rather than how many ids were sent.
   *
   * @returns {Promise<boolean>} true when the clear landed. False covers both
   *   "nothing was selected" and a failed request — the receipt says which,
   *   and no caller currently branches on the difference.
   */
  async function clearIconsOnSelected() {
    const notices = useNoticeStore();
    const ids = selectedModelIds.value;
    if (!ids.length) return false;
    try {
      const body = await clearModelIcons(ids);
      await fetchRows();
      const cleared = body?.cleared?.length ?? 0;
      notices.push({
        level: cleared ? "success" : "info",
        text: cleared
          ? `Cleared the icon on ${modelCount(cleared)}.`
          : "None of those had an icon.",
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not clear those icons.",
      });
      return false;
    }
  }

  function resetForSession() {
    epoch += 1;
    rows.value = [];
    selectedIds.value = new Set();
    anchorId.value = null;
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
    anchorId,
    isSelected,
    toggleSelected,
    selectFromClick,
    selectVisible,
    clearSelection,
    editSelected,
    editModelIds,
    forgetSelected,
    setIconOnSelected,
    clearIconsOnSelected,
    selectedModelIds,
    setAttachment,
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
