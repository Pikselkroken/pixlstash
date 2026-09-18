// Pure helpers behind WorkflowCard and ChipRow, kept out of the components so
// the fitting arithmetic and the accessible name are testable without layout.
//
// THE CARD SHAPE. One card as `GET /workflows/cards` will serve it (plan §6.4,
// step B3). It is **snake_case, like every other response this app reads**, and
// a slot's `mark` uses B1's own vocabulary (#1390: `structural` | `recipe`), so
// nothing translates between the two and inverts a meaning on the way:
//
//   {
//     key, name, type, imported,
//     models: [{ name, kind, mark? }],  // every non-LoRA slot: checkpoint,
//                                       // unet, vae, clip… `kind` is the slot
//     loras:  [{ name, mark }],         // "structural" = in the workflow
//                                       // (a filled chip), "recipe" = a slot
//                                       // the recipe fills (dashed)
//     differs_by: [string],             // a stack: the union over its members
//     picture_count, rating,            // rating 1-5; 0 or null is unrated
//     covers: [url],                    // up to 3, the cover first
//     stack_size,                       // 2 or more makes the card a stack
//     saved_recipe_count,
//     defaults: [{ label, value }],
//   }
//
// A card row names the checkpoint only; ⓘ lists every model, so a stack whose
// difference is "other models" always has the models behind it.

const RECIPE = "recipe";

/**
 * How many chips fit on one line, leaving room for a "+N" chip when some do not.
 *
 * At least one chip is always shown (it ellipsizes instead), because a row that
 * reads only "+3" says nothing about what is in it.
 */
export function fitChipCount(widths, available, gap, moreWidth) {
  const total = widths.reduce((sum, w) => sum + w, 0);
  if (total + gap * Math.max(widths.length - 1, 0) <= available) {
    return widths.length;
  }
  let used = 0;
  let fits = 0;
  for (const width of widths) {
    used += width + gap;
    if (used + moreWidth > available) break;
    fits += 1;
  }
  return Math.min(Math.max(fits, 1), widths.length);
}

export function isStack(card) {
  return (card.stack_size ?? 0) > 1;
}

/** The model the card's second row names: the checkpoint, or the first slot. */
export function checkpointModel(card) {
  const models = card.models ?? [];
  return (
    models.find((model) => model.kind === "checkpoint") ?? models[0] ?? null
  );
}

/** The LoRA row: a filled chip per workflow LoRA, a dashed one per recipe slot. */
export function loraChips(card) {
  return (card.loras ?? []).map((lora, i) => ({
    key: `lora-${i}`,
    label: lora.mark === RECIPE ? "recipe LoRA" : lora.name,
    icon: lora.mark === RECIPE ? "plus" : "layers",
    dashed: lora.mark === RECIPE,
  }));
}

/**
 * The special-facts row: what a stack differs by, otherwise the workflow's type
 * and whether it was imported.
 */
export function factChips(card) {
  const labels = isStack(card)
    ? (card.differs_by ?? [])
    : [
        ...(card.differs_by ?? []),
        card.type,
        card.imported ? "imported" : null,
      ].filter(Boolean);
  return labels.map((label, i) => ({ key: `fact-${i}`, label, fact: true }));
}

/** "4.9 of 5", or null when nothing is rated (0 or missing). */
export function ratingLabel(rating) {
  return rating > 0 ? `${rating.toFixed(1)} of 5` : null;
}

/**
 * The card's accessible name. "+N" is not a control, so this is the only place
 * a screen reader hears the chips a narrow card clipped, and it says "workflow"
 * or "recipe" because the dashed border is not announced.
 */
export function cardAccessibleName(card) {
  const count = card.picture_count ?? 0;
  const checkpoint = checkpointModel(card);
  const loras = (card.loras ?? []).map((lora) =>
    lora.mark === RECIPE ? "recipe LoRA slot" : `${lora.name}, workflow LoRA`,
  );
  const facts = factChips(card).map((chip) => chip.label);
  const parts = [
    card.name,
    isStack(card) ? `stack of ${card.stack_size} workflows` : null,
    checkpoint ? `${checkpoint.kind} ${checkpoint.name}` : null,
    loras.length ? `LoRAs: ${loras.join("; ")}` : "no LoRAs",
    facts.length
      ? `${isStack(card) ? "differs by" : "facts"}: ${facts.join(", ")}`
      : null,
    count === 1 ? "1 picture" : `${count} pictures`,
    ratingLabel(card.rating) ? `rated ${ratingLabel(card.rating)}` : null,
  ];
  return parts.filter(Boolean).join(", ");
}
