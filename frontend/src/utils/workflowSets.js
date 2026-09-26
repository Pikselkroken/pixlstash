// The model shelf's Workflow set axis (#1438): turning the combinations
// `GET /models/workflow-sets` serves into the groups the grid draws.
//
// **A card is one base model, and its tray holds the MODELS that have run with
// it.** One card per checkpoint (or per diffusion file, where a graph loads one
// instead), carrying the union of everything it has co-occurred with across all
// of its recipes. So the grid is as long as the list of base models that have
// made a picture and no longer, and a model appears in every group it has served
// - a VAE shared by three checkpoints is in three trays, which is the overlap the
// row list's sticky bands could not express.
//
// **What a union gives up, and why this file keeps the combinations anyway.** A
// group is not reproducible: two VAEs can both be in one tray having never run
// together, and the union cannot say which. That is a real cost of grouping this
// way, and the tray says so in words rather than letting a reader assume
// otherwise. The per-combination evidence is therefore NOT discarded on the way
// in - `worksWith` reads it to answer pairwise questions exactly, and a member's
// own recipe and picture counts come from the combinations that name it. Union
// for the card, combinations for every claim.
//
// **Co-occurrence is evidence; its absence is not.** Nothing here may hide a
// model and nothing may infer a pairing: two models never seen together are not
// drawn as a pair, and that is not a claim they cannot work. The models no recipe
// names arrive from the server under `no_set` and get a card of their own.

import { fileKindLabel } from "./modelShelf";

/**
 * How many covers a card's mosaic draws.
 *
 * Three, the depth the server serves per combination (`SET_COVER_DEPTH`): a group
 * pools its combinations' covers and then cuts back to this, so a group built
 * from six recipes is not six bitmaps for three cells.
 */
const COVER_DEPTH = 3;

/**
 * The file a combination is named after: the head of the server's own order.
 *
 * The server sorts a combination's members checkpoint-first, then unclassified,
 * then the support files, then the adapters, so the head is the checkpoint where
 * there is one and the diffusion file where there is not (Flux, Wan). No fallback
 * is invented here: reading the head is what makes that rule one decision in one
 * place rather than two that can disagree.
 */
export function headModel(combination) {
  return (combination.models ?? [])[0] ?? null;
}

/**
 * Sort by the weight of the evidence behind each entry, strongest first.
 *
 * The server already orders combinations this way; re-applied because a group's
 * covers and its `sets` list are read in this order, so a caller that filtered or
 * concatenated payloads must not be able to change what a card leads with.
 */
function byEvidence(entries) {
  return [...entries].sort(
    (a, b) =>
      (b.picture_count ?? b.pictures ?? 0) -
        (a.picture_count ?? a.pictures ?? 0) ||
      (b.recipes ?? 0) - (a.recipes ?? 0) ||
      String(a.key).localeCompare(String(b.key)),
  );
}

/**
 * Group combinations by the file they are named after.
 *
 * One group per head model, holding the union of every member across its
 * combinations. Keyed on the head's ID and never its name: two shelf rows can
 * carry the same basename, and merging them would draw one card claiming both
 * files' pictures.
 *
 * Each member carries the evidence for ITSELF rather than the group's totals -
 * `recipes` and `pictures` counted over the combinations in this group that name
 * that model, so a LoRA used once inside a group of forty pictures says "1 recipe"
 * rather than inheriting the forty. `otherSets` is how many OTHER groups it
 * appears in, which is what lets a tray mark a shared VAE as shared and a
 * single-purpose one as its own.
 *
 * @param {Array<Object>} combinations - from `GET /models/workflow-sets`.
 * @returns {Array<Object>} groups, strongest evidence first.
 */
