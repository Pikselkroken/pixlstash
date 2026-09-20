// Pure helpers behind WorkflowCard and ChipRow, kept out of the components so
// the fitting arithmetic and the accessible name are testable without layout.
//
// THE CARD SHAPE. One card as `GET /workflows/cards` will serve it (plan §6.4,
// step B3). It is **snake_case, like every other response this app reads**, and
// a slot's `mark` uses B1's own vocabulary (#1390: `structural` | `recipe`), so
// nothing translates between the two and inverts a meaning on the way:
//
//   {
//     key, name, type, type_label, imported, hidden,
//                                       // `hidden` is only ever true when the
//                                       // grid asked for the hidden cards
//                                       // (F7's *Show hidden workflows*), and
//                                       // `factChips` leads the row with it:
//                                       // unmarked, such a card reads as an
//                                       // ordinary one.
//                                       // `type_label` is `type` as ComfyUI
//                                       // spells it ("Text to Image"); the
//                                       // name row and the type chip both
//                                       // read it, so one fact has one voice
//     models: [{ name, title, icon, base_model, base_model_folded, kind,
//               mark?, slot_label? }],
//                                       // every non-LoRA slot: checkpoint,
//                                       // unet, vae, clip… `kind` is the slot.
//                                       // `title` is the MODEL SHELF's name
//                                       // for the file and is what a client
//                                       // shows (`modelDisplayName`): the card's
//                                       // `name` row was built from it, so
//                                       // showing `name` beside it describes
//                                       // one model twice. Null where the
//                                       // shelf has not scanned the file
//                                       // `icon` is the sha256 of the
//                                       // picture the owner chose for the
//                                       // model on the shelf; the two
//                                       // base-model spellings are what a
//                                       // generated mark takes its colour
//                                       // from, under the same names the
//                                       // shelf serves them by. A card with
//                                       // no pictures draws itself out of
//                                       // these (#1466). Usually all null
//     loras:  [{ name, title, icon, base_model, base_model_folded, mark,
//               slot_label? }],
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
//     specials: [string] | null,        // the post-processing the workflow
//                                       // carries ("upscale",
//                                       // "face_detailer"), which `name`
//                                       // already spells as "+ FaceDetailer".
//                                       // NULL IS NOT []: null means nothing
//                                       // has read the card's document for
//                                       // it yet, [] means it has and the
//                                       // graph carries none
//     stack_size,                       // 2 or more makes the card a stack
//     member_keys,                      // the stack's OTHER cards, so it is
//                                       // `stack_size - 1` long. The service
//                                       // sets the whole list INCLUDING this
//                                       // card; `routes/workflows.py` is what
//                                       // excludes self on the way out, and
//                                       // fills it for a stack only.
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
//     ghosts, model_ghosts,             // what the card keeps of something
//                                       // deleted, for F7's Ghosts row.
//                                       // `ghosts` are this library's picture
//                                       // ghosts; `model_ghosts` counts
//                                       // VALUES the shelf does not hold (a
//                                       // filename or a digest), so it is not
//                                       // a count of models. Neither is a
//                                       // library total - see the route.
//     last_used,                        // ISO, or null when the card has no
//                                       // kept pictures. The *Recently used*
//                                       // sort; null sorts below every date.
//     variant_count,                    // how many recipes the card is made
//                                       // of. ZERO IS A REAL CARD (#1466): a
//                                       // stored editor-format workflow file
//                                       // and nothing else, whose models were
//                                       // recovered from the file rather than
//                                       // read off a recipe. See
//                                       // `modelsUnread` — such a card with
//                                       // no models has not been read, and
//                                       // must not be said to have none.
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
 * Whether nobody has read this card's models, as against having read none.
 *
 * A card with `variant_count: 0` (#1466) is a stored workflow file and nothing
 * else. It has no recipe, so no cached slot list, and its models are whatever
 * could be recovered from the file's own widget values — which is the real
 * names out of a real file and nothing at all out of a template-style export
 * whose loaders were never filled in. An empty model row on such a card is
 * therefore "not read", and "no checkpoint" would be a claim nobody made.
 *
 * A card that carries no `variant_count` at all is read as having a recipe:
 * every card the route serves carries one, so the missing case is a caller
 * that predates the field, and the ordinary answer is the safe one to give it.
 */
export function modelsUnread(card) {
  return (
    (card.variant_count ?? 1) === 0 &&
    !(card.models ?? []).length &&
    !(card.loras ?? []).length
  );
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

/**
 * What to CALL a model the payload carries: the model shelf's name for it,
 * else the file it sits in.
 *
 * The same preference the server built the card's `name` row from, and it has
 * to be the same one: the row would otherwise read `Krea 2: Text to Image`
 * over a chip reading `realvisxl.safetensors`, which is one model described
 * twice and is exactly the pair that drifted in #1416. `title` is null for
 * every model the shelf has not scanned, which is the ordinary case, so this
 * falls through to the filename rather than blanking the chip.
 *
 * **Not `modelLabel`**, which `utils/filterChips.js` already exports and which
 * takes a NAME and strips its extension for the Filters menu. Two same-named
 * exports with incompatible arguments fail quietly in both directions - a
 * string through this one is `null`, an object through that one is
 * `[object Object]` - so the newcomer is the one that renames.
 */
export function modelDisplayName(model) {
  return model?.title || model?.name || null;
}

/** The LoRA row: a filled chip per workflow LoRA, a dashed one per recipe slot. */
export function loraChips(card) {
  return (card.loras ?? []).map((lora, i) => ({
    key: `lora-${i}`,
    label: lora.mark === RECIPE ? "recipe LoRA" : modelDisplayName(lora),
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
    ? // **First, and on a stack too.** A hidden card is only ever drawn
      // because somebody ticked *Show hidden workflows* (F7), and the row
      // clips to "+N" — a mark that can be clipped away is a card that reads
      // as an ordinary one in the grid it was deliberately kept out of.
      [card.hidden ? "hidden" : null, ...(card.differs_by ?? [])].filter(
        Boolean,
      )
    : [
        card.hidden ? "hidden" : null,
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
    lora.mark === RECIPE
      ? "recipe LoRA slot"
      : `${modelDisplayName(lora)}, workflow LoRA`,
  );
  const facts = factChips(card).map((chip) => chip.label);
  const parts = [
    card.name,
    isStack(card) && !member ? `stack of ${card.stack_size} workflows` : null,
    checkpoint ? `${checkpoint.kind} ${modelDisplayName(checkpoint)}` : null,
    // "not read" rather than "no LoRAs" where nothing was read at all: such a
    // card is silent about its models, not certain it has none.
    loras.length
      ? `LoRAs: ${loras.join("; ")}`
      : modelsUnread(card)
        ? "models not read"
        : "no LoRAs",
    facts.length
      ? `${isStack(card) ? "differs by" : "facts"}: ${facts.join(", ")}`
      : null,
    count === 1 ? "1 picture" : `${count} pictures`,
    ratingLabel(card.rating) ? `rated ${ratingLabel(card.rating)}` : null,
  ];
  return parts.filter(Boolean).join(", ");
}
