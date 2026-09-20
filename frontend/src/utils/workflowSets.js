// The model shelf's Workflow set axis (#1438): folding the combinations
// `GET /models/workflow-sets` serves into the cards the grid draws.
//
// **A card is one combination** - the exact files a picture proves ran together.
// That produces near-duplicates by the dozen (one per LoRA tried, one per VAE
// swapped), so combinations within a chosen distance of each other are folded
// into one stack, the same gesture the Workflows grid already makes. The fold
// is the READER's, never the data's: the server groups nothing, the distance is
// a toolbar control, and *Don't fold* is a real setting rather than a debug
// switch. Everything here is pure so the arithmetic is testable without a grid.
//
// **Co-occurrence is evidence; its absence is not.** Nothing in this file may
// hide a combination, and nothing may infer one: two models that have never
// been seen together are not drawn together, and that is not a claim they
// cannot work. The models no recipe names come back from the server under
// `no_set` and are drawn as their own card.

import { fileKindLabel } from "./modelShelf";

/** The fold settings the toolbar offers, in the order the menu lists them. */
export const FOLD_KEYS = ["none", "one", "two", "checkpoint"];

/**
 * What each fold setting is called, and the glyph that stands for it.
 *
 * Named for what they DO to the cards rather than graded as loose/strict:
 * "loose" means nothing before you have seen the result, and the menu states
 * each setting's card count beside it for the same reason.
 */
export const FOLD_LABELS = {
  none: { label: "Don't fold", icon: "mdi-selection-off" },
  one: { label: "1 file apart", icon: "mdi-check" },
  two: { label: "2 files apart", icon: "mdi-layers" },
  checkpoint: { label: "By checkpoint only", icon: "mdi-cube-outline" },
};

/**
 * How many covers a card's mosaic draws.
 *
 * Three, the depth the server serves per combination (`SET_COVER_DEPTH`) and the
 * one the shipped workflow card draws: a folded stack pools its members' covers
 * and then cuts back to this, so a card of six sets is not six bitmaps for three
 * holes.
 */
const COVER_DEPTH = 3;

/** How many files a setting lets two combinations differ by. */
const FOLD_DISTANCE = { none: 0, one: 1, two: 2 };

/**
 * How far apart two combinations are, in files.
 *
 * `max(added, removed)` and NOT the size of the symmetric difference, because
 * a *swap* is one change and the symmetric difference counts it as two: a set
 * that differs only in which VAE it loaded would otherwise need "2 files apart"
 * to fold, and "1 file apart" would separate the very pair a reader most wants
 * side by side. Adding one file and removing another at once is still one step
 * under this measure, which is the reading the *differs by* chips give it.
 *
 * @param {Set<number>} a
 * @param {Set<number>} b
 * @returns {number}
 */
export function foldDistance(a, b) {
  let added = 0;
  let removed = 0;
  for (const id of a) if (!b.has(id)) removed += 1;
  for (const id of b) if (!a.has(id)) added += 1;
  return Math.max(added, removed);
}

/** The member ids of one combination, as a Set. */
function idsOf(combination) {
  return new Set((combination.models ?? []).map((model) => model.id));
}

/**
 * The file a combination is named after: the head of the server's own order.
 *
 * The server sorts a combination's members checkpoint-first, then unclassified,
 * then the support files, then the adapters, so the head is the checkpoint
 * where there is one and the diffusion file where there is not (Flux, Wan). No
 * fallback is invented here: reading the head is what makes that rule one
 * decision in one place rather than two that can disagree.
 */
export function headModel(combination) {
  return (combination.models ?? [])[0] ?? null;
}

/**
 * Sort combinations into the order the grid draws them, strongest evidence first.
 *
 * The server already sorts this way; re-applied here because the fold reads the
 * first member of each stack as its SEED, so a caller that filtered or
 * concatenated payloads must not be able to change which combination a stack is
 * measured against.
 */
function byEvidence(combinations) {
  return [...combinations].sort(
    (a, b) =>
      (b.picture_count ?? 0) - (a.picture_count ?? 0) ||
      (b.recipes ?? 0) - (a.recipes ?? 0) ||
      String(a.key).localeCompare(String(b.key)),
  );
}

