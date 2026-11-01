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

// The precision a file was stored at, written into its name. Mirrors the
// `_QUANT_*` vocabulary in `pixlstash/utils/model_utils.py` TOKEN FOR TOKEN:
// `cleanAssetName` has already split on `_` and `-`, so `Q4_K_M` arrives as
// three tokens, and the shelf row's name and the badge beside it come from one
// parser on each side rather than two that can disagree.
//
// Two rules and not one list. A GGUF level (`k`, `s`, `m`, `l`, `0`, `1`) is an
// ordinary token in a real name, so it is only ever eaten as the tail of an
// explicit `q<n>`, `iq<n>`, or `tq<n>` head; the rest (`scaled`, `awq`, `gptq`, `fast`) are
// distinctive enough to pop next to any real quant token, and never alone.
const GGUF_HEAD_RE = /^(?:i?q|tq)\d+$/i;
const GGUF_LEVELS = new Set([
  "k",
  "s",
  "m",
  "l",
  "xs",
  "xl",
  "xxs",
  "nl",
  "0",
  "1",
]);

/** Filename spelling -> canonical id. The refinement wins where both appear. */
const QUANT_TOKENS = {
  fp32: "fp32",
  f32: "fp32",
  fp16: "fp16",
  f16: "fp16",
  bf16: "bf16",
  fp8: "fp8",
  f8: "fp8",
  e4m3: "fp8_e4m3",
  e4m3fn: "fp8_e4m3",
  e5m2: "fp8_e5m2",
  fp8mixed: "fp8",
  nvfp4: "nvfp4",
  mxfp4: "mxfp4",
  fp4: "fp4",
  nf4: "nf4",
  int8: "int8",
  i8: "int8",
  int4: "int4",
  i4: "int4",
};

/** Popped only alongside a real quant token; alone they are somebody's name. */
const QUANT_MODIFIERS = new Set([
  "scaled",
  "awq",
  "gptq",
  "fast",
  "mixed",
  "convrot",
  "kj",
]);

/** A version after the precision (`…-bf16-1.0`), kept in the name. */
const QUANT_TRAILING_VERSION_RE = /^(?:v\d+(?:\.\d+)?|\d+\.\d+)$/i;

/**
 * Split a token list into its name and the quant postfix on the end.
 *
 * Mirrors `_split_quant`. The one parser both {@link deriveModelName} and
 * {@link quantFromFilename} run.
 *
 * @param {string[]} tokens - `cleanAssetName(...)` split on whitespace.
 * @returns {{tokens: string[], quant: string|null}}
 */
function splitQuant(tokens) {
  // The GGUF tail first, longest match wins so `Q4 K M` beats the `Q4` in it.
  for (const width of [3, 2, 1]) {
    if (tokens.length < width) continue;
    const tail = tokens.slice(tokens.length - width);
    if (!GGUF_HEAD_RE.test(tail[0])) continue;
    if (!tail.slice(1).every((t) => GGUF_LEVELS.has(t.toLowerCase()))) continue;
    return {
      tokens: tokens.slice(0, tokens.length - width),
      quant: tail.map((t) => t.toLowerCase()).join("_"),
    };
  }
  const kept = [...tokens];
  const version =
    kept.length > 1 && QUANT_TRAILING_VERSION_RE.test(kept[kept.length - 1])
      ? [kept.pop()]
      : [];
  const popped = [];
  while (kept.length) {
    const token = kept[kept.length - 1];
    const last = token.toLowerCase();
    const dot = token.lastIndexOf(".");
    const tail = token.slice(dot + 1);
    if (Object.hasOwn(QUANT_TOKENS, last) || QUANT_MODIFIERS.has(last)) {
      popped.push(kept.pop());
    } else if (dot > 0 && Object.hasOwn(QUANT_TOKENS, tail.toLowerCase())) {
      // `…_t2i.int8_convrot`: a dot, not a separator, before the quant.
      kept[kept.length - 1] = token.slice(0, dot);
      popped.push(tail);
    } else {
      break;
    }
  }
  // The guard the whole safety of this rests on: `scaled` and `fast` are
  // ordinary tokens in real model names and may only be eaten when a genuine
  // quant token was eaten with them, or `some_model_scaled` silently becomes
  // `some model`.
  const real = popped.find((t) =>
    Object.hasOwn(QUANT_TOKENS, t.toLowerCase()),
  );
  if (!real) return { tokens: [...tokens], quant: null };
  return {
    tokens: [...kept, ...version],
    quant: QUANT_TOKENS[real.toLowerCase()],
  };
}

/**
 * The precision a model's filename says it was stored at.
 *
 * Mirrors `quant_from_filename`. The shelf and the workflow card are served
 * the canonical id by the backend; this is here so the two halves of the
 * parser stay provably identical (`modelShelf.test.js`) and so a client with
 * only a filename in hand can still answer.
 *
 * @param {string} filename - file name or path.
 * @returns {string|null} a canonical id, or null for the usual name that
 *   carries no quant postfix.
 */
export function quantFromFilename(filename) {
  return splitQuant(cleanAssetName(filename).split(/\s+/).filter(Boolean))
    .quant;
}

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
 * Mirrors `derive_model_name`: drops the trailing quant postfix and then any
 * training bookkeeping, because both are parsed into their own fields and
 * repeating them turns six checkpoints of one run into six unrelated-looking
 * rows, and puts the precision in the place a person reads the model's
 * identity.
 *
 * **The precision is dropped from the name, never lost.** Two quant variants
 * of one model collapse to one name here, and the badge the row draws from
 * `quant` is what keeps them apart - a caller that strips without badging has
 * made two rows read identically.
 *
 * @param {string} filename - file name or path.
 * @returns {string} a human-readable name, or `""` when nothing survives.
 */
export function deriveModelName(filename) {
  const { tokens } = splitQuant(
    cleanAssetName(filename).split(/\s+/).filter(Boolean),
  );
  while (tokens.length && TRAINING_SUFFIX_RE.test(tokens[tokens.length - 1])) {
    tokens.pop();
  }
  return tokens.join(" ");
}

