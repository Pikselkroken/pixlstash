// Model-shelf row helpers: the name fallback chain, size, and location state.
//
// 37% of real adapters carry no title, no base model and no trigger word at
// all, so none of this is edge-case handling - it is what most of the column
// renders. Two rules follow from that and are load-bearing:
//
//   * The derived name is computed HERE, at render, never stored. That is what
//     keeps `display_name IS NULL` an exact "nobody has named this" queue on
//     the backend and stops a guess being mistaken for a choice.
//   * A missing value is still rendered. "Base model not set" occupies the same
//     slot in the same type as a real value; a blank cell is the failure mode.

import { ICON_CARDS, SET_COLORS } from "./setAppearance";

/** Trailing tokens that record where in a training run a file was saved.
 *
 * Mirrors `_TRAINING_SUFFIX_RE` in `pixlstash/utils/model_utils.py`. The
 * bare-digit rule needs five digits on purpose: ai-toolkit zero-pads its step
 * counts, so `000002750` goes while the `2` in `portrait mix v2` stays.
 */
const TRAINING_SUFFIX_RE = /^(?:step\d+|epoch\d+|\d+ep|\d{5,})$/i;

/**
 * Strip the extension and turn separators into spaces.
 *
 * Mirrors `clean_asset_name`, which must not change: its output is baked into
 * stored sentence embeddings. Anything the shelf wants on top goes in
 * {@link deriveModelName}.
 *
 * @param {string} filename - file name or path.
 * @returns {string}
 */
export function cleanAssetName(filename) {
  const base = String(filename || "")
    .split(/[\\/]/)
    .pop();
  const stem = base.replace(/\.[^.]+$/, "");
  return stem.replace(/[_-]/g, " ").trim();
}

/**
 * Derive a display name for a file that never said what it is called.
 *
 * Mirrors `derive_model_name`: drops trailing training bookkeeping, because
 * the step is parsed into its own field and repeating it turns six checkpoints
 * of one run into six unrelated-looking rows.
 *
 * @param {string} filename - file name or path.
 * @returns {string} a human-readable name, or `""` when nothing survives.
 */
export function deriveModelName(filename) {
  const tokens = cleanAssetName(filename).split(/\s+/).filter(Boolean);
  while (tokens.length && TRAINING_SUFFIX_RE.test(tokens[tokens.length - 1])) {
    tokens.pop();
  }
  return tokens.join(" ");
}

/**
 * Resolve what a row is called, and WHO decided it.
 *
 * The chain is: the name the user gave, else a readable one we made from the
 * filename, else the filename itself, else nothing. `state` is the whole point
 * - the row draws each of the four differently, because "somebody named this"
 * and "we guessed" and "there is nothing here to read" are three different
 * things to a reader deciding what to fix, and the shelf used to render all of
 * them as one string.
 *
 * The last state returns an EMPTY string on purpose. A row with no filename
 * used to read `no name in file`, which looks like a name, sorts like a name
 * and reads as inert - so the one row that most needs naming was the one that
 * least invited it. The row renders the empty case as a field.
 *
 * @param {Object} model - a row from `/adapters` or `/checkpoints`.
 * @returns {{text: string, state: "named"|"derived"|"from-file"|"needs-a-name"}}
 */
export function modelName(model) {
  const given = String(model?.display_name || "").trim();
  if (given) return { text: given, state: "named" };
  const filename = String(model?.filename || "").trim();
  const derived = deriveModelName(filename);
  // A real readable name, and OURS: `deriveModelName` rewrote the file's own
  // string, so nothing on disk says this. It must not be mistaken for a title.
  if (derived) return { text: derived, state: "derived" };
  // Nothing survived the strip (a file called `000002750.safetensors`). The
  // raw filename is the only honest thing left to show - and it is the file's
  // string verbatim, which is what the row says out loud.
  if (filename) return { text: filename, state: "from-file" };
  return { text: "", state: "needs-a-name" };
}

/**
 * What each sort key is called on screen, and the glyph that stands for it.
 *
 * Keyed by the API's own `SortKey` values so there is one vocabulary rather
 * than a UI one mapped onto a wire one.
 */
export const SORT_LABELS = {
  added_at: { label: "Date added", icon: "mdi-clock-plus-outline" },
  file_mtime: { label: "File date", icon: "mdi-file-clock-outline" },
  name: { label: "Name", icon: "mdi-sort-alphabetical-variant" },
  size: { label: "Size", icon: "mdi-harddisk" },
  base_model: { label: "Base model", icon: "mdi-cube-outline" },
};

/**
 * Which date axis the shelf's date column is drawn in.
 *
 * The column FOLLOWS the sort. Two of the five keys are dates, and a column
 * that always showed `added_at` would read as unordered the moment the shelf
 * was sorted on the other one - the reader would be looking at a column of
 * dates in what looks like no order at all. Every non-date key falls back to
 * `added_at`, which is the shelf's own default axis.
 *
 * @param {string} sortKey - the active `SORT_LABELS` key.
 * @returns {"added_at"|"file_mtime"}
 */
export function dateColumnKey(sortKey) {
  return sortKey === "file_mtime" ? "file_mtime" : "added_at";
}

/**
 * What one row says in the date column, as an ISO string a formatter takes.
 *
 * Mirrors `SORT_VALUE` in `useModelShelfStore`, which is the whole point: the
 * column has to agree with the order the rows are drawn in.
 *
 * On `added_at` that means a stack's date is its newest member's and never its
 * cover's, and `own` reads a single step of a run instead. It is needed:
 * `newest_member_at` is a JOIN over the stack, so every member row carries the
 * run's date too, and without `own` the whole opened strip would print one
 * stamp.
 *
 * On `file_mtime` there is nothing to choose and `own` is not read.
 * `newest_file_mtime` is grouped by MODEL rather than by stack (`_LOCATION_JOIN`
 * in `model_shelf_service.py`), so a cover answers for itself, which is also
 * what the sort orders that row on. Taking a maximum across the members here
 * would print a date the sort does not use - the disagreement this whole
 * function exists to prevent. Opening the run is what shows a step that was
 * written later.
 *
 * `file_mtime` arrives as `st_mtime_ns`, so it is divided down to the
 * milliseconds `Date` counts in rather than parsed.
 *
 * @param {Object} row - a shelf row (or a stack member).
 * @param {string} sortKey - the active sort key.
 * @param {boolean} [own=false] - on `added_at`, read the row's own date rather
 *   than its stack's.
 * @returns {string} an ISO timestamp, or `""` when the row cannot answer.
 */
export function modelDate(row, sortKey, own = false) {
  if (dateColumnKey(sortKey) === "file_mtime") {
    const ns = Number(row?.newest_file_mtime);
    // `!(ns > 0)` and not `ns <= 0`, so a null or a non-number is caught here
    // rather than by the range check below.
    if (!(ns > 0)) return "";
    const at = new Date(ns / 1e6);
    // `toISOString` THROWS on a date out of range, and this runs inside render:
    // one corrupt mtime would take the whole shelf down rather than one cell.
    return Number.isNaN(at.getTime()) ? "" : at.toISOString();
  }
  return (!own && row?.newest_member_at) || row?.added_at || "";
}

/**
 * The two directions, worded for the axis being sorted.
 *
 * "Ascending" is not wrong so much as useless: nobody thinks of a date as
 * ascending, and on a size column it is the opposite of what the reader wants
 * to hear. Each key says what its own two ends are.
 */
const DIRECTION_WORDS = {
  added_at: ["Oldest first", "Newest first"],
  file_mtime: ["Oldest first", "Newest first"],
  name: ["A to Z", "Z to A"],
  base_model: ["A to Z", "Z to A"],
  size: ["Smallest first", "Largest first"],
};

/**
 * Name one direction of one sort key.
 *
 * @param {string} key - a `SORT_LABELS` key.
 * @param {"asc"|"desc"} direction
 * @returns {string} e.g. `Largest first`.
 */
export function sortDirectionLabel(key, direction) {
  const words = DIRECTION_WORDS[key] || DIRECTION_WORDS.name;
  return direction === "asc" ? words[0] : words[1];
}

/**
 * Which end a key starts at when the reader first sorts on it.
 *
 * Carrying the previous key's direction over is what a plain toggle would do,
 * and it is wrong at the moment it matters most: arriving at Name from "Date
 * added, newest first" would hand back Z to A, which nobody asked for and
 * reads as a broken sort. A name starts at A, a size starts at the big files -
 * the reason anyone sorts a shelf by size is to find what is eating the disk -
 * and a date starts at the newest, which is the shelf's own default.
 *
 * @param {string} key - a `SORT_LABELS` key.
 * @returns {"asc"|"desc"}
 */
export function defaultSortDirection(key) {
  return key === "name" || key === "base_model" ? "asc" : "desc";
}

/** What each grouping axis is called, and the glyph that stands for it. */
export const GROUP_BY_LABELS = {
  none: { label: "None", icon: "mdi-format-list-bulleted" },
  base_model: { label: "Base model", icon: "mdi-cube-outline" },
  folder: { label: "Folder", icon: "mdi-folder-outline" },
  feature: { label: "Feature", icon: "mdi-star-four-points-outline" },
};

/**
 * What each stored capability is called on screen.
 *
 * The screen's words, not the database's: `model_capability` stores machine
 * vocabulary (`captioner`, `scorer`) because a stored value is not a thing a
 * designer gets to change, and these are. Named for the FEATURE rather than the
 * ML task for the same reason the classifier is - nobody who switched
 * captioning on thinks they have an `image-to-text` model.
 *
 * An unrecognised value falls through to itself rather than to a placeholder:
 * a server that grew an eighth capability should show it, not hide it behind
 * "Unknown".
 */
