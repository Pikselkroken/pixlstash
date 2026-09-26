// Pure helpers behind WorkflowCard and ChipRow, kept out of the components so
// the fitting arithmetic and the accessible name are testable without layout.
//
// THE CARD SHAPE. One card as `GET /workflows` will serve it (plan §6.4,
// step B3). It is **snake_case, like every other response this app reads**, and
// a slot's `mark` uses B1's own vocabulary (#1390: `structural` | `recipe`), so
// nothing translates between the two and inverts a meaning on the way:
//
//   {
//     key, name, type, type_label, imported, hidden,
//                                       // `hidden` is only ever true when the
//                                       // grid asked for the hidden cards
//                                       // (F7's *Hidden: Show*), and
//                                       // `factChips` leads the row with it:
//                                       // unmarked, such a card reads as an
//                                       // ordinary one.
//                                       // `type_label` is `type` as ComfyUI
//                                       // spells it ("Text to Image"); the
//                                       // name row and the type chip both
//                                       // read it, so one fact has one voice
//     models: [{ name, title, icon, base_model, base_model_folded, kind,
//               quant, mark, slot_label }],
//                                       // EVERY key is always present: these
//                                       // are Pydantic defaults, so an
//                                       // absent value arrives as null and
//                                       // never as a missing key.
//                                       // every non-LoRA slot: checkpoint,
//                                       // unet, vae, clip… `kind` is the slot.
//                                       // `name` is DERIVED, not the raw
//                                       // widget value: no folder, no
//                                       // extension and no quant postfix
//                                       // (`t5xxl`, not
//                                       // `t5xxl_fp8_e4m3fn.safetensors`).
//                                       // Null for a slot whose name the
//                                       // recipe never recorded.
//                                       // `quant` is the precision that was
//                                       // stripped off it, as one canonical
//                                       // id (`bf16`, `fp8_e4m3`, `q4_k_m`,
//                                       // `mixed`) - the shelf's own column
//                                       // where it holds the file, the
//                                       // filename postfix otherwise, and
//                                       // null where neither says. `quantBadge`
//                                       // (utils/modelShelf.js) turns it into
//                                       // words; it is what keeps two quant
//                                       // variants of one model apart once
//                                       // their names have collapsed into one
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
//                                       // shelf serves them by. Row 2's
//                                       // marks are drawn out of these
//                                       // (#1485). Usually all null
//     loras:  [{ name, title, icon, base_model, base_model_folded, kind,
//               quant, mark, slot_label }],
//                                       // "structural" = in the workflow
//                                       // (a mark), "recipe" = a slot the
//                                       // recipe fills (drawn as the pile
//                                       // of `recipe_loras`).
//                                       // `slot_label` is the address
//                                       // `PUT /workflows/{key}/slots` marks
//     recipe_loras: [{ name, recipes, character_id, character_name }],
//                                       // every LoRA the card's recipes put
//                                       // in its recipe slots, most used
//                                       // first; `recipes` is how many.
//                                       // `character_id` is null for a LoRA
//                                       // attached to no one character
//     differs_by: [string],             // a stack member: its chips against
//                                       // the cover; empty on the cover
//     picture_count, rating,            // rating 1-5; 0 or null is unrated
//     covers: [{ url, picture_id, thumbnail_width, thumbnail_height,
//                square_crop_x, square_crop_y, square_crop_side }],
//                                       // `picture_id` is the picture the
//                                       // cover DRAWS, so a client can open
//                                       // it (#1455): the id is inside the
//                                       // url and parsing it back out is a
//                                       // path shape, not an interface.
//                                       // up to 3, the cover first. `url` is
//                                       // API-RELATIVE, so a consumer putting
//                                       // one in an <img src> has to prepend
//                                       // API_BASE_URL and append the share
//                                       // token itself (`workflowCoverUrl`).
//                                       // The rest is the stored face-weighted
//                                       // square rectangle within the bitmap,
//                                       // which `coverCellStyle` below fits
//                                       // the cell's own ratio around; all
//                                       // three are null until the
//                                       // picture has been processed (#1465).
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
//     members: [{ key, name, sets_apart, differs_by }],
//                                       // the WHOLE stack in its order, this
//                                       // card included; [] outside one.
//                                       // Members often share a generated
//                                       // `name`, so `sets_apart` is what
//                                       // each loads that not every member
//                                       // does, and `differs_by` its chips
//                                       // against the cover ([] on it)
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
//                                       // On such a card an EMPTY model row
//                                       // means nobody read it, never that
//                                       // the workflow has none — see
//                                       // `checkpointUnread`.
//   }
//
// A card row names the BASE MODEL only (checkpoint, or the unet a Flux or SD3
// graph carries instead); ⓘ lists every model, so a stack whose difference is
// "other models" always has the models behind it.

