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

import { SET_COLORS } from "./setAppearance";

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
 * — the row draws each of the four differently, because "somebody named this"
 * and "we guessed" and "there is nothing here to read" are three different
 * things to a reader deciding what to fix, and the shelf used to render all of
 * them as one string.
 *
 * The last state returns an EMPTY string on purpose. A row with no filename
 * used to read `no name in file`, which looks like a name, sorts like a name
 * and reads as inert — so the one row that most needs naming was the one that
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
  // raw filename is the only honest thing left to show — and it is the file's
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
  feature: { label: "Feature", icon: "mdi-star-four-points-outline" },
};

/**
 * What each stored capability is called on screen.
 *
 * The screen's words, not the database's: `model_capability` stores machine
 * vocabulary (`captioner`, `scorer`) because a stored value is not a thing a
 * designer gets to change, and these are. Named for the FEATURE rather than the
 * ML task for the same reason the classifier is — nobody who switched
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
  const key = String(capability || "");
  return CAPABILITY_LABELS[key] || key;
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
 * What each folder tier is marked with, in ONE icon family.
 *
 * Shared with `ModelFoldersDialog`, which is where these glyphs started: the
 * shelf header and the dialog row are two views of the same registry, and two
 * copies of the map would be two vocabularies for one fact. Nothing here is
 * hand-drawn — the header used to have no tier mark at all, and the mock that
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
  // that gets the lock — the same rule the folders dialog's glyph column uses.
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
 * offline state is a DASHED rail and muted ink — never the error colour, for
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
/**
 * The band a folder belongs to.
 *
 * The drive when one was measured, the folder itself when none was — the same
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
    const key = bandKeyFor(folderId, deviceByFolderId);
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
 * per cent of a 256 GB SSD is 25 GB, which IS a problem — but so is 60 GB free
 * on that same disk, and the fraction calls that fine.
 */
export const LOW_FREE_BYTES = 50 * 1024 ** 3;

/**
 * How a drive's space divides into the three things a reader asks about.
 *
 * The meter answers two questions at once — "how full is this disk" and "how
 * much of that is us" — and it can only answer the second if the shelf's share
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
 *     a filesystem can genuinely report more free than it holds — thin
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
 * *before* the pointer is released — which means a fourth segment carved out of
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
 * object today — the band's low treatment deliberately goes through `bandUsage`
 * — and that is exactly why the inconsistency would be found late.
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
 * Reduce a row's copies to the one state worth reporting.
 *
 * `missing` is a fact (the folder was readable and the file was not in it);
 * `unreachable` is the absence of one (we could not look). They must not read
 * the same, and one present copy makes both moot — the file is usable.
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
 * The registered folders whose every copy is out of reach, and how many rows
 * each one takes with it.
 *
 * This is what lets an unplugged drive state its scope ONCE. `unreachable` is
 * the common case for anyone keeping adapters on an external disk — the whole
 * folder flips together (`ModelFolderScanner._mark_unreachable`) — and 300 rows
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
 *   path, so the banner reads the same on every render — under the shelf's one
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
 * "the run is gone" cannot both be true — saying it anyway would tell the
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
 * Fold each stack's members into the one row that stands for them.
 *
 * A training run is many `model` rows and the list query returns all of them,
 * so without this a six-step run reads as six unrelated adapters — which is the
 * state the shelf shipped in until F5.
 *
 * The **cover** is `stack_position` 0, which the backend already ordered: the
 * bare final file if the run wrote one, else its highest step. A stack whose
 * cover is filtered out of view collapses onto its lowest surviving position
 * rather than vanishing, because a run half-hidden by a base-model filter is
 * still a run and dropping it would make the filter lie about what is on disk.
 *
 * `memberIds` is the whole point of the fold and not decoration: stacks are
 * **atomic** here exactly as they are for pictures (`services/stack_membership`
 * — "applied to EVERY member of its stack, so state can never go partial"), so
 * selecting a collapsed row has to select the run, or Move would take the cover
 * and leave five steps behind.
 *
 * @param {Array<Object>} rows - shown rows, already narrowed by the filters.
 * @returns {Array<Object>} one row per unstacked model and per stack, each
 *   stacked row carrying `memberIds`, `memberCount` and `members`.
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
    const members = [...byStack.get(row.stack_id)].sort(
      (a, b) => (a.stack_position ?? 0) - (b.stack_position ?? 0),
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
    });
  }
  return out;
}

/**
 * Say how many runs were grouped, and how many were not.
 *
 * One call per group, so a partial outcome is real: a group whose rows were
 * stacked between the dry run and the confirmation comes back 409 and is
 * counted rather than throwing, or one stale group would discard the others.
 *
 * @param {number} grouped - runs collapsed into a stack.
 * @param {number} failed - runs the server refused.
 * @returns {string}
 */
export function stackReceipt(grouped, failed) {
  const runs = (n) => `${n.toLocaleString()} ${n === 1 ? "run" : "runs"}`;
  if (!grouped) {
    return failed
      ? `Nothing was grouped. ${runs(failed)} could not be, and the files are unchanged.`
      : "Nothing to group.";
  }
  const note = failed
    ? ` ${runs(failed)} could not be grouped; something changed them first.`
    : "";
  return `Grouped ${runs(grouped)}.${note}`;
}

/**
 * The training step a filename records, or null for a bare final file.
 *
 * Mirrors `_step_of` in `pixlstash/services/stack_detector.py`, and reads the
 * same trailing token {@link deriveModelName} strips — so a file is never
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
// lightness, exactly as `applyStackBadgeTint` does for a stack badge — the
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
 * **Unset is never blank.** A checkpoint never has a sample — PixlStash
 * registers it in place and generates nothing for it — and 37% of real adapters
 * carry no title, base model or trigger either, so an empty identity slot is
 * the common case rather than the edge one.
 *
 * **Computed at render from a hash, and deliberately NOT the rule characters
 * use.** `character_color` takes the *first unused* colour from this same list,
 * which needs a bounded set and a moment of assignment. Models are unbounded
 * and have no such moment, and a mark that shifted when a neighbour was deleted
 * would be worse than no mark — so this is a pure function of the row. The two
 * must not be unified, however similar the palettes look.
 *
 * **Keyed on the FOLDED base model**, so every spelling of FLUX.2 lands on one
 * colour instead of scattering across the palette — which is the whole reason
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
 * solid ring rather than as its own thing — it fails exactly where it has to
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
 * the maps inside it made the column cost rows x entities — 1,800 rows against a
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
  set: { list: "sets", noun: "set", colorKey: "set_color" },
};

/**
 * The ring one row's identity mark wears, from what the model is assigned to
 * (#892, redrawn for #904).
 *
 * `attachments` carries `entity_type` and `entity_id` and nothing else, so the
 * names and colours come from the shared entity lists the sidebar already
 * fetches. An id the lists do not answer still gets a ring — the vault is the
 * authority on what is attached, and dropping the ring would say "not
 * assigned", which is a different and wrong fact. It reads `#12` in the label
 * until the list lands, which is a loading state rather than a lie.
 *
 * **Colour is never the only carrier.** The hue is the entity's own, so a
 * character wears the same one here as in the sidebar — but the STYLE is what
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
 *   label: string, count: number}} `style`
 *   is `"none"` and `hue` empty when nothing is attached, which is the dashed
 *   grey ring — never an absent ring, because a mark with no edge at all would
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
    label: named.map((one) => one.label).join(", "),
    count: named.length,
  };
}
