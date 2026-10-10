// Pure helpers behind WorkflowCard and ChipRow, kept out of the components so
// the fitting arithmetic and the accessible name are testable without layout.
//
// THE CARD SHAPE. One card as `GET /workflows` serves it: ONE PER WORKFLOW
// since #1623, keyed by `id`. It is **snake_case, like every other response
// this app reads**:
//
//   {
//     id,                               // the workflow, OPAQUE: `auto:<digest>`
//                                       // or `manual:<uuid hex>`. Every
//                                       // `/workflows/{id}/…` route, the Run
//                                       // body's `workflow_id` and the picture
//                                       // grid's `workflow=` filter take it
//     name, type, type_label, imported, hidden,
//     manual, from_name,                // `manual`: a stored document of its
//                                       // own (imported, pulled, duplicated,
//                                       // extracted from a recipe), never
//                                       // grouped. `from_name` names what a
//                                       // manual one was made from, or null
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
//               quant, slot_label }],   // the workflow's BASE card's
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
//               quant, slot_label }],   // the base card's LoRA loaders
//     recipe_values: { checkpoints: [{ name, pictures }],
//                      loras: [{ name, pictures }] },
//                                       // the checkpoint and LoRA filenames
//                                       // the workflow's kept pictures used,
//                                       // most used first. `name` is what the
//                                       // picture grid's `comfyui_model` /
//                                       // `comfyui_lora` filters take
//     default_recipe: null | { sampled, models: [{ address, kind, filename,
//                      provenance }], loras: [{ filename, sha256, strength,
//                      provenance }], values: [default], stages: { upscale,
//                      face_detailer, seed_variance } },
//                                       // NULL ON THE GRID; filled on the
//                                       // detail read and on write answers.
//                                       // `models[].address` is what a run's
//                                       // `models` pin names
//     base_topology, topologies: [string],
//                                       // every graph shape the workflow
//                                       // holds, sorted
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
//     created_at, changed_at,           // ISO: when the hub first had the
//                                       // workflow and when it last changed
//                                       // (a manual one's newest version, an
//                                       // automatic one's newest recipe). The
//                                       // *Recently created* / *changed*
//                                       // sorts, with last_used's null rule.
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
// graph carries instead); ⓘ lists every model.

import { quantBadge } from "./modelShelf.js";
import { cropImgStyle } from "./squareCrop.js";

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
 * cell is a little narrower than its share: 0.8% on the card's narrowest
 * cell. Not worth a `calc` here — `cropImgStyle` spends the mismatch on a
 * couple of cropped pixels rather than on a squeeze, and says so.
 *
 * It is a hand-copy of arithmetic that lives in the card's stylesheet, so
 * `WorkflowCard.test.js` derives the card's ratios from its own `<style>`
 * block, and the two drifting apart is a failing test rather than a wrong
 * crop.
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
  return baseModels(card)[0] ?? null;
}

/**
 * Every base model the card is about: each named slot of its first base kind,
 * one per FILE. Two files the shelf gives one title are still two models, and
 * the Checkpoint filter matches by file, so the dedup is on `name`; a surface
 * printing them dedups the joined text instead (`baseModelText`). A Wan 2.2 graph loads a high- and a low-noise UNET and is both,
 * so a surface that names "the" model lists these, never only the first. The
 * server names each loader by the file wired into it where it can (#1691),
 * but nothing says which loader is high and which low, so no surface claims it.
 * Mirrors `_base_model_slots` in `routes/workflows.py`, which names the card.
 */