/**
 * Fold combinations into stacks.
 *
 * Greedy against each stack's SEED - the most-used combination in it - rather
 * than single-linkage against any member. Single linkage would chain: A folds
 * with B, B with C, and C ends up on a stack it is three files from, under a
 * name that does not describe it. Seeded, every member of a stack is within the
 * chosen distance of the card's own title, which is the promise the *differs
 * by* chips make.
 *
 * **A fold never swaps the file the set is named after.** Two combinations join
 * only when they share a head, whatever the distance says: exchanging the
 * checkpoint is one file by the arithmetic and a different set by every other
 * reading, and a card whose name is one checkpoint must not hold another's
 * pictures. Every stack is therefore inside one head's group, which is what
 * keeps `By checkpoint only` the coarsest setting.
 *
 * **The settings are NOT nested partitions, and the menu does not claim they
 * are.** Seeding is what breaks it: the seed set itself changes with the
 * distance, so loosening the fold can move a member onto a different card
 * rather than only merging cards. Three combinations A, B, C under one head
 * with A-B = 2, A-C = 3, B-C = 1 fold to `{A} {B,C}` at one file apart and
 * `{A,B} {C}` at two - B and C pulled apart by a looser setting. That is a real
 * cost of seeding and it is why the menu states a CARD COUNT per setting rather
 * than promising anything about what is inside them: the count is what changes
 * under the reader, and it is the thing they can see.
 *
 * `checkpoint` is not a distance at all: it collapses every combination under
 * the file it is named after, which is the one-card-per-checkpoint reading of
 * the same data. It is offered as a menu row rather than as a second view
 * because that is all it is.
 *
 * @param {Array<Object>} combinations - from `GET /models/workflow-sets`.
 * @param {string} fold - one of {@link FOLD_KEYS}.
 * @returns {Array<{key: string, members: Array<Object>}>} stacks, seed first.
 */
export function foldSets(combinations, fold) {
  const ordered = byEvidence(combinations ?? []);
  if (fold === "checkpoint") {
    const byHead = new Map();
    for (const combination of ordered) {
      // Keyed on the head's ID, never its name: two shelf rows can carry the
      // same basename, and merging them here would draw one card claiming both
      // files' pictures.
      const head = headModel(combination);
      const key = head ? `model:${head.id}` : combination.key;
      const stack = byHead.get(key);
      if (stack) stack.members.push(combination);
      else byHead.set(key, { key, members: [combination] });
    }
    return [...byHead.values()];
  }

  const distance = FOLD_DISTANCE[fold] ?? 0;
  const stacks = [];
  for (const combination of ordered) {
    const ids = idsOf(combination);
    const head = headModel(combination)?.id ?? null;
    const home = distance
      ? stacks.find(
          (stack) =>
            stack.head === head && foldDistance(stack.ids, ids) <= distance,
        )
      : undefined;
    if (home) home.members.push(combination);
    else
      stacks.push({ key: combination.key, ids, head, members: [combination] });
  }
  return stacks.map(({ key, members }) => ({ key, members }));
}

/**
 * What each fold setting would cost in cards, for the menu that offers them.
 *
 * Counts, not adjectives: the setting changes how many cards are on screen, and
 * a reader cannot judge that from a word. Every option is counted in one pass
 * over the same data so the numbers are consistent with each other.
 *
 * @param {Array<Object>} combinations
 * @returns {Record<string, number>}
 */
export function foldCounts(combinations) {
  return Object.fromEntries(
    FOLD_KEYS.map((key) => [key, foldSets(combinations, key).length]),
  );
}

/**
 * How one combination differs from another, as the chips a card wears.
 *
 * A combination has no name of its own, so it is titled by its files and told
 * apart by what changed. A one-for-one exchange inside the same kind is drawn
 * as an arrow rather than as an add and a remove, because that is what it is:
 * `sdxl_vae → vae-ft-mse` is one decision and "+ one VAE, − another" is two
 * facts a reader has to put back together.
 *
 * @param {Object} from - the seed.
 * @param {Object} to - the member being described.
 * @returns {Array<string>} chip labels, empty when the two are the same files.
 */
export function differences(from, to) {
  const before = new Map((from.models ?? []).map((m) => [m.id, m]));
  const after = new Map((to.models ?? []).map((m) => [m.id, m]));
  const removed = [...before.values()].filter((m) => !after.has(m.id));
  const added = [...after.values()].filter((m) => !before.has(m.id));

  const labels = [];
  const takenAway = [...removed];
  for (const model of added) {
    // Matched on kind, so a swapped VAE reads as a swap and an added LoRA does
    // not steal a removed text encoder's place in the sentence.
    const index = takenAway.findIndex((other) => other.kind === model.kind);
    if (index === -1) {
      labels.push(`+ ${model.name}`);
      continue;
    }
    labels.push(`${takenAway.splice(index, 1)[0].name} → ${model.name}`);
  }
  for (const model of takenAway) labels.push(`− ${model.name}`);
  return labels;
}

/** "3 recipes", "1 recipe" - the evidence behind one combination. */
export function recipeCount(recipes) {
  const n = Number(recipes) || 0;
  return n === 1 ? "1 recipe" : `${n} recipes`;
}

/**
 * One stack as `ModelSetCard` draws it.
 *
 * The card's SHAPE is the shipped workflow card's - a cover mosaic, a layered
 * count, four single-line rows - because a reader should not have to learn a
 * second card. Its DATA is its own: `WorkflowCard`'s ⓘ panel says "Stack of 3
 * workflows" and "Saved recipes" and lists pictures through a workflow, and a
 * set is none of those, so borrowing the component would have put three wrong
 * words on every card. See `ModelSetCard.vue`.
 *
 * The name is the first three members joined, which is the server's order and
 * therefore the checkpoint, the VAE and the text encoder - the files that
 * identify the set. An adapter is drawn as a chip on its own row instead, so a
 * set that differs only by its LoRA does not get a name that hides which LoRA.
 *
 * @param {{key: string, members: Array<Object>}} stack
 * @returns {Object} a card for `ModelSetCard`.
 */
