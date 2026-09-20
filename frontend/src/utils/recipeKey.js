// What makes two looks the same look.
//
// **One function per side, used by everything that compares.** The server
// decides this — `saved_recipe_service.prompt_key` and `lora_key` are what
// credit is grouped by — and the client mirrors it so the lightbox's "Matches
// your saved recipe X" banner cannot say one thing while the recipe's own
// picture count says another.
//
// The pair was two private copies inside `OverlayRecipePanel` first, and the
// bug that moved it here was exactly the one a second copy invites: the banner
// keyed on every LoRA the picture named while the save dropped the ones the
// model shelf could not digest, so a saved recipe never matched the picture it
// was saved from and the same look could be kept over and over.

/**
 * The comparable form of a prompt: stripped, and absent is the empty one.
 *
 * Stripped because the two sides arrive by different routes — a recipe's
 * prompt is typed or copied into the Save dialog, a picture's is read out of
 * the graph — and a trailing newline is not a different look. Nothing
 * stronger: case and inner whitespace are looks the owner told apart on
 * purpose.
 *
 * @param {?string} value
 * @returns {string}
 */
export function promptKey(value) {
  return (value || "").trim();
}

/**
 * The comparable form of a LoRA list, from either side of the match.
 *
 * A picture's `loras` are file names; a recipe's are `{filename, sha256,
 * strength}` objects. Both reduce to lowercase base names, and anything that
 * is neither is dropped rather than compared as itself.
 *
 * **Sorted, and duplicates kept**, which is why this is not a set: a graph
 * that loads one file twice is a stacked LoRA and a materially different look,
 * so a recipe that stacks the same file twice has to key the same way. Sorted
 * because neither side's order means anything — one follows node numbering and
 * the other the dialog's rows.
 *
 * @param {Array<string|{filename: string}>} entries
 * @returns {string} a single comparable string; `""` when nothing was named.
 */
export function loraKey(entries) {
  return (entries || [])
    .map((entry) => (typeof entry === "string" ? entry : entry?.filename))
    .filter((name) => typeof name === "string" && name)
    .map((name) => name.trim().toLowerCase().split(/[\\/]/).pop())
    .sort()
    .join("\u0000");
}

/**
 * Whether a saved recipe keeps the look this picture was made with.
 *
 * **A recipe that keeps neither a prompt nor a LoRA matches nothing.** Its key
 * is the empty prompt with no LoRAs, which is also the key of every picture
 * PixlStash has not read the ComfyUI metadata out of yet; the server refuses
 * that class for credit and the banner has to refuse it for the same reason,
 * or one such recipe claims every prompt-less picture on the card.
 *
 * @param {{prompt: ?string, loras: Array}} recipe - a saved recipe row.
 * @param {{prompt: ?string, loras: Array}} look - the picture's own.
 * @returns {boolean}
 */
export function keepsTheSameLook(recipe, look) {
  const prompt = promptKey(look?.prompt);
  const loras = loraKey(look?.loras);
  if (!prompt && !loras) return false;
  return promptKey(recipe?.prompt) === prompt && loraKey(recipe?.loras) === loras;
}

/**
 * Whether saving what a form is showing would write a row this one already is.
 *
 * **A different question from `keepsTheSameLook`, asked by one surface.** The
 * look's key is prompt and LoRA file names, because that is what the server
 * groups credit by; a recipe also holds a negative prompt, the LoRA
 * strengths, the parameters the owner changed and whether it keeps a seed, and
 * none of those are part of the look. On a read-only surface the distinction
 * is idle - the lightbox's Recipe tab cannot edit any of them - but the Run
 * popup is a form, and there an inert **Saved** keyed on the look alone
 * refuses to save a recipe that differs from the kept one in every setting it
 * carries. That is not a duplicate; it is a variation, and it was unsaveable.
 *
 * So: the look first, then everything else the save would write. A recipe
 * keeping a seed never matches, because a save leaves the seed off unless the
 * owner ticks it and the two are then different recipes.
 *
 * @param {Object} recipe - a saved recipe row, as the API serves it.
 * @param {Object} save - `{prompt, negative, loras, overrides}` as the form
 *   would send them: `loras` are `{filename, strength}`, `overrides` is the
 *   `{"<slot>/<input>": value}` map.
 * @returns {boolean}
 */
export function wouldDuplicate(recipe, save) {
  if (!keepsTheSameLook(recipe, save)) return false;
  if (recipe?.keep_seed) return false;
  if ((recipe?.negative || "").trim() !== (save?.negative || "").trim()) {
    return false;
  }
  return extrasKey(recipe) === extrasKey(save);
}

/** The strengths and the overrides, in one comparable string. */
function extrasKey(side) {
  const strengths = (side?.loras || [])
    .map((lora) => [
      String(lora?.filename || "")
        .trim()
        .toLowerCase()
        .split(/[\\/]/)
        .pop(),
      Number(lora?.strength ?? 1),
    ])
    .sort();
  const overrides = Object.entries(side?.overrides || {})
    .map(([address, value]) => [address, JSON.stringify(value)])
    .sort();
  return JSON.stringify([strengths, overrides]);
}