export function setGroups(combinations) {
  const byHead = new Map();
  for (const combination of byEvidence(combinations ?? [])) {
    const head = headModel(combination);
    if (!head) continue;
    const key = `model:${head.id}`;
    let group = byHead.get(key);
    if (!group) {
      group = { key, head, combinations: [], recipes: 0, pictures: 0 };
      byHead.set(key, group);
    }
    group.combinations.push(combination);
    group.recipes += combination.recipes ?? 0;
    group.pictures += combination.picture_count ?? 0;
  }

  const groups = byEvidence([...byHead.values()]);
  // How many groups each model is in, counted across all of them before any one
  // group is shaped: `otherSets` is a fact about the whole grid.
  const groupsPerModel = new Map();
  for (const group of groups) {
    for (const id of memberIds(group)) {
      groupsPerModel.set(id, (groupsPerModel.get(id) ?? 0) + 1);
    }
  }

  return groups.map((group) => ({
    ...group,
    models: members(group, groupsPerModel),
    covers: pooledCovers(group.combinations),
  }));
}

/** Every model id in a group, across its combinations. */
function memberIds(group) {
  const ids = new Set();
  for (const combination of group.combinations) {
    for (const model of combination.models ?? []) ids.add(model.id);
  }
  return ids;
}

/**
 * A group's members, each with its own evidence and how widely it is shared.
 *
 * Ordered as the server orders a combination's members - the head first, then
 * unclassified, the support files, the adapters - by reading the best position
 * each model has taken in any of the group's combinations rather than re-deciding
 * that ranking here.
 */
function members(group, groupsPerModel) {
  const found = new Map();
  for (const combination of group.combinations) {
    (combination.models ?? []).forEach((model, position) => {
      const seen = found.get(model.id) ?? {
        ...model,
        kindLabel: memberKindLabel(model),
        recipes: 0,
        pictures: 0,
        ambiguous: false,
        position,
      };
      seen.recipes += combination.recipes ?? 0;
      seen.pictures += combination.picture_count ?? 0;
      // One witness that could not pin the file down is enough to say so; a
      // cleaner second witness does not unmake the first.
      seen.ambiguous = seen.ambiguous || Boolean(model.ambiguous);
      seen.position = Math.min(seen.position, position);
      found.set(model.id, seen);
    });
  }
  return [...found.values()]
    .map((model) => ({
      ...model,
      otherSets: Math.max((groupsPerModel.get(model.id) ?? 1) - 1, 0),
    }))
    .sort(
      (a, b) =>
        a.position - b.position ||
        b.recipes - a.recipes ||
        a.name.localeCompare(b.name),
    );
}

/**
 * A group's cover strip: its combinations' covers, best first, deduplicated.
 *
 * The RECORDS, not URLs. `{picture_id, version}` is what the payload sends and
 * what the card turns into a `src` with `pictureThumbnailUrl` - this module stays
 * free of the api layer, and that path stays spelled in one place. Deduplicated by
 * picture, because two recipes in one group can nominate the same best one.
 */
function pooledCovers(combinations) {
  return [
    ...new Map(
      combinations
        .flatMap((combination) => combination.covers ?? [])
        .map((cover) => [cover.picture_id, cover]),
    ).values(),
  ].slice(0, COVER_DEPTH);
}

/** "3 recipes", "1 recipe" - the evidence behind a group or one of its members. */
export function recipeCount(recipes) {
  const n = Number(recipes) || 0;
  return n === 1 ? "1 recipe" : `${n} recipes`;
}

/** "3 pictures", "1 picture". */
export function pictureCount(pictures) {
  const n = Number(pictures) || 0;
  return n === 1 ? "1 picture" : `${n} pictures`;
}

/**
 * How widely a member is shared, in the words the tray uses.
 *
 * The count rather than the list: a VAE in seventeen groups reads as a number,
 * and which seventeen is one click away in `Works with`.
 */
export function sharingLabel(otherSets) {
  if (!otherSets) return "Only in this set";
  return otherSets === 1
    ? "Also in 1 other set"
    : `Also in ${otherSets} other sets`;
}

/**
 * One group as `ModelSetCard` draws it.
 *
 * Three meta rows, which is what the approved design gives this card: the name
 * with its kind, the per-kind counts, and the facts. The per-kind row answers the
 * question a grid is scanned for - what SHAPE is this set, one VAE and two
 * encoders or none at all - without opening anything.
 *
 * @param {Object} group - one entry from {@link setGroups}.
 * @returns {Object} a card for `ModelSetCard`.
 */