import { quantBadge } from "./modelShelf.js";
import { cropImgStyle } from "./squareCrop.js";

const RECIPE = "recipe";

/**
 * The aspect ratio of one cover cell, by how many pictures the strip holds.
 *
 * The cover box is 6:5 at every count and only the tracks inside it change
 * (#1456), so the count alone fixes the shape of every cell in it - the same
 * arithmetic `WorkflowCard.vue`'s stylesheet states, against a cover W wide
 * and (5/6)W tall:
 *   one    W × (5/6)W                    → 6/5
 *   two    (1/2)W × (5/6)W               → 3/5, twice
 *   three  big (2/3)W × (5/6)W           → 4/5
 *          small (1/3)W × (5/12)W        → 4/5
 * The mosaic's three cells are all 4:5, which is why this takes no index.
 *
 * These are the shapes the tracks would make with NO gap between them. The
 * `--space-1` gap comes out of the cells rather than out of the box, so a real
 * cell is a little narrower than its share: 0.8% on the card's narrowest cell
 * and 2.1% on the stack panel's, whose box is a fixed 96×80 against the same
 * 2px. Not worth a `calc` here — `cropImgStyle` spends the mismatch on a
 * couple of cropped pixels rather than on a squeeze, and says so.
 *
 * Both surfaces that draw a strip read this: the card's cover and the stack
 * panel's List row, which draws the same arrangement one size down. It is a
 * hand-copy of arithmetic that lives in two stylesheets, so both are asserted
 * against it - `WorkflowCard.test.js` derives the card's ratios from its own
 * `<style>` block and `StackPanel.test.js` does the same for the row's, and
 * either drifting from this map is a failing test rather than a wrong crop.
 *
 * `undefined` for a count that is not 1, 2 or 3, and deliberately no default:
 * both callers cap the strip at three and return early on none, so there is no
 * such count to answer for. Should one ever arrive, `cropRectForRatio` refuses
 * a ratio that is not a positive number and the cell falls back to the
 * stylesheet's crop, which is the honest answer to "what shape is this cell?"
 * — a guessed 4:5 would crop it wrongly and look deliberate.
 */
const COVER_CELL_RATIO = { 1: 6 / 5, 2: 3 / 5, 3: 4 / 5 };

export function coverCellRatio(count) {
  return COVER_CELL_RATIO[count];
}

/**
 * The inline `<img>` style that crops one cover around its stored rectangle,
 * or null when there is no rectangle yet and CSS `cover` has to do it.
 *
 * Null is the shipped look and not a defect: `square_crop_x` is NULL while a
 * picture is still being processed, and those covers must keep today's
 * top-anchored crop rather than jumping to some invented framing (#1465).
 *
 * @param {Object} cover - One `covers` entry.
 * @param {number} count - How many cells the strip is drawing.
 * @returns {Object|null}
 */
