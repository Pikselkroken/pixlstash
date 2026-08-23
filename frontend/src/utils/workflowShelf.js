// Pure shaping for the Workflows view (implementation plan §F1).
//
// Everything here is a function of the payload and the view axes, so the whole
// of what the list decides — what a row is called, which models it names, how
// it is grouped, sorted and filtered — is testable without mounting anything.
// The component owns the DOM and the store owns the fetch; neither owns a rule.
//
// The one decision worth stating up front: **nothing in v1.11 has a name.**
// Naming a workflow is a later step, so every row falls back to a descriptor
// built from what the graph actually says — its base model, how many adapters
// it loads and how big it is. That is the "not named" state from the design's
// `States.dc.html`, and it is the ordinary case rather than the exception.

/** Widget names whose value is the checkpoint or diffusion model. */
const BASE_WIDGETS = new Set([
  "ckpt_name",
  "unet_name",
  "diffusion_model",
  "model_path",
]);

/** File extensions that mean "a picture the graph loads", not a model. */
const IMAGE_SUFFIXES = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"];

/**
 * What an asset is, from the input it was given to.
 *
 * The hasher records `(widget, filename)` and deliberately invents no taxonomy
 * (`pixlstash/services/workflow_hash.py`), so the classification lives here,
 * where it is one small table a reader can check against a real graph.
 *
 * @param {{widget?: string, name?: string}} asset
 * @returns {"base"|"lora"|"image"|"other"}
 */
export function assetKind(asset) {
  const widget = String(asset?.widget || "").toLowerCase();
  const name = String(asset?.name || "").toLowerCase();
  if (IMAGE_SUFFIXES.some((suffix) => name.endsWith(suffix))) return "image";
  if (widget.includes("lora")) return "lora";
  if (BASE_WIDGETS.has(widget)) return "base";
  return "other";
}

/** A filename without its extension — what a person calls the model. */
export function modelStem(filename) {
  const name = String(filename || "");
  const cut = name.lastIndexOf(".");
  return cut > 0 ? name.slice(0, cut) : name;
}

/**
 * The assets that are models, deduplicated, base model first.
 *
 * Pictures a graph loads are excluded: they are inputs rather than what the
 * workflow is made of, and the Models column is answering "what does this
 * need".
 *
 * @param {Array<Object>} assets
 * @returns {Array<{widget: string, name: string, kind: string}>}
 */
export function modelAssets(assets) {
  const seen = new Set();
  const out = [];
  for (const asset of assets || []) {
    const kind = assetKind(asset);
    if (kind === "image") continue;
    const name = String(asset?.name || "");
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push({ widget: String(asset?.widget || ""), name, kind });
  }
  const rank = { base: 0, lora: 1, other: 2 };
  return out.sort(
    (a, b) =>
      (rank[a.kind] ?? 3) - (rank[b.kind] ?? 3) || a.name.localeCompare(b.name),
  );
}

/**
 * The base model's stem, or `null` when there is no single answer.
 *
 * A row's `assets` are the **set** of files its variants reach for, so a
 * topology whose variants each pick a different checkpoint names several — and
 * picking one of them to head the row would be a lie of specificity about which
 * model this workflow uses. `null` is the honest answer there; the Models cell
 * still lists what it found.
 *
 * @param {Array<Object>} assets
 * @returns {string|null}
 */
export function baseModelName(assets) {
  const bases = modelAssets(assets).filter((asset) => asset.kind === "base");
  return bases.length === 1 ? modelStem(bases[0].name) : null;
}

/**
 * How many distinct adapter FILES a topology's variants name between them.
 *
 * **Not how many one run loads** — see `adapter_slots` on the payload, which is
 * the number the descriptor wants. The worst family in the owner's library is
 * 159 character LoRAs in one slot: this returns 159 and the slot count returns
 * 1, and a row built from this one would say it loads all of them at once.
 */
export function namedAdapterCount(assets) {
  return modelAssets(assets).filter((asset) => asset.kind === "lora").length;
}

