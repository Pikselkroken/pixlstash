// What the Models rail's inspector says and sums (#2168). Kept out of the
// components so each figure and word can be stated, and tested, on its own.

import { relativeDate } from "./snapshots";
import {
  baseModelCell,
  copyPath,
  formatModelSize,
  modelName,
  shelfTypeOf,
} from "./modelShelf";
import { headName, handMadeBase, handMadeName, missingFileNames } from "./workflowSets";
import { activityOf, combinationsOfSet } from "./setActivity";

/** The provide/inject key the rail hands its sections their open or shut memory by. */
export const RAIL_SECTIONS = Symbol("modelsRailSections");

/** The rail head's name for each place on the sidebar. */
export const PLACE_LABELS = {
  sets: "Sets",
  all: "All models",
  favourites: "Favourites",
  recent: "Recently added",
  loose: "In no set yet",
};

/** The three disk buckets the "Disk by type" bar draws, in its order. */
export const DISK_BUCKETS = [
  { key: "checkpoint", label: "Checkpoints", tone: "tertiary", types: ["checkpoint"] },
  { key: "encoders", label: "Encoders + VAEs", tone: "neutral", types: ["text_encoder", "vae"] },
  { key: "lora", label: "LoRAs", tone: "secondary", types: ["lora"] },
  { key: "other", label: "Other", tone: "neutral-light", types: [] },
];

/** How many files *Largest files* lists. */
export const LARGEST_COUNT = 5;

/**
 * The rail head's view name: the training runs page, the open folder, else
 * the place.
 *
 * @param {{runs: boolean, place: string, folderName?: string}} view
 */
export function viewName({ runs, place, folderName = "" }) {
  if (runs) return "Training runs";
  if (place === "folder") return folderName || "Folder";
  return PLACE_LABELS[place] ?? "";
}

/**
 * "1 model" / "12 models", for a head that counts a mix as "N selected".
 *
 * @param {number} sets - sets picked.
 * @param {number} models - models picked.
 */
export function selectionCount(sets, models) {
  if (sets && models) return `${(sets + models).toLocaleString()} selected`;
  if (sets) return `${sets.toLocaleString()} ${sets === 1 ? "set" : "sets"}`;
  return `${models.toLocaleString()} ${models === 1 ? "model" : "models"}`;
}

/** Sum of recorded file sizes, in bytes. A row with no size counts as nothing. */
export function totalBytes(rows) {
  return rows.reduce((sum, row) => sum + (Number(row?.file_size) || 0), 0);
}

/**
 * The "Disk by type" bar: each bucket's bytes and its share of the total.
 *
 * @param {Array<Object>} rows - model rows, each with `file_size` and `file_kind`.
 * @returns {Array<{key: string, label: string, tone: string, bytes: number, pct: number}>}
 */
export function diskByType(rows) {
  const total = totalBytes(rows);
  const bytes = new Map(DISK_BUCKETS.map((bucket) => [bucket.key, 0]));
  for (const row of rows) {
    const type = shelfTypeOf(row);
    const bucket =
      DISK_BUCKETS.find((candidate) => candidate.types.includes(type)) ??
      DISK_BUCKETS.at(-1);
    bytes.set(bucket.key, bytes.get(bucket.key) + (Number(row?.file_size) || 0));
  }
  return DISK_BUCKETS.map((bucket) => {
    const sum = bytes.get(bucket.key);
    return {
      key: bucket.key,
      label: bucket.label,
      tone: bucket.tone,
      bytes: sum,
      pct: total ? (100 * sum) / total : 0,
    };
  });
}

/** The figures a disk bar's accessible name states: "Checkpoints 11.9 GB, …". */
export function diskLabel(buckets) {
  return buckets.map((b) => `${b.label} ${formatModelSize(b.bytes)}`).join(", ");
}

/** The largest files of a view, biggest first. */
export function largestFiles(rows, count = LARGEST_COUNT) {
  return rows
    .filter((row) => Number(row?.file_size) > 0)
    .sort((a, b) => Number(b.file_size) - Number(a.file_size))
    .slice(0, count);
}

/** The warning under the figures: how many cards lack their checkpoint. */
export function missingNote(count) {
  return count === 1
    ? "1 set is missing its checkpoint."
    : `${count.toLocaleString()} sets are missing their checkpoints.`;
}

/** The verdict line at the foot of *Models* in a set. */
export function setVerdict(missing) {
  return missing
    ? "One file is missing: replace it in the workflows that load it."
    : "Each of these has run with each other.";
}

/** The base model a row was trained or downloaded for, as the shelf reads it. */
export function baseOf(row) {
  return baseModelCell(row).text;
}

/**
 * Where a model file is: the folder of its first copy on disk.
 *
 * @param {Object} row - a model row with `locations`.
 * @returns {string} the full path, or "" when no copy is on disk.
 */
export function modelFolder(row) {
  const present = (row?.locations ?? []).find((loc) => loc?.state === "present");
  return present ? copyPath(present) : "";
}

/** The name a row is called on the rail: the shelf's own reading of it. */
export function rowName(row) {
  return modelName(row).text || row?.filename || "";
}

/** A set's name as the grid and the rail both say it. */
export function setName(group) {
  return group.set ? handMadeName(group.set) : headName(group.head) || "Unnamed set";
}

/**
 * One set, as the rail's *Models*, *About* and *Works in* read it.
 *
 * @param {Object} group - a `setGroups` entry (evidence or missing), or a
 *   `handMadeGroups` entry (`set` set).
 * @param {{rowsById: Map<number, Object>, combinations: Array<Object>}} context
 */
export function describeSet(group, { rowsById, combinations }) {
  const hand = Boolean(group.set);
  const members = hand
    ? (group.set.members ?? []).map((m) => ({
        id: m.id ?? null,
        key: `m${m.id ?? m.name ?? m.filename}`,
        name: m.name || m.filename || m.label || "",
        onShelf: Boolean(m.on_shelf),
        size: rowsById.get(m.id)?.file_size ?? null,
      }))
    : (group.models ?? []).map((m) => ({
        id: m.id,
        key: `m${m.id}`,
        name: m.name || m.filename || "",
        onShelf: Boolean(m.on_shelf ?? true),
        size: rowsById.get(m.id)?.file_size ?? null,
      }));
  // Every file the card flags as missing, not only a missing head: the same
  // list draws the card's "Missing a file" badge, so the rail cannot disagree.
  if (!hand) {
    for (const name of missingFileNames(group)) {
      const isHead = group.head?.missing && name === group.head.name;
      members.push({
        id: null,
        key: `missing:${name}`,
        name: isHead ? headName(group.head) : name,
        onShelf: false,
        size: null,
      });
    }
  }
  const activity = activityOf(combinationsOfSet(group, combinations));
  const base = hand ? handMadeBase(group.set) : baseOf(group.head ?? {});
  return {
    key: group.key,
    name: setName(group),
    base: base || "–",
    members,
    missing: members.some((m) => !m.onShelf),
    size: members.reduce((sum, m) => sum + (Number(m.size) || 0), 0),
    madeBy: hand ? "You" : "Read from your pictures",
    lastUsed: activity.lastUsedAt ? relativeDate(activity.lastUsedAt) : "never",
    // The evidence behind the set, which the card no longer prints.
    recipes: Number(hand ? group.set.recipes : group.recipes) || 0,
    runs: Number(hand ? group.set.history_runs : group.historyRuns) || 0,
    activity,
  };
}
