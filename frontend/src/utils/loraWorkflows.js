// loraWorkflows.js - which workflows can make pictures with a given LoRA.
//
// "Create with LoRA…" (a person's or a picture set's menu) opens the Run popup
// with the workflow picker narrowed to what this answers. It is worked out in
// the client from reads the app already makes: the workflow cards, the LoRA's
// shelf row and the hand-made workflow sets. See frontend_architecture.md,
// `RunDialog.vue`, "Create with LoRA".
//
// The same rule read the other way (`fitPeople`) says which people a workflow
// run by itself can be of: the Run popup's person picker.

import { checkpointModel } from "./workflowCard";

/**
 * The card types Create with LoRA offers: a picture or a video made from a
 * prompt. Anything else (img2img, inpaint, upscale…) needs a picture put in,
 * which a run started from a person or a set has none of; a card with no type
 * is one nobody has read, and is offered. `video` covers a start-frame graph
 * too (the type cannot tell them apart): its picture input then runs on the
 * frame the graph names, shown "As the workflow has it" in the Pictures
 * section where the owner can change it, or is refused as unfilled when that
 * file is gone (#1695).
 */
const OFFERED_TYPES = new Set(["txt2img", "video"]);

/**
 * A model name as three sources spell it, folded to one comparable key:
 * no folders, no extension, lowercase. A card's `recipe_values` name and a
 * shelf row's `filename` then compare equal for one file.
 */
export function nameKey(value) {
  if (!value) return "";
  const base = String(value).split(/[\\/]/).pop();
  return base
    .replace(/\.(safetensors|ckpt|pt|pth|bin|gguf)$/i, "")
    .toLowerCase();
}

/**
 * The digests of every checkpoint a hand-made workflow set puts beside *sha256*.
 *
 * @param {Array<Object>} handMade - `fetchWorkflowSets().hand_made`.
 * @param {string} sha256 - the LoRA's digest.
 * @returns {Set<string>}
 */
export function pairedCheckpoints(handMade, sha256) {
  const paired = new Set();
  if (!sha256) return paired;
  for (const set of handMade || []) {
    const members = set.members || [];
    if (!members.some((m) => m.slot === "lora" && m.sha256 === sha256))
      continue;
    for (const member of members) {
      if (member.slot === "checkpoint" && member.sha256)
        paired.add(member.sha256);
    }
  }
  return paired;
}

/**
 * How one workflow card fits one LoRA.
 *
 * `fit` is one of:
 *   - `match`         - the checkpoint's base-model family is the LoRA's.
 *   - `unknown`       - one side has no known family, so nothing says either
 *                       way; offered after the matches, never hidden.
 *   - `clash`         - both families are known and differ.
 *   - `needs_picture` - the workflow makes a picture FROM a picture.
 *
 * Both sides read `base_model_family`, the shelf's identified base model with
 * filename guesses included: a LoRA the shelf shows as Krea 2 narrows to Krea 2
 * workflows even when nothing in the file stated it. Whether the graph has a
 * LoRA loader does not matter: the run adds the LoRA in a loader of its own
 * (`add_loras`).
 *
 * `paired` is a hand-made workflow set naming this LoRA and this checkpoint;
 * `usedBefore` is the LoRA among the ones this workflow's kept pictures used.
 *
 * @param {Object} card - a `GET /workflows` card.
 * @param {Object} lora - a shelf row (`listAdapters`).
 * @param {Set<string>} paired - `pairedCheckpoints(...)` for this LoRA.
 */
export function workflowFit(card, lora, paired = new Set()) {
  const checkpoint = checkpointModel(card);
  const base = {
    card,
    checkpoint,
    paired: Boolean(checkpoint?.sha256 && paired.has(checkpoint.sha256)),
    usedBefore: (card.recipe_values?.loras || []).some(
      (value) => nameKey(value.name) === nameKey(lora?.filename),
    ),
  };
  if (card.type && !OFFERED_TYPES.has(card.type)) {
    return { ...base, fit: "needs_picture" };
  }
  return { ...base, fit: familyFit(checkpoint, lora) };
}

/** `match`, `unknown` or `clash`: one checkpoint and one LoRA, by base-model family. */
function familyFit(checkpoint, lora) {
  const loraFamily = lora?.base_model_family || null;
  const cardFamily = checkpoint?.base_model_family || null;
  if (!loraFamily || !cardFamily) return "unknown";
  return loraFamily === cardFamily ? "match" : "clash";
}

/**
 * The people a run of *card* can be of: each person with a LoRA attached that
 * works with the card's checkpoint.
 *
 * The other way round from {@link fitWorkflows}, on the same family rule: a
 * `match` first, an `unknown` after it (never hidden, since nothing says it
 * will not work), and a person whose every LoRA is for another base model
 * counted in `clash` rather than offered. A workflow that starts from a
 * picture still takes a person, so the card's type is not asked.
 *
 * @param {Object|null} card - a `GET /workflows` card.
 * @param {Array<Object>} loras - shelf rows (`listAdapters`), with `attachments`.
 * @param {Array<Object>} characters - `[{id, name}]`.
 * @returns {{people: Array<{id: number, name: string, loras: Array<Object>}>,
 *   clash: number}} each person's fitting LoRAs, best first.
 */
export function fitPeople(card, loras, characters) {
  const checkpoint = card ? checkpointModel(card) : null;
  const attached = new Map();
  for (const lora of loras || []) {
    for (const attachment of lora?.attachments || []) {
      if (attachment.entity_type !== "character" || !lora.sha256) continue;
      const id = Number(attachment.entity_id);
      attached.set(id, [...(attached.get(id) || []), lora]);
    }
  }
  const rank = { match: 0, unknown: 1 };
  const people = [];
  let clash = 0;
  for (const person of characters || []) {
    const own = attached.get(Number(person.id));
    if (!own) continue;
    const fitting = own
      .map((lora) => ({ lora, fit: familyFit(checkpoint, lora) }))
      .filter((entry) => entry.fit in rank)
      // `sort` is stable, so ties keep the shelf's order.
      .sort((a, b) => rank[a.fit] - rank[b.fit]);
    if (!fitting.length) {
      clash += 1;
      continue;
    }
    people.push({
      id: Number(person.id),
      name: person.name,
      best: rank[fitting[0].fit],
      loras: fitting.map((entry) => entry.lora),
    });
  }
  people.sort((a, b) => a.best - b.best);
  return { people, clash };
}

/**
 * Every card, fitted against *lora*: the matches and the unknowns each ranked
 * by a hand-made pairing first, then prior use, then the server's order.
 *
 * @returns {{match: Array<Object>, unknown: Array<Object>, clash: Array<Object>,
 *   needsPicture: Array<Object>}}
 */
export function fitWorkflows(cards, lora, handMade) {
  const paired = pairedCheckpoints(handMade, lora?.sha256);
  const fits = (cards || []).map((card) => workflowFit(card, lora, paired));
  const score = (entry) => (entry.paired ? 2 : 0) + (entry.usedBefore ? 1 : 0);
  // `sort` is stable, so ties keep the server's order.
  const ranked = (fit) =>
    fits
      .filter((entry) => entry.fit === fit)
      .sort((a, b) => score(b) - score(a));
  return {
    match: ranked("match"),
    unknown: ranked("unknown"),
    clash: fits.filter((entry) => entry.fit === "clash"),
    needsPicture: fits.filter((entry) => entry.fit === "needs_picture"),
  };
}
