// Pure helpers behind WorkflowCard and ChipRow, kept out of the components so
// the fitting arithmetic and the accessible name are testable without layout.
//
// The card's input is one workflow card from the Workflows & Recipes contract:
//   { key, name, checkpoint, loras: [{ name, recipe }], type, imported,
//     differsBy: [string], pictureCount, rating, covers: [url],
//     stackSize, savedRecipeCount, defaults: [{ label, value }] }
// A LoRA with `recipe: true` is a slot the recipe fills, not a file the
// workflow carries. A `stackSize` of 2 or more makes the card a stack.

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
  return (card.stackSize ?? 0) > 1;
}

/** The LoRA row's chips: solid for the workflow's own, dashed for a slot. */
export function loraChips(card) {
  return (card.loras ?? []).map((lora, i) => ({
    key: `lora-${i}`,
    label: lora.recipe ? "recipe LoRA" : lora.name,
    icon: lora.recipe ? "plus" : "layers",
    variant: lora.recipe ? "dashed" : "solid",
  }));
}

/**
 * The special-facts row: what a stack differs by, otherwise the workflow's type
 * and whether it was imported.
 */
export function factChips(card) {
  const labels = isStack(card)
    ? (card.differsBy ?? [])
    : [
        ...(card.differsBy ?? []),
        card.type,
        card.imported ? "imported" : null,
      ].filter(Boolean);
  return labels.map((label, i) => ({
    key: `fact-${i}`,
    label,
    variant: "fact",
  }));
}

/** "4.9 of 5", or null when nothing is rated (0 or missing). */
export function ratingLabel(rating) {
  return rating > 0 ? `${rating.toFixed(1)} of 5` : null;
}

/**
 * The card's accessible name. "+N" is not a control, so this is the only place
 * a screen reader hears the chips a narrow card clipped, and it says "workflow"
 * or "recipe" because the solid/dashed border is not announced.
 */
export function cardAccessibleName(card) {
  const count = card.pictureCount ?? 0;
  const loras = (card.loras ?? []).map((lora) =>
    lora.recipe ? "recipe LoRA slot" : `${lora.name}, workflow LoRA`,
  );
  const facts = factChips(card).map((chip) => chip.label);
  const parts = [
    card.name,
    isStack(card) ? `stack of ${card.stackSize} workflows` : null,
    card.checkpoint ? `checkpoint ${card.checkpoint}` : null,
    loras.length ? `LoRAs: ${loras.join("; ")}` : "no LoRAs",
    facts.length
      ? `${isStack(card) ? "differs by" : "facts"}: ${facts.join(", ")}`
      : null,
    count === 1 ? "1 picture" : `${count} pictures`,
    ratingLabel(card.rating) ? `rated ${ratingLabel(card.rating)}` : null,
  ];
  return parts.filter(Boolean).join(", ");
}