export const CAPABILITY_LABELS = {
  captioner: "Captioning",
  tagger: "Tagging",
  detector: "Detection",
  face: "Faces",
  search: "Search",
  scorer: "Quality score",
  checkpoint: "Checkpoint",
  other: "Other",
};

/**
 * Name one capability for display.
 *
 * @param {string} capability - a stored `model_capability.capability`.
 * @returns {string} e.g. `Captioning`.
 */
export function capabilityLabel(capability) {
  return labelFrom(CAPABILITY_LABELS, String(capability || ""));
}

/**
 * What each non-adapter `file_kind` is called on screen.
 *
 * Named as roles rather than as file types, because the reader deciding what to
 * keep is asking what the file DOES beside a checkpoint. `checkpoint` repeats
 * the word `CAPABILITY_LABELS` uses, deliberately: a scanned checkpoint records
 * no capability and a DECLARED one records `checkpoint`, and the two are one
 * heading on the feature axis rather than a group of one beside a bucket of
 * eighty.
 *
 * `adapter` and `engine` are absent, and that is what {@link fileKindLabel}
 * returning "" means: an adapter is named by its algorithm and an engine by the
 * features it declares, both of which say more than the word "Adapter".
 *
 * Not exported, for `ADAPTER_KIND_LABELS`' reason one table down: `file_kind`
 * is owner-correctable over `PATCH /models` and carries no CHECK, so a caller
 * indexing this raw with a row storing `constructor` gets `Object`'s
 * constructor FUNCTION - truthy, so a `|| ""` fallback never fires, and it
 * lands in a group label where `localeCompare` throws and takes the whole
 * `groups` computed with it. `fileKindLabel` is the only way in.
 */
const FILE_KIND_LABELS = {
  checkpoint: "Checkpoint",
  vae: "VAE",
  text_encoder: "Text encoder",
  unknown: "Unclassified",
};

/**
 * Name one `file_kind` for display.
 *
 * @param {string} fileKind - a stored `model.file_kind`.
 * @returns {string} e.g. `Checkpoint`, or `""` for a kind named some other way.
 */
export function fileKindLabel(fileKind) {
  const key = String(fileKind || "");
  return Object.hasOwn(FILE_KIND_LABELS, key) ? FILE_KIND_LABELS[key] : "";
}

/**
 * Look a free-text value up in a label table, falling through to itself.
 *
 * `Object.hasOwn` rather than a plain index, because both tables are keyed by
 * strings that come off the wire or out of a file header. `kind` reaches this
 * as `constructor` and the index returns `Object`'s constructor FUNCTION -
 * truthy, so `|| key` never fires, and it lands in a group label where
 * `localeCompare` throws and takes the whole `groups` computed with it.
 */
function labelFrom(table, key) {
  return Object.hasOwn(table, key) ? table[key] : key;
}

/**
 * How the shelf spells each adapter algorithm. Trainers spell them however
 * they like; `model.kind` stores whatever was read out of the header.
 *
 * Not exported: `adapterKindLabel` is the only way in, so no caller can index
 * it raw and reintroduce the `Object.prototype` hole `labelFrom` closes.
 */
const ADAPTER_KIND_LABELS = {
  lora: "LoRA",
  lokr: "LoKr",
  loha: "LoHa",
  dora: "DoRA",
  oft: "OFT",
  // `KIND_UNKNOWN` - `detect_adapter_kind` found adapter markers but no
  // algorithm it knows. A real thing to filter on ("which of these could we
  // not identify") and a real thing to print in the Kind cell, so it is
  // spelled here like the rest. It is only the GROUP axis that declines it,
  // because a heading called `UNKNOWN` beside `NO FEATURE RECORDED` is two
  // shrugs presented as one feature and one not.
  unknown: "Unknown",
};

/**
 * Fold one stored `model.kind` to the value everything keys on.
 *
 * `model.kind` is free text - the scanner writes a constant, but `PATCH
 * /models` stores whatever reaches it verbatim - so `LoRA`, `lora` and ` lora `
 * are one algorithm spelled three ways. The shelf folds them here, once: the
 * group key, the `Show` panel's checkbox list and the kind filter all read this
 * rather than the raw column, or the panel offers two boxes labelled `LoRA`
 * where the axis draws one group and ticking either hides half of it.
 *
 * Both halves earn their place. The CASE is reachable from the app's own edit
 * dialog, which trims but does not fold. The WHITESPACE only arrives over the
 * raw API, and an untrimmed value sorts to the very TOP of a grouped shelf,
 * ahead of every letter.
 *
 * @param {string} kind - a stored `model.kind`.
 * @returns {string} e.g. `lora`, or `""` when the row records no algorithm.
 */
export function adapterKindKey(kind) {
  return String(kind || "")
    .trim()
    .toLowerCase();
}

/**
 * Name one adapter algorithm for display.
 *
 * Falls through to the folded word, like `capabilityLabel`: an algorithm this
 * table has not heard of should show its own name rather than be hidden.
 *
 * @param {string} kind - a stored `model.kind`, or an `adapterKindKey`.
 * @returns {string} e.g. `LoRA`, or `""` when the row records no algorithm.
 */
export function adapterKindLabel(kind) {
  return labelFrom(ADAPTER_KIND_LABELS, adapterKindKey(kind));
}

/**
 * The glyph that stands for each capability, in the app's OWN vocabulary.
 *
 * Not a new icon family. Where the product already marks a feature, that mark
 * is the one used here and it is not re-drawn somewhere else: the face from
 * `ImageOverlay` and the toolbar, the SHAPE from the same two - it is what
 * "Object boxes" and "Detect objects" wear, so `detector` takes it and the
 * catch-all does not - the tag, the star and the caption box from the operation
 * log's icon rules. The two features nothing else in the app marks take glyphs
 * nothing else in the app uses: a whole packaged model for `checkpoint` (NOT
 * the cube, which is the base-model axis's), and the overflow dots for `other`.
 *
 * Grouped by feature, the shelf otherwise drew the AXIS's glyph on every
 * header, so eight different headers all read as one star and the mark carried
 * no information at all.
 */
export const CAPABILITY_ICONS = {
  captioner: "mdi-text-box-outline",
  tagger: "mdi-tag-outline",
  detector: "mdi-shape-outline",
  face: "mdi-face-recognition",
  search: "mdi-magnify",
  scorer: "mdi-star-outline",
  checkpoint: "mdi-package-variant-closed",
  other: "mdi-dots-horizontal-circle-outline",
};

/**
 * The glyph for one capability.
 *
 * Returns "" for a capability this build has never seen, so the caller falls
 * back to the grouping axis's own glyph rather than drawing a wrong one. Unlike
 * {@link capabilityLabel}, which falls through to the STORED WORD, there is no
 * such fallback for a glyph: an unknown capability has a name to show and no
 * picture to show, and inventing one would be the confident wrong answer.
 *
 * @param {string} capability - a stored `model_capability.capability`.
 * @returns {string} an mdi class name, or `""`.
 */
export function capabilityIcon(capability) {
  return CAPABILITY_ICONS[String(capability || "")] || "";
}

const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"];

/**
 * Format a byte count for the size column.
 *
 * Deliberately its own function rather than the two `formatBytes` copies in
 * `ProjectFiles.vue` and `ImageImporter.vue`: both are local, and the first
 * tops out at MB, which understates a 4.3 GB adapter by three orders.
 *
 * @param {number|null|undefined} bytes
 * @returns {string} e.g. `179.4 MB`, or `""` when the size is unknown.
 */
