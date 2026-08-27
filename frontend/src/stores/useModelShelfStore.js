import { computed, onScopeDispose, reactive, ref } from "vue";
import { defineStore } from "pinia";
import { clearModelIcons, setModelIcon } from "../api/modelIcons";
import {
  BASE_MODEL_UNASSIGNED,
  deleteModels,
  editModels,
  forgetModels,
  listAdapters,
  listBaseModelCompletions,
  listCheckpoints,
  setAdapterAttachments,
} from "../api/modelShelf";
import { onSessionReset } from "../utils/apiClient";
import { useNoticeStore } from "./useNoticeStore";
import { errorDetail } from "../utils/apiError";
import {
  adapterKindKey,
  adapterKindLabel,
  baseModelKey,
  collapseStacks,
  capabilityIcon,
  capabilityLabel,
  compareGroups,
  defaultSortDirection,
  fileKindLabel,
  locationState,
  presentCopies,
  trashName,
  modelName,
  offlineFolders,
  UNSET_GROUP_KEY,
} from "../utils/modelShelf";

/** Where the `Show` selection is remembered between visits. */
const FILTERS_KEY = "pixlstash:modelShelfFilters";

/**
 * Bumped when a *default* below changes; a blob from another `v` is discarded.
 *
 * A remembered selection outlives the reason it was remembered. `Unclassified`
 * shipped off by default, so every blob written before this carries
 * `unclassified: false` whether or not anyone chose it - and a shelf that now
 * has something real to show under that box would have gone on hiding it from
 * exactly the people who have been using the screen longest (#927).
 */
const FILTERS_SCHEMA_VERSION = 1;

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

/** How long a failed completion fetch waits before the field may ask again. */
const COMPLETION_RETRY_MS = 30_000;

/**
 * The axes the shelf can group by. `none` is the flat F1 list.
 *
 * `feature` is the axis the multi-capability rule needs: a model that serves
 * several features is listed under EACH of them, which `groupsOf` already
 * supports because `folder` needed the same fan-out for a file copied twice.
 */
export const GROUP_BY_KEYS = ["none", "base_model", "folder", "feature"];

/**
 * How folder groups are laid out, which is a sub-choice of `Folder` rather than
 * a fourth axis: `drive` bands them by the disk they sit on, `alpha` runs them
 * A to Z. It was once offered as `Sort: Drive | Folder`, which was never a sort
 * and is why the absence of real sorting went unnoticed for so long.
 */
export const FOLDER_LAYOUTS = ["drive", "alpha"];

/**
 * The four data columns the reader can resize, and what each starts at.
 *
 * The first three figures are the resolved design's own
 * (ui_kits/app/model-shelf.html, row anatomy) and were fixed widths in the
 * stylesheet until the header strip gave them a grip. The Name column is
 * deliberately absent: it is the flexible track that takes whatever the others
 * leave, so it has no width of its own to remember.
 *
 * `date` is not in the kit - the column postdates it. 96px is what `ymd-jp`
 * needs, the widest of the eight day formats (`2026年08月16日`: three
 * full-width glyphs and eight tabular digits at `--text-xs`), and `locale`
 * hands back whatever the reader's browser writes - so it is the figure that
 * keeps the common formats clear of the ellipsis rather than a proof against
 * every one, and the grip is there for the reader it does not suit.
 */
export const COLUMN_KEYS = ["kind", "base", "size", "date"];

/** @type {Record<string, number>} */
export const DEFAULT_COLUMN_WIDTHS = { kind: 64, base: 84, size: 74, date: 96 };

/**
 * The floor each column is held above, per column rather than one figure.
 *
 * A floor keeps a column from being dragged to nothing and then being
 * unfindable to drag back, and what "nothing" means is different per column:
 * `Kind` holds a word like `Checkpoint`, `Base` holds a model name, and `Size`
 * holds five characters of right-aligned figure. One shared 48px let the two
 * wordy columns be dragged into permanent ellipsis. Each floor is at or under
 * that column's default, which is what keeps a stored default from being
 * clamped UP on read-back.
 *
 * `Date` is the fourth: 96px is what the widest of the day formats needs, so
 * its floor is the point below which the common ones start ellipsising rather
 * than a proof against every one.
 *
 * @type {Record<string, number>}
 */
export const MIN_COLUMN_WIDTHS = { kind: 64, base: 72, size: 56, date: 80 };

/**
 * The ceiling, which is a sanity bound on a stored blob and NOT the limit a
 * drag actually meets.
 *
 * It was a flat 200 for three columns, then 150 once the date column made it
 * four, chosen each time so every column at the ceiling could not overflow the
 * panel sideways on a 1024px window. That bound was doing the wrong job: it is
 * a guess about the narrowest panel anyone has, so on a wide one it stopped the
 * reader at four columns totalling ~600px and a Name track that could never be
 * less than about half the shelf - and it had to be re-derived every time a
 * column was added. The real limit is the panel
 * in front of the reader, so the header strip measures the Name track and
 * refuses to widen a column past `MIN_NAME_WIDTH` - see `ModelShelf.vue`. This
 * figure only has to be a number no legitimate column reaches.
 */
export const MAX_COLUMN_WIDTH = 400;

/**
 * Hold a width inside its column's bounds, or reject it.
 *
 * A finite NUMBER and nothing else. `Number()` coercion would take `null`,
 * `""`, `[]` and `false` as 0 and hand back the floor, so a stored blob
 * carrying `{"size": null}` would silently come back as a 56px column instead
 * of falling through to the default - which is the one thing the read-back
 * loop exists to prevent.
 *
 * @returns {number|null} the clamped width, or null if `px` is not one.
 */
export function clampColumnWidth(key, px) {
  if (typeof px !== "number" || !Number.isFinite(px)) return null;
  const n = Math.round(px);
  return Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTHS[key], n));
}

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

// Ceiling on one bulk thumbnail set, mirroring the clear route's
// `MAX_MODELS_PER_CLEAR` (`pixlstash/routes/model_icons.py`). The set route is
// per-model, so no server cap can see the gesture: Ctrl+A on a long shelf is
// one keystroke, and this is where it stops being one.
const MAX_MODELS_PER_ICON_SET = 500;

