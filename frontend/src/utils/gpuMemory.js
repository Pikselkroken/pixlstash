// Pure helpers for the GPU-memory surfaces: the Tasks tab's VRAM bars, the
// workflow card's need pill, the Workflow tab's peaks, and the run-id matching
// every reader of a POST /workflows/run answer shares.
//
// Every figure arrives from the backend in MB. The text a row shows is the
// backend's own (`waiting_reason.text`, `headline`); these helpers only format
// the figures and place them.

const SEGMENT_KINDS = ["pixlstash", "comfyui", "aitoolkit"];

/** @param {unknown} value a figure from the wire, MB or null. */
function nonNegative(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

/**
 * A figure in MB as "9.8 GB". Null and non-numbers have no figure, so the
 * caller decides what to show instead.
 *
 * @param {number|null|undefined} mb
 * @returns {string}
 */
export function formatGb(mb) {
  if (mb == null || !Number.isFinite(Number(mb))) return "";
  return `${(Number(mb) / 1024).toFixed(1)} GB`;
}

/**
 * A ComfyUI run id with the `comfyui-` prefix the plugin events carry taken
 * off, so an event's id and a POST's answer compare as equal. "" for none.
 *
 * @param {unknown} id
 * @returns {string}
 */
export function normalizeRunId(id) {
  if (id == null) return "";
  return String(id).replace(/^comfyui-/i, "");
}

/**
 * Every id a run is known by, normalised: its `run_id` (always present) and its
 * `prompt_id` (null while the run is held, and set once it is sent to ComfyUI).
 * A reader matches on any of them, never on one alone.
 *
 * @param {{run_id?: ?string, prompt_id?: ?string}} entry
 * @returns {string[]}
 */
export function runKeys(entry) {
  return [entry?.run_id, entry?.prompt_id]
    .map(normalizeRunId)
    .filter((key) => key !== "");
}

/**
 * The one key a run is tracked under: its `run_id`, falling back to its
 * `prompt_id` for an answer that predates run ids.
 *
 * @param {{run_id?: ?string, prompt_id?: ?string}} entry
 * @returns {string}
 */
export function runKey(entry) {
  return runKeys(entry)[0] ?? "";
}

/**
 * Whether *entry* is the run *id* names, whichever of its ids the caller has.
 *
 * @param {{run_id?: ?string, prompt_id?: ?string}} entry
 * @param {unknown} id
 * @returns {boolean}
 */
export function runMatches(entry, id) {
  const key = normalizeRunId(id);
  return key !== "" && runKeys(entry).includes(key);
}

/**
 * How one device's memory splits into the bar's segments, in MB and in order:
 * what is in use (PixlStash, ComfyUI, AI Toolkit, other apps), then what is free
 * but kept back: each tenant's reservation not yet in use (`reserved: true`,
 * drawn in that tenant's colour) and the spare margin PixlStash keeps. Whatever
 * is free beyond that is the empty track, so no segment is drawn for it.
 *
 * The holders are the backend's `resources.holders`. Memory in use that no
 * holder accounts for is other apps' too, so the segments always add up to the
 * card's used memory. `device.reserved_by` says whose reservations the kept-back
 * memory holds; a backend that does not send it draws all of it as spare.
 *
 * @param {{uuid: string, total_mb: number, free_mb?: number, used_mb?: number, budget_mb?: number, reserved_by?: Object<string, number>}} device
 * @param {Array<{device: string, tenant: string, used_mb: number, label?: string}>} [holders]
 * @returns {{total: number, free: number, segments: Array<{key: string, kind: string, mb: number, pct: number, holders: string[], reserved: boolean}>}}
 */
export function deviceVramBar(device, holders = []) {
  const total = nonNegative(device?.total_mb);
  const free = Math.min(
    total,
    device?.free_mb != null
      ? nonNegative(device.free_mb)
      : nonNegative(total - nonNegative(device?.used_mb)),
  );
  const used = total - free;
  const budget = Math.min(free, nonNegative(device?.budget_mb ?? free));
  const spare = free - budget;

  const byKind = new Map();
  const labels = new Map();
  let accounted = 0;
  for (const holder of holders) {
    if (holder?.device !== device?.uuid) continue;
    const mb = nonNegative(holder.used_mb);
    const kind = SEGMENT_KINDS.includes(holder.tenant) ? holder.tenant : "other";
    byKind.set(kind, (byKind.get(kind) ?? 0) + mb);
    if (!labels.has(kind)) labels.set(kind, []);
    if (holder.label) labels.get(kind).push(holder.label);
    accounted += mb;
  }
  // Memory in use that no holder claims is someone else's.
  const unclaimed = Math.max(0, used - accounted);
  if (unclaimed > 0) {
    byKind.set("other", (byKind.get("other") ?? 0) + unclaimed);
  }

  const segments = [];
  const push = (kind, mb, reserved = false) => {
    if (mb <= 0) return;
    segments.push({
      key: reserved ? `${kind}-reserved` : kind,
      kind,
      mb,
      pct: total > 0 ? (mb / total) * 100 : 0,
      holders: reserved ? [] : (labels.get(kind) ?? []),
      reserved,
    });
  };
  for (const kind of [...SEGMENT_KINDS, "other"]) push(kind, byKind.get(kind) ?? 0);
  // The kept-back memory is the reservations first; what is left is spare.
  // Over-committed (reservations beyond it), each is scaled down alike, so no
  // tenant drops off the bar for coming later in the order.
  const reserved = SEGMENT_KINDS.map((kind) => nonNegative(device?.reserved_by?.[kind]));
  const reservedTotal = reserved.reduce((sum, mb) => sum + mb, 0);
  const scale = reservedTotal > spare ? spare / reservedTotal : 1;
  SEGMENT_KINDS.forEach((kind, i) => push(kind, reserved[i] * scale, true));
  push("spare", scale < 1 ? 0 : spare - reservedTotal);
  return { total, free, segments };
}

/**
 * The bar of a workflow's last measured peaks (`vram.peaks`, newest first),
 * scaled to the tallest of them. The ones the need is taken from are `used`;
 * an out-of-memory run is `oom`.
 *
 * @param {Array<{peak_mb: number, pixels?: number, outcome: string, used?: boolean}>} peaks
 * @returns {Array<{peak_mb: number, pixels: ?number, outcome: string, used: boolean, pct: number}>}
 */
export function peakBars(peaks) {
  const list = Array.isArray(peaks) ? peaks : [];
  const tallest = Math.max(1, ...list.map((peak) => nonNegative(peak.peak_mb)));
  return list.map((peak) => ({
    peak_mb: nonNegative(peak.peak_mb),
    pixels: peak.pixels ?? null,
    outcome: peak.outcome,
    used: Boolean(peak.used),
    pct: (nonNegative(peak.peak_mb) / tallest) * 100,
  }));
}

/**
 * Where a request sits on its GPU's bar, for the Run anyway dialog: the
 * card's physical memory is the whole bar, and the request is laid over it from
 * where the free memory starts. A request that is more than the free memory
 * sticks out past the end of the bar, and `overMb` is that part.
 *
 * @param {{uuid: string, total_mb: number, free_mb?: number, used_mb?: number, budget_mb?: number}} device
 * @param {Array<Object>} holders
 * @param {number} needMb what the waiting run asks for
 * @returns {{total: number, free: number, segments: Array<Object>, startPct: number, needPct: number, overMb: number, overPct: number}}
 */
export function requestOnBar(device, holders, needMb) {
  const bar = deviceVramBar(device, holders);
  const need = nonNegative(needMb);
  const overMb = Math.max(0, need - bar.free);
  const pct = (mb) => (bar.total > 0 ? (mb / bar.total) * 100 : 0);
  const startPct = pct(bar.total - bar.free);
  return {
    total: bar.total,
    free: bar.free,
    segments: bar.segments,
    startPct,
    needPct: pct(need),
    overMb,
    overPct: Math.max(0, startPct + pct(need) - 100),
  };
}