export function setCard(group) {
  const models = group.models ?? [];
  return {
    key: group.key,
    // The head names the card, so a Flux or Wan group with no checkpoint row is
    // named by its diffusion file without this file inventing a second rule.
    name: group.head?.name || "Unnamed set",
    kindLabel: memberKindLabel(group.head),
    kinds: kindCounts(models.filter((model) => model.id !== group.head?.id)),
    facts: [
      models.length === 1 ? "1 model" : `${models.length} models`,
      recipeCount(group.recipes),
      pictureCount(group.pictures),
    ],
    pictures: group.pictures,
    recipes: group.recipes,
    covers: group.covers ?? [],
    size: models.length,
  };
}

/**
 * The per-kind tally the card's second row draws: `VAE 2`, `Text enc 1`.
 *
 * In the members' own order rather than alphabetically, so the row reads in the
 * sequence the tray does. A count of one is still written out, because `VAE`
 * alone would read as a label rather than as a tally beside `LoRA 3`.
 */
export function kindCounts(models) {
  const tally = new Map();
  for (const model of models) {
    const label = model.kindLabel || memberKindLabel(model) || "Other";
    tally.set(label, (tally.get(label) ?? 0) + 1);
  }
  return [...tally.entries()].map(([label, count]) => `${label} ${count}`);
}

/**
 * What a member's kind is called.
 *
 * `fileKindLabel` names every kind but the adapter, which it leaves blank because
 * the row list names an adapter by its algorithm instead. A set has no algorithm
 * column, so the word is supplied here rather than leaving the one kind a reader
 * most wants to see unlabelled.
 */
export function memberKindLabel(model) {
  return (
    fileKindLabel(model?.kind) || (model?.kind === "adapter" ? "LoRA" : "")
  );
}

/**
 * The companions of one model: everything it has been seen beside, ranked.
 *
 * **Read from the COMBINATIONS, never from a group's union.** That is the whole
 * reason this module keeps them: a union puts every model a checkpoint has ever
 * loaded into one bag, so answering from it would report two VAEs as companions
 * of each other on the strength of sharing a checkpoint. Ranked by how many
 * recipes back each pairing, which is the only thing co-occurrence can measure;
 * the caller draws the bar from `share` and the number from `recipes`.
 *
 * **A model missing from this list has not been ruled out.** It has simply never
 * been in the same picture's recipe, which is a fact about what has been tried
 * here and not about what works - so every caller says so beside the list.
 *
 * @param {Array<Object>} combinations
 * @param {number} modelId
 * @returns {{companions: Array<Object>, recipes: number, sets: Array<Object>}}
 *   `sets` is the combinations the model is in, strongest first.
 */
export function worksWith(combinations, modelId) {
  const sets = byEvidence(
    (combinations ?? []).filter((combination) =>
      (combination.models ?? []).some((model) => model.id === modelId),
    ),
  );
  const found = new Map();
  let recipes = 0;
  for (const combination of sets) {
    recipes += combination.recipes ?? 0;
    for (const model of combination.models ?? []) {
      if (model.id === modelId) continue;
      const seen = found.get(model.id) ?? {
        id: model.id,
        name: model.name,
        kind: model.kind,
        kindLabel: memberKindLabel(model),
        recipes: 0,
        pictures: 0,
        ambiguous: false,
      };
      seen.recipes += combination.recipes ?? 0;
      seen.pictures += combination.picture_count ?? 0;
      seen.ambiguous = seen.ambiguous || Boolean(model.ambiguous);
      found.set(model.id, seen);
    }
  }
  const companions = [...found.values()].sort(
    (a, b) =>
      b.recipes - a.recipes ||
      b.pictures - a.pictures ||
      a.name.localeCompare(b.name),
  );
  const top = companions[0]?.recipes || 1;
  return {
    companions: companions.map((companion) => ({
      ...companion,
      share: Math.round((companion.recipes / top) * 100),
    })),
    recipes,
    sets,
  };
}