// How many of those uploads are in the air at once. A browser gives ~6 sockets
// per origin, so a wider fan-out only queues - and a queued request still burns
// the client's own 60 s timeout, which would report as failed writes the server
// had committed. It also leaves sockets for the row thumbnails.
const ICON_SET_CONCURRENCY = 6;

/** What each curated column is called in a receipt. */
const FIELD_WORDS = {
  display_name: "name",
  base_model: "base model",
  kind: "algorithm",
  file_kind: "type",
  capabilities: "features",
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
 * The refusal reasons stay apart. "Still has a copy" is the gate doing its job
 * and the file is fine; "already gone" means the row had been forgotten before
 * this call reached it; "PixlStash's own" is a row nothing the owner does will
 * clear. Reporting any of the other two as "still has a copy" sends the reader
 * looking on the disk for a file that is not there.
 *
 * @param {number} gone - rows the call destroyed.
 * @param {number} kept - rows refused because a copy is still present or
 *   unreachable.
 * @param {number} [vanished=0] - rows that no longer existed to forget.
 * @param {number} [engines=0] - rows refused as PixlStash's own.
 */
export function forgetReceipt(gone, kept, vanished = 0, engines = 0) {
  const notes = [];
  if (kept) {
    notes.push(
      `${modelCount(kept)} still ${kept === 1 ? "has a copy" : "have copies"} and ${kept === 1 ? "was" : "were"} kept.`,
    );
  }
  if (engines) {
    notes.push(
      `${modelCount(engines)} ${engines === 1 ? "is one" : "are ones"} PixlStash downloaded for itself and would fetch again.`,
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
 * Say what a delete destroyed, where it put it, and what it left alone.
 *
 * Where the bytes went is the first thing the reader needs, because it is the
 * difference between recoverable and not - so the trash is named rather than
 * implied, and a permanent delete says so in the same slot.
 *
 * The refusals are grouped by reason rather than counted together: "on a drive
 * that is not plugged in" is something the reader can act on, and "PixlStash's
 * own" is something they should stop trying to. Rolling both into "2 kept"
 * would leave them re-selecting rows to find out which was which.
 *
 * @param {number} gone - models whose files and rows were destroyed.
 * @param {Array<{reason: string}>} refused - the server's refusals, verbatim.
 * @param {boolean} permanent - whether the files were unlinked or trashed.
 * @param {string} trash - what the SERVER calls its trash.
 * @param {number} [filesRemoved=0] - how many files actually moved. Zero with
 *   `gone` above it is the row-only case - every copy was already off the disk -
 *   and saying "moved to the Trash" there would name a place the reader could go
 *   and fail to find them.
 */
export function deleteReceipt(
  gone,
  refused,
  permanent,
  trash,
  filesRemoved = 0,
) {
  const counts = new Map();
  for (const item of refused || []) {
    counts.set(item?.reason, (counts.get(item?.reason) || 0) + 1);
  }
  const notes = [];
  const note = (reason, sentence) => {
    const n = counts.get(reason);
    if (n) notes.push(sentence(n));
    counts.delete(reason);
  };
  note(
    "not_a_user_folder",
    (n) =>
      `${modelCount(n)} ${n === 1 ? "sits" : "sit"} in a folder PixlStash keeps for itself and ${n === 1 ? "was" : "were"} left alone.`,
  );
  note(
    "is_a_builtin_engine",
    (n) =>
      `${modelCount(n)} ${n === 1 ? "is one" : "are ones"} PixlStash downloaded for itself and would fetch again.`,
  );
  note(
    "unreachable_copy",
    (n) =>
      `${modelCount(n)} ${n === 1 ? "has a copy" : "have copies"} on a drive that is not plugged in.`,
  );
  note(
    "trash_unavailable",
    (n) =>
      `There is no ${trash} this server can reach, so ${modelCount(n)} ${n === 1 ? "was" : "were"} kept. Hold Shift to delete permanently.`,
  );
  note(
    "partly_deleted",
    (n) =>
      `${modelCount(n)} lost some of ${n === 1 ? "its" : "their"} copies before the delete failed, and ${n === 1 ? "was" : "were"} kept on the shelf so you can see what is left.`,
  );
  note(
    "escapes_its_folder",
    (n) =>
      `${modelCount(n)} ${n === 1 ? "is" : "are"} recorded at a path outside the folder ${n === 1 ? "it belongs" : "they belong"} to; rescan that folder.`,
  );
  note(
    "no_such_model",
    (n) => `${modelCount(n)} ${n === 1 ? "was" : "were"} already gone.`,
  );
  // Anything the server adds later, and `delete_failed`: named as kept rather
  // than dropped, because a row still on the shelf with its file still on disk
  // is the one outcome the reader must not be left to discover for themselves.
  const rest = [...counts.values()].reduce((sum, n) => sum + n, 0);
  if (rest) {
    notes.push(
      `${modelCount(rest)} could not be deleted; the server log says why.`,
    );
  }

  if (!gone) {
    return notes.length
      ? `Nothing was deleted. ${notes.join(" ")}`
      : "Nothing to delete.";
  }
  const head = !filesRemoved
    ? `Removed ${modelCount(gone)} from the shelf; the files were already gone.`
    : permanent
      ? `Permanently deleted ${modelCount(gone)}.`
      : `Moved ${modelCount(gone)} to the ${trash}.`;
  return [head, ...notes].join(" ");
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
    columnWidths: { ...DEFAULT_COLUMN_WIDTHS },
  };
}

/**
 * The default `Show` selection, and therefore what "no active filter" means.
 *
 * `unclassified` is on, and it is a first-class state with its own checkbox,
 * never folded into either other bucket. `engines` is on for the same reason:
 * they are the answer to "where did my disk go", and on a measured machine
 * they are 118 GB against the adapters' few - invisible by default is how they
 * came to be missing from the shelf for three releases while the architecture
 * note claimed they were on it.
 * `adapterKinds: []` means *every* kind, not *no* kind - an empty multi-select
 * is unconstrained, the standard convention, and the only reading under which
 * a fresh install shows anything. `capabilities` reads the same way.
 */
function defaultFilters() {
  return {
    adapters: true,
    adapterKinds: [],
    checkpoints: true,
    // On, for the reason `engines` is on: a file nothing can classify is still
    // taking the disk the shelf exists to account for, and off by default is
    // how a 339 MB leftover in PixlStash's own download folder stayed invisible
    // (#927). The box is still there to turn it off.
    unclassified: true,
    engines: true,
    // The VAEs and text encoders a generation graph loads beside a checkpoint.
    // On for the same reason as the two above and rather more urgently: until
    // they had kinds of their own the big ones were counted as checkpoints, so
    // "what are my base models" answered with a list mostly made of encoders.
    support: true,
    baseModels: [],
    capabilities: [],
    // Deliberately NOT restored by `storedFilters()` on startup, unlike every box
    // above it. "Only the files written twice" is a question somebody asks once
    // while reclaiming disk, not a preference; remembered across a restart it is
    // a shelf that opens showing four rows of an eighteen-hundred-row library
    // with no obvious reason why.
    duplicatesOnly: false,
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
  // A blob an older build wrote is discarded whole rather than half-applied,
  // the same trade `storedView` makes: the alternative is carrying every past
  // default forward forever.
  if (!parsed || parsed.v !== FILTERS_SCHEMA_VERSION) return null;
  const filters = defaultFilters();
  // `support` is absent from every blob an earlier build wrote, which needs no
  // schema bump: the key simply misses this loop and keeps its default of on.
  for (const key of BLOCKS) {
    if (typeof parsed[key] === "boolean") filters[key] = parsed[key];
  }
  for (const key of ["adapterKinds", "baseModels", "capabilities"]) {
    if (Array.isArray(parsed[key])) {
      filters[key] = parsed[key].filter((v) => typeof v === "string");
    }
  }
  // Folded on the way in, because the facet and the match now key on the
  // FOLDED algorithm and a shipped build persisted the raw column. A remembered
  // `LoRA` would otherwise match no row and appear in no checkbox: the shelf
  // shows nothing, the Adapters box reads fully on, the one algorithm box reads
  // off, and the only way out is `Reset filters` - which also throws away the
  // base models and capabilities the user did not ask to lose.
  //
  // Folded rather than gated behind a `FILTERS_SCHEMA_VERSION` bump for that
  // same reason: the version discards the blob WHOLE, and this selection is
  // still exactly what the user chose. It only needs spelling the new way.
  filters.adapterKinds = [
    ...new Set(filters.adapterKinds.map(adapterKindKey).filter(Boolean)),
  ];
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
  // Per column and clamped on the way in, for the reason the fields above are
  // read one at a time: a blob written before the columns could be dragged is
  // still a valid remembered sort, and a width edited by hand in devtools is
  // not a reason to hand back a shelf whose Size column is 4,000px wide.
  for (const key of COLUMN_KEYS) {
    const width = clampColumnWidth(key, parsed.columnWidths?.[key]);
    if (width !== null) view.columnWidths[key] = width;
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
 *
 * `feature` is the second axis to fan out, and for the same kind of reason:
 * Florence-2 captions AND detects, and the embedder's CLIP is both the search
 * encoder and the aesthetic scorer's backbone. Filing either under one heading
 * answers "what breaks if I delete this" wrongly, which is the question this
 * axis exists to answer, so the row appears under each. An adapter declares no
 * capability and is filed under its algorithm instead - see below.
 */
function groupsOf(row, axis) {
  if (axis === "feature") {
    const capabilities = Array.isArray(row.capabilities)
      ? row.capabilities
      : [];
    if (!capabilities.length) {
      // An adapter's algorithm IS its heading here. The row's own Kind column
      // has always read `LoRA`, so filing it under the catch-all left
      // the axis saying one thing and the cell beside it another - and it
      // swallowed most of the shelf into one bucket, which is the one shape a
      // grouping axis must not take. A checkpoint has no algorithm either, and
      // is headed by its FILE KIND two branches down for the same reason.
      //
      // `unknown` is excluded because it is not an algorithm: it is
      // `detect_adapter_kind`'s explicit refusal (`KIND_UNKNOWN`) for an
      // adapter whose tensor markers matched nothing it knows. Promoting that
      // to a heading would stand a second shrug next to the real one and call
      // it a feature - the confident wrong answer the classifier declined to
      // give. The comparison catches every spelling of it because the label is
      // trimmed and folded first.
      //
      // Keyed on the FOLDED value and labelled from it, the same split
      // `base_model` makes one branch down: the key is what a collapsed group
      // is remembered by, so keying on the label would orphan everyone's
      // stored `kind:GLoRA` the day this table learns to spell `glora`. The
      // `kind:` prefix keeps an algorithm out of the capability keyspace,
      // where a collision would silently merge two unrelated headings.
      const key = adapterKindKey(row.kind);
      if (row.file_kind === "adapter" && key && key !== "unknown") {
        return [
          {
            key: `kind:${key}`,
            label: adapterKindLabel(key),
            labelKind: "name",
          },
        ];
      }
      // The same argument one file kind over. A checkpoint, a VAE and a text
      // encoder never declare a capability - nothing scans one and writes
      // `model_capability` - so all three sat under the catch-all while
      // the cell beside them read `Checkpoint`. The sting is sharper here than
      // for adapters: a DECLARED checkpoint (an HF-cache repo, which enters as
      // `file_kind=engine`) DOES record the capability, so the axis drew a
      // `Checkpoint` header holding the one packaged model and left all eighty
      // scanned checkpoints out of it.
      //
      // Unprefixed, and that is the point - the opposite of the branch above.
      // `kind:` keeps an ALGORITHM out of the capability keyspace because a
      // collision there would merge two unrelated headings; here the collision
      // IS the fix, because `checkpoint` the file kind and `checkpoint` the
      // capability are one heading and the declared row keys on the latter. A
      // fifth file kind takes the same unprefixed key, and takes it knowingly:
      // the keyspace it shares is `CAPABILITY_LABELS`, which is short and
      // reviewable, not free text.
      //
      // `unknown` is excluded for the reason the algorithm above is: an
      // unclassified file is the classifier's shrug, and a heading called
      // "Unclassified" beside `Other` is the same shrug twice. It falls to
      // `Other` with everything else that says nothing.
      if (row.file_kind !== "unknown" && fileKindLabel(row.file_kind)) {
        return [
          {
            key: String(row.file_kind),
            label: fileKindLabel(row.file_kind),
            labelKind: "name",
            // "" for the support kinds, which no capability marks - the header
            // falls back to the axis glyph rather than borrowing a wrong one.
            icon: capabilityIcon(row.file_kind),
          },
        ];
      }
      // Whatever is left is `Other`, the SAME group the stored capability of
      // that name lands in. Two headings were two answers to one question:
      // "the classifier looked and found nothing it names" and "nobody has
      // said" are a distinction the reader cannot act on differently, and a
      // shelf that draws both makes them scan two buckets for one file.
      // Merged on the capability's own key, so it is one group and not two
      // spelled the same, exactly as `checkpoint` is above.
      return [
        {
          key: "other",
          label: capabilityLabel("other"),
          labelKind: "name",
          icon: capabilityIcon("other"),
        },
      ];
    }
    return capabilities.map((capability) => ({
      key: String(capability),
      label: capabilityLabel(capability),
      labelKind: "name",
      // The feature's own glyph, so eight headers read as eight things. Empty
      // for a capability this build does not know, which leaves the header on
      // the axis's glyph rather than on a wrong one.
      icon: capabilityIcon(capability),
    }));
  }
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

/** The top-level type checkboxes, each one row bucket. */
const BLOCKS = [
  "adapters",
  "checkpoints",
  "unclassified",
  "engines",
  "support",
];

/**
 * Which block a row came from, so a fetch only replaces what it asked for.
 *
 * `support` is the one block that is two kinds. A VAE and a text encoder are
 * separate answers to "what is this file" and one answer to "what does the
 * shelf show", so they are separate `file_kind`s and one checkbox - the alt
 * being two boxes nobody wants to tick separately.
 */
function blockOf(row) {
  if (row.file_kind === "checkpoint") return "checkpoints";
  if (row.file_kind === "unknown") return "unclassified";
  if (row.file_kind === "engine") return "engines";
  if (row.file_kind === "vae" || row.file_kind === "text_encoder") {
    return "support";
  }
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
  /** Model ids the last scan added, for the `New` badge. Never persisted. */
  const newIds = ref(new Set());
  const loading = ref(false);
  const error = ref("");
  /** True once a fetch has completed, so "empty" and "not asked yet" differ. */
  const loaded = ref(false);
  // Discards a list read the user has already overtaken, and one that was on
  // the wire when the credential changed. Every fetch takes the next number,
  // so only the newest one may write.
  let epoch = 0;

  function remember() {
    writeStored(FILTERS_KEY, { v: FILTERS_SCHEMA_VERSION, ...filters });
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
   *
   * `markNew` is what a SCAN passes: the ids this fetch brought back that the
   * last one did not are what the scan added, and the rows wear a `New` badge
   * until the next fetch clears it. Diffed against the previous ids rather than
   * read off a timestamp, because "new" here means "this appeared while you
   * were looking", which is a fact about the two payloads and not about
   * `added_at` - a folder re-registered after a Forget hands back rows whose
   * `added_at` is months old and which are nonetheless new to this shelf.
   *
   * @param {{markNew?: boolean}} [options]
   */
  async function fetchRows({ markNew = false } = {}) {
    const startedAt = (epoch += 1);
    const before = new Set(rows.value.map((row) => row.id));
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
      // Two requests for one checkbox: the route takes a single `file_kind`,
      // and these two kinds are one thing to a reader deciding what to keep.
      if (filters.support) {
        requests.push(listAdapters({ fileKind: "vae" }));
        requests.push(listAdapters({ fileKind: "text_encoder" }));
      }
      const results = await Promise.all(requests);
      if (startedAt !== epoch) return;
      const refreshed = new Set(BLOCKS.filter((block) => filters[block]));
      rows.value = [
        ...rows.value.filter((row) => !refreshed.has(blockOf(row))),
        ...results.flat(),
      ];
      // Cleared on every ordinary fetch, so the badge is exactly "what the scan
      // you just ran added" and never a stale mark from three refreshes ago.
      newIds.value = markNew
        ? new Set(
            rows.value.map((row) => row.id).filter((id) => !before.has(id)),
          )
        : new Set();
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

  /**
   * Every adapter algorithm present, for the nested kind checkboxes.
   *
   * FOLDED, like `baseModelOptions` is folded: `model.kind` is free text, so
   * `LoRA` and `lora` are one algorithm and two raw strings. Faceting on the
   * raw column offered two checkboxes that the panel now draws with the same
   * label, each ticking half the rows - and the `feature` axis folds them into
   * one group, so ticking either would have emptied half of a group the user
   * can see.
   *
   * Sorted on the KEY, like `baseModelOptions` below and unlike
   * `capabilityOptions`, which sorts on the label: these ARE the folded keys
   * and every label differs from its key only in case, so ordering by label
   * could not put a single box anywhere else.
   */
  const adapterKindOptions = computed(() =>
    [
      ...new Set(
        rows.value
          .filter((r) => r.file_kind === "adapter")
          // `.filter(Boolean)` and not `r.kind`: the hub CHECK makes an
          // adapter's kind NOT NULL but not non-empty, so a whitespace-only
          // one folds to "" and would otherwise be a checkbox with no label.
          .map((r) => adapterKindKey(r.kind))
          .filter(Boolean),
      ),
    ].sort(),
  );

  /**
   * Every capability present, for the nested feature checkboxes.
   *
   * Ordered by the label a reader sees rather than by the stored word, because
   * the list is read as labels: sorting on `scorer` would file "Quality score"
   * under S. Faceted over every row's whole set, so a model serving two
   * features contributes to both boxes - the same rule as the group axis.
   */
  const capabilityOptions = computed(() =>
    [
      ...new Set(
        rows.value.flatMap((r) =>
          (Array.isArray(r.capabilities) ? r.capabilities : []).map(String),
        ),
      ),
    ].sort((a, b) => capabilityLabel(a).localeCompare(capabilityLabel(b))),
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
    // each tick a quarter of one base - and ticking "SDXL" would hide the rows
    // whose file happens to spell it `sdxl base`.
    const named = [
      ...new Set(rows.value.map(baseModelKey).filter(Boolean)),
    ].sort();
    const hasUnset = rows.value.some((r) => !baseModelKey(r));
    return hasUnset ? [...named, BASE_MODEL_UNASSIGNED] : named;
  });

  /**
   * What the base-model FIELD completes against, which is not what the FILTER
   * facets on.
   *
   * `baseModelOptions` above is built from the rows on screen and is folded,
   * because a filter checkbox must tick exactly the rows behind it. A
   * completion list has the opposite job: it offers strings that are NOT on the
   * shelf yet - the labels the server ships, so the field is useful on a fresh
   * install where nothing records a base model at all - and it offers them in
   * the spelling they will be stored in. Two lists, two jobs; deriving one from
   * the other would break whichever one lost.
   */
  const fetchedCompletions = ref([]);
  let completionsLoaded = false;
  let completionsAttemptedAt = 0;

  /**
   * The fetched list, plus the base models on screen the server did not know.
   *
   * The fetch is not the only writer of `base_model`: the scanner and the
   * importer write it too, and neither goes anywhere near this store - so a
   * value that arrived with a scan would not be offered until a reload. The
   * rows carry their own spellings, so unioning them in costs no request and
   * closes that window.
   *
   * **A row whose `base_model_folded` is set is skipped**, which is the
   * server's own rule (`completions()` drops an extra that folds to something
   * it ships) applied to the field the server already computed for the row.
   * Without it the client hands back every alias the server deliberately
   * dropped: a shelf spelling `sdxl base`, `SDXL` and `sdxl_base_v1-0` would
   * offer three more entries for the `SDXL 1.0` already in the list, and with
   * eight slots in the menu the canonical label can be pushed off it. The
   * client cannot fold on its own - the alias table is the server's - so this
   * is the only place the rule can be honoured.
   *
   * Deduplication is on case, spacing and punctuation (the server's `_norm`),
   * with the fetched label winning: that catches two rows spelling one unknown
   * base differently, which is all it claims to catch.
   */
  const baseModelCompletions = computed(() => {
    const seen = new Set();
    const out = [];
    const add = (value) => {
      const key = String(value || "")
        .toLowerCase()
        .replace(/[^a-z0-9]/g, "");
      if (!key || seen.has(key)) return;
      seen.add(key);
      out.push(String(value).trim());
    };
    for (const value of fetchedCompletions.value) add(value);
    for (const row of rows.value) {
      if (row.base_model_folded) continue;
      add(row.base_model);
    }
    return out;
  });

  /**
   * Fetch the completion list once, then leave it alone.
   *
   * The field is opened and closed constantly and the list only moves when
   * somebody saves a base model the server has never seen, so it is fetched on
   * first use and invalidated by the write that could change it rather than
   * polled. A failure is not worth a notice: the field still takes free text,
   * which is what it took before there was a list at all.
   *
   * A failure is **throttled, not latched**. The field asks on every keystroke,
   * so clearing the stamp on the error path turned one dead endpoint into one
   * request per character typed; latching it instead would mean a single blip
   * cost completion for the rest of the session.
   */
  async function loadBaseModelCompletions() {
    const now = Date.now();
    if (completionsLoaded) return;
    if (now - completionsAttemptedAt < COMPLETION_RETRY_MS) return;
    completionsAttemptedAt = now;
    try {
      fetchedCompletions.value = await listBaseModelCompletions();
      completionsLoaded = true;
    } catch (err) {
      console.debug("[shelf] could not load base-model completions", err);
    }
  }

  /** Make the next open re-fetch: the list this holds can no longer be right. */
  function invalidateBaseModelCompletions() {
    completionsLoaded = false;
    completionsAttemptedAt = 0;
  }

  /** The rows the current selection actually shows, with display fields. */
  const visibleRows = computed(() => {
    const kinds = filters.adapterKinds;
    const bases = filters.baseModels;
    const capabilities = filters.capabilities;
    const shown = rows.value
      .filter((row) => {
        // The type checkboxes narrow here as well as choosing what to fetch:
        // a block already fetched stays in `rows` so its options survive.
        if (!filters[blockOf(row)]) return false;
        if (row.file_kind === "adapter" && kinds.length) {
          // Matched against the same folded key the facet list was built from,
          // exactly as the base-model filter is two branches down.
          //
          // A row whose kind folds to nothing is NOT excluded. The facet offers
          // no box for it - an unlabelled checkbox is not a control - so a kind
          // selection cannot hold an opinion about it, and hiding it would
          // leave a row no box can bring back. That is the failure this store
          // already memorialises one computed up: base models that stayed
          // selected with no box left to untick them.
          const key = adapterKindKey(row.kind);
          if (key && !kinds.includes(key)) return false;
        }
        // "HAS this capability", not "IS this kind" - the whole point of the
        // set. A model serving two features survives a tick of either.
        //
        // Scoped to the engines block for the same reason the kind boxes are
        // scoped to adapters: a nested filter narrows the block it hangs under
        // and leaves the others alone, so ticking `Captioning` no longer hides
        // every LoRA on the shelf than ticking `lora` hides every checkpoint.
        if (blockOf(row) === "engines" && capabilities.length) {
          const own = Array.isArray(row.capabilities) ? row.capabilities : [];
          if (!own.some((c) => capabilities.includes(String(c)))) return false;
        }
        if (bases.length) {
          // Matched against the same key the facet list was built from.
          const key = baseModelKey(row) || BASE_MODEL_UNASSIGNED;
          if (!bases.includes(key)) return false;
        }
        // Last, so it narrows whatever the boxes above left - the point is
        // "duplicates among the files I am looking at", not a separate screen.
        // Against the row's OWN copies, before the folder narrowing two
        // computeds down: a file written twice into one folder must survive
        // this on every grouping axis, including the one that then shows the
        // reader only one of the two.
        if (filters.duplicatesOnly && presentCopies(row.locations) < 2) {
          return false;
        }
        return true;
      })
      .map((row) => ({
        ...row,
        name: modelName(row),
        locState: locationState(row.locations),
        // Counted HERE, off the row's whole `locations`, and carried on the row
        // from then on. The folder axis narrows a draw to the one copy that
        // folder holds, so a template calling `presentCopies` at render time
        // would report `1` for exactly the rows this is meant to mark.
        copies: presentCopies(row.locations),
        isNew: newIds.value.has(row.id),
      }));
    // Folded LAST, so the filters narrow individual models and the stack is
    // then built from what survived. Folding first would let a stack whose
    // cover matches drag hidden members back into view.
    //
    // A stack is `New` when ANY member is: a scan that adds a seventh step to
    // a six-step run leaves the cover untouched, and a run that grew is what
    // the badge is for. The fold takes the cover's own fields, so this is the
    // one that has to be recomputed across the members.
    return collapseStacks(shown).map((row) =>
      row.members ? { ...row, isNew: row.members.some((m) => m.isNew) } : row,
    );
  });

  /**
   * A stack's members as this folder's draw sees them, one copy each.
   *
   * The per-member half of the narrowing the drawn row gets; see the comment at
   * the push below.
   */
  function narrow(members, group) {
    return members.map((member) => {
      const here = (member.locations ?? []).filter(
        (loc) => loc.folder_id === group.location.folder_id,
      );
      return here.length
        ? { ...member, locations: here, locState: locationState(here) }
        : member;
    });
  }

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
            // The header's glyph, where the axis has one per group rather than
            // one for the whole axis. Empty elsewhere, and `withFolderSignals`
            // fills it in for `folder` after the fact.
            icon: group.icon || "",
            rows: [],
          };
          byKey.set(group.key, bucket);
        }
        // `rowKey` carries the GROUP on every grouped axis, not only on the
        // ones that can draw a row twice. Two axes fan out now - a file copied
        // into two folders, a model serving two features - and both drew the
        // same model under two headers with one key, which is the collision
        // that put `tabindex="0"` on several draws at once and made `indexOf`
        // return the first. Under `base_model` a row is in exactly one group,
        // so the suffix is redundant there and costs nothing; the key is opaque
        // to every reader of it.
        //
        // Under `folder` a row also reports THAT copy's state rather than the
        // merged one, or a file present here and missing there would claim to
        // be fine in the folder it is absent from. `locations` is narrowed to
        // the same one copy for the same reason: the draw stands for one copy,
        // and the file line's path tooltip reading the merged array would
        // answer "where is this file" with a path in the folder above.
        //
        // Narrowing here is safe because nothing else reads a DRAWN row's
        // locations - `selectedRows` and the verbs all read `visibleRows`,
        // which still carries every copy.
        //
        // A stack's `members` are narrowed with it, for the reason the row
        // itself is: an expanded strip under `/models` would otherwise answer
        // "where is this step" with a path in the folder above. Members with no
        // copy in this folder keep their own - the run is drawn here because
        // the run is here, and inventing an absence for one step would be worse
        // than a merged tooltip.
        bucket.rows.push(
          group.location
            ? {
                ...row,
                // The COPY, not the folder. `group.key` is the folder path, so
                // a file written twice into one folder produced two draws under
                // one key - the very collision the note above describes, hit by
                // the axis it was written for. `relpath` is unique within a
                // folder by definition (it is half the `model_file` key), so
                // appending it separates them and changes nothing for the
                // single-copy rows that are the rest of the shelf.
                rowKey: `${row.id}:${group.key}:${group.location.relpath}`,
                locState: locationState([group.location]),
                locations: [group.location],
                ...(row.members ? { members: narrow(row.members, group) } : {}),
              }
            : { ...row, rowKey: `${row.id}:${group.key}` },
        );
      }
    }
    return [...byKey.values()].sort(compareGroups);
  });

  /**
   * The folders that are wholly out of reach - an unplugged drive, usually.
   *
   * Derived from `rows` and NOT from `visibleRows`, because it is a fact about
   * the disk rather than about the current `Show` selection: a filter that
   * hides the one present copy in a folder must not promote that folder to
   * "offline", and the banner's count must not shrink when the reader narrows
   * the list.
   */
  const offlineMounts = computed(() => offlineFolders(rows.value));

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
    // A NEW sort key arrives at its own end unless the caller named one. Here
    // rather than in the caller, because there are two writers of `sortKey` -
    // the column headings and the Sort panel - and putting it in one of them
    // is how the two came to disagree: the panel carried "Newest first" over
    // onto Name and handed back Z to A, which is exactly what
    // `defaultSortDirection` exists to stop.
    if (
      patch.sortKey !== undefined &&
      patch.sortKey !== view.sortKey &&
      patch.sortDirection === undefined
    ) {
      patch = { ...patch, sortDirection: defaultSortDirection(patch.sortKey) };
    }
    Object.assign(view, patch);
    rememberView();
  }

  /**
   * Resize one data column, in pixels.
   *
   * `persist` is off for the frames of a drag and on for the end of it: a
   * `pointermove` arrives every frame, and `rememberView` rebuilds the whole
   * blob, walks four collapsed sets and does a synchronous `setItem`, so
   * writing per move is ~120 storage writes a second on the main thread for a
   * value that only matters once the pointer is up. A press of the arrow keys
   * IS the end of its own gesture and persists.
   *
   * Persisting is not conditional on the width having changed: the last frame
   * of a drag usually sets the same pixel the frame before did, and skipping
   * the write there would throw the whole drag away.
   *
   * @param {string} key - one of {@link COLUMN_KEYS}.
   * @param {number} px - the requested width, clamped to the bounds.
   * @param {boolean} [persist=true] - whether to write the view blob.
   */
  function setColumnWidth(key, px, persist = true) {
    if (!COLUMN_KEYS.includes(key)) return;
    const width = clampColumnWidth(key, px);
    if (width === null) return;
    if (width !== view.columnWidths[key]) {
      view.columnWidths = { ...view.columnWidths, [key]: width };
    }
    if (persist) rememberView();
  }

  /**
   * Active filters, counted by section rather than by box.
   *
   * A section contributes 1 when it deviates from its default, however many
   * boxes are ticked inside it. Counting boxes would report "9" for a mild
   * narrowing and the number would stop meaning anything.
   */
  const activeCount = computed(() => {
    // Every block defaults to ON, so the departure is turning one OFF - and
    // read off {@link BLOCKS} rather than named one by one, which is how
    // `engines` came to be uncounted: a shelf showing engines alone reported
    // "3 filters active" while the fourth block was the only one showing
    // anything.
    let n = BLOCKS.filter((block) => !filters[block]).length;
    // The nested kinds are part of the Adapters section, so they add nothing
    // when that block is already counted.
    if (filters.adapters && filters.adapterKinds.length) n += 1;
    if (filters.baseModels.length) n += 1;
    if (filters.capabilities.length) n += 1;
    if (filters.duplicatesOnly) n += 1;
    return n;
  });

  /**
   * True when the selection asks for no rows at all - a distinct empty state.
   *
   * Read off {@link BLOCKS} rather than named block by block, which is how
   * `engines` came to be left out of it: a shelf showing engines alone asks
   * for plenty of rows, fetched them, and then drew "Nothing is selected in
   * Show" over the top of them.
   *
   * This and `activeCount` now derive; `defaultFilters`, `storedFilters`,
   * `blockOf` and `fetchRows` still spell the blocks out, because each says
   * something different about each one. A fifth block is four deliberate
   * edits, not five - the two that could silently disagree with the fetch are
   * the two that no longer can.
   */
  const nothingSelected = computed(
    () => !BLOCKS.some((block) => filters[block]),
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
  /**
   * A run counts as one row while the whole of it is selected; the moment part
   * of it is, the parts count for themselves. `visibleRows` holds one row per
   * stack, so a member picked out of an expanded strip is not in it and has to
   * be contributed from `members` - otherwise selecting a step gives the bar an
   * empty selection and every verb a phantom to gate on.
   */
  const selectedRows = computed(() => {
    const chosen = selectedIds.value;
    const out = [];
    for (const row of visibleRows.value) {
      if (!row.members || row.members.length < 2) {
        if (chosen.has(row.id)) out.push(row);
        continue;
      }
      if (row.memberIds.every((id) => chosen.has(id))) {
        out.push(row);
        continue;
      }
      // Part of a run: the members stand on their own, cover included. A verb
      // then writes the files that are actually ticked rather than the run
      // they happen to belong to, which is the whole point of being able to
      // pick one out of the strip.
      for (const member of row.members) {
        if (chosen.has(member.id)) out.push(member);
      }
    }
    return out;
  });

  /**
   * Every selected model, stack members included.
   *
   * `selectedRows` is one row per *shown* row, so a collapsed stack appears
   * once - right for counting and for what the bar says, wrong for what a verb
   * writes. A verb must act on the whole run or a Forget would destroy a run's
   * cover and leave its five steps on the shelf, which is precisely the partial
   * state `services/stack_membership` exists to forbid.
   *
   * A member picked out of an expanded strip is its own row and carries no
   * `memberIds`, so it expands to itself. That is the deliberate exception: the
   * reader opened the run and pointed at one file inside it, which is a
   * different gesture from selecting the run.
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
   *
   * A **member** of an expanded strip stands for itself: it is not in
   * `visibleRows`, so the lookup misses and the id comes back alone. That is
   * the intent rather than a gap - opening a run and pointing at one file
   * inside it is how that file is taken out of the run, or made its cover.
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
      // An OPEN run is in the sequence member by member, so its cover must
      // stand for itself inside a range or the range reaches past the row the
      // reader aimed at: ending on a run's second file would otherwise pull in
      // its third, through the cover. A CLOSED run has only its cover in the
      // sequence and still expands to the whole thing, which is what makes a
      // range over collapsed rows take whole runs.
      const drawn = new Set(sequence);
      const withinRange = (rowId) => {
        const behind = modelsBehind(rowId);
        return behind.length > 1 &&
          behind.some((m) => m !== rowId && drawn.has(m))
          ? [rowId]
          : behind;
      };
      // The anchor stays where it was: dragging a range out and back with
      // repeated Shift+clicks has to measure from the same end each time.
      selectedIds.value = new Set(
        sequence.slice(start, end + 1).flatMap(withinRange),
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
      // A base model the server had never seen is a completion target the
      // moment it is stored, so the list it came from is now one entry short.
      if ("base_model" in changes) invalidateBaseModelCompletions();
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
      // The refusal reasons are different news and must not be conflated:
      // `still_has_a_copy` means the file turned up, `no_such_model` means the
      // row was already gone (another tab forgot it, or this list is stale),
      // `is_a_builtin_engine` means it is ours and the owner has nothing to do.
      // Anything the server may add later counts as "kept", which is the
      // conservative reading - not forgotten, and possibly still there.
      const count = (reason) =>
        refused.filter((r) => r.reason === reason).length;
      const vanished = count("no_such_model");
      const engines = count("is_a_builtin_engine");
      const kept = refused.length - vanished - engines;
      notices.push({
        level: gone ? "success" : "info",
        text: forgetReceipt(gone, kept, vanished, engines),
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
   * Delete the selection from disk, then say what went and what did not.
   *
   * The shelf's only destructive verb. The confirmation is the view's - this
   * runs after it - and both of the things that decide what is destroyed come
   * from the gesture rather than from anything this store remembers: `permanent`
   * from the press's own Shift, and `ids` from the list the prompt counted, so
   * the reader can never agree to one number and have another one deleted.
   *
   * @param {Object} [options]
   * @param {boolean} [options.permanent=false] - Shift+Delete: unlink rather
   *   than trash.
   * @param {number[]} [options.ids] - the models to delete. Defaults to the
   *   whole selection, stacks expanded; the view narrows it to the subset the
   *   route will accept.
   * @returns {Promise<boolean>} true when the call was made at all.
   */
  async function deleteSelected({ permanent = false, ids } = {}) {
    const notices = useNoticeStore();
    const targets = ids?.length ? ids : selectedModelIds.value;
    if (!targets.length) return false;
    try {
      const body = await deleteModels(targets, { permanent });
      clearSelection();
      await fetchRows();
      const gone = body?.deleted?.length ?? 0;
      notices.push({
        level: gone ? "success" : "info",
        text: deleteReceipt(
          gone,
          body?.refused ?? [],
          Boolean(body?.permanent),
          // The server's word for its own trash, because the server is the
          // machine the files were on. `trashName()` is the browser's guess and
          // stands in only for an older backend.
          body?.trash_name || trashName(),
          body?.files_removed ?? 0,
        ),
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not delete those models.",
      });
      return false;
    }
  }

  /**
   * Attach or detach one character/set across the selected adapters.
   *
   * `PUT /adapters/{sha256}/attachments` REPLACES one adapter's whole set, so
   * this is N calls with the union computed here - never one call, and never a
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
   * Give every selected model the same icon.
   *
   * Addressed off `selectedModelIds`, never `selectedRows`: a fully-ticked
   * stack is ONE row whose `id` is the cover's, so iterating rows would mark
   * the cover and silently skip the other eleven versions. This is the same
   * expansion `clearIconsOnSelected` uses, and set and clear have to agree
   * about what a selection is.
   *
   * One upload per model rather than one bulk route: the icon store is
   * content-addressed, so identical bytes collapse to one file on disk however
   * many times they are posted, and this reuses the write path all three ways
   * of choosing an icon already share. Windowed rather than fired all at once,
   * for the socket reason above.
   *
   * The caller confirms a bulk set that would REPLACE existing marks; this
   * writes.
   *
   * @param {File|Blob} file
   * @returns {Promise<boolean>} true when at least one icon landed.
   */
  async function setIconOnSelected(file) {
    const notices = useNoticeStore();
    const ids = selectedModelIds.value;
    if (!ids.length || !file) return false;
    if (ids.length > MAX_MODELS_PER_ICON_SET) {
      notices.push({
        level: "error",
        text: `At most ${MAX_MODELS_PER_ICON_SET} models in one thumbnail set.`,
      });
      return false;
    }

    // Captured before the writes: `fetchRows()` below replaces the rows, and
    // the receipt names the model the reader pointed at.
    const only = ids.length === 1 ? selectedRows.value[0] : null;

    const failed = [];
    let done = 0;
    for (let i = 0; i < ids.length; i += ICON_SET_CONCURRENCY) {
      const batch = ids.slice(i, i + ICON_SET_CONCURRENCY);
      const results = await Promise.allSettled(
        batch.map((id) => setModelIcon(id, file)),
      );
      results.forEach((result, offset) => {
        if (result.status === "rejected") {
          failed.push([batch[offset], result.reason]);
        } else {
          done += 1;
        }
      });
    }

    if (failed.length) {
      // The ids as well as the reasons: the receipt can only say how many
      // failed, and without this there is nothing anywhere saying WHICH - and
      // the only recourse is otherwise to redo the whole selection.
      console.warn(
        `[modelShelf] ${failed.length} thumbnail write(s) failed:`,
        failed.map(([id, reason]) => `${id}: ${errorDetail(reason) || reason}`),
      );
    }
    await fetchRows();
    if (!done) {
      notices.push({
        level: "error",
        text: errorDetail(failed[0]?.[1]) || "Could not set that thumbnail.",
      });
      return false;
    }
    const notes = failed.length
      ? ` ${modelCount(failed.length)} could not be written.`
      : "";
    notices.push({
      level: failed.length ? "warning" : "success",
      // A row in the `needs-a-name` state has no name to say, by design - its
      // `text` is empty so the shelf can draw it as a field. The receipt still
      // has to name something the reader can recognise. Named only when the
      // selection was one model: which of a partly failed batch landed is not
      // known here.
      text: only
        ? `Set the thumbnail on ${only.name?.text || only.filename || "the model"}.${notes}`
        : `Set the thumbnail on ${modelCount(done)}.${notes}`,
    });
    return true;
  }

  /**
   * Clear the icon on the selection.
   *
   * The caller confirms a BULK clear: one row is reconstructable by setting it
   * again, and a selection is not - the same test the bulk base-model overwrite
   * falls on. The server reports which rows actually had one, so the receipt
   * says what changed rather than how many ids were sent.
   *
   * @returns {Promise<boolean>} true when the clear landed. False covers both
   *   "nothing was selected" and a failed request - the receipt says which,
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
          ? `Cleared the thumbnail on ${modelCount(cleared)}.`
          : "None of those had a thumbnail.",
      });
      return true;
    } catch (err) {
      notices.push({
        level: "error",
        text: errorDetail(err) || "Could not clear those thumbnails.",
      });
      return false;
    }
  }

  function resetForSession() {
    epoch += 1;
    rows.value = [];
    newIds.value = new Set();
    selectedIds.value = new Set();
    anchorId.value = null;
    loaded.value = false;
    error.value = "";
    loading.value = false;
    // The completion list goes with the rows. It is derived from this machine's
    // `model` rows, the credential that could read them has just changed, and
    // the stamp beside it would otherwise say "already fetched" forever.
    fetchedCompletions.value = [];
    invalidateBaseModelCompletions();
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
    deleteSelected,
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
    capabilityOptions,
    baseModelOptions,
    baseModelCompletions,
    loadBaseModelCompletions,
    visibleRows,
    groups,
    offlineMounts,
    renderedCount,
    activeCount,
    nothingSelected,
    isCollapsed,
    toggleGroup,
    resetFilters,
    resetForSession,
    setFilters,
    setView,
    setColumnWidth,
  };
});