export function setCard(stack) {
  const [seed] = stack.members;
  const pictures = stack.members.reduce(
    (total, member) => total + (member.picture_count ?? 0),
    0,
  );
  const recipes = stack.members.reduce(
    (total, member) => total + (member.recipes ?? 0),
    0,
  );
  const folded = stack.members.length > 1;
  const members = seed.models ?? [];
  return {
    key: stack.key,
    name: setName(seed),
    // The shelf's own kind words, in the server's order, so the row says what
    // SHAPE the set is: one VAE and two encoders, or none at all.
    kinds: members
      .filter((model) => model.kind !== "adapter")
      .map((model) => memberKindLabel(model))
      .filter(Boolean),
    loras: members
      .filter((model) => model.kind === "adapter")
      .map((model) => model.name),
    differsBy: folded
      ? [
          ...new Set(
            stack.members
              .slice(1)
              .flatMap((member) => differences(seed, member)),
          ),
        ]
      : // "Nothing to fold" is said in as many words, so the absence of a deck
        // is never ambiguous - a stack of one looks exactly like a card that
        // could have had members and did not.
        [recipeCount(recipes), "nothing to fold"],
    pictures,
    recipes,
    // Pooled across the whole stack, seed first, not taken from the seed alone:
    // the count beside them sums every member, so a mosaic drawn from one of
    // them under-represents its own number. Deduplicated by picture, because two
    // recipes of one combination can nominate the same best picture.
    covers: [
      ...new Map(
        stack.members
          .flatMap((member) => member.covers ?? [])
          .map((cover) => [cover.picture_id, cover.url]),
      ).values(),
    ].slice(0, COVER_DEPTH),
    size: stack.members.length,
  };
}

/** The files that identify a set: its first three members, joined. */
export function setName(combination) {
  const named = (combination.models ?? [])
    .filter((model) => model.kind !== "adapter")
    .slice(0, 3)
    .map((model) => model.name);
  return named.length ? named.join(" · ") : setAllNames(combination);
}

/** Every member's name, for a set whose files are all adapters. */
function setAllNames(combination) {
  return (
    (combination.models ?? [])
      .slice(0, 3)
      .map((model) => model.name)
      .join(" · ") || "Unnamed set"
  );
}

/**
 * One combination as the panel under an open stack draws it.
 *
 * Every file is listed with its kind, not truncated behind a chevron: this is
 * the level at which "run exactly this" is a real offer, and an offer with a
 * hidden line is not one.
 *
 * @param {Object} combination
 * @param {Object} seed - the stack's seed, which this one is described against.
 * @param {number} index - position in the stack; 0 is the seed itself.
 * @returns {Object} the shape `ModelComboCard` reads.
 */
export function comboCard(combination, seed, index) {
  const diff = index === 0 ? [] : differences(seed, combination);
  return {
    key: combination.key,
    // The seed carries its own name; the rest are named by what changed, which
    // is the only thing that tells two near-identical combinations apart.
    name: index === 0 ? setName(combination) : `… ${diff.join(", ")}`,
    seed: index === 0,
    files: (combination.models ?? []).map((model) => ({
      ...model,
      kindLabel: memberKindLabel(model),
    })),
    covers: (combination.covers ?? []).map((cover) => cover.url),
    picture_count: combination.picture_count ?? 0,
    recipes: combination.recipes ?? 0,
    note:
      index === 0
        ? `most used · ${recipeCount(combination.recipes)}`
        : `${diffNote(diff)} · ${recipeCount(combination.recipes)}`,
  };
}

/** "1 file from the set above", "the VAE is the only difference". */
function diffNote(diff) {
  if (!diff.length) return "the same files";
  const swap = diff.length === 1 && diff[0].includes("→");
  if (swap) return "one file swapped";
  return diff.length === 1
    ? "1 file from the set above"
    : `${diff.length} files differ`;
}

/**
 * What a member's kind is called on the card.
 *
 * `fileKindLabel` names every kind but the adapter, which it leaves blank
 * because the row list names an adapter by its algorithm instead. A set card
 * has no algorithm column, so the word is supplied here rather than leaving the
 * one kind a reader most wants to see unlabelled.
 */
export function memberKindLabel(model) {
  return (
    fileKindLabel(model?.kind) || (model?.kind === "adapter" ? "LoRA" : "")
  );
}

/**
 * The companions of one model: everything it has been seen beside, ranked.
 *
 * Ranked by how many recipes back each pairing, which is the only thing
 * co-occurrence can actually measure. The caller draws the bar from `share` and
 * the number from `recipes`: the bar is the ranking, the number is the evidence.
 *
 * **A model missing from this list has not been ruled out.** It has simply
 * never been in the same picture's recipe, which is a fact about what has been
 * tried here and not about what works - so every caller says so beside the list.
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
      // One witness that could not pin the file down is enough to say so; a
      // cleaner second witness does not unmake the first.
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
