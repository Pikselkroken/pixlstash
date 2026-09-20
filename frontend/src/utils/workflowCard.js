// Pure helpers behind WorkflowCard and ChipRow, kept out of the components so
// the fitting arithmetic and the accessible name are testable without layout.
//
// THE CARD SHAPE. One card as `GET /workflows/cards` will serve it (plan §6.4,
// step B3). It is **snake_case, like every other response this app reads**, and
// a slot's `mark` uses B1's own vocabulary (#1390: `structural` | `recipe`), so
// nothing translates between the two and inverts a meaning on the way:
//
//   {
//     key, name, type, type_label, imported,
//                                       // `type_label` is `type` as ComfyUI
//                                       // spells it ("Text to Image"); the
//                                       // name row and the type chip both
//                                       // read it, so one fact has one voice
//     models: [{ name, kind, mark?, slot_label? }],
//                                       // every non-LoRA slot: checkpoint,
//                                       // unet, vae, clip… `kind` is the slot
//     loras:  [{ name, mark, slot_label? }],
//                                       // "structural" = in the workflow
//                                       // (a filled chip), "recipe" = a slot
//                                       // the recipe fills (dashed).
//                                       // `slot_label` is the address
//                                       // `PUT /workflows/{key}/slots` marks
//     differs_by: [string],             // a stack: the union over its members
//     picture_count, rating,            // rating 1-5; 0 or null is unrated
//     covers: [url],                    // up to 3, the cover first. An
//                                       // API-RELATIVE path, so a consumer
//                                       // putting one in an <img src> has to
//                                       // prepend API_BASE_URL and append the
//                                       // share token itself (WorkflowCard).
//     stack_size,                       // 2 or more makes the card a stack
//     saved_recipe_count,
//     defaults: [{ label, slot_label, input_name, value, provenance }],
//                                       // `provenance` is best | all | edited
//     // Read by the Workflows grid rather than by the card itself, and listed
//     // here because this block is the shape's one description (F1a, #1402):
//     rank,                             // the Bayesian cover rank the grid is
//                                       // ordered by. NOT `rating`: it is
//                                       // smoothed towards the library mean,
//                                       // so it orders cards against each
//                                       // other and means nothing alone.
//     stack_id,                         // the stack this card sits in, or
//                                       // null. `PUT /workflows/stacks/{id}/
//                                       // order` is addressed by it and
//                                       // nothing else on the card derives
//                                       // it (F2, #1405).
//     last_used,                        // ISO, or null when the card has no
//                                       // kept pictures. The *Recently used*
//                                       // sort; null sorts below every date.
//   }
//
// A card row names the BASE MODEL only (checkpoint, or the unet a Flux or SD3
// graph carries instead); ⓘ lists every model, so a stack whose difference is
// "other models" always has the models behind it.

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

/**
 * The slot kinds that name the BASE MODEL, most preferred first.
 *
 * **Kept identical to `BASE_MODEL_KINDS`** in
 * `services/workflow_card_service.py`, which the server derives from
 * `CHECKPOINT_WIDGETS`, and asserted by
 * `tests/test_architecture_guardrails.py::test_base_model_kinds_agree_across_the_stack`.
 *
 * A list here at all because this is a PREFERENCE among slots the payload
 * already carries, not a fact about the graph - but a preference that has to
 * agree with the one the card was NAMED by, or the name row and the model row
 * describe different models. That pair is exactly what drifted before (#1416),
 * so it is asserted rather than agreed.
 */
const BASE_MODEL_KINDS = [
  "checkpoint",
  "unet",
  "checkpoint_id",
  "diffusion_model",
  "model_path",
];

/**
 * The model the card's second row names: its base model.
 *
 * **Never "the first slot".** It was, and slot order is document order, so a
 * Flux or SD3 graph - which has no `checkpoint` kind at all, only `unet` -
 * showed its VAE or a text encoder as the model the card is about, and said so
 * to a screen reader too. The card's name row learned this first and the two
 * halves of one card then disagreed. `null` when a graph loads no base model,
 * so the row can say so rather than name an accessory.
 */
export function checkpointModel(card) {
  const models = card.models ?? [];
  for (const kind of BASE_MODEL_KINDS) {
    const match = models.find((model) => model.kind === kind && model.name);
    if (match) return match;
  }
  return null;
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
        // The SERVED label, so the chip and a generated name say the type in
        // one vocabulary rather than reading `Text to Image` on row 1 and
        // `txt2img` on row 4. Falls back to the token for a payload that
        // predates `type_label`.
        card.type_label ?? card.type,
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
 *
 * `member` is a row inside its own stack's panel. It carries the WHOLE stack's
 * `stack_size` — the service sets it on every member deliberately, so a member
 * opened alone still shows what it differs by — so without this every row in
 * the panel announced itself as "stack of N workflows" while sitting inside
 * that very stack. The difference chips stay: they are what the row is for.
 */
export function cardAccessibleName(card, { member = false } = {}) {
  const count = card.picture_count ?? 0;
  const checkpoint = checkpointModel(card);
  const loras = (card.loras ?? []).map((lora) =>
    lora.mark === RECIPE ? "recipe LoRA slot" : `${lora.name}, workflow LoRA`,
  );
  const facts = factChips(card).map((chip) => chip.label);
  const parts = [
    card.name,
    isStack(card) && !member ? `stack of ${card.stack_size} workflows` : null,
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
