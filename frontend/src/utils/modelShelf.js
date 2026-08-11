// Model-shelf row helpers: the name fallback chain, size, and location state.
//
// 37% of real adapters carry no title, no base model and no trigger word at
// all, so none of this is edge-case handling — it is what most of the column
// renders. Two rules follow from that and are load-bearing:
//
//   * The derived name is computed HERE, at render, never stored. That is what
//     keeps `display_name IS NULL` an exact "nobody has named this" queue on
//     the backend and stops a guess being mistaken for a choice.
//   * A missing value is still rendered. "Base model not set" occupies the same
//     slot in the same type as a real value; a blank cell is the failure mode.

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
 * Resolve what a row is called, and whether anybody chose it.
 *
 * The chain is: the name the user gave, else one derived from the filename,
 * else the filename itself. `derived` is what the row uses to mark the name as
 * a fact about the file rather than a title someone wrote.
 *
 * @param {Object} model - a row from `/adapters` or `/checkpoints`.
 * @returns {{text: string, derived: boolean}}
 */
export function modelName(model) {
  const given = String(model?.display_name || "").trim();
  if (given) return { text: given, derived: false };
  const filename = String(model?.filename || "").trim();
  const derived = deriveModelName(filename);
  if (derived) return { text: derived, derived: true };
  // Nothing survived the strip (a file called `000002750.safetensors`). The
  // raw filename is the only honest thing left to show.
  return { text: filename || "no name in file", derived: true };
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

/** What each grouping axis is called, and the glyph that stands for it. */
export const GROUP_BY_LABELS = {
  none: { label: "None", icon: "mdi-format-list-bulleted" },
  base_model: { label: "Base model", icon: "mdi-cube-outline" },
  folder: { label: "Folder", icon: "mdi-folder-outline" },
};

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
 * no group at all — and the managed store holds nothing on every fresh install,
 * despite being the ruled default destination for a drop or an import. A
 * destination you cannot see is not a destination.
 *
 * The empty group carries `emptyReason`, because "registered and empty" and
 * "never scanned" are different facts and only one of them is the owner's to
 * act on. `last_checked` is the discriminator for that pair rather than a zero
 * count: a folder that has never been walked has no count to be zero.
 *
 * `file_count` decides something else — whether the folder is empty at all.
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
    // narrowed to checkpoints — and calling that folder empty would be a plain
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
 * Arrange folder groups into drive bands.
 *
 * Two levels, which is what the plan allows and no more: the band is the drive
 * and the header under it is the folder. Groups are RE-ORDERED so a band's
 * folders are contiguous — a band drawn over a non-contiguous run would claim
 * a grouping the list does not have — and each group is tagged with the band it
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
export function bandGroups(groups, deviceByFolderId) {
  const byBand = new Map();
  // Not every folder group names a folder. A model whose folders have all been
  // forgotten falls into "No registered copy", which has no `folderId` and is
  // not on a drive at all — banding it would put a disk glyph and a capacity
  // line over the one group that exists precisely because there is no disk.
  // It stays unbanded and sorts last, the same place `compareGroups` already
  // puts the absence of a value.
  const unbanded = groups.filter((group) => !Number.isInteger(group.folderId));
  for (const group of groups) {
    if (!Number.isInteger(group.folderId)) continue;
    const folderId = Number(group.folderId);
    const device = deviceByFolderId?.get?.(folderId) || null;
    // Unmeasured folders band alone, keyed by folder rather than by device.
    const key = device?.device_id ? `d:${device.device_id}` : `f:${folderId}`;
    let band = byBand.get(key);
    if (!band) {
      band = {
        key,
        // The volume's name if it has one, else where it is mounted. A Linux
        // mount point runs to `/media/glindkvist/102AB4B6757AF9A3` and crowds
        // the header out; the precise string stays available as the tooltip.
        label: device?.label || device?.mount_point || group.label,
        mountPoint: device?.mount_point || group.label,
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
 * How full a drive is, as a percentage of its size.
 *
 * `used`, not `free`, because a meter fills from empty. Returns `null` when the
 * drive could not be measured, which the caller must draw as "unknown" rather
 * than as an empty bar: an empty bar reads as a drive with nothing on it.
 *
 * @param {Object} band - a band from {@link bandGroups}.
 * @returns {{usedPct: number, shelfPct: number}|null}
 */
export function bandUsage(band) {
  const total = Number(band?.totalBytes);
  const free = Number(band?.freeBytes);
  if (!Number.isFinite(total) || total <= 0) return null;
  if (!Number.isFinite(free) || free < 0) return null;
  const used = Math.max(0, total - free);
  const shelf = Math.max(0, Math.min(used, Number(band?.shelfBytes) || 0));
  return {
    usedPct: Math.min(100, (used / total) * 100),
    shelfPct: Math.min(100, (shelf / total) * 100),
  };
}

/**
 * Reduce a row's copies to the one state worth reporting.
 *
 * `missing` is a fact (the folder was readable and the file was not in it);
 * `unreachable` is the absence of one (we could not look). They must not read
 * the same, and one present copy makes both moot — the file is usable.
 *
 * @param {Array<Object>} locations - the row's `locations` array.
 * @returns {"present"|"missing"|"unreachable"|"forgotten"}
 */
export function locationState(locations) {
  const list = Array.isArray(locations) ? locations : [];
  if (!list.length) return "forgotten";
  if (list.some((loc) => loc?.state === "present")) return "present";
  if (list.some((loc) => loc?.state === "unreachable")) return "unreachable";
  return "missing";
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
 *   scanned, and every engine loader looks for them at a fixed path — moving
 *   one out breaks the tagger and re-downloads it on the next run.
 * - **An `external` folder.** The HuggingFace cache and insightface's store are
 *   shared with other software. Taking a file out of one is not ours to do.
 *
 * Per COPY and not per model: `model_file`'s primary key is
 * `(folder_id, relpath)`, so a model registered in three folders offers three
 * copies and the caller moves the ones it named. That is also why the size is
 * summed off the row rather than off the copy — the hub records one
 * `file_size` per model, and every copy of it is that size.
 *
 * @param {Array<Object>} rows - shelf rows, each with `locations`.
 * @param {Map<number, Object>|null} foldersById - `model_folder.id` to the
 *   folder row, for the two folder-level exclusions. Omitted, only the
 *   `present` rule applies.
 * @returns {{items: Array<{folder_id: number, relpath: string}>, totalBytes: number}}
 */
export function movableCopies(rows, foldersById = null) {
  const items = [];
  let totalBytes = 0;
  for (const row of Array.isArray(rows) ? rows : []) {
    for (const loc of row?.locations || []) {
      if (loc?.state !== "present") continue;
      const folder = foldersById?.get(Number(loc.folder_id));
      if (folder?.owner === "pixlstash") continue;
      if (folder?.movable === "external") continue;
      items.push({ folder_id: loc.folder_id, relpath: loc.relpath });
      totalBytes += Number(row.file_size) || 0;
    }
  }
  return { items, totalBytes };
}
