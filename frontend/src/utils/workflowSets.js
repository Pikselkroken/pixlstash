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
