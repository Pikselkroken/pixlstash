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
 * The card types that make a picture from nothing. Anything else (img2img,
 * inpaint, upscale…) needs a picture put in, which this dialog does not ask
 * for; a card with no type is one nobody has read, and is offered.
 */
const TEXT_TO_IMAGE_TYPES = new Set(["txt2img"]);

/**
 * How one workflow card fits one LoRA.
 *
 * `fit` is one of:
 *   - `ready`     - the graph has a LoRA loader and nothing says it clashes.
 *   - `clash`     - the LoRA's family differs from the checkpoint's.
 *   - `no_loader` - the graph has no LoRA loader, or nobody has read whether
 *                   it has one. Putting a LoRA in one means saving a new
 *                   workflow (Edit LoRAs), which this dialog does not do.
 *   - `needs_picture` - the workflow makes a picture FROM a picture.
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
  if (card.type && !TEXT_TO_IMAGE_TYPES.has(card.type)) {
    return { ...base, fit: "needs_picture" };
  }
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
 * @returns {{ready: Array<Object>, clash: Array<Object>, noLoader: Array<Object>,
 *   needsPicture: Array<Object>}}
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
    needsPicture: fits.filter((entry) => entry.fit === "needs_picture"),
  };
}

/** Whether a loader holds no LoRA: an empty widget, or ComfyUI's `None`. */
function isEmptyLoader(loader) {
  const key = nameKey(loader?.filename);
  return !key || key === "none";
}

/** Whether a loader already loads *lora*, by digest first, then by file name. */
export function loadsLora(loader, lora) {
  if (lora?.sha256 && loader?.sha256 === lora.sha256) return true;
  const wanted = nameKey(lora?.filename);
  return Boolean(wanted) && nameKey(loader?.filename) === wanted;
}

/**
 * Which loader of *loaders* the LoRA goes into by default.
 *
 * The one already loading these bytes or this file, then an empty one, then
 * the LAST - so a workflow whose loaders are all taken swaps its final LoRA,
 * and the dialog says which one it replaces and lets the owner pick another.
 */
export function defaultLoader(loaders, lora) {
  if (!loaders?.length) return null;
  return (
    loaders.find((l) => loadsLora(l, lora)) ||
    loaders.find(isEmptyLoader) ||
    loaders[loaders.length - 1]
  );
}

/** What putting *lora* into *loader* replaces, or null when nothing is lost. */
export function replacedBy(loader, lora) {
  if (!loader || isEmptyLoader(loader) || loadsLora(loader, lora)) return null;
  return loader.name || loader.filename || null;
}

/**
 * Where a LoRA can go in a workflow, from its chain read.
 *
 * `trunk` - the loaders every pass reads, one of which takes the LoRA; the
 *           owner may pick which.
 * `lanes` - no shared loader but one chain per pass (a two-sampler graph):
 *           the LoRA goes into one loader of EACH pass, or it would shape only
 *           half the picture.
 *
 * @param {Object} chain - `getLoraChain(workflowId)`.
 * @returns {?{mode: "trunk"|"lanes", loaders: Array<Object>}} null when the
 *   chain has no loader the dialog can address.
 */
export function loraPlacement(chain) {
  const trunk = chain?.loaders || [];
  if (trunk.length) return { mode: "trunk", loaders: trunk };
  const lanes = (chain?.lanes || []).filter(
    (lane) => (lane.loaders || []).length,
  );
  if (lanes.length) {
    return { mode: "lanes", loaders: lanes.map((lane) => lane.loaders) };
  }
  return null;
}
