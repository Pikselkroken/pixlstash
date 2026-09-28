// loraWorkflows.js - which workflows can make pictures with a given LoRA.
//
// The "Create with LoRA…" dialog (CreateWithLoraDialog.vue) answers this in the
// client from reads the app already makes: the workflow cards, the LoRA's shelf
// row and the hand-made workflow sets. There is no server route for it, because
// every input is already on those payloads; see frontend_architecture.md,
// `CreateWithLoraDialog.vue`.

import { checkpointModel } from "./workflowCard";

/**
 * A model name as three sources spell it, folded to one comparable key:
 * no folders, no extension, lowercase. A card's `recipe_values` name, a shelf
 * row's `filename` and a loader's `filename` then compare equal for one file.
 */
export function nameKey(value) {
  if (!value) return "";
  const base = String(value).split(/[\\/]/).pop();
  return base
    .replace(/\.(safetensors|ckpt|pt|pth|bin|gguf)$/i, "")
    .toLowerCase();
}

/**
 * Whether a LoRA was made for a different architecture than the checkpoint.
 *
 * The same rule as the server's `families_clash`: only where BOTH sides name a
 * family. An unknown family is never a clash, because "we cannot tell" is not
 * "they differ".
 */
export function familiesClash(loraFamily, checkpointFamily) {
  if (!loraFamily || !checkpointFamily) return false;
  return loraFamily !== checkpointFamily;
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
 *   - `ready`     - the graph has a LoRA loader and nothing says it clashes.
 *   - `clash`     - the LoRA's family differs from the checkpoint's.
 *   - `no_loader` - the graph has no LoRA loader, or nobody has read whether
 *                   it has one. Putting a LoRA in one means saving a new
 *                   workflow (Edit LoRAs), which this dialog does not do.
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
  if (!(card.loras || []).length) return { ...base, fit: "no_loader" };
  if (familiesClash(lora?.family, checkpoint?.family)) {
    return { ...base, fit: "clash" };
  }
  return { ...base, fit: "ready" };
}

/**
 * Every card, fitted against *lora*, `ready` ones ranked: known pairings
 * first, then the ones that used this LoRA before, then the server's order.
 *
 * @returns {{ready: Array<Object>, clash: Array<Object>, noLoader: Array<Object>}}
 */
export function fitWorkflows(cards, lora, handMade) {
  const paired = pairedCheckpoints(handMade, lora?.sha256);
  const fits = (cards || []).map((card, index) => ({
    ...workflowFit(card, lora, paired),
    index,
  }));
  const score = (entry) => (entry.paired ? 2 : 0) + (entry.usedBefore ? 1 : 0);
  const ready = fits
    .filter((entry) => entry.fit === "ready")
    .sort((a, b) => score(b) - score(a) || a.index - b.index);
  return {
    ready,
    clash: fits.filter((entry) => entry.fit === "clash"),
    noLoader: fits.filter((entry) => entry.fit === "no_loader"),
  };
}

/**
 * Which of a workflow's LoRA slots the LoRA goes into, from its chain read.
 *
 * The slot already loading these bytes, then one naming the same file, then an
 * empty one, then the LAST loader of the chain - so a workflow whose loaders
 * are all taken swaps its final LoRA and the dialog says which one it replaced.
 * The trunk's loaders come before any lane's.
 *
 * @param {Object} chain - `getLoraChain(workflowId)`.
 * @param {Object} lora - the shelf row.
 * @returns {?{loader: Object, replaces: ?string}} null when the chain has no
 *   loader at all.
 */
export function pickLoraSlot(chain, lora) {
  const loaders = [
    ...(chain?.loaders || []),
    ...(chain?.lanes || []).flatMap((lane) => lane.loaders || []),
  ];
  if (!loaders.length) return null;
  const wanted = nameKey(lora?.filename);
  const same =
    loaders.find((l) => lora?.sha256 && l.sha256 === lora.sha256) ||
    loaders.find((l) => wanted && nameKey(l.filename) === wanted);
  if (same) return { loader: same, replaces: null };
  const empty = loaders.find(
    (l) => !nameKey(l.filename) || nameKey(l.filename) === "none",
  );
  if (empty) return { loader: empty, replaces: null };
  const last = loaders[loaders.length - 1];
  return { loader: last, replaces: last.name || last.filename || null };
}