/**
 * What the Models column reads.
 *
 * Two names and then a count, because the column is a glance rather than an
 * inventory and the inspector carries the full list.
 *
 * **An empty answer is a state, and it says which one it is not.** A recipe
 * whose asset rows were deleted to forget a model's name keeps its graph and
 * loses only the ability to say what it used, so the cell says there are no
 * names rather than drawing a blank — but it does NOT claim they were
 * forgotten, because nothing in the payload distinguishes that from a graph
 * that names no model at all.
 *
 * @param {Array<Object>} assets
 * @param {number} [max=2]
 * @returns {string}
 */
export function modelSummary(assets, max = 2) {
  const models = modelAssets(assets);
  if (!models.length) return "";
  const shown = models.slice(0, max).map((asset) => modelStem(asset.name));
  const rest = models.length - shown.length;
  return rest > 0 ? `${shown.join(", ")}, +${rest}` : shown.join(", ");
}

/**
 * The line a row is identified by, when nothing has named it.
 *
 * Reads as a sentence about the graph — "realvisxl, 2 LoRAs, 47 nodes" — and
 * drops each clause it cannot fill rather than printing an empty one.
 *
 * @param {Object} row - a `/workflows` row.
 * @returns {string}
 */
export function workflowDescriptor(row) {
  const parts = [];
  const base = baseModelName(row?.assets);
  if (base) parts.push(base);
  // `adapter_slots`, never a count of the names: the names are the union across
  // every variant, so counting them describes the 159-LoRA family as loading
  // 159 adapters in one run. The slot count is a property of the graph, which
  // is what this line is describing. A variant row has no slot count of its
  // own — every one of its names IS loaded — so it falls back to counting.
  const loras =
    row?.adapter_slots != null
      ? Number(row.adapter_slots) || 0
      : namedAdapterCount(row?.assets);
  if (loras) parts.push(loras === 1 ? "1 LoRA" : `${loras} LoRAs`);
  const nodes = Number(row?.node_count) || 0;
  parts.push(nodes === 1 ? "1 node" : `${nodes} nodes`);
  return parts.join(", ");
}

/** Counts are grouped the way every other count in the app is. */
export function groupedNumber(n) {
  return Number(n || 0)
    .toLocaleString("en-GB")
    .replace(/,/g, " ");
}

/** The sort axes the list offers. `used` is the default: most pictures first. */
export const SORT_KEYS = ["used", "recent", "variants", "nodes", "added"];

/** The grouping axes. `none` is the flat list the design draws. */
export const GROUP_BY_KEYS = ["none", "base_model", "size"];

/** Which rows are listed. `all` is the default. */
export const SHOW_KEYS = ["all", "in_use", "unused"];

/** The key each sort reads, so a comparator is one line. */
const SORT_VALUE = {
  used: (row) => Number(row.pictures) || 0,
  recent: (row) => Date.parse(row.last_used || "") || 0,
  variants: (row) => Number(row.variants) || 0,
  nodes: (row) => Number(row.node_count) || 0,
  added: (row) => Date.parse(row.first_seen_at || "") || 0,
};

/**
 * Sort rows by one axis.
 *
 * Ties break on the topology hash, which is stable and unique, so the list
 * never reorders under itself between two reads of the same data — a row that
 * moved for no reason is indistinguishable from a row whose count changed.
 *
 * @param {Array<Object>} rows
 * @param {string} sortKey - one of {@link SORT_KEYS}.
 * @param {boolean} [descending=true]
 * @returns {Array<Object>} a new array.
 */
export function sortWorkflows(rows, sortKey, descending = true) {
  const read = SORT_VALUE[sortKey] || SORT_VALUE.used;
  const direction = descending ? -1 : 1;
  return [...(rows || [])].sort((a, b) => {
    const delta = read(a) - read(b);
    if (delta) return delta * direction;
    return String(a.topology_hash).localeCompare(String(b.topology_hash));
  });
}