export function coverCellStyle(cover, count) {
  return cropImgStyle(cover, coverCellRatio(count));
}

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
 * A card with no recipe: every model it names was recovered from its own file.
 *
 * `variant_count: 0` (#1466) is a stored editor-format workflow file and
 * nothing else — no recipe, so no cached slot list, and its models are
 * whatever could be read out of the file's own widget values.
 *
 * A card carrying no `variant_count` at all is read as having a recipe: every
 * card the route serves carries one, so the missing case is a caller that
 * predates the field, and the ordinary answer is the safe one to give it.
 */
function noRecipe(card) {
  return (card.variant_count ?? 1) === 0;
}

/**
 * Whether the base-model row is silent because nobody read it.
 *
 * **An empty row on a recipe-less card is never a claim.** The recovery finds
 * loaders by class, and no list of classes is every loader there is — so a
 * graph loading through one it does not know recovers its LoRAs, leaves
 * `models` empty, and the row would say "No checkpoint" about a workflow that
 * plainly has one. That is the exact sentence this card exists to stop a card
 * making, one loader family over, so a card with no recipe says nothing at all
 * about what its file did not name.
 */
export function checkpointUnread(card) {
  return noRecipe(card) && !checkpointModel(card);
}

/**
 * Whether the graph loads a base model the card has no name for.
 *
 * A `checkpoint` or `unet` slot whose name was never recorded, or was
 * forgotten because the shelf no longer holds the file: either way nothing can
 * load it. Read off `BASE_MODEL_KINDS` so the list the guardrail checks is the
 * one this asks. The card alone cannot see a NAMED file ComfyUI lacks; only the
 * run pre-flight can, which is the Workflow tab's business, not the grid's.
 */
export function checkpointMissing(card) {
  return (card.models ?? []).some(
    (model) => BASE_MODEL_KINDS.includes(model.kind) && !model.name,
  );
}

/** The same for the LoRA row, and for the same reason. */
export function lorasUnread(card) {
  return noRecipe(card) && !(card.loras ?? []).length;
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
const BASE_MODEL_KINDS = ["checkpoint", "unet"];

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

/**
 * The two types whose served label is long enough to cost the facts row a
 * chip, said short (#1485). The other three are one word already. Only the
 * visible chip: the accessible name keeps `type_label`.
 */
const SHORT_TYPE_LABELS = { txt2img: "T2I", img2img: "I2I" };

/**
 * The special-facts row: what a stack member differs by from its cover,
 * otherwise the workflow's type and whether it was imported. `short` spells
 * the type the way the card's chip does (`T2I`).
 *
 * The cover - which is also what the grid draws for a collapsed stack - has no
 * chips of its own, so it falls through to the type like a lone card rather
 * than leaving its row empty.
 */
export function differsBy(card) {
  return isStack(card) && Boolean(card.differs_by?.length);
}

export function factChips(card, { short = false } = {}) {
  const labels = differsBy(card)
    ? // **First, and on a stack too.** A hidden card is only ever drawn
      // because somebody chose *Hidden: Show* (F7), and the row
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
        // `txt2img` on row 4 - or its abbreviation, which a generated name
        // already spells out in full. Falls back to the token for a payload
        // that predates `type_label`.
        (short && SHORT_TYPE_LABELS[card.type]) || card.type_label || card.type,
        card.imported ? "imported" : null,
      ].filter(Boolean);
  return labels.map((label, i) => ({ key: `fact-${i}`, label, fact: true }));
}

/**
 * One recipe LoRA as the card names it: its character's, where it has one,
 * because that is who the pile shows. A character with several LoRAs in
 * `all` (two versions, say) draws the same face for each, so there the
 * LoRA's own name - which is where a version lives - follows the character's.
 */
export function recipeLoraLabel(lora, all = []) {
  if (!lora.character_name) return lora.name;
  const shared = all.filter(
    (other) => other.character_id === lora.character_id,
  ).length;
  return shared > 1
    ? `${lora.character_name} · ${lora.name}`
    : `${lora.character_name}'s LoRA`;
}

/**
 * Row 3's count of what the recipe slots have held, people apart from the
 * rest: "3 characters", "1 character + 1 recipe LoRA", "1 recipe LoRA". ""
 * for none, so the caller can fall back to counting the slot itself.
 */
export function recipeLoraCount(recipeLoras) {
  const people = new Set(
    recipeLoras
      .filter((lora) => lora.character_id != null)
      .map((lora) => lora.character_id),
  ).size;
  const rest = recipeLoras.filter((lora) => lora.character_id == null).length;
  return [
    people ? (people === 1 ? "1 character" : `${people} characters`) : "",
    rest ? (rest === 1 ? "1 recipe LoRA" : `${rest} recipe LoRAs`) : "",
  ]
    .filter(Boolean)
    .join(" + ");
}

/** "4.9 of 5", or null when nothing is rated (0 or missing). */
export function ratingLabel(rating) {
  return rating > 0 ? `${rating.toFixed(1)} of 5` : null;
}

/**
 * The card's accessible name. "+N" is not a control, so this is the only place
 * a screen reader hears the chips a narrow card clipped, and it says "workflow"
 * or "recipe" because which kind a mark is shows only by where it sits.
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
  const cast = (card.recipe_loras ?? []).map((lora, _, all) =>
    recipeLoraLabel(lora, all),
  );
  const loras = (card.loras ?? []).map((lora) => {
    if (lora.mark === RECIPE) return "recipe LoRA slot";
    const quant = quantBadge(lora.quant);
    return `${modelDisplayName(lora)}, ${quant ? `${quant.title}, ` : ""}workflow LoRA`;
  });
  const facts = factChips(card).map((chip) => chip.label);
  const parts = [
    card.name,
    isStack(card) && !member ? `stack of ${card.stack_size} workflows` : null,
    // The precision is SPOKEN, not just drawn. Every chip on the card is
    // `aria-hidden` and this string is the whole of what a screen reader gets,
    // so a badge left out here is a badge that does not exist for that reader -
    // and it is the one thing telling two quant variants of one model apart.
    checkpoint
      ? [
          checkpoint.kind,
          modelDisplayName(checkpoint),
          quantBadge(checkpoint.quant)?.title,
        ]
          .filter(Boolean)
          .join(" ")
      : null,
    // "not read" rather than "no LoRAs" where nothing was read at all: such a
    // card is silent about its models, not certain it has none.
    // What the visible rows say, in the same words: a card with no recipe is
    // silent about what its file did not name, never certain it has none.
    checkpointUnread(card) ? "base model not read" : null,
    !checkpoint && checkpointMissing(card) ? "checkpoint missing" : null,
    loras.length
      ? `LoRAs: ${loras.join("; ")}`
      : lorasUnread(card)
        ? "LoRAs not read"
        : "no LoRAs",
    cast.length ? `recipe LoRAs used: ${cast.join("; ")}` : null,
    facts.length
      ? `${differsBy(card) ? "differs by" : "facts"}: ${facts.join(", ")}`
      : null,
    count === 1 ? "1 picture" : `${count} pictures`,
    ratingLabel(card.rating) ? `rated ${ratingLabel(card.rating)}` : null,
  ];
  return parts.filter(Boolean).join(", ");
}

// The chips that only say a model differs, which `sets_apart` already names.
const MODEL_CHIPS = new Set(["other checkpoint", "other models"]);

/** "Name — what sets it apart": the models only it loads, then its chips. */
function memberLabel(member) {
  const models = member.sets_apart || [];
  const chips = (member.differs_by || []).filter(
    (chip) => !models.length || !MODEL_CHIPS.has(chip),
  );
  const apart = [...models, ...chips];
  return apart.length ? `${member.name} — ${apart.join(", ")}` : member.name;
}

/** Rows that still read alike are numbered, as the Pictures rows are. */
function numberAlike(rows) {
  const total = {};
  for (const row of rows) total[row.label] = (total[row.label] || 0) + 1;
  const seen = {};
  return rows.map((row) => {
    if (total[row.label] < 2) return row;
    seen[row.label] = (seen[row.label] || 0) + 1;
    return { ...row, label: `${row.label} (${seen[row.label]})` };
  });
}

/**
 * A stack's members as picker options, in the stack's order: members often
 * share a generated name, so each says what sets it apart from the others.
 */
export function stackMemberOptions(members) {
  return numberAlike(
    (members || []).map((member) => ({
      value: member.key,
      label: memberLabel(member),
    })),
  );
}