export function baseModels(card) {
  const models = card.models ?? [];
  for (const kind of BASE_MODEL_KINDS) {
    const named = models.filter((model) => model.kind === kind && model.name);
    if (named.length) {
      const seen = new Set();
      return named.filter(
        (model) => !seen.has(model.name) && seen.add(model.name),
      );
    }
  }
  return [];
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
 * The base models as a row prints them, `A + B`, each spelling once: two
 * files under one shelf title read as that title, not `T + T`. `label` maps a
 * model to its text and defaults to `modelDisplayName`.
 */
export function baseModelText(models, label = modelDisplayName) {
  return [...new Set(models.map(label))].join(" + ");
}

/**
 * The two types whose served label is long enough to cost the facts row a
 * chip, said short (#1485). The other three are one word already. Only the
 * visible chip: the accessible name keeps `type_label`.
 */
const SHORT_TYPE_LABELS = { txt2img: "T2I", img2img: "I2I" };

/** The type the way the card's chip spells it (`T2I`), or "" for none. */
export function shortTypeLabel(card) {
  return SHORT_TYPE_LABELS[card.type] || card.type_label || card.type || "";
}

/**
 * The special-facts row: the workflow's type, whether it is manual or was
 * imported, and what a manual one was made from. `short` spells the type the
 * way the card's chip does (`T2I`).
 *
 * "Manual" is the one BADGE in the row (a pill, semibold, still the fact's
 * neutral ink): it is the kind of workflow, not one more datum. It replaces
 * "imported", which every manual workflow is. "from <name>" goes last because
 * it is the longest and the first the row may clip; ⓘ prints it whole.
 */
export function factChips(card, { short = false } = {}) {
  const labels = [
    // **First.** A hidden card is only ever drawn because somebody chose
    // *Hidden: Show* (F7), and the row clips to "+N" — a mark that can be
    // clipped away is a card that reads as an ordinary one in the grid it was
    // deliberately kept out of.
    card.hidden ? "hidden" : null,
    // Second, for the same reason: what kind of workflow this is.
    card.manual ? "Manual" : null,
    // The SERVED label, so the chip and a generated name say the type in
    // one vocabulary rather than reading `Text to Image` on row 1 and
    // `txt2img` on row 4 - or its abbreviation, which a generated name
    // already spells out in full. Falls back to the token for a payload
    // that predates `type_label`.
    short ? shortTypeLabel(card) : card.type_label || card.type,
    card.imported && !card.manual ? "imported" : null,
    card.manual && card.from_name ? `from ${card.from_name}` : null,
  ].filter(Boolean);
  return labels.map((label, i) => ({
    key: `fact-${i}`,
    label,
    fact: true,
    badge: Boolean(card.manual) && label === "Manual",
  }));
}

/** "4.9 of 5", or null when nothing is rated (0 or missing). */
export function ratingLabel(rating) {
  return rating > 0 ? `${rating.toFixed(1)} of 5` : null;
}

/**
 * The card's accessible name. "+N" is not a control, so this is the only place
 * a screen reader hears the chips a narrow card clipped.
 */
export function cardAccessibleName(card) {
  const count = card.picture_count ?? 0;
  const bases = baseModels(card);
  const checkpoint = bases[0];
  const loras = (card.loras ?? []).map((lora) => {
    const quant = quantBadge(lora.quant);
    return `${modelDisplayName(lora)}, ${quant ? `${quant.title}, ` : ""}workflow LoRA`;
  });
  const facts = factChips(card).map((chip) => chip.label);
  const parts = [
    card.name,
    // The precision is SPOKEN, not just drawn. Every chip on the card is
    // `aria-hidden` and this string is the whole of what a screen reader gets,
    // so a badge left out here is a badge that does not exist for that reader -
    // and it is the one thing telling two quant variants of one model apart.
    checkpoint
      ? [
          checkpoint.kind,
          [
            ...new Set(
              bases.map((model) =>
                [modelDisplayName(model), quantBadge(model.quant)?.title]
                  .filter(Boolean)
                  .join(" "),
              ),
            ),
          ].join(" and "),
        ].join(" ")
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
    facts.length ? `facts: ${facts.join(", ")}` : null,
    count === 1 ? "1 picture" : `${count} pictures`,
    ratingLabel(card.rating) ? `rated ${ratingLabel(card.rating)}` : null,
    card.origin_category === "comfyui" ? "from ComfyUI" : null,
  ];
  return parts.filter(Boolean).join(", ");
}

/**
 * Why a missing model has no "Replace with…" (`replacements_reason` of
 * `GET …/model-swap?replacing=`), in words.
 */
export const NO_REPLACEMENT_TEXT = {
  no_checkpoint:
    "Nothing to offer: the checkpoint is not on your shelf, so nothing says what goes with it.",
  none_go_with_it: "Nothing on your shelf is known to work with this checkpoint.",
  none_loadable:
    "What works with this checkpoint is not something this loader can load.",
  needs_pixlstash_nodes:
    "What works with this checkpoint needs a PixlStash loader, and ComfyUI-PixlStash is not installed in ComfyUI.",
  unread: "Could not read what could replace it just now.",
};

/**
 * Beside a missing checkpoint's picker when its offer was not held to a base
 * model (`replacements_narrowed: false`): nothing said which it was, or
 * nothing loadable has it, so the list is every checkpoint the loader takes.
 */
export const UNMATCHED_REPLACEMENTS_TEXT =
  "No checkpoint this workflow can load is known to match it, so every one it can load is listed: pick one its LoRAs were made for.";

/**
 * A replacement candidate as a "Replace with…" option reads it.
 *
 * `declared`: only the file layout fits; nothing has run with it. `loader`:
 * the workflow's loader cannot load it, so a run swaps in PixlStash's.
 */
export function replacementLabel(model) {
  return `${model.display_name || model.filename}${
    model.via === "declared" ? " (untested)" : ""
  }${model.loader ? " (through a PixlStash loader)" : ""}`;
}

/**
 * A "Replace with…" picker's options for a `model-swap` answer's
 * `replacements`; none at all when there is nothing to offer.
 *
 * @param {?Array<Object>} models
 * @returns {Array<{value: string, label: string}>}
 */
export function replacementOptions(models) {
  return models?.length
    ? [
        { value: "", label: "Replace with…" },
        ...models.map((model) => ({
          value: model.filename,
          label: replacementLabel(model),
        })),
      ]
    : [];
}