/**
 * The files that identify one combination, joined.
 *
 * Read by the `Works with` dialog's chips, which name the exact combinations a
 * model is in - the one place a reader sees a reproducible set rather than a
 * group's union.
 */
export function setName(combination) {
  const named = (combination.models ?? [])
    .filter((model) => model.kind !== "adapter")
    .slice(0, 3)
    .map((model) => model.name);
  if (named.length) return named.join(" · ");
  return (
    (combination.models ?? [])
      .slice(0, 3)
      .map((model) => model.name)
      .join(" · ") || "Unnamed set"
  );
}

// ── Hand-made sets (#1520) ───────────────────────────────────────────────────
//
// **A hand-made set is a MENU, not a recipe.** The owner lists models that work
// together, slot by slot, to seed new workflows; there is no order, no strength
// and no graph. Evidence is kept apart from it on purpose: a hand-made card never
// shows a recipe count as if it were its own, and an evidence card never changes
// membership through a gesture. A picture belongs to a hand-made set only when
// EVERY model it used is in the set - the server works that out and reports it
// as the set's `picture_count` and the combinations' `covered_by`.

/**
 * The fixed slots every hand-made set has, in the order the tray draws them.
 *
 * `kinds` are the `file_kind`s a slot offers in its picker. Checkpoint also takes
 * `unknown`, because a Flux or Wan diffusion file is often filed that way and is
 * still the base model of its set.
 */
export const SET_SLOTS = [
  {
    id: "checkpoint",
    label: "Checkpoint",
    hint: "exactly one",
    add: "Add checkpoint",
    noun: "checkpoints",
    kinds: ["checkpoint", "unknown"],
  },
  {
    id: "text_encoder",
    label: "Text encoders",
    hint: "any number",
    add: "Add text encoder",
    noun: "text encoders",
    kinds: ["text_encoder"],
  },
  {
    id: "vae",
    label: "VAE",
    hint: "any number",
    add: "Add VAE",
    noun: "VAEs",
    kinds: ["vae"],
  },
  {
    id: "lora",
    label: "LoRAs",
    hint: "any number",
    add: "Add LoRA",
    noun: "LoRAs",
    kinds: ["adapter"],
  },
  {
    id: "other",
    label: "Other",
    hint: "upscalers, ControlNets",
    add: "Add",
    noun: "other models",
    kinds: ["unknown", "checkpoint", "vae", "text_encoder", "adapter"],
  },
];

/** The slot a file goes in when nobody says otherwise; the server's own rule. */
export function defaultSlot(fileKind) {
  return (
    {
      checkpoint: "checkpoint",
      vae: "vae",
      text_encoder: "text_encoder",
      adapter: "lora",
    }[fileKind] ?? "other"
  );
}

/** The set's checkpoint member, or null. */
export function setCheckpoint(set) {
  return (set?.members ?? []).find((m) => m.slot === "checkpoint") ?? null;
}

/**
 * What a set is called: its own name, else its checkpoint's, else "Untitled set".
 */
export function handMadeName(set) {
  return set?.name || setCheckpoint(set)?.name || "Untitled set";
}

/** The base model a set's suggestions follow: its checkpoint's, or null. */
export function handMadeBase(set) {
  return setCheckpoint(set)?.base_model || null;
}

/**
 * One hand-made set as `ModelSetCard` draws it.
 *
 * The same card shape as an evidence card, so the grid reads as one grid, with
 * the words changed: "Grouped by you", a picture count only when pictures really
 * belong to the set, and the incomplete warning when there is no checkpoint.
 */