/**
 * What each canonical quant id is called on a badge, and what the badge says
 * on hover.
 *
 * **The display copy lives here and not in the API**, the way slot kinds
 * already do: the backend serves the id, one map turns it into words, and the
 * shelf row, the workflow card, its ⓘ list and the lightbox's recipe all read the
 * same one rather than each inventing a spelling.
 *
 * `fp8_e4m3` and `fp8_e5m2` deliberately share the label `FP8`: the variant is
 * a detail for the tooltip, and two chips reading `FP8_E4M3` and `FP8_E5M2` in
 * a 240px column are two chips that clip to nothing.
 */
const QUANT_LABELS = {
  fp32: ["FP32", "32-bit float"],
  fp16: ["FP16", "16-bit float"],
  bf16: ["BF16", "bfloat16"],
  fp8_e4m3: ["FP8", "FP8 E4M3"],
  fp8_e5m2: ["FP8", "FP8 E5M2"],
  fp8: ["FP8", "8-bit float"],
  nvfp4: ["NVFP4", "NVIDIA FP4"],
  mxfp4: ["MXFP4", "mixed FP4"],
  fp4: ["FP4", "4-bit float"],
  nf4: ["NF4", "4-bit NormalFloat"],
  int8: ["INT8", "8-bit integer"],
  int4: ["INT4", "4-bit integer"],
  int32: ["INT32", "32-bit integer"],
  mixed: ["MIXED", "No single dtype holds most of the parameters"],
};

/** A GGUF id (`q4_k_m`): the level IS the name a person recognises. */
const GGUF_ID_RE = /^(?:i?q|tq)\d+(?:_[a-z0-9]+)*$/i;

/**
 * The badge for one model's `quant`, or null when there is nothing to say.
 *
 * Null and not an empty badge: a model whose precision nothing recorded gets
 * NO chip, rather than a blank one or an `UNKNOWN` that reads like a fact.
 *
 * An id this map has never seen is shown verbatim, uppercased, rather than
 * swallowed - a safetensors dtype nobody anticipated is still true, and a
 * reader can look it up.
 *
 * @param {string|null|undefined} quant - a canonical id from the API.
 * @returns {{label: string, title: string}|null}
 */
export function quantBadge(quant) {
  const id = String(quant ?? "").trim();
  if (!id) return null;
  const known = QUANT_LABELS[id.toLowerCase()];
  if (known) return { label: known[0], title: known[1] };
  const label = id.toUpperCase();
  return {
    label,
    title: GGUF_ID_RE.test(id)
      ? `GGUF ${label} quantisation`
      : `Stored as ${label}`,
  };
}

/** How many trigger words a tooltip spells out before it counts the rest. */
const TRIGGER_TITLE_WORDS = 12;

/**
 * The trigger words a row has ON RECORD, as opposed to the person's name the
 * server serves in their place while nothing has been set.
 *
 * What a bulk write would replace: a default is not stored, so writing over it
 * loses nothing.
 *
 * @param {Object} row - a model row from the API.
 * @returns {string[]}
 */
export function recordedTriggerWords(row) {
  return row?.trigger_words_source === "recorded" &&
    Array.isArray(row.trigger_words)
    ? row.trigger_words
    : [];
}

/**
 * The chip for one model's trigger words, or null when it has none.
 *
 * `isDefault` is the person's name standing in for a word nobody recorded. It
 * is drawn differently because it is a different claim: a recorded word is in
 * the file or was typed here, and the default only says who the model is
 * assigned to.
 *
 * A file trained on tagged captions records its whole tag table, most frequent
 * first, so the label is the first word and a count rather than the list.
 *
 * @param {Object} row - a model row from the API.
 * @returns {{label: string, title: string, isDefault: boolean}|null}
 */
export function triggerChip(row) {
  const words = Array.isArray(row?.trigger_words) ? row.trigger_words : [];
  if (!words.length) return null;
  if (row.trigger_words_source === "character") {
    return {
      label: words[0],
      title: `Trigger word: ${words[0]}, the name of the person this is assigned to. None has been set.`,
      isDefault: true,
    };
  }
  const rest = words.length - TRIGGER_TITLE_WORDS;
  const listed = words.slice(0, TRIGGER_TITLE_WORDS).join(", ");
  return {
    label: words.length > 1 ? `${words[0]} +${words.length - 1}` : words[0],
    title: `Trigger ${words.length > 1 ? "words" : "word"}: ${listed}${rest > 0 ? ` and ${rest} more` : ""}`,
    isDefault: false,
  };
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
  // A checkpoint whose filename simply IS a known base model
  // (`flux1-dev.safetensors`) reads as that model's label. Still ours, so it
  // draws as `derived`; the server sets it only for a checkpoint and never
  // from a guessed base model, so a LoRA's name is never its base model.
  const matched = String(model?.matched_name || "").trim();
  if (matched) return { text: matched, state: "derived" };
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
  type: { label: "Type", icon: "mdi-shape-outline" },
  sets: { label: "Sets", icon: "mdi-view-grid-outline" },
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
    const ms = fileTime(row);
    return ms === null ? "" : new Date(ms).toISOString();
  }
  return (!own && row?.newest_member_at) || row?.added_at || "";
}

/**
 * A row's file date in milliseconds, or null when it has none to trust.
 *
 * The file's own date on disk, which for a download is when it was downloaded:
 * the shelf's `added_at` is when a scan first saw it, and a first scan stamps a
 * whole library with one moment.
 *
 * @param {Object} row - a shelf row.
 * @returns {number|null}
 */
export function fileTime(row) {
  const ns = Number(row?.newest_file_mtime);
  // `!(ns > 0)` and not `ns <= 0`, so a null or a non-number is caught here
  // rather than by the range check below.
  if (!(ns > 0)) return null;
  const ms = ns / 1e6;
  // `toISOString` THROWS on a date out of range, and its callers run inside
  // render: one corrupt mtime would take a whole list down rather than one row.
  return Number.isNaN(new Date(ms).getTime()) ? null : ms;
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
  type: ["A to Z", "Z to A"],
  size: ["Smallest first", "Largest first"],
  sets: ["Fewest first", "Most first"],
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
  return key === "name" || key === "base_model" || key === "type"
    ? "asc"
    : "desc";
}