/**
 * Narrow the list to what `Show` asks for.
 *
 * "Unused" is a real question rather than a tidy inverse: a workflow with no
 * kept pictures is exactly what the hub exists to preserve, and it is also the
 * shortlist for anything that ends retention later.
 *
 * @param {Array<Object>} rows
 * @param {string} show - one of {@link SHOW_KEYS}.
 * @returns {Array<Object>}
 */
export function filterWorkflows(rows, show) {
  if (show === "in_use") {
    return (rows || []).filter((row) => (Number(row.pictures) || 0) > 0);
  }
  if (show === "unused") {
    return (rows || []).filter((row) => !(Number(row.pictures) || 0));
  }
  return [...(rows || [])];
}

/** The band a row falls in, per axis. `null` means "not grouped". */
function groupKeyOf(row, groupBy) {
  if (groupBy === "base_model") return baseModelName(row?.assets);
  if (groupBy === "size") {
    const nodes = Number(row?.node_count) || 0;
    if (nodes <= 15) return "small";
    if (nodes <= 50) return "medium";
    return "large";
  }
  return null;
}

/** What each band is called. Keys outside this map are their own label. */
const GROUP_LABELS = {
  small: "Up to 15 nodes",
  medium: "16 to 50 nodes",
  large: "More than 50 nodes",
};

/** The order the size bands read in; every other axis sorts alphabetically. */
const SIZE_ORDER = ["small", "medium", "large"];

/** The band a row with nothing to group by falls in. */
export const UNSET_GROUP_KEY = "__unset__";

/**
 * Cut the list into bands.
 *
 * One band with a `null` key when `groupBy` is `none`, so the component draws
 * grouped and ungrouped through the same loop instead of branching twice.
 *
 * @param {Array<Object>} rows - already sorted and filtered.
 * @param {string} groupBy - one of {@link GROUP_BY_KEYS}.
 * @returns {Array<{key: string|null, label: string, rows: Array<Object>}>}
 */
export function groupWorkflows(rows, groupBy) {
  const list = rows || [];
  if (!groupBy || groupBy === "none") {
    return [{ key: null, label: "Workflows", rows: [...list] }];
  }
  const bands = new Map();
  for (const row of list) {
    const key = groupKeyOf(row, groupBy) || UNSET_GROUP_KEY;
    if (!bands.has(key)) bands.set(key, []);
    bands.get(key).push(row);
  }
  const keys = [...bands.keys()].sort((a, b) => {
    // The unnamed band sinks, whatever the axis: it is the least specific
    // answer and a reader scanning for a base model should not meet it first.
    if (a === UNSET_GROUP_KEY) return 1;
    if (b === UNSET_GROUP_KEY) return -1;
    if (groupBy === "size")
      return SIZE_ORDER.indexOf(a) - SIZE_ORDER.indexOf(b);
    return a.localeCompare(b);
  });
  return keys.map((key) => ({
    key,
    label:
      key === UNSET_GROUP_KEY
        ? groupBy === "base_model"
          ? "No model named"
          : "Ungrouped"
        : GROUP_LABELS[key] || key,
    rows: bands.get(key),
  }));
}

/**
 * Which of the four states the list is in.
 *
 * Three of them are the first thing a new user sees, and the list alone cannot
 * tell them apart: "correct and nearly empty" is not a failure, and each state
 * has to say which of the three it is rather than wearing an error hue.
 *
 * @param {{pictures?: number, scanned?: number}} scan
 * @param {number} rowCount
 * @returns {"unscanned"|"scanning"|"none"|"listed"}
 */
export function libraryState(scan, rowCount) {
  const pictures = Number(scan?.pictures) || 0;
  const scanned = Number(scan?.scanned) || 0;
  if (rowCount > 0) return "listed";
  // An empty library has been "read" in the only sense that matters: there is
  // nothing left to look at, so it is the looked-and-found-nothing state rather
  // than the not-looked-yet one, which would offer a scan that would do nothing.
  if (pictures === 0) return "none";
  if (scanned === 0) return "unscanned";
  if (scanned < pictures) return "scanning";
  return "none";
}