export function handMadeCard(set) {
  const members = set.members ?? [];
  const checkpoint = setCheckpoint(set);
  const pictures = Number(set.picture_count) || 0;
  const offShelf = members.filter((m) => !m.on_shelf).length;
  return {
    key: `hand:${set.id}`,
    handMade: true,
    incomplete: Boolean(set.incomplete ?? !checkpoint),
    name: handMadeName(set),
    kindLabel: checkpoint?.base_model || "",
    kinds: kindCounts(
      members
        .filter((m) => m.slot !== "checkpoint")
        .map((m) => ({ ...m, kindLabel: memberKindLabel(m) })),
    ),
    facts: [
      members.length === 1 ? "1 model" : `${members.length} models`,
      pictures ? pictureCount(pictures) : "no picture yet",
      offShelf ? `${offShelf} not on shelf` : null,
    ].filter(Boolean),
    pictures,
    recipes: Number(set.recipes) || 0,
    covers: set.covers ?? [],
    // What the cover draws when no picture belongs to the set yet: the
    // checkpoint's own mark, or nothing (a dashed empty cover).
    markModel: checkpoint?.on_shelf ? checkpoint : null,
    size: members.length,
  };
}

/**
 * Does this shelf row belong in this slot's picker? Engines never do: no slot
 * lists `engine`. A row still waiting for its hash cannot be kept by hash yet.
 */
function fitsSlot(row, slot) {
  return Boolean(row?.sha256) && slot.kinds.includes(row?.file_kind);
}

/** A row's base model as the set picker compares it. */
export function rowBaseModel(row) {
  return row?.base_model_canonical || row?.base_model || null;
}

/**
 * The picker's sections for one slot of one set, ranked as the design ranks them.
 *
 * With a checkpoint chosen: your other sets with the same base model, then what
 * recipes have run with this checkpoint, then the same base model, then all. With
 * none, one unfiltered list - suggestions follow the checkpoint, so there is
 * nothing to rank by yet. A model appears once, in the first section it earns,
 * and never when the set already holds it.
 *
 * @param {Object} args
 * @param {Object} args.set - the hand-made set being filled.
 * @param {string} args.slotId
 * @param {Array<Object>} args.rows - the shelf's rows.
 * @param {Array<Object>} args.sets - every hand-made set.
 * @param {Array<Object>} args.combinations - the evidence payload.
 * @param {string} [args.query] - the type-to-filter text.
 * @returns {Array<{id: string, label: string, items: Array<Object>, total: number}>}
 */
export function slotSuggestions({
  set,
  slotId,
  rows,
  sets,
  combinations,
  query = "",
}) {
  const slot = SET_SLOTS.find((s) => s.id === slotId);
  if (!slot) return [];
  const held = new Set((set?.members ?? []).map((m) => m.sha256));
  const needle = query.trim().toLowerCase();
  const byId = new Map();
  for (const row of rows ?? []) {
    if (!fitsSlot(row, slot) || held.has(row.sha256)) continue;
    const name = row.display_name || row.filename || "";
    if (
      needle &&
      !`${name} ${row.filename || ""}`.toLowerCase().includes(needle)
    )
      continue;
    byId.set(row.id, row);
  }
  const sorted = (items) =>
    [...items].sort((a, b) =>
      (a.display_name || a.filename || "").localeCompare(
        b.display_name || b.filename || "",
      ),
    );
  const checkpoint = setCheckpoint(set);
  if (!checkpoint?.on_shelf) {
    const all = sorted(byId.values());
    return [
      { id: "all", label: `All ${slot.noun}`, items: all, total: all.length },
    ];
  }
  const base = checkpoint.base_model;
  const used = new Set();
  const take = (ids) => {
    const items = [];
    for (const id of ids) {
      if (used.has(id) || !byId.has(id)) continue;
      used.add(id);
      items.push(byId.get(id));
    }
    return items;
  };
  const inSets = take(
    (sets ?? [])
      .filter(
        (other) => other.id !== set.id && base && handMadeBase(other) === base,
      )
      .flatMap((other) =>
        (other.members ?? [])
          .filter((m) => m.slot === slotId && m.on_shelf)
          .map((m) => m.id),
      ),
  );
  const withCheckpoint = take(
    worksWith(combinations, checkpoint.id).companions.map((c) => c.id),
  );
  const sameBase = base
    ? take(
        sorted(
          [...byId.values()].filter((row) => rowBaseModel(row) === base),
        ).map((row) => row.id),
      )
    : [];
  const rest = take(sorted(byId.values()).map((row) => row.id));
  return [
    {
      id: "sets",
      label: base ? `In your ${base} sets` : "In your sets",
      items: inSets,
    },
    {
      id: "checkpoint",
      label: "Used with this checkpoint",
      items: withCheckpoint,
    },
    base
      ? { id: "base", label: `Other ${base} ${slot.noun}`, items: sameBase }
      : null,
    { id: "all", label: `All other ${slot.noun}`, items: rest },
  ]
    .filter(Boolean)
    .map((section) => ({ ...section, total: section.items.length }));
}

