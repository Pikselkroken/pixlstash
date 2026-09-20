// The LoRA rows a recipe is saved with, resolved against the model shelf.
//
// Two surfaces build this list — the lightbox's Recipe tab, from the picture
// it is showing, and the Recipes tab, from the cover picture of a look nobody
// has saved — and both have to get the same two things right, which is why it
// is one function.

import { loraKey } from "./recipeKey";

/** A file name as every side of this spells it: lowercase, no directory. */
function baseName(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .split(/[\\/]/)
    .pop();
}

/**
 * Build a recipe's `loras` from what a picture names and what the shelf holds.
 *
 * **The strength is matched on the normalized base name.** `loraNames` is the
 * graph's raw `lora_name` widget — which may carry a folder and any case —
 * while `modelSlots` carries the same file already normalized, so an identity
 * comparison silently falls back to strength 1 for every LoRA loaded out of a
 * subfolder.
 *
 * **Every name is kept, digest or no digest.** `POST /workflows/run` applies
 * only the saved LoRAs carrying a `sha256`, so an unresolved one will not
 * reach the graph — but dropping it here would make the recipe's key disagree
 * with the picture's, and the key is what the match and the server's credit
 * are both built on. The dialog says which row a run will ignore instead.
 *
 * @param {Array<string>} loraNames - the picture's own `loras`.
 * @param {Array<{name: string, widget: string, strength: ?number}>} modelSlots
 * @param {Array<{filename: string, sha256: ?string}>} shelf - `listAdapters()`.
 * @returns {Array<{filename: string, sha256: string, strength: number}>}
 */
export function resolveRecipeLoras(loraNames, modelSlots, shelf) {
  const strengths = new Map(
    (modelSlots || [])
      .filter((slot) => /lora|adapter/i.test(slot?.widget || ""))
      .map((slot) => [baseName(slot.name), slot.strength]),
  );
  return (loraNames || []).map((filename) => {
    const base = baseName(filename);
    const hits = (shelf || []).filter(
      (row) => row.sha256 && baseName(row.filename) === base,
    );
    // Exactly one row, as `apply_adapter` resolves it: two files of that name
    // on the shelf is a coin toss over which one would load.
    return {
      filename,
      sha256: hits.length === 1 ? String(hits[0].sha256) : "",
      strength: Number(strengths.get(base) ?? 1),
    };
  });
}

/** Whether two LoRA lists name the same files, however they are spelled. */
export function sameLoras(left, right) {
  return loraKey(left) === loraKey(right);
}