/** What each grouping axis is called, and the glyph that stands for it. */
export const GROUP_BY_LABELS = {
  none: { label: "None", icon: "mdi-format-list-bulleted" },
  // The one axis whose groups OVERLAP, and so the one that is a grid rather
  // than a band: a VAE belongs to every set it has run in (#1438).
  workflow_set: { label: "Workflow set", icon: "mdi-view-grid-outline" },
  // The sidebar's Type rows as bands (#2168): All models' default.
  type: { label: "Type", icon: "mdi-shape-outline" },
  base_model: { label: "Base model", icon: "mdi-cube-outline" },
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
  controlnet: "ControlNet",
  upscaler: "Upscaler",
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
 * The Models sidebar's Type rows (#2168), in the order the design lists them.
 *
 * `mark` is the letter the row, the list and a set card's member chips draw a
 * model under; `tint` names the colour role it is painted in (checkpoints in
 * the teal `tertiary`, LoRAs in the raspberry `secondary`, the support files in
 * one neutral), so the three surfaces cannot disagree about what a C means.
 */
export const SHELF_TYPES = [
  { key: "checkpoint", label: "Checkpoints", one: "Checkpoint", mark: "C", tint: "tertiary" },
  { key: "lora", label: "LoRAs", one: "LoRA", mark: "L", tint: "secondary" },
  { key: "vae", label: "VAEs", one: "VAE", mark: "V", tint: "neutral" },
  { key: "text_encoder", label: "Text encoders", one: "Text encoder", mark: "T", tint: "neutral" },
  { key: "upscaler", label: "Upscalers", one: "Upscaler", mark: "U", tint: "neutral" },
  { key: "controlnet", label: "ControlNets", one: "ControlNet", mark: "N", tint: "neutral" },
];

/**
 * Which Type row a shelf row sits under, or `""` for none.
 *
 * Every adapter algorithm is a LoRA here (LoKr, LoHa and DoRA are LoRA
 * variants a reader looks for under that word); an engine or an unclassified
 * file sits under no Type row and is still listed in All models.
 *
 * @param {{file_kind?: string}} row
 * @returns {string} a {@link SHELF_TYPES} key, or `""`.
 */
export function shelfTypeOf(row) {
  const kind = String(row?.file_kind || "");
  if (kind === "adapter") return "lora";
  return SHELF_TYPES.some((type) => type.key === kind) ? kind : "";
}

/**
 * The always-present anchor of the metadata line, whatever else is null.
 *
 * A model that serves several features lists them ALL, because a single label
 * answers "what breaks if I delete this" wrongly for exactly the rows a reader
 * is most likely to be deciding about: Florence-2 captions and detects, and the
 * CLIP the embedder loads is both the search encoder and the aesthetic scorer's
 * backbone. `capabilities` arrives primary-first, so the first word is the one
 * `row.kind` holds and the column still reads as one thing at a glance.
 */
function kindLabel(row) {
  // One table with the `feature` group axis, which names its headings from the
  // same one - the cell reading `Checkpoint` under a header saying something
  // else is the contradiction that table exists to prevent.
  const named = fileKindLabel(row.file_kind);
  if (named) return named;
  const capabilities = Array.isArray(row.capabilities) ? row.capabilities : [];
  if (capabilities.length) return capabilities.map(capabilityLabel).join(", ");
  // The axis still files `unknown` under `Other` where the cell says
  // "Unclassified": a shrug is not a heading of its own. The CELL always names
  // what the row is.
  return adapterKindLabel(row.kind) || "Adapter";
}

/**
 * The Type cell's words: the Type row's singular, or the kind label where there
 * is none. The Type sort orders on the same words.
 *
 * @param {Object} row - a shelf row.
 * @returns {string}
 */
export function shelfTypeLabel(row) {
  return (
    SHELF_TYPES.find((type) => type.key === shelfTypeOf(row))?.one ??
    kindLabel(row)
  );
}

/**
 * The Type letter and tint a row is drawn under (`TypeMark`'s props), or a
 * neutral "?" for a file no Type row takes.
 *
 * @param {{file_kind?: string}} row
 * @returns {{mark: string, tint: string}}
 */
export function shelfTypeMark(row) {
  const type = SHELF_TYPES.find((one) => one.key === shelfTypeOf(row));
  return type ? { mark: type.mark, tint: type.tint } : { mark: "?", tint: "neutral" };
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
 * `Lightbox` and the toolbar, the SHAPE from the same two - it is what
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
 * The base model a row should be GROUPED, FILTERED and FACETED by.
 *
 * `base_model_canonical` when the server identified one - from what the file
 * declares, from its filename, or from what a person typed - which is also
 * what the server sorts and filters on. Else `base_model_folded` when the
 * server recognised the string, the raw one when it did not. Folding is what makes `sdxl_base_v1-0`, `SDXL`, `sdxl base` and
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
  return (
    row?.base_model_canonical || row?.base_model_folded || row?.base_model || ""
  );
}

/** The identification sources that are a guess, not something the file said. */
const GUESSED_BASE_SOURCES = new Set([
  "filename",
  "declared_fuzzy",
  "filename_fuzzy",
]);

/**
 * What a row's Base model cell shows, and whether it is a guess.
 *
 * The raw `base_model` when the file (or a person) said it, because that
 * spelling is what the file actually says. A GUESS shows the label it was
 * read as instead - the raw string is not the guess - and says what it was
 * read from, for the tag's tooltip.
 *
 * @param {Object} row - a row from `/adapters` or `/checkpoints`.
 * @returns {{text: string, guess: string|null, note?: string}} `text` is `""`
 *   when nothing is recorded; `guess` is the tooltip copy for a guessed value,
 *   else null; `note` says a value came from Civitai, which is not a guess.
 */
export function baseModelCell(row) {
  const canonical = row?.base_model_canonical || "";
  const source = row?.base_model_source || "";
  if (canonical && GUESSED_BASE_SOURCES.has(source)) {
    const raw = String(row?.base_model || "").trim();
    const how =
      source === "declared_fuzzy" && raw
        ? `The file says “${raw}”, read as ${canonical}.`
        : "Guessed from the filename. Nothing in the file says this.";
    return {
      text: canonical,
      guess: `${how} Click to confirm it; double-click the label or press Shift+F2 to correct it.`,
    };
  }
  if (source === "civitai") {
    return {
      text: row?.base_model || canonical,
      guess: null,
      note: "From Civitai, matched by file hash. Double-click or press Shift+F2 to correct it.",
    };
  }
  return { text: row?.base_model || canonical, guess: null };
}

/** How many models one Civitai lookup may ask about; the route's own cap. */
export const MAX_BASE_MODEL_LOOKUP = 100;

/**
 * Whether Look up base model on Civitai would ask about this model.
 *
 * Mirrors the server's eligibility, which stays the authority: an adapter or
 * checkpoint, hashed, with no base model, that the owner has not cleared
 * (a cleared base model is null with source `user`) and whose header does not
 * already name one (source `declared`, which outranks Civitai).
 *
 * @param {Object} row - a model row from `/adapters` or `/checkpoints`.
 * @returns {boolean}
 */
export function lookupEligible(row) {
  return (
    (row?.file_kind === "adapter" || row?.file_kind === "checkpoint") &&
    Boolean(row.sha256) &&
    row.base_model == null &&
    row.base_model_source !== "user" &&
    row.base_model_source !== "declared"
  );
}

// The rail keeps its palette entry's HUE and pins saturation and lightness, the
// same renormalisation `markBackground` does and for the same reason: a colour
// picked for identity is not automatically a colour that reads as a 3px line.
// Mid lightness rather than the mark's 30%, because this line sits on the
// canvas in BOTH themes and nothing is ever written on it.
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
 * `removed` is the one absence that is not a fault at all: the reader deleted
 * that copy themselves to keep another (#1439), and the row is here on purpose.
 * It is reported only when every copy is one, which is the folder axis's case -
 * that draw stands for the removed copy alone - because a model that still has a
 * copy somewhere is `present` and a model with a genuinely missing one must
 * still say so. Ranked after `unreachable` for that reason: a mix of removed and
 * missing is a fault, and drawing it as a tidy-up would hide one.
 *
 * @param {Array<Object>} locations - the row's `locations` array.
 * @returns {"present"|"missing"|"not_downloaded"|"unreachable"|"removed"|"forgotten"}
 */
export function locationState(locations) {
  const list = Array.isArray(locations) ? locations : [];
  if (!list.length) return "forgotten";
  if (list.some((loc) => loc?.state === "present")) return "present";
  if (list.some((loc) => loc?.state === "unreachable")) return "unreachable";
  if (list.every((loc) => loc?.state === "not_downloaded"))
    return "not_downloaded";
  if (list.every((loc) => loc?.state === "removed")) return "removed";
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
  // A copy removed to keep one of several (#1439). Its own word rather than
  // `missing`, which is the scanner saying it looked and the file was gone: this
  // one is gone because the reader said so, and the row is still here on purpose.
  removed: "removed as a duplicate",
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
 * One copy's full path: its registered folder and its relpath.
 *
 * The separator is taken from the registered folder rather than assumed, and a
 * Windows folder takes the relpath's separators with it: the two halves come
 * from different places (`model_folder.path` as registered, `relpath` as the
 * scanner wrote it), so joining them without a rule is how a path comes back
 * half-slashed. A POSIX folder leaves the relpath alone, where a backslash is a
 * legal character in a filename rather than a separator.
 *
 * @param {{folder_path: string, relpath: string}} copy
 * @returns {string}
 */
export function copyPath(copy) {
  const folder = String(copy?.folder_path || "");
  const relpath = String(copy?.relpath || "");
  const windows = folder.includes("\\") && !folder.includes("/");
  const sep = windows ? "\\" : "/";
  const tail = windows ? relpath.replace(/\//g, "\\") : relpath;
  return `${folder.replace(/[/\\]+$/, "")}${sep}${tail.replace(/^[/\\]+/, "")}`;
}

/**
 * Where a row's copies sit, one path per line, each saying what is there.
 *
 * No list header names a folder (the folder grouping went in #2168), and
 * where a file is is the first question of a reader with the same adapter on
 * two disks, so the file line carries the answer as its tooltip.
 *
 * Every copy, not the first: a model registered in two folders is one row, and
 * naming one of its homes would read as naming its only one.
 *
 * Each path is {@link copyPath}'s. A copy missing either half is skipped rather than half-named: both are
 * NOT NULL on the wire, so this is a broken row, and `a.st` on its own answers
 * "where is this file" with the one thing that is not a location.
 *
 * @param {Array<Object>} locations - the row's `locations` array.
 * @returns {string} the paths, newline-separated, or `""` when there are none.
 */
export function copyPathsTitle(locations) {
  return (Array.isArray(locations) ? locations : [])
    .map((loc) => {
      if (!loc?.folder_path || !loc?.relpath) return "";
      const path = copyPath(loc);
      const note = copyStateNote(loc?.state);
      return note ? `${path} · ${note}` : path;
    })
    .filter(Boolean)
    .join("\n");
}

/**
 * A model's file tooltip: where its copies sit, then the SHA-256 it is matched by.
 *
 * The hash is whole, not shortened: it is what a set's membership and a
 * recipe's digest are compared against, so it is here to be compared.
 *
 * "Not read yet" is said only of a row the hasher still owes a hash
 * (`hash_pending`). A row that never gets one (an engine, a cached Hugging
 * Face repo) and a set member whose row is not loaded get no line: neither is
 * waiting for anything.
 *
 * @param {Object} row - a shelf row, or a set member (which has no `locations`).
 * @returns {string} the lines, or `""` - which disables the Tooltip.
 */
export function modelFileTitle(row) {
  const sha = row?.sha256;
  return [
    copyPathsTitle(row?.locations),
    sha ? `SHA-256: ${sha}` : row?.hash_pending ? "SHA-256: not read yet" : "",
  ]
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
 * - **A locked folder** (`movable` other than `per_item`). The HuggingFace
 *   cache and the InsightFace packs are shared with other software or move
 *   only as a whole. Taking a file out of one is not ours to do.
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
 * In a folder view (#2204) only the copy in the open folder moves: a model
 * with a second copy elsewhere is selected there as "this file", and taking
 * the other copy along would move a file the reader cannot see.
 *
 * @param {Array<Object>} rows - shelf rows, each with `locations`.
 * @param {Map<number, Object>|null} foldersById - `model_folder.id` to the
 *   folder row, for the folder-level exclusions. Omitted, only the `present`
 *   rule applies.
 * @param {{folderId: number, path: string, locked?: boolean}|null} [scope] -
 *   the open folder, or null for every copy.
 * @returns {{items: Array<{folder_id: number, relpath: string}>,
 *   totalBytes: number, bytesByFolderId: Map<number, number>,
 *   kinds: Map<string, string>, stays: number}} `kinds` is each copy's Type
 *   by {@link copyKey}, for the drop tip's ComfyUI name; the items stay bare
 *   because the move route refuses any other field. `stays` counts the rows
 *   that have a copy in scope but none that can move (missing, offline,
 *   PixlStash's own or locked), so a drag can say what it leaves behind; a
 *   row with no copy in the open folder is not here, and is not counted.
 */
export function movableCopies(rows, foldersById = null, scope = null) {
  const items = [];
  const kinds = new Map();
  const bytesByFolderId = new Map();
  let totalBytes = 0;
  let stays = 0;
  for (const row of Array.isArray(rows) ? rows : []) {
    const before = items.length;
    let here = false;
    for (const loc of row?.locations || []) {
      if (scope && !inFolderScopeOf(loc, scope)) continue;
      here = true;
      if (loc?.state !== "present") continue;
      const folder = foldersById?.get(Number(loc.folder_id));
      if (folder?.owner === "pixlstash") continue;
      // A locked folder (the lock in the Folders tree): files do not leave it
      // one at a time, and the server refuses them.
      if (folder?.movable && folder.movable !== "per_item") continue;
      const bytes = Number(row.file_size) || 0;
      const folderId = Number(loc.folder_id);
      items.push({ folder_id: loc.folder_id, relpath: loc.relpath });
      kinds.set(copyKey(loc), shelfTypeOf(row));
      bytesByFolderId.set(
        folderId,
        (bytesByFolderId.get(folderId) || 0) + bytes,
      );
      totalBytes += bytes;
    }
    if (here && items.length === before) stays += 1;
  }
  return { items, totalBytes, bytesByFolderId, kinds, stays };
}

/**
 * One copy's key: its folder and its relpath.
 *
 * @param {{folder_id: number, relpath: string}} loc
 * @returns {string}
 */
export function copyKey(loc) {
  return `${Number(loc?.folder_id)}\u0000${loc?.relpath}`;
}

/**
 * Whether a key press came from the Folders tree (#2206).
 *
 * The shelf's window listeners cannot tell the tree's keys from their own by
 * the event alone: the tree is in the sidebar, outside `.shelf`. Its rows carry
 * `.mft`, and a press from one belongs to the folder under the focus, never to
 * a model selection. Delete is the one that matters: from a row it would open a
 * FILE deletion.
 *
 * @param {KeyboardEvent} event
 * @returns {boolean}
 */
export function isInFoldersTree(event) {
  return Boolean(event?.target?.closest?.(".mft"));
}

/**
 * The POSIX directory of a copy's `relpath`, `""` at its folder's root. The
 * scanner stores relpaths with the host's separator.
 *
 * @param {string} relpath
 * @returns {string}
 */
export function relpathDir(relpath) {
  const posix = String(relpath ?? "").replace(/\\/g, "/");
  const at = posix.lastIndexOf("/");
  return at < 0 ? "" : posix.slice(0, at);
}

/**
 * Whether a copy is in an open folder: directly in it, or anywhere under a
 * locked one, which cannot be browsed into.
 *
 * @param {{folder_id: number, relpath: string}} loc
 * @param {{folderId: number, path?: string, locked?: boolean}} scope
 * @returns {boolean}
 */
export function inFolderScopeOf(loc, scope) {
  if (Number(loc?.folder_id) !== Number(scope.folderId)) return false;
  const dir = relpathDir(loc?.relpath);
  const path = scope.path ?? "";
  if (!scope.locked) return dir === path;
  return !path || dir === path || dir.startsWith(`${path}/`);
}

/**
 * The directory ComfyUI loads a copy's kind from that holds it (#2205), the
 * deepest when two do, or null. ComfyUI loads a folder recursively.
 *
 * @param {{folder_id: number, relpath: string}} loc
 * @param {string} kind - a {@link SHELF_TYPES} key, from `shelfTypeOf`.
 * @param {{known: boolean, reads: Array<{folder_id: number, subpath: string,
 *   kind: string}>}} reads - `GET /model-folders/comfyui-reads`.
 * @returns {Object|null} the read.
 */
export function comfyuiReadFor(loc, kind, reads) {
  if (!reads?.known || !kind) return null;
  const dir = relpathDir(loc?.relpath);
  let best = null;
  for (const read of reads.reads ?? []) {
    if (read.kind !== kind) continue;
    if (Number(read.folder_id) !== Number(loc?.folder_id)) continue;
    const sub = read.subpath ?? "";
    if (sub && dir !== sub && !dir.startsWith(`${sub}/`)) continue;
    if (!best || sub.length > best.subpath.length) best = { ...read, subpath: sub };
  }
  return best;
}

/**
 * Whether ComfyUI's own loaders cannot see a copy: ComfyUI loads its kind
 * from some folder on the shelf, and it sits outside every one. Always false
 * when where ComfyUI looks is not known, so nothing is guessed.
 *
 * @param {{folder_id: number, relpath: string}} loc
 * @param {string} kind - a {@link SHELF_TYPES} key; `""` is never misplaced.
 * @param {Object} reads - see {@link comfyuiReadFor}.
 * @returns {boolean}
 */
export function isMisplaced(loc, kind, reads) {
  // Only a kind ComfyUI loads from somewhere on the shelf: otherwise there is
  // no folder to drag it into, and "ComfyUI does not list it" would be noise.
  if (!reads?.known || !reads.reads?.some((read) => read.kind === kind)) {
    return false;
  }
  return comfyuiReadFor(loc, kind, reads) == null;
}

/**
 * What ComfyUI calls a copy in its loader's list: its path under the folder
 * it is loaded from, `flux/x.safetensors`, or null when it is not listed. A
 * registered folder inside ComfyUI's carries its own path as the read's
 * `prefix`.
 *
 * @param {{folder_id: number, relpath: string}} loc
 * @param {string} kind
 * @param {Object} reads - see {@link comfyuiReadFor}.
 * @returns {string|null}
 */
export function comfyuiName(loc, kind, reads) {
  const read = comfyuiReadFor(loc, kind, reads);
  if (!read) return null;
  const posix = String(loc.relpath ?? "").replace(/\\/g, "/");
  const name = read.subpath ? posix.slice(read.subpath.length + 1) : posix;
  return read.prefix ? `${read.prefix}/${name}` : name;
}

/**
 * A folder mark's words: `ComfyUI loads LoRAs from here`. A Type's label is
 * lowercased mid-sentence unless it is spelled with capitals inside it.
 *
 * @param {{label: string}} type - a {@link SHELF_TYPES} entry.
 * @returns {string}
 */
export function comfyuiLoadsTitle(type) {
  const label = /^[A-Z][a-z]*(\s|$)/.test(type.label)
    ? type.label[0].toLowerCase() + type.label.slice(1)
    : type.label;
  return `ComfyUI loads ${label} from here`;
}

/**
 * The Types ComfyUI loads from exactly this directory, for its folder marks.
 *
 * @param {number} folderId
 * @param {string} path - POSIX, relative to the folder, `""` for its root.
 * @param {Object} reads - see {@link comfyuiReadFor}.
 * @returns {Array<Object>} {@link SHELF_TYPES} entries, in their order.
 */
export function comfyuiMarks(folderId, path, reads) {
  if (!reads?.known) return [];
  const kinds = new Set(
    (reads.reads ?? [])
      .filter(
        (read) =>
          Number(read.folder_id) === Number(folderId) &&
          (read.subpath ?? "") === (path ?? ""),
      )
      .map((read) => read.kind),
  );
  return SHELF_TYPES.filter((type) => kinds.has(type.key));
}

/**
 * The Type a known tool files under each directory of its models folder:
 * StabilityMatrix's `Models/<name>` and ComfyUI's `models/<name>`, by the
 * name squashed to lowercase letters and digits. Only for a folder ComfyUI's
 * own reads do not cover; those are asked first.
 */
const LAYOUT_KINDS = {
  stablediffusion: "checkpoint",
  diffusionmodels: "checkpoint",
  checkpoints: "checkpoint",
  unet: "checkpoint",
  lora: "lora",
  loras: "lora",
  lycoris: "lora",
  vae: "vae",
  textencoders: "text_encoder",
  clip: "text_encoder",
  esrgan: "upscaler",
  realesrgan: "upscaler",
  swinir: "upscaler",
  upscalemodels: "upscaler",
  controlnet: "controlnet",
};

/** What a directory name must contain to already say it holds a Type. */
const TYPE_STEMS = {
  checkpoint: "checkpoint",
  lora: "lora",
  vae: "vae",
  text_encoder: "textencoder",
  upscaler: "upscale",
  controlnet: "controlnet",
};

const squash = (name) =>
  String(name ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");

/**
 * The Type a known tool's layout files under a directory, or `""`: a
 * directory straight under a `models` folder, or straight under the registered
 * folder itself, that is StabilityMatrix's (by the folder's `tool`) or
 * ComfyUI's (a `ComfyUI` segment in its path). The registered folder counts
 * whatever its name, because it is stored at its resolved path and a `Models`
 * moved behind a symlink resolves to the name of wherever it went; a library
 * root holds `Models` and `Packages`, never a Type folder, so nothing there is
 * mistaken. A registered Type folder's own subfolders are the owner's sorting.
 * Anything else has no known layout and is never guessed.
 *
 * @param {{path: string, tool?: string}} folder - the registered folder.
 * @param {string} path - POSIX, relative to it, `""` for its root.
 * @returns {string} a {@link SHELF_TYPES} key, or `""`.
 */
export function layoutKind(folder, path) {
  const root = String(folder?.path ?? "")
    .split(/[\\/]+/)
    .filter(Boolean);
  const segments = [...root, ...String(path ?? "").split("/").filter(Boolean)];
  if (segments.length < 2) return "";
  const inRoot =
    segments.length === root.length + 1 && !LAYOUT_KINDS[squash(root.at(-1))];
  if (!inRoot && squash(segments.at(-2)) !== "models") return "";
  const known =
    folder.tool === "StabilityMatrix" ||
    segments.some((segment) => /comfyui/i.test(segment));
  return known ? (LAYOUT_KINDS[squash(segments.at(-1))] ?? "") : "";
}

/**
 * Where models of each Type belong in the registered folders: the directories
 * ComfyUI loads them from, or for a folder none of its reads lands in, the
 * directories its tool's layout names. A layout directory is only found once
 * its parent has been listed.
 *
 * @param {Array<{id: number, path: string, tool?: string}>} folders
 * @param {Map<string, {listing?: Object}>} listings - the folders store's
 *   `dirListings`, keyed `"<folder id>:<path>"`.
 * @param {Object} reads - see {@link comfyuiReadFor}.
 * @returns {Array<{folderId: number, path: string, kind: string,
 *   count: number}>} `count` is the models directly in it, 0 when unlisted.
 */
export function modelDestinations(folders, listings, reads) {
  const out = [];
  for (const folder of folders) {
    const id = Number(folder.id);
    const counts = new Map();
    for (const [key, entry] of listings ?? []) {
      if (!key.startsWith(`${id}:`) || !entry?.listing) continue;
      counts.set(key.slice(`${id}:`.length), entry.listing.direct_model_count);
      for (const dir of entry.listing.dirs ?? []) {
        if (!counts.has(dir.path)) counts.set(dir.path, dir.direct_model_count);
      }
    }
    const read = reads?.known
      ? (reads.reads ?? []).filter((one) => Number(one.folder_id) === id)
      : [];
    const found = read.length
      ? read.map((one) => [one.subpath ?? "", one.kind])
      : [...counts.keys()].map((path) => [path, layoutKind(folder, path)]);
    const seen = new Set();
    for (const [path, kind] of found) {
      if (!kind || seen.has(`${path}\u0000${kind}`)) continue;
      seen.add(`${path}\u0000${kind}`);
      out.push({ folderId: id, path, kind, count: Number(counts.get(path)) || 0 });
    }
  }
  return out;
}

/**
 * The Types a directory holds that its name does not already say, for the
 * second line under it: `DiffusionModels` reads Checkpoints, `Lora` nothing.
 *
 * @param {string} name - the directory's name.
 * @param {Set<string>} kinds - {@link SHELF_TYPES} keys it holds.
 * @returns {Array<Object>} {@link SHELF_TYPES} entries, in their order.
 */
export function unsaidTypes(name, kinds) {
  const said = squash(name);
  return SHELF_TYPES.filter(
    (type) => kinds.has(type.key) && !said.includes(TYPE_STEMS[type.key]),
  );
}

/**
 * Whether every copy is already directly in a destination, so a move there
 * would do nothing: the server skips such a copy rather than moving it.
 *
 * @param {Array<{folder_id: number, relpath: string}>} items
 * @param {{folderId: number, path?: string}} destination
 * @returns {boolean}
 */
export function allAlreadyIn(items, destination) {
  const scope = { folderId: destination.folderId, path: destination.path };
  return items.length > 0 && items.every((item) => inFolderScopeOf(item, scope));
}

/**
 * What a move does on disk: a rename on one drive, or a copy that hands the
 * other drives their bytes back. For the drop tip and the Move dialog.
 *
 * Measured on the drive (`device_id`), as `model_mover.same_device` measures
 * it. A drive that could not be measured is never guessed as "same".
 *
 * @param {{bytesByFolderId: Map<number, number>}} copies - from
 *   {@link movableCopies}.
 * @param {number} destinationFolderId
 * @param {Map<number, Object>} deviceByFolderId - from the folders store.
 * @returns {{how: "rename"|"copy"|"unknown", freed: number,
 *   drives: Array<string>}} `freed` is what leaves the other drives, and
 *   `drives` names them.
 */
export function moveMechanism(copies, destinationFolderId, deviceByFolderId) {
  const target = deviceByFolderId?.get(Number(destinationFolderId));
  if (!target?.device_id) return { how: "unknown", freed: 0, drives: [] };
  let freed = 0;
  const drives = new Set();
  for (const [folderId, bytes] of copies?.bytesByFolderId ?? []) {
    const device = deviceByFolderId.get(Number(folderId));
    if (device?.device_id === target.device_id) continue;
    freed += bytes;
    drives.add(device?.label || device?.mount_point || "the other drive");
  }
  if (!drives.size) return { how: "rename", freed: 0, drives: [] };
  return { how: "copy", freed, drives: [...drives] };
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

/** At most three names, then a count: a delete prompt is not a file listing. */
function nameList(items) {
  const names = items.map((item) => item.name);
  if (names.length <= 3) {
    return names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
  }
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
}

/**
 * What a delete leaves behind, as the sentences its confirmation adds (#1314).
 *
 * Built from `POST /models/companions`. Every clause names files, because
 * "some support files may be unused" is a sentence nobody can act on. None of
 * them says a file is safe to delete: `orphaned` means every model a recipe
 * ran it with is going, which is what the sentence says - never "nothing uses
 * it" - and `unrecorded` then says how many kept base models no workflow names,
 * any of which could. `unknown` and `no_evidence` are said out loud rather than
 * left out, because silence would read as "nothing is affected".
 *
 * @param {Object|null} companions - the route's answer, or null when it could
 *   not be read.
 * @param {number} count - how many models the delete names.
 * @returns {string} Zero or more sentences, space-joined.
 */
export function companionsSentences(companions, count) {
  if (!companions) {
    return "PixlStash could not check which VAEs and text encoders these models use.";
  }
  const said = [];
  const { in_use = [], orphaned = [], shared = [], unknown = [] } = companions;
  // Not lockedDelete's `plural`: tests/test_comfyui_pack_model_mark.py runs this
  // file in Node beside setAppearance.js alone, so it imports nothing else.
  const plural = (items, one, many) => (items.length === 1 ? one : many);
  if (in_use.length) {
    said.push(
      `${nameList(in_use)} ${plural(in_use, "is", "are")} still used by models you are keeping.`,
    );
  }
  if (orphaned.length) {
    said.push(
      `In the workflows PixlStash has read, ${nameList(orphaned)} ran only with ` +
        `models you are deleting; ${plural(orphaned, "it stays", "they stay")} on disk.`,
    );
    const unrecorded = companions.unrecorded ?? 0;
    if (unrecorded) {
      said.push(
        `${unrecorded === 1 ? "1 other base model has" : `${unrecorded} other base models have`} ` +
          `no workflow on record and may still need ${plural(orphaned, "it", "them")}.`,
      );
    }
  }
  if (shared.length) {
    said.push(
      `${nameList(shared)} ${plural(shared, "is", "are")} still used by other models.`,
    );
  }
  if (unknown.length) {
    said.push(
      `Whether ${nameList(unknown)} ${plural(unknown, "is", "are")} still needed ` +
        "is unknown: another file on the shelf has the same name.",
    );
  }
  const unseen = companions.no_evidence?.length ?? 0;
  if (unseen) {
    const subject =
      count === 1
        ? "this model"
        : unseen === count
          ? "these models"
          : `${unseen} of these models`;
    said.push(
      `No workflow PixlStash has read uses ${subject}, so which support files ` +
        `${unseen === 1 ? "it needs" : "they need"} is unknown.`,
    );
  }
  return said.join(" ");
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

// Words that carry no identity in a name, so "World of Wordcraft" marks as WW
// rather than WO. Deliberately without "a": a lone letter in a model name is
// more often a variant label than an article.
const MARK_FILLER_WORDS = new Set([
  "of",
  "in",
  "on",
  "at",
  "by",
  "for",
  "from",
  "to",
  "with",
  "the",
  "and",
]);

/**
 * Up to two letters for a mark, from whatever the row is actually called: the
 * first letters of the first two words that start with a letter, so the
 * version number goes to {@link versionOf} instead of taking a letter's place
 * (`Stable Diffusion 3.5` is SD, not S3). A `v1.0` is a version too, not a
 * word that happens to start with one (`Lightning 4steps V1.0` is LI, not LV).
 *
 * Exported for the workflow card's two-line tiles (`markDetails`).
 */
export function initialsOf(text) {
  const all = String(text || "")
    .split(/[\s_\-.]+/)
    .filter(Boolean);
  // The first word always counts: a leading "In" or "On" is usually part of
  // the name ("In-Context LoRA" is IC), not a joiner between two others.
  const significant = all.filter(
    (word, index) => index === 0 || !MARK_FILLER_WORDS.has(word.toLowerCase()),
  );
  // Skipping must still leave two words to take a letter from each.
  const words = significant.length > 1 ? significant : all;
  const lettered = words.filter(
    (word) => /^\p{L}/u.test(word) && !/^v\d/i.test(word),
  );
  // By code point, not UTF-16 unit, so a letter outside the BMP ("𝐀pple") is
  // taken whole instead of as a lone surrogate.
  if (lettered.length > 1) {
    return ([...lettered[0]][0] + [...lettered[1]][0]).toUpperCase();
  }
  if (lettered.length === 1) {
    return [...lettered[0].replace(/\P{L}/gu, "")]
      .slice(0, 2)
      .join("")
      .toUpperCase();
  }
  return words.length ? [...words[0]].slice(0, 2).join("").toUpperCase() : "?";
}

/**
 * What a mark's version tab shows: the first number in the name. An `x.y`
 * version keeps its dot and its whole minor (`3.5`, `1.0`, `1.10`, `0.05`: one
 * digit of `1.10` would name a version the file is not), and the tab widens to
 * fit, up to two minor digits; anything else is cut to two digits (`2511` is 25, and so is `25.1`,
 * whose major already fills the tab).
 * A zero-padded number (`000002750`) is a counter, not a version, and is
 * skipped; `0.9` is still one. Empty when the name carries no number, and then
 * the mark has no tab.
 */
function versionOf(text) {
  const match = /(?<!\d)(?!0\d)(\d+)(?:\.(\d+))?/.exec(String(text || ""));
  if (!match) return "";
  const [, major, minor] = match;
  // A longer "minor" is a date or a counter, not a version: the major alone.
  return major.length === 1 && minor?.length <= 2
    ? `${major}.${minor}`
    : major.slice(0, 2);
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
 * **The ComfyUI pack ports this** (`web/js/model_mark.js`), so a loader node
 * draws the same mark; `tests/test_comfyui_pack_model_mark.py` fails when the
 * two disagree, so change them together.
 *
 * **Two letters and a version.** Two letters cannot tell `Stable Diffusion
 * 3.5 Large` from `sd3.5 large controlnet`, and neither could the old rule's
 * S5, so the letters say which model and a tab on the corner carries the
 * version (the mark design round's option D2).
 *
 * @param {Object} row - a shelf row.
 * @returns {{color: string, ink: string, initials: string, version: string}}
 *   `color` is an `hsl()` string, not a hex, because it is renormalised rather
 *   than taken from the palette. `ink` travels with it so the pair cannot be
 *   separated. `version` is the tab's digits, `""` for no tab.
 */
export function generatedMark(row) {
  const key = baseModelKey(row);
  const entry = SET_COLORS[hash32(key) % SET_COLORS.length].value;
  const name = modelName(row);
  return {
    color: markBackground(entry),
    ink: markForeground(),
    initials: initialsOf(name.text),
    version: versionOf(name.text),
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

/**
 * The Hide row for a selection of models, or null when nothing is selected.
 *
 * One wording for every menu that offers it (the shelf's and the rail's), and
 * the rule `toggleHiddenModels` runs: it hides the ones still shown, and turns
 * into "show again" only when every one is already hidden. Worded *model* so
 * it cannot be read as the workflow set's own Hide, which sits beside it on a
 * set card's menu.
 *
 * @param {Array<number>} ids - the selected `model.id`s.
 * @param {Set<number>} hiddenIds - the models this library hides.
 * @returns {?{hide: boolean, icon: string, label: string, title: string}}
 */
export function hideModelsVerb(ids, hiddenIds) {
  if (!ids?.length) return null;
  const shown = ids.filter((id) => !hiddenIds.has(id)).length;
  const n = shown || ids.length;
  const models = n === 1 ? "model" : `${n} models`;
  return shown
    ? {
        hide: true,
        icon: "mdi-eye-off-outline",
        label: `Hide ${models}`,
        title:
          "Leaves it off the shelf and out of every menu in this library. " +
          "No file is touched. Show, Hidden models brings it back.",
      }
    : {
        hide: false,
        icon: "mdi-eye-outline",
        label: `Show ${models} again`,
        title: "",
      };
}