/**
 * What "Fill from pictures" offers: every model recipes have run with this
 * set's checkpoint that the set does not hold, strongest evidence first.
 *
 * @returns {Array<{id, name, kindLabel, recipes, slot, sha256}>}
 */
export function fillFromPictures(set, combinations, rows) {
  const checkpoint = setCheckpoint(set);
  if (!checkpoint?.on_shelf) return [];
  const held = new Set((set.members ?? []).map((m) => m.sha256));
  const byId = new Map((rows ?? []).map((row) => [row.id, row]));
  return (
    worksWith(combinations, checkpoint.id)
      .companions.map((c) => ({ ...c, row: byId.get(c.id) }))
      // Another checkpoint (a refiner) is not offered: the set holds one, and
      // one refused member fails the whole all-or-nothing add.
      .filter(
        (c) =>
          c.row?.sha256 &&
          !held.has(c.row.sha256) &&
          c.kind !== "engine" &&
          defaultSlot(c.kind) !== "checkpoint",
      )
      .map((c) => ({
        id: c.id,
        name: c.name,
        kindLabel: c.kindLabel,
        detail: `${c.kindLabel || "Model"} · ${recipeCount(c.recipes)}`,
        slot: defaultSlot(c.kind),
      }))
  );
}

/**
 * What "Fill from a set" offers: the members of your other sets with the same
 * base model (every other set, while this one has no checkpoint), each once,
 * labelled with the sets it comes from.
 */
export function fillFromSets(set, sets) {
  const base = handMadeBase(set);
  const held = new Set((set.members ?? []).map((m) => m.sha256));
  const found = new Map();
  for (const other of sets ?? []) {
    if (other.id === set.id) continue;
    if (base && handMadeBase(other) !== base) continue;
    for (const member of other.members ?? []) {
      if (!member.on_shelf || held.has(member.sha256)) continue;
      // One checkpoint per set: another set's checkpoint is not offered, since
      // adding it would either fail or replace nothing.
      if (member.slot === "checkpoint") continue;
      const seen = found.get(member.sha256) ?? {
        id: member.id,
        name: member.name,
        kindLabel: memberKindLabel(member),
        slot: member.slot,
        from: [],
      };
      seen.from.push(handMadeName(other));
      found.set(member.sha256, seen);
    }
  }
  return [...found.values()].map((entry) => ({
    ...entry,
    detail: `${entry.kindLabel || "Model"} · ${entry.from.join(", ")}`,
  }));
}

/**
 * A hand-made set's tray, slot by slot, in the order it is drawn and walked.
 *
 * Every member tile, then the slot's ＋ tile - which the Checkpoint slot drops
 * once it holds its one. The keys are what the tray and the grid's cursor agree
 * on: `m:<sha256>` for a member, `add:<slot>` for a ＋ tile.
 *
 * @returns {Array<{slot: Object, items: Array<{key, member: Object|null}>}>}
 */
export function setSlots(set) {
  const members = set?.members ?? [];
  return SET_SLOTS.map((slot) => {
    const held = members.filter((m) => m.slot === slot.id);
    const items = held.map((member) => ({ key: `m:${member.sha256}`, member }));
    if (slot.id !== "checkpoint" || !held.length) {
      items.push({ key: `add:${slot.id}`, member: null });
    }
    return { slot, items };
  });
}