export function formatModelSize(bytes) {
  // `Number(null)` is 0, so the null check has to come first or a row with no
  // recorded size claims to be empty.
  if (bytes === null || bytes === undefined || bytes === "") return "";
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "";
  let value = n;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${SIZE_UNITS[unit]}`;
}

/**
 * The group a row with no value on the current axis falls into.
 *
 * Here rather than in the store because {@link compareGroups} needs it and the
 * store imports this module, so the other direction would be a cycle.
 */
export const UNSET_GROUP_KEY = "\u0000unset";

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
export function compareGroups(a, b) {
  if (a.key === UNSET_GROUP_KEY) return b.key === UNSET_GROUP_KEY ? 0 : 1;
  if (b.key === UNSET_GROUP_KEY) return -1;
  return a.label.localeCompare(b.label, undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

/**
 * Add a group for every registered folder that has none.
 *
 * Groups are built from `model_file` rows, so a folder holding nothing produces
 * no group at all - and the managed store holds nothing on every fresh install,
 * despite being the ruled default destination for a drop or an import. A
 * destination you cannot see is not a destination.
 *
 * The empty group carries `emptyReason`, because "registered and empty" and
 * "never scanned" are different facts and only one of them is the owner's to
 * act on. `last_checked` is the discriminator for that pair rather than a zero
 * count: a folder that has never been walked has no count to be zero.
 *
 * `file_count` decides something else - whether the folder is empty at all.
 * Absence from `groups` cannot answer that, because `groups` is built from the
 * visible rows and a filter can empty a folder that is full.
 *
 * @param {Array<Object>} groups - the folder groups the rows produced.
 * @param {Array<Object>} folders - rows from `GET /model-folders`.
 * @returns {Array<Object>} groups plus the empties, in one sorted run.
 */
export function withEmptyFolders(groups, folders) {
  const held = new Set(groups.map((group) => group.key));
  const empties = [];
  for (const folder of folders || []) {
    const key = String(folder.path || folder.id || "");
    if (!key || held.has(key)) continue;
    // "Has no group" is NOT "is empty". `groups` is built from the VISIBLE
    // rows, so a folder full of adapters has no group at all while Show is
    // narrowed to checkpoints - and calling that folder empty would be a plain
    // lie about the disk. The registry knows better: `file_count` counts the
    // copies registered under the folder in any state, so a folder that holds
    // something is skipped and simply stays absent from a filtered view, which
    // is what every other filtered-out row does.
    const unscanned = !folder.last_checked;
    if (!unscanned && Number(folder.file_count) > 0) continue;
    empties.push({
      key,
      label: key,
      labelKind: "path",
      folderId: Number(folder.id),
      emptyReason: unscanned ? "unscanned" : "empty",
      rows: [],
    });
  }
  return empties.length ? [...groups, ...empties].sort(compareGroups) : groups;
}

/**
 * The base model a row should be GROUPED, FILTERED and FACETED by.
 *
 * `base_model_folded` when the server recognised the string, the raw one when
 * it did not. Folding is what makes `sdxl_base_v1-0`, `SDXL`, `sdxl base` and
 * `stable diffusion xl` one bucket instead of four; falling back to the raw
 * value is what keeps a base model nobody has heard of selectable rather than
 * swept into "not set".
 *
 * Note what this is NOT for: the row still DISPLAYS `base_model`, because the
 * raw spelling is what the file actually says. Group by the fold, show the
 * original.
 *
 * @param {Object} row - a row from `/adapters` or `/checkpoints`.
 * @returns {string} the grouping key, or `""` when the row records nothing.
 */
export function baseModelKey(row) {
  return row?.base_model_folded || row?.base_model || "";
}

/** What each folder layout is called, and the glyph that stands for it. */
export const FOLDER_LAYOUT_LABELS = {
  drive: { label: "Drive, then folder", icon: "mdi-harddisk" },
  alpha: { label: "Folder, A to Z", icon: "mdi-sort-alphabetical-variant" },
};

/**
 * What each folder tier is marked with, in ONE icon family.
 *
 * Shared with `ModelFoldersDialog`, which is where these glyphs started: the
 * shelf header and the dialog row are two views of the same registry, and two
 * copies of the map would be two vocabularies for one fact. Nothing here is
 * hand-drawn - the header used to have no tier mark at all, and the mock that
 * proposed one built a folder out of a div and a `::before` tab, which is a
 * second icon family by construction.
 *
 * `chip` is the WORD the tier is stated in, and it is what makes the tier
 * survive greyscale together with the glyph's shape. `user` has none: a folder
 * the owner registered is the unmarked case, and chipping every header would
 * make the two that matter invisible among them.
 */
export const FOLDER_TIERS = {
  managed: {
    icon: "mdi-folder-home-outline",
    chip: "Managed",
    note: "PixlStash keeps its own models here.",
  },
  // The only kind the owner can neither scan nor forget, so it is the only one
  // that gets the lock - the same rule the folders dialog's glyph column uses.
  foreign: {
    icon: "mdi-folder-lock-outline",
    chip: "Locked",
    note: "Owned by another tool. PixlStash reads it and never writes to it.",
  },
  source: {
    icon: "mdi-folder-cog-outline",
    chip: "ai-toolkit",
    note: "Training output. Models are imported from here, not catalogued in place.",
  },
  user: { icon: "mdi-folder-outline", chip: "", note: "" },
};

/** The glyph a folder whose drive is not plugged in wears, instead of its tier's. */
const OFFLINE_ICON = "mdi-lan-disconnect";

// The rail keeps its palette entry's HUE and pins saturation and lightness, the
// same renormalisation `markBackground` does and for the same reason: a colour
// picked for identity is not automatically a colour that reads as a 3px line.
// Mid lightness rather than the mark's 30%, because this line sits on the
// canvas in BOTH themes and nothing is ever written on it.
const RAIL_SATURATION = 60;
const RAIL_LIGHTNESS = 50;

/**
 * The rail colour a drive gets, by its position in the drive order.
 *
 * A grouping hint and never the identity: the chip (or the band above) names
 * the volume, and the palette repeats after 48 drives. `SET_COLORS` is
 * deliberately interleaved so neighbouring indices are far apart in hue, which
 * is what makes "these two folders are on one disk" readable at a glance.
 *
 * @param {number} index - position in the drive order.
 * @returns {string} an `hsl()` colour.
 */
export function driveRailColor(index) {
  const entry = SET_COLORS[index % SET_COLORS.length].value;
  return `hsl(${hueOf(entry)} ${RAIL_SATURATION}% ${RAIL_LIGHTNESS}%)`;
}

/**
 * True when `path` sits inside `parent`, one registered folder inside another.
 *
 * String comparison on a separator boundary, which is what keeps `/models` from
 * swallowing `/models-old`. Both separators, because a registry written on
 * Windows holds backslashes.
 */
function isInside(path, parent) {
  if (!path || !parent || path === parent) return false;
  const head = parent.replace(/[/\\]+$/, "");
  const rest = path.slice(head.length);
  return path.startsWith(head) && /^[/\\]/.test(rest);
}

/**
 * Tell each folder header what drive it is on, what tier it is and whether it
 * can be reached (#899).
 *
 * A header used to carry a path and a count and nothing else, so the answer to
 * "which disk is this on", "is this one PixlStash writes to" and "is this drive
 * even plugged in" lived only in the folders dialog. All three are properties
 * of the folder registry, which the shelf already holds; none of them needed a
 * request.
 *
 * **Every distinction here survives greyscale.** The drive is a hue on the rail
 * AND a chip that names the volume; the tier is a glyph shape AND a word; the
 * offline state is a DASHED rail and muted ink - never the error colour, for
 * the reason the offline row treatment is not the error colour either. Nothing
 * is carried by hue alone.
 *
 * Drives are numbered in a stable order (by device id) rather than by the order
 * the groups happen to arrive in, or plugging a disk in would repaint every
 * other folder's rail. A folder whose drive could not be measured gets NO rail
 * colour: we do not know what disk it is on, and inventing a colour for it
 * would claim a grouping nothing measured.
 *
 * @param {Array<Object>} groups - folder groups, each carrying `folderId`.
 * @param {Object} context
 * @param {Array<Object>} context.folders - rows from `GET /model-folders`.
 * @param {Map<number, Object>} [context.deviceByFolderId] - from the folder store.
 * @param {Set<number>} [context.offlineFolderIds] - folders wholly out of reach.
 * @returns {Array<Object>} the same groups, each with `tier`, `icon`, `chip`,
 *   `drive` (`{label, rail}` or null), `offline` and `nested`.
 */
export function withFolderSignals(
  groups,
  { folders = [], deviceByFolderId = null, offlineFolderIds = null } = {},
) {
  const byId = new Map(
    (folders || []).map((folder) => [Number(folder.id), folder]),
  );
  const paths = (folders || []).map((folder) => String(folder.path || ""));
  // Drive order, fixed before anything is drawn. Sorted by device id so it is
  // the same on every render and the same for every reader.
  const driveIndex = new Map(
    [
      ...new Set(
        [...(deviceByFolderId?.values?.() || [])]
          .map((device) => device?.device_id)
          .filter(Boolean)
          .map(String),
      ),
    ]
      .sort()
      .map((id, index) => [id, index]),
  );
  return (groups || []).map((group) => {
    const folderId = Number(group.folderId);
    const folder = Number.isInteger(folderId) ? byId.get(folderId) : null;
    const device = Number.isInteger(folderId)
      ? deviceByFolderId?.get?.(folderId) || null
      : null;
    const tier = FOLDER_TIERS[folder?.kind] ? folder.kind : "user";
    const offline = Boolean(offlineFolderIds?.has?.(folderId));
    const deviceId = device?.device_id ? String(device.device_id) : "";
    return {
      ...group,
      tier: folder ? tier : null,
      icon: offline ? OFFLINE_ICON : FOLDER_TIERS[tier].icon,
      chip: folder ? FOLDER_TIERS[tier].chip : "",
      note: folder ? FOLDER_TIERS[tier].note : "",
      drive: deviceId
        ? {
            label: device.label || device.mount_point || "",
            rail: driveRailColor(driveIndex.get(deviceId) ?? 0),
          }
        : null,
      offline,
      // One level, never two: nesting here says "this folder lives inside
      // another registered one", which is a yes or a no. A registry three deep
      // would otherwise walk the headers off the left edge of the panel.
      nested: paths.some((other) =>
        isInside(String(folder?.path || ""), other),
      ),
    };
  });
}

/**
 * Arrange folder groups into drive bands.
 *
 * Two levels, which is what the plan allows and no more: the band is the drive
 * and the header under it is the folder. Groups are RE-ORDERED so a band's
 * folders are contiguous - a band drawn over a non-contiguous run would claim
 * a grouping the list does not have - and each group is tagged with the band it
 * opens, so the caller draws a band header exactly when `bandStart` is set
 * rather than nesting the markup.
 *
 * A folder whose drive could not be measured still gets a band of its own,
 * labelled with its own path: an unplugged drive has to keep somewhere to sit,
 * and merging two unmeasurable folders would assert a sameness nothing
 * measured. Those bands sort last, because a drive we cannot read is not the
 * one the reader is scanning for space on.
 *
 * @param {Array<Object>} groups - folder groups, each carrying `folderId`.
 * @param {Map<number, Object>} deviceByFolderId - from `useModelFoldersStore`.
 * @returns {Array<Object>} the same groups, reordered, each with `band` and
 *   `bandStart` (true on the first group of each band).
 */
/**
 * The band a folder belongs to.
 *
 * The drive when one was measured, the folder itself when none was - the same
 * rule {@link bandGroups} keys on, exported so a caller asking "is this copy
 * already on that drive?" cannot answer it with a second, drifting copy of the
 * rule.
 *
 * @param {number} folderId - `model_folder.id`.
 * @param {Map<number, Object>} deviceByFolderId - from `useModelFoldersStore`.
 * @returns {string} the band key.
 */
export function bandKeyFor(folderId, deviceByFolderId) {
  const device = deviceByFolderId?.get?.(Number(folderId)) || null;
  return device?.device_id ? `d:${device.device_id}` : `f:${Number(folderId)}`;
}

export function bandGroups(groups, deviceByFolderId) {
  const byBand = new Map();
  // Not every folder group names a folder. A model whose folders have all been
  // forgotten falls into "No registered copy", which has no `folderId` and is
  // not on a drive at all - banding it would put a disk glyph and a capacity
  // line over the one group that exists precisely because there is no disk.
  // It stays unbanded and sorts last, the same place `compareGroups` already
  // puts the absence of a value.
  const unbanded = groups.filter((group) => !Number.isInteger(group.folderId));
  for (const group of groups) {
    if (!Number.isInteger(group.folderId)) continue;
    const folderId = Number(group.folderId);
    const device = deviceByFolderId?.get?.(folderId) || null;
    // Unmeasured folders band alone, keyed by folder rather than by device.
    const key = bandKeyFor(folderId, deviceByFolderId);
    let band = byBand.get(key);
    if (!band) {
      band = {
        key,
        // The volume's name if it has one, else where it is mounted. A Linux
        // mount point runs to `/media/<user>/A1B2C3D4E5F60789` and crowds the
        // header out; the precise string stays available as the tooltip.
        label: device?.label || device?.mount_point || group.label,
        mountPoint: device?.mount_point || group.label,
        // `local`, `network`, `removable`, `ramdisk` - or null, which is the
        // answer on macOS and for any filesystem the backend will not classify.
        // Null is normal and draws the plain disk glyph; see `device_kind`.
        kind: device?.kind || null,
        measured: Boolean(device?.device_id),
        totalBytes: device?.total_bytes ?? null,
        freeBytes: device?.free_bytes ?? null,
        shelfBytes: device?.shelf_bytes ?? null,
        groups: [],
      };
      byBand.set(key, band);
    }
    band.groups.push(group);
  }
  const bands = [...byBand.values()].sort((a, b) => {
    if (a.measured !== b.measured) return a.measured ? -1 : 1;
    return a.label.localeCompare(b.label, undefined, {
      numeric: true,
      sensitivity: "base",
    });
  });
  const arranged = [];
  for (const band of bands) {
    band.groups.forEach((group, index) => {
      arranged.push({ ...group, band, bandStart: index === 0 });
    });
  }
  // `band: null` rather than a band of their own: the caller draws a header
  // only where `bandStart` is set, so these render as bare folder groups.
  for (const group of unbanded) {
    arranged.push({ ...group, band: null, bandStart: false });
  }
  return arranged;
}

/**
 * When a drive is close enough to full to say so.
 *
 * Bytes, not a proportion, because the question the band answers is "does the
 * next checkpoint fit" and that is answered in bytes. A full SDXL or Flux
 * checkpoint is 6–24 GB, so 50 GiB is two or three more files: close enough to
 * warn, far enough not to nag.
 *
 * A percentage is wrong in both directions on exactly the hardware this
 * feature targets. Ten per cent of a 4 TB model drive is 400 GB, which is not a
 * problem and would cry wolf on the drive people actually keep models on; ten
 * per cent of a 256 GB SSD is 25 GB, which IS a problem - but so is 60 GB free
 * on that same disk, and the fraction calls that fine.
 */
export const LOW_FREE_BYTES = 50 * 1024 ** 3;

/**
 * How a drive's space divides into the three things a reader asks about.
 *
 * The meter answers two questions at once - "how full is this disk" and "how
 * much of that is us" - and it can only answer the second if the shelf's share
 * is drawn as its OWN segment rather than as a fill overlaid on the used one.
 * Overlaying was the original shape and it made the two questions the same
 * pixel: a reader could see one boundary and had no way to know which of the
 * two it marked.
 *
 * The three add to exactly 100 by construction (`shelf + (used - shelf) + free
 * === total`), which is what lets the caller lay them out in a row without a
 * rounding sliver opening at the right-hand end. That identity is the whole
 * reason the segments need no per-segment clamp, so BOTH inputs are clamped
 * into range before it is relied on:
 *
 *   * `free` to `total`, because the two are separate reads of the device and
 *     a filesystem can genuinely report more free than it holds - thin
 *     provisioning and transparent compression (ZFS, btrfs) do it by design,
 *     and a network mount's `statvfs` can simply be wrong. Unclamped that
 *     yields `freePct > 100` and a segment running off the end of the track.
 *   * `shelf` to `used`, because `shelf_bytes` is counted from the hub and the
 *     capacity from the disk, so a scan mid-flight can briefly make the first
 *     exceed the second.
 *
 * Returns `null` when the drive could not be measured, which the caller must
 * draw as "unknown" rather than as an empty bar: an empty bar reads as a drive
 * with nothing on it.
 *
 * @param {Object} band - a band from {@link bandGroups}.
 * @returns {{shelfPct: number, otherPct: number, freePct: number,
 *   usedPct: number, lowFree: boolean}|null}
 */
export function bandUsage(band) {
  const total = Number(band?.totalBytes);
  const reportedFree = Number(band?.freeBytes);
  if (!Number.isFinite(total) || total <= 0) return null;
  if (!Number.isFinite(reportedFree) || reportedFree < 0) return null;
  const free = Math.min(reportedFree, total);
  const used = total - free;
  const shelf = Math.max(0, Math.min(used, Number(band?.shelfBytes) || 0));
  return {
    shelfPct: (shelf / total) * 100,
    otherPct: ((used - shelf) / total) * 100,
    freePct: (free / total) * 100,
    usedPct: (used / total) * 100,
    lowFree: free < LOW_FREE_BYTES,
  };
}

/**
 * What a drive would hold after a drop, and whether the drop fits.
 *
 * The meter is the drop target (#894), so the consequence has to be drawable
 * *before* the pointer is released - which means a fourth segment carved out of
 * the free one rather than a fifth number in the label. `freePct` is therefore
 * already reduced by the projection and the four still sum to exactly 100, so
 * the caller lays them out in the same flex row with no rounding sliver at the
 * right-hand end and needs no per-segment clamp. That is the whole reason this
 * returns a REPLACEMENT for `bandUsage`'s object rather than something to draw
 * alongside it.
 *
 * `added` is clamped into the free space for the SEGMENT, because a bar cannot
 * draw past its own track; `fits` is decided on the unclamped figure, so the
 * over-full case is a full-width hatch in the error treatment and not a bar
 * that quietly stops looking wrong at 100%. `freeAfter` goes negative in that
 * case, which is how far short the drive is.
 *
 * **Every** derived field is projected, not just the segments. `usedPct` and
 * `lowFree` are recomputed from the free space that would be LEFT, because a
 * replacement carrying two fields measured before the drop is a trap: they sit
 * on the same object as segments drawn from after it, and the first caller to
 * read `meter(band).lowFree` next to a projected bar gets an answer about a
 * different drive state than the one on screen. Nothing reads them off this
 * object today - the band's low treatment deliberately goes through `bandUsage`
 * - and that is exactly why the inconsistency would be found late.
 *
 * A drive that could not be measured returns `null`, exactly as `bandUsage`
 * does: a projection onto an unknown capacity is a guess, and the caller must
 * read that as "cannot say" rather than as "does not fit".
 *
 * @param {Object} band - a band from {@link bandGroups}.
 * @param {number} addedBytes - bytes the drop would ADD to this drive. Copies
 *   already on it are renames and add nothing, so the caller nets them out.
 * @returns {{shelfPct: number, otherPct: number, addedPct: number,
 *   freePct: number, usedPct: number, lowFree: boolean, addedBytes: number,
 *   freeAfter: number, fits: boolean}|null}
 */
export function bandProjection(band, addedBytes) {
  const use = bandUsage(band);
  if (!use) return null;
  const total = Number(band.totalBytes);
  const free = Math.min(Number(band.freeBytes), total);
  const added = Math.max(0, Number(addedBytes) || 0);
  const drawn = Math.min(added, free);
  const freeAfter = free - added;
  return {
    ...use,
    addedPct: (drawn / total) * 100,
    freePct: ((free - drawn) / total) * 100,
    // The drawn figure, so this stays `shelfPct + otherPct + addedPct` and the
    // bar cannot claim to be more than full.
    usedPct: ((total - free + drawn) / total) * 100,
    // The unclamped one, so a drop that overruns the drive reports low rather
    // than reporting the zero free space it was clamped to.
    lowFree: freeAfter < LOW_FREE_BYTES,
    addedBytes: added,
    freeAfter,
    fits: added <= free,
  };
}

/**
 * How many copies of this model are actually on the disk right now.
 *
 * Only `present` copies count. A `missing` or `not_downloaded` row is a
 * registration, not bytes, so folding it in would report a model as taking
 * twice the disk it takes and offer the reader nothing to delete.
 *
 * Two or more is the definition of a duplicate everywhere on the shelf: the hub
 * is content-addressed, one `model` row per SHA-256, so a second `present` copy
 * IS the same bytes written twice. That is why this counts copies rather than
 * comparing anything - the comparison already happened, upstream, by hash.
 *
 * @param {Array<Object>} locations - the row's `locations` array.
 * @returns {number} copies on disk; 0 for a row whose every copy is a promise.
 */
export function presentCopies(locations) {
  return (Array.isArray(locations) ? locations : []).filter(
    (loc) => loc?.state === "present",
  ).length;
}

/**
 * Reduce a row's copies to the one state worth reporting.
 *
 * `missing` is a fact (the folder was readable and the file was not in it);
 * `unreachable` is the absence of one (we could not look). They must not read
 * the same, and one present copy makes both moot - the file is usable.
 *
 * `not_downloaded` is neither: it is a file PixlStash declares and fetches on
 * demand, which nothing has needed yet. Only an ALL-`not_downloaded` row reports
 * it, so a model with one genuinely missing copy still states the fault, and any
 * state this build does not know still falls through to `missing` rather than
 * being quietly reported as fine.
 *
 * @param {Array<Object>} locations - the row's `locations` array.
 * @returns {"present"|"missing"|"not_downloaded"|"unreachable"|"forgotten"}
 */
export function locationState(locations) {
  const list = Array.isArray(locations) ? locations : [];
  if (!list.length) return "forgotten";
  if (list.some((loc) => loc?.state === "present")) return "present";
  if (list.some((loc) => loc?.state === "unreachable")) return "unreachable";
  if (list.every((loc) => loc?.state === "not_downloaded"))
    return "not_downloaded";
  return "missing";
}

/**
 * What a copy's own state adds to its path, per {@link copyPathsTitle}.
 *
 * A path with nothing after it is a claim that the file is THERE. Three of the
 * four states are the claim that it is not, and the shelf spends a rail, a
 * glyph and a word telling them apart on the row (#898) - a tooltip that
 * rendered all four as bare identical lines would be the one place they read
 * the same. `present` adds nothing, because that is what a path already says.
 *
 * Read through `copyStateNote` and never indexed raw, for the reason
 * `ADAPTER_KIND_LABELS` is not exported: `state` comes off the wire, and
 * `constructor` indexes a FUNCTION off `Object.prototype` that is truthy and
 * would be pasted into the tooltip.
 */
const COPY_STATE_NOTE = {
  missing: "not where it was",
  unreachable: "out of reach",
  not_downloaded: "not downloaded yet",
};

/**
 * What one copy's state adds to its path, or nothing.
 *
 * Unlike {@link labelFrom} an unknown state falls through to NOTHING rather
 * than to itself: the fallthrough there shows a value a human chose, and this
 * one is machine vocabulary that would read as a fault ("/x/a.st · sundered").
 * A state this build has never seen is not a claim it may make.
 *
 * @param {string} state - a stored `model_file.state`.
 * @returns {string} the note, or `""`.
 */
function copyStateNote(state) {
  const key = String(state || "");
  return Object.hasOwn(COPY_STATE_NOTE, key) ? COPY_STATE_NOTE[key] : "";
}

/**
 * Where a row's copies sit, one path per line, each saying what is there.
 *
 * The folder is only on screen under `groupBy: 'folder'`, where the header
 * names it. Group by base model or by feature - or not at all, which is the
 * default - and the shelf stops saying where anything is, which is the first
 * question of a reader with the same adapter on two disks. So the file line
 * carries the answer as its tooltip on every axis, including `folder`, where
 * the header names the folder but nothing names the SUBDIRECTORY under it.
 *
 * Every copy, not the first: a model registered in two folders is one row, and
 * naming one of its homes would read as naming its only one. Under `folder` the
 * store hands this ONE copy, because that draw stands for one copy.
 *
 * The separator is taken from the registered folder rather than assumed, and a
 * Windows folder takes the relpath's separators with it: the two halves come
 * from different places (`model_folder.path` as registered, `relpath` as the
 * scanner wrote it), so joining them without a rule is how a path comes back
 * half-slashed. A POSIX folder leaves the relpath alone, where a backslash is a
 * legal character in a filename rather than a separator.
 *
 * A copy missing either half is skipped rather than half-named: both are
 * NOT NULL on the wire, so this is a broken row, and `a.st` on its own answers
 * "where is this file" with the one thing that is not a location.
 *
 * @param {Array<Object>} locations - the row's `locations` array.
 * @returns {string} the paths, newline-separated, or `""` when there are none -
 *   which the caller must bind as no tooltip at all rather than an empty one.
 */
export function copyPathsTitle(locations) {
  return (Array.isArray(locations) ? locations : [])
    .map((loc) => {
      const folder = String(loc?.folder_path || "");
      const relpath = String(loc?.relpath || "");
      if (!folder || !relpath) return "";
      const windows = folder.includes("\\") && !folder.includes("/");
      const sep = windows ? "\\" : "/";
      const tail = windows ? relpath.replace(/\//g, "\\") : relpath;
      const path = `${folder.replace(/[/\\]+$/, "")}${sep}${tail.replace(/^[/\\]+/, "")}`;
      const note = copyStateNote(loc?.state);
      return note ? `${path} · ${note}` : path;
    })
    .filter(Boolean)
    .join("\n");
}

/**
 * The registered folders whose every copy is out of reach, and how many rows
 * each one takes with it.
 *
 * This is what lets an unplugged drive state its scope ONCE. `unreachable` is
 * the common case for anyone keeping adapters on an external disk - the whole
 * folder flips together (`ModelFolderScanner._mark_unreachable`) - and 300 rows
 * each carrying their own mark is 300 statements of one fact.
 *
 * A folder qualifies only when NOTHING under it was readable. One `present`
 * copy means the drive is plugged in and this is a per-row story again, and one
 * `missing` copy means the folder WAS readable, which is a different fact and
 * not one to fold into "offline".
 *
 * Counted per ROW rather than per copy, because a row is what the reader sees
 * and a model registered twice in one folder is still one line on the shelf.
 *
 * @param {Array<Object>} rows - shelf rows, each with `locations`.
 * @returns {Array<{folderId: number, path: string, count: number}>} sorted by
 *   path, so the banner reads the same on every render - under the shelf's one
 *   collation, numeric and case-insensitive, so `/mnt/2` precedes `/mnt/10`
 *   here as it does in every other list on the screen.
 */
export function offlineFolders(rows) {
  const byFolder = new Map();
  for (const row of Array.isArray(rows) ? rows : []) {
    const seen = new Set();
    for (const loc of row?.locations || []) {
      const id = Number(loc?.folder_id);
      if (!Number.isInteger(id)) continue;
      let folder = byFolder.get(id);
      if (!folder) {
        folder = {
          folderId: id,
          path: String(loc?.folder_path || ""),
          count: 0,
          offline: true,
        };
        byFolder.set(id, folder);
      }
      if (loc?.state !== "unreachable") folder.offline = false;
      // One row, one tally, however many copies of it this folder holds.
      else if (!seen.has(id)) {
        seen.add(id);
        folder.count += 1;
      }
    }
  }
  return [...byFolder.values()]
    .filter((folder) => folder.offline && folder.count)
    .map(({ folderId, path, count }) => ({ folderId, path, count }))
    .sort((a, b) =>
      a.path.localeCompare(b.path, undefined, {
        numeric: true,
        sensitivity: "base",
      }),
    );
}

/**
 * Say what an ai-toolkit import produced, naming the failures rather than
 * swallowing them.
 *
 * Per FILE, because the server decides per file: a run whose five steps landed
 * and whose sixth did not is a normal outcome of an interrupted copy, and a
 * receipt reporting only the five would read as a clean import.
 *
 * The source deletion is named only when something actually landed. The server
 * unlinks last and only after each row is committed, so "nothing imported" and
 * "the run is gone" cannot both be true - saying it anyway would tell the
 * reader their run had been deleted for nothing.
 *
 * @param {Object} report - the body of `POST /model-imports`.
 * @returns {string}
 */
export function importReceipt(report) {
  const files = report?.files || [];
  const landed = files.filter((f) => f.status === "imported").length;
  const failed = files.filter((f) => f.status === "failed").length;
  const count = (n) =>
    `${n.toLocaleString()} ${n === 1 ? "checkpoint" : "checkpoints"}`;
  const notes = [];
  if (failed) {
    // The verb agrees with the count. `moveReceipt` had this same bug and it
    // was fixed there; writing it again a few hundred lines later is why the
    // singular case is now asserted in both receipts' tests.
    notes.push(
      `${count(failed)} could not be copied and ${failed === 1 ? "was" : "were"} left in the run.`,
    );
  }
  // A checkpoint whose previews did not copy is still `imported` - losing a
  // preview must not cost the weights, so the server does not fail the file for
  // it. Which means the status counts above cannot see it, and a receipt built
  // from them alone would call a run whose samples were lost a clean import.
  // Named separately rather than folded into `failed`, because the checkpoint
  // is genuinely on the shelf and telling someone otherwise is worse.
  const withoutSamples = files.filter(
    (f) => f.status === "imported" && f.sample_count === 0 && f.detail,
  ).length;
  if (withoutSamples) {
    notes.push(
      `${count(withoutSamples)} landed without ${withoutSamples === 1 ? "its" : "their"} training previews.`,
    );
  }
  if (report?.deleted_source && landed) {
    notes.push("The run's own files have been removed.");
  }
  const head = landed
    ? `Imported ${count(landed)} from ${report.run_name}.`
    : `Nothing was imported from ${report?.run_name || "that run"}.`;
  return [head, ...notes].join(" ");
}

/**
 * The copies a move may actually pick up, and what they weigh.
 *
 * Three exclusions, each for a different reason:
 *
 * - **Not `present`.** There is no file to move. `missing` says the folder was
 *   readable and the file was not in it; `unreachable` says we could not look.
 *   Sending either would be asking the server to copy bytes nobody has seen.
 * - **PixlStash's own folder.** Those files are ours, declared rather than
 *   scanned, and every engine loader looks for them at a fixed path - moving
 *   one out breaks the tagger and re-downloads it on the next run.
 * - **An `external` folder.** The HuggingFace cache and insightface's store are
 *   shared with other software. Taking a file out of one is not ours to do.
 *
 * Per COPY and not per model: `model_file`'s primary key is
 * `(folder_id, relpath)`, so a model registered in three folders offers three
 * copies and the caller moves the ones it named. That is also why the size is
 * summed off the row rather than off the copy - the hub records one
 * `file_size` per model, and every copy of it is that size.
 *
 * @param {Array<Object>} rows - shelf rows, each with `locations`.
 * @param {Map<number, Object>|null} foldersById - `model_folder.id` to the
 *   folder row, for the two folder-level exclusions. Omitted, only the
 *   `present` rule applies.
 * `bytesByFolderId` is the same weight split by where the bytes are NOW, which
 * is what the capacity projection needs: a copy moved between two folders on
 * one drive is a rename and adds nothing to it, so the drive a drop is aimed at
 * has to net out the copies already sitting on it (#894). It is deliberately
 * kept off `items`, which is posted to `/model-moves` verbatim.
 *
 * @param {Array<Object>} rows - shelf rows, each with `locations`.
 * @param {Map<number, Object>|null} foldersById - `model_folder.id` to the
 *   folder row, for the two folder-level exclusions. Omitted, only the
 *   `present` rule applies.
 * @returns {{items: Array<{folder_id: number, relpath: string}>,
 *   totalBytes: number, bytesByFolderId: Map<number, number>}}
 */
export function movableCopies(rows, foldersById = null) {
  const items = [];
  const bytesByFolderId = new Map();
  let totalBytes = 0;
  for (const row of Array.isArray(rows) ? rows : []) {
    for (const loc of row?.locations || []) {
      if (loc?.state !== "present") continue;
      const folder = foldersById?.get(Number(loc.folder_id));
      if (folder?.owner === "pixlstash") continue;
      if (folder?.movable === "external") continue;
      const bytes = Number(row.file_size) || 0;
      const folderId = Number(loc.folder_id);
      items.push({ folder_id: loc.folder_id, relpath: loc.relpath });
      bytesByFolderId.set(
        folderId,
        (bytesByFolderId.get(folderId) || 0) + bytes,
      );
      totalBytes += bytes;
    }
  }
  return { items, totalBytes, bytesByFolderId };
}

/**
 * What the machine in front of the reader calls its trash.
 *
 * A label, never a decision: what actually happens is `permanent`, which the
 * server echoes back. The browser's platform is a good-enough proxy for the
 * server's here because the delete route is `LOCAL_OWNER_ONLY` - it is refused
 * outright unless the caller is on the same machine or the same network - so
 * the two differ only in a mixed-OS LAN, where the cost is one wrong noun.
 *
 * @param {Object} [nav=navigator] - injectable for tests.
 * @returns {string} `Recycle Bin` on Windows, `Trash` everywhere else.
 */
export function trashName(
  nav = typeof navigator !== "undefined" ? navigator : null,
) {
  const platform = nav?.userAgentData?.platform || nav?.userAgent || "";
  return /win/i.test(platform) ? "Recycle Bin" : "Trash";
}

/**
 * The selected models a delete could actually act on.
 *
 * Every gate the route enforces, checked here so the verb is never offered
 * where it could only come back refused - and per MODEL rather than per copy,
 * because the server deletes a model whole or not at all: unlinking the
 * reachable half of a model that also lives in the HuggingFace cache would
 * leave the row the owner wanted gone still on the shelf.
 *
 * A stack is judged across its members for the same reason `selectedModelIds`
 * expands them: a run is deleted whole, or the cover would go and leave five
 * steps behind.
 *
 * With no folder map (the registry has not loaded, or could not be read)
 * nothing is deletable, which is the safe direction to fail in and the same one
 * Move already fails in.
 *
 * Which folders those are is the server's `deletable`, not a kind list mirrored
 * over here: `foreign` covers PixlStash's own download folder as well as the
 * InsightFace packs and the HuggingFace cache, and only a path tells the first
 * apart from the other two. Its unclaimed leftovers are the owner's and the
 * engines beside them are not, which is the check above.
 *
 * @param {Array<Object>} rows - shelf rows, each with `locations`.
 * @param {Map<number, Object>|null} foldersById - `model_folder.id` to the
 *   folder row.
 * @returns {Array<Object>} the subset of `rows` a delete would act on.
 */
export function deletableModels(rows, foldersById = null) {
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    const parts = row?.members?.length ? row.members : [row];
    return parts.every((part) => {
      // An engine is declared again on every start, so deleting one removes a
      // file that comes straight back - after the feature broke.
      if (part?.file_kind === "engine") return false;
      return (part?.locations || []).every((loc) => {
        // "We could not look", not "it is gone": an unplugged drive must never
        // be read as a deletion.
        if (loc?.state === "unreachable") return false;
        return Boolean(foldersById?.get(Number(loc.folder_id))?.deletable);
      });
    });
  });
}

/**
 * Why this selection cannot be deleted, in the reader's own terms.
 *
 * The three gates {@link deletableModels} checks, said back. The sentence it
 * replaced claimed PixlStash "only removes files from your own model folders,
 * and never from a drive that is not plugged in", which was two thirds of one
 * gate and untrue on its face: the shelf deletes from the managed store and
 * from PixlStash's own download folder, neither of which is a folder the owner
 * registered. It also named no folder, so the one thing the reader wanted - WHY
 * this file and what to do instead - was the thing it left out.
 *
 * The folder is named by its PATH rather than by a kind, because the path is
 * the answer: `~/.cache/huggingface/hub` explains itself, and no vocabulary of
 * ours explains it better. A folder another tool owns (`movable: fixed`, which
 * is what the HuggingFace cache carries and what the column means) gets the one
 * clause that is actually actionable, since the tool that put the file there is
 * the one that can take it away.
 *
 * Every reason present is reported, not the first: a selection of forty can be
 * blocked three different ways, and a reader who fixes the drive only to hit
 * the folder rule has been sent round twice.
 *
 * @param {Array<Object>} rows - the selected rows, none of them deletable.
 * @param {Map<number, Object>|null} foldersById - `model_folder.id` to folder.
 * @returns {string} one to three sentences, or "" for an empty selection.
 */
export function undeletableNotice(rows, foldersById = null) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return "";
  if (!foldersById) {
    // The registry has not loaded or could not be read, which is the one case
    // where the honest answer is that we do not know yet.
    return "The folder list has not loaded yet, so nothing can be deleted.";
  }
  let engine = false;
  let unreachable = false;
  const folders = new Set();
  let anotherToolOwns = false;
  for (const row of list) {
    for (const part of row?.members?.length ? row.members : [row]) {
      if (part?.file_kind === "engine") engine = true;
      for (const loc of part?.locations || []) {
        if (loc?.state === "unreachable") {
          unreachable = true;
          continue;
        }
        const folder = foldersById.get(Number(loc?.folder_id));
        if (folder && !folder.deletable) {
          folders.add(String(folder.path || ""));
          if (folder.movable === "fixed") anotherToolOwns = true;
        }
      }
    }
  }
  const notes = [];
  if (folders.size) {
    // One path is named; several are counted. A notice listing six paths is one
    // nobody finishes reading.
    const where =
      folders.size === 1
        ? [...folders][0]
        : `${folders.size} folders PixlStash does not delete from`;
    notes.push(
      `PixlStash does not delete from ${where}. It removes files from folders you registered, its own managed store and the folder it downloads engines into.`,
    );
    if (anotherToolOwns) {
      notes.push("Another tool owns that folder, so remove them with that.");
    }
  }
  if (engine) {
    notes.push(
      "PixlStash downloaded some of these for itself and would fetch them again.",
    );
  }
  if (unreachable) {
    notes.push("Some sit on a drive that is not plugged in.");
  }
  return notes.join(" ");
}

/**
 * Fold each stack's members into the one row that stands for them.
 *
 * A stack is many `model` rows and the list query returns all of them, so
 * without this a six-step run reads as six unrelated adapters - which is the
 * state the shelf shipped in until F5.
 *
 * The **cover** is `stack_position` 0, which the backend already ordered: the
 * newest version, and within it the bare final file if the run wrote one, else
 * its highest step. A stack whose
 * cover is filtered out of view collapses onto its lowest surviving position
 * rather than vanishing, because a run half-hidden by a base-model filter is
 * still a run and dropping it would make the filter lie about what is on disk.
 *
 * `memberIds` is the whole point of the fold and not decoration: stacks are
 * **atomic** here exactly as they are for pictures (`services/stack_membership`
 * - "applied to EVERY member of its stack, so state can never go partial"), so
 * selecting a collapsed row has to select the run, or Move would take the cover
 * and leave five steps behind.
 *
 * @param {Array<Object>} rows - shown rows, already narrowed by the filters.
 * @returns {Array<Object>} one row per unstacked model and per stack, each
 *   stacked row carrying `memberIds`, `memberCount`, `members` and
 *   `spansVersions`. All four describe what is SHOWN - a filter that hides half
 *   a stack changes them, which is deliberate and is why they are recomputed
 *   here rather than read off the payload.
 */
export function collapseStacks(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const byStack = new Map();
  for (const row of list) {
    if (row?.stack_id == null) continue;
    const members = byStack.get(row.stack_id);
    if (members) members.push(row);
    else byStack.set(row.stack_id, [row]);
  }

  const emitted = new Set();
  const out = [];
  for (const row of list) {
    if (row?.stack_id == null) {
      out.push(row);
      continue;
    }
    if (emitted.has(row.stack_id)) continue;
    emitted.add(row.stack_id);
    // A member with no position sorts LAST, matching the server's
    // `ORDER BY stack_position IS NULL, stack_position` - `?? 0` put it level
    // with the cover and, on a stable sort, ahead of it, so an unpositioned
    // row could be drawn as the face of a run the server does not agree it
    // covers.
    const members = [...byStack.get(row.stack_id)].sort(
      (a, b) =>
        (a.stack_position ?? Infinity) - (b.stack_position ?? Infinity) ||
        a.id - b.id,
    );
    const cover = members[0];
    out.push({
      ...cover,
      members,
      memberIds: members.map((m) => m.id),
      // Counted from what is SHOWN, not from the payload's `member_count`: a
      // filter can hide part of a run, and a badge reading 6 over a strip that
      // opens to 4 would be describing rows the reader cannot reach.
      memberCount: members.length,
      // Computed ONCE per stack, here, rather than per member row at render:
      // the member label needs to know whether the stack spans versions, and
      // asking that question inside the label made it O(n²) in the members -
      // 200 members is 40,000 filename parses, redone on every re-render.
      //
      // Compared on the PARSED version, matching `propose_stacks`, so `v2` and
      // `V2.0` are one version here exactly as they are on the server.
      spansVersions:
        new Set(members.map((m) => versionSortKey(modelVersion(m.filename))))
          .size > 1,
    });
  }
  return out;
}

/**
 * Say how many stacks were made, and how many were not.
 *
 * One call per group, so a partial outcome is real: a group whose rows were
 * stacked between the dry run and the confirmation comes back 409 and is
 * counted rather than throwing, or one stale group would discard the others.
 *
 * "Stacks", not "runs": a stack can span training runs now - several versions
 * of one character LoRA - so calling every one of them a run would be false.
 *
 * @param {number} grouped - stacks made.
 * @param {number} failed - groups the server refused.
 * @returns {string}
 */
export function stackReceipt(grouped, failed) {
  const stacks = (n) => `${n.toLocaleString()} ${n === 1 ? "stack" : "stacks"}`;
  if (!grouped) {
    return failed
      ? `Nothing was grouped. ${stacks(failed)} could not be, and the files are unchanged.`
      : "Nothing to group.";
  }
  const note = failed
    ? ` ${stacks(failed)} could not be grouped; something changed them first.`
    : "";
  return `Grouped ${stacks(grouped)}.${note}`;
}

/**
 * Say how many stacks were broken up, and how many were not.
 *
 * Says **where the files went**, which the grouping receipt does not have to:
 * "ungrouped" on its own is the one word in this view that a reader could
 * reasonably fear meant "deleted", and the whole point of the verb is that
 * nothing on disk moved.
 *
 * @param {number} released - stacks dissolved.
 * @param {number} failed - stacks the server refused.
 * @returns {string}
 */
export function unstackReceipt(released, failed) {
  const stacks = (n) => `${n.toLocaleString()} ${n === 1 ? "stack" : "stacks"}`;
  if (!released) {
    return failed
      ? `Nothing was ungrouped. ${stacks(failed)} could not be, and the files are unchanged.`
      : "Nothing to ungroup.";
  }
  const note = failed ? ` ${stacks(failed)} could not be ungrouped.` : "";
  return `Ungrouped ${stacks(released)}; the files are still on the shelf.${note}`;
}

/**
 * Say how many files were taken out of their runs, and what that cost.
 *
 * Names the **dissolved** runs separately, because that is the one outcome the
 * reader did not literally ask for: taking one file out of a pair leaves a run
 * of one, which is not a run, so its last file comes loose too. A receipt that
 * said only "took 1 file out" would leave them looking for a stack that is no
 * longer there.
 *
 * @param {number} released - members taken out.
 * @param {number} dissolved - runs that stopped existing as a result.
 * @param {number} failed - members the server refused.
 * @returns {string}
 */
export function releaseReceipt(released, dissolved, failed) {
  const files = (n) => `${n.toLocaleString()} ${n === 1 ? "file" : "files"}`;
  const runs = (n) => `${n.toLocaleString()} ${n === 1 ? "run" : "runs"}`;
  if (!released) {
    return failed
      ? `Nothing was taken out. ${files(failed)} could not be, and the runs are unchanged.`
      : "Nothing to take out.";
  }
  const gone = dissolved
    ? ` ${runs(dissolved)} had a single file left and stopped being a run.`
    : "";
  const note = failed ? ` ${files(failed)} could not be taken out.` : "";
  return `Took ${files(released)} out; still on the shelf.${gone}${note}`;
}

/**
 * The training step a filename records, or null for a bare final file.
 *
 * Mirrors `_step_of` in `pixlstash/services/stack_detector.py`, and reads the
 * same trailing token {@link deriveModelName} strips - so a file is never
 * labelled by a suffix the name derivation cannot also explain. `step00500` and
 * `000000500` both give 500; `portrait mix v2` gives null, because `v2` is not
 * training bookkeeping and the name keeps it.
 *
 * @param {string} filename
 * @returns {number|null}
 */
export function trainingStep(filename) {
  const tokens = cleanAssetName(filename).split(/\s+/).filter(Boolean);
  const last = tokens[tokens.length - 1];
  if (!last || !TRAINING_SUFFIX_RE.test(last)) return null;
  const digits = last.replace(/\D/g, "");
  return digits ? Number(digits) : null;
}

/** A trailing version token. Mirrors `_VERSION_SUFFIX_RE` in `model_utils.py`.
 *
 * Only an explicit `v<digits>` counts, optionally with one decimal. A bare
 * trailing `2` is not a version: `JimmyVehicle` beside `JimmyVehicle2` is the
 * ambiguous prefix case, and reading it as a version would merge two subjects.
 */
const VERSION_SUFFIX_RE = /^v(\d+)(?:\.(\d+))?$/i;

/**
 * The version a filename records, or null when it carries none.
 *
 * Mirrors `split_model_version`: runs on top of {@link deriveModelName}, so
 * training bookkeeping is already gone and `Foxglove_v2_000000500` answers the
 * same as `Foxglove_v2`.
 *
 * **Returned exactly as the file wrote it**, `V2.1` and all, because this token
 * is shown to a reader. Never compare two of these as strings - put them
 * through {@link versionSortKey}, which is what agrees with the server about
 * `v2` and `V2.0` being one version.
 *
 * @param {string} filename
 * @returns {string|null}
 */
export function modelVersion(filename) {
  const tokens = deriveModelName(filename).split(/\s+/).filter(Boolean);
  const last = tokens[tokens.length - 1];
  // Verbatim, matching `split_model_version`: this token is shown, and the
  // comparison that matters goes through {@link versionSortKey} instead.
  return last && VERSION_SUFFIX_RE.test(last) ? last : null;
}

/**
 * Order two version tokens, newest highest. Mirrors `version_sort_key`.
 *
 * **This is what versions must be compared on, never the raw token.** The
 * backend tests distinctness on the parsed pair, so `v2` and `V2.0` are one
 * version there; comparing strings here instead made the shelf call them two
 * and label a run's members with a version it does not have. An unversioned
 * file reads as `v1`, exactly as the server reads it.
 *
 * @param {string|null} version - a token from {@link modelVersion}.
 * @returns {string} a stable `major.minor` key for identity comparison.
 */
export function versionSortKey(version) {
  const match = VERSION_SUFFIX_RE.exec(version || "");
  return match ? `${Number(match[1])}.${Number(match[2] || 0)}` : "1.0";
}

/**
 * A stable 32-bit hash of a string. FNV-1a, which is small and has no
 * dependencies; nothing here is security-sensitive, only stable.
 */
function hash32(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash;
}

/** Up to two initials for a mark, from whatever the row is actually called. */
function initialsOf(text) {
  const words = String(text || "")
    .split(/[\s_\-.]+/)
    .filter(Boolean);
  if (!words.length) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

// The mark's tile keeps its palette entry's HUE and pins saturation and
// lightness, exactly as `applyStackBadgeTint` does for a stack badge - the
// established way in this codebase to take a colour chosen for identity and
// renormalise it for a job that also has to be legible. The values differ
// because the job differs: the badge tints a GLYPH light (72%) against a dark
// chrome, this fills a TILE that white initials sit on.
//
// Pinning rather than picking black-or-white is not a preference. Measured
// against the shipped `contrastRatio`: with the raw palette entries, **22 of
// the 48** clear neither white nor near-black at WCAG AA, because the mid-tones
// are unreachable from either end. Hue is what carries the identity, so hue is
// what is kept.
const MARK_TINT_SATURATION = 55;
const MARK_TINT_LIGHTNESS = 30;

/** The hue of a `#rrggbb` colour, in degrees. */
function hueOf(hex) {
  const [r, g, b] = [1, 3, 5].map(
    (i) => parseInt(hex.slice(i, i + 2), 16) / 255,
  );
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const span = max - min;
  if (!span) return 0;
  let hue;
  if (max === r) hue = ((g - b) / span) % 6;
  else if (max === g) hue = (b - r) / span + 2;
  else hue = (r - g) / span + 4;
  return Math.round(hue * 60 + 360) % 360;
}

/**
 * The tile colour a mark is drawn on: the palette entry's hue, renormalised.
 *
 * @param {string} hex - a `SET_COLORS` value.
 * @returns {string} an `hsl()` colour that white initials are legible on.
 */
export function markBackground(hex) {
  return `hsl(${hueOf(hex)} ${MARK_TINT_SATURATION}% ${MARK_TINT_LIGHTNESS}%)`;
}

/**
 * The ink a mark's initials take.
 *
 * Always white, and that is a consequence of {@link markBackground} rather than
 * an assumption: the tile's lightness is pinned dark enough that white clears
 * WCAG AA on every hue, which the test asserts for all 48 rather than for the
 * few that looked risky.
 */
export function markForeground() {
  return "#ffffff";
}

/**
 * The mark a model wears when it has no icon.
 *
 * **Unset is never blank.** A checkpoint never has a sample - PixlStash
 * registers it in place and generates nothing for it - and 37% of real adapters
 * carry no title, base model or trigger either, so an empty identity slot is
 * the common case rather than the edge one.
 *
 * **Computed at render from a hash, and deliberately NOT the rule characters
 * use.** `character_color` takes the *first unused* colour from this same list,
 * which needs a bounded set and a moment of assignment. Models are unbounded
 * and have no such moment, and a mark that shifted when a neighbour was deleted
 * would be worse than no mark - so this is a pure function of the row. The two
 * must not be unified, however similar the palettes look.
 *
 * **Keyed on the FOLDED base model**, so every spelling of FLUX.2 lands on one
 * colour instead of scattering across the palette - which is the whole reason
 * the folding table exists. A row recording no base model hashes on the empty
 * string and so shares one colour with every other unset row: correct, because
 * they genuinely are one group, and the shelf already treats "not set" as a
 * value rather than an absence.
 *
 * @param {Object} row - a shelf row.
 * @returns {{color: string, ink: string, initials: string}} `color` is an
 *   `hsl()` string, not a hex, because it is renormalised rather than taken
 *   from the palette. `ink` travels with it so the pair cannot be separated.
 */
export function generatedMark(row) {
  const key = baseModelKey(row);
  const entry = SET_COLORS[hash32(key) % SET_COLORS.length].value;
  const name = modelName(row);
  return {
    color: markBackground(entry),
    ink: markForeground(),
    initials: initialsOf(name.text),
  };
}

/**
 * The ring's second axis (#904).
 *
 * Four treatments, and dotted is deliberately not among them: a 24px mark's ring
 * is roughly 75px of edge, so 2px dotted is about 37 dots and reads as a faded
 * solid ring rather than as its own thing - it fails exactly where it has to
 * work, at a glance in a list.
 *
 * Style MULTIPLIES the palette rather than replacing it: four styles against the
 * hues an entity already carries is what makes the ring survive greyscale,
 * colour blindness and forced-colors, where hue alone does not.
 */
export const RING_STYLES = ["solid", "dashed", "thick", "double"];

/**
 * `id -> row` for an entity list, built once per list rather than once per row.
 *
 * `assignmentRing` is called from a `v-for` over the whole shelf, so rebuilding
 * the maps inside it made the column cost rows x entities - 1,800 rows against a
 * few hundred characters, on every render. Keyed on the ARRAY, which the entity
 * store replaces wholesale on every refresh (`lists.value = { ...lists.value }`)
 * and never mutates in place, so a new list is a new key and the cache cannot go
 * stale. A `WeakMap` because the entry should die with the list it indexes.
 */
const ID_INDEX = new WeakMap();

function indexById(list) {
  if (!Array.isArray(list)) return new Map();
  let index = ID_INDEX.get(list);
  if (!index) {
    index = new Map(list.map((row) => [String(row.id), row]));
    ID_INDEX.set(list, index);
  }
  return index;
}

/** Which entity list answers an attachment's `entity_type`. */
const ATTACHMENT_KIND = {
  character: {
    list: "characters",
    noun: "person",
    colorKey: "character_color",
  },
  // `iconKey` is what a set may carry INSTEAD of a thumbnail; a character has
  // no such column, and the absence here is what keeps the lookup per-type
  // rather than a hardcoded field read in the loop below.
  set: {
    list: "sets",
    noun: "set",
    colorKey: "set_color",
    iconKey: "set_icon",
  },
};

/**
 * The ring one row's identity mark wears, from what the model is assigned to
 * (#892, redrawn for #904).
 *
 * `attachments` carries `entity_type` and `entity_id` and nothing else, so the
 * names and colours come from the shared entity lists the sidebar already
 * fetches. An id the lists do not answer still gets a ring - the vault is the
 * authority on what is attached, and dropping the ring would say "not
 * assigned", which is a different and wrong fact. It reads `#12` in the label
 * until the list lands, which is a loading state rather than a lie.
 *
 * **Colour is never the only carrier.** The hue is the entity's own, so a
 * character wears the same one here as in the sidebar - but the STYLE is what
 * makes the ring survive greyscale, and the label is what makes it readable
 * aloud. Hue, style and label always travel together.
 *
 * **Style is a property of the entity, not of the row.** It is hashed off the
 * same `type:id` key the hue falls back to, so one character wears one
 * treatment across all 1,800 rows and removing an attachment repaints nothing
 * else. Position in a list would have repainted every row that shared it.
 *
 * The FIRST attachment owns the ring, and the label names them all: a mark has
 * one edge, and drawing four rings around a 24px square is how you get a mark
 * that is mostly ring. The count is what the row says out loud instead.
 *
 * @param {Array<{entity_type: string, entity_id: number}>} attachments
 * @param {Object} [lists]
 * @param {Array<Object>} [lists.characters] - `useEntityListsStore().characters`.
 * @param {Array<Object>} [lists.sets] - `useEntityListsStore().pictureSets`.
 * @returns {{style: string, hue: string, type: string, id: ?number,
 *   icon: string, iconHue: string, label: string, count: number}} `icon` is
 *   the mdi name a picture set carries instead of its thumbnail, empty
 *   otherwise, and `iconHue` is that set's own colour - empty when it has none,
 *   which is theme ink and NOT the ring's hashed fallback. `style`
 *   is `"none"` and `hue` empty when nothing is attached, which is the dashed
 *   grey ring - never an absent ring, because a mark with no edge at all would
 *   read as a rendering gap rather than as a state.
 */
export function assignmentRing(
  attachments,
  { characters = [], sets = [] } = {},
) {
  const byId = {
    characters: indexById(characters),
    sets: indexById(sets),
  };
  const named = (attachments ?? []).map((att) => {
    const type = att.entity_type === "character" ? "character" : "set";
    const kind = ATTACHMENT_KIND[type];
    const entity = byId[kind.list].get(String(att.entity_id));
    const name = entity?.name || `#${att.entity_id}`;
    return {
      key: `${type}:${att.entity_id}`,
      type,
      id: att.entity_id,
      label: `${name} (${kind.noun})`,
      // A set carrying an icon has said what its face is; the thumbnail is what
      // an icon REPLACES, in the sidebar and here alike, so borrowing the
      // picture would draw a face no other surface shows. `cards` is the
      // sentinel for "keep the thumbnail", not an icon name.
      icon:
        kind.iconKey &&
        entity?.[kind.iconKey] &&
        entity[kind.iconKey] !== ICON_CARDS
          ? entity[kind.iconKey]
          : "",
      // The icon's own colour, and deliberately NOT `hue` below: `hue` always
      // resolves to something so the ring is never invisible, while an icon
      // with no `set_color` is drawn in theme ink by the sidebar. Inventing a
      // hashed hue here would paint one set two different colours on one
      // screen, which is exactly what the ring's "the hue is the entity's own"
      // rule exists to prevent.
      iconHue: entity?.[kind.colorKey] || "",
      hue:
        entity?.[kind.colorKey] ||
        SET_COLORS[hash32(`${type}:${att.entity_id}`) % SET_COLORS.length]
          .value,
    };
  });
  if (!named.length) {
    return {
      style: "none",
      hue: "",
      type: "",
      id: null,
      icon: "",
      iconHue: "",
      label: "Unassigned",
      count: 0,
    };
  }
  const [first] = named;
  return {
    style: RING_STYLES[hash32(first.key) % RING_STYLES.length],
    hue: first.hue,
    // The entity the mark borrows a face from when the model has no picture of
    // its own. Carried here rather than looked up again in the component, so
    // which attachment owns the ring and which owns the face cannot drift.
    type: first.type,
    id: first.id,
    icon: first.icon,
    iconHue: first.iconHue,
    label: named.map((one) => one.label).join(", "),
    count: named.length,
  };
}
