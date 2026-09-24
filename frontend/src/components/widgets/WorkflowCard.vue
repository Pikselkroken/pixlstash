<template>
  <article
    class="wf-card"
    :class="{ 'wf-card--stack': stackMark, 'wf-card--selected': selected }"
    role="group"
    :aria-label="accessibleName"
    data-testid="workflow-card"
  >
    <!-- One cell per picture, never a fixed three: the cell used to be drawn
         whether or not there was an image for it, so a workflow with one or two
         pictures showed its picture beside painted `input-background`
         rectangles and read as a card that had failed to load (#1456). -->
    <div
      v-if="hasPictures"
      class="wf-card__cover"
      :class="`wf-card__cover--${coverCells.length}`"
    >
      <!-- Inert, as they have always been: a cell is a `<span>` and a click
           on it selects the card like a click anywhere else. The tiles carry
           their picture's identity only so a RIGHT-CLICK can tell the host
           which one the pointer was over (#1455) - opening a picture is a
           context-menu verb, never a click, so nothing here takes a press and
           nothing is drawn over the picture. -->
      <span
        v-for="(cell, i) in coverCells"
        :key="i"
        class="wf-card__pic"
        :data-picture-id="cell.id ?? undefined"
        :data-picture-index="cell.id == null ? undefined : i + 1"
        :data-picture-total="cell.id == null ? undefined : coverCells.length"
      >
        <img
          v-if="cell.src"
          :src="cell.src"
          :style="cell.style"
          alt=""
          loading="lazy"
        />
      </span>
      <span class="wf-card__badge wf-card__badge--end" aria-hidden="true">
        <v-icon size="12">mdi-image-multiple</v-icon>{{ card.picture_count }}
      </span>
      <span
        v-if="rating"
        class="wf-card__badge wf-card__badge--end wf-card__badge--bottom"
        aria-hidden="true"
      >
        <v-icon size="12">mdi-star</v-icon>{{ card.rating.toFixed(1) }}
      </span>
    </div>
    <div
      v-else
      class="wf-card__cover wf-card__cover--empty"
      :class="{ 'wf-card__cover--stack': stackMark }"
    >
      <span v-if="card.type" class="wf-card__type" aria-hidden="true">{{
        card.type
      }}</span>
      <!-- A card with no recipe says nothing more here: its models are on the
           strip below, where every other card says what it is made of (#1485
           retired the cover marks #1466 put here). -->
      <span v-if="hasRecipe" class="wf-card__empty-line" aria-hidden="true"
        >No pictures yet</span
      >
      <AppButton
        variant="outline"
        size="sm"
        icon-left="play"
        tabindex="-1"
        @click="emit('run')"
        >Run it…</AppButton
      >
    </div>

    <!-- The layered count opens the stack too (#1402): it is the mark that
         says there are more cards under this one, so it is where a reader
         reaches first. A button, but `aria-hidden` and off the tab order like
         the rest of the card's decoration — ▸ below is the announced control,
         and hiding this one is only defensible while the two do exactly the
         same thing. So no `.stop`: like ▸, the click also reaches the row
         underneath and selects the card. -->
    <button
      v-if="stackMark"
      type="button"
      class="wf-card__badge wf-card__badge--start wf-card__badge--button"
      tabindex="-1"
      aria-hidden="true"
      @click="emit('toggle')"
    >
      <Tooltip :text="stackLabel" activator="parent" />
      <v-icon size="12">mdi-layers</v-icon>{{ card.stack_size }}
    </button>

    <!-- The visible rows are hidden from assistive tech: the card's own label
         already reads all of them, including what "+N" clipped. -->
    <div class="wf-card__meta">
      <div class="wf-card__row">
        <AppButton
          v-if="stackMark"
          class="wf-card__toggle"
          :class="{ 'wf-card__toggle--open': expanded }"
          variant="ghost"
          size="sm"
          icon-left="menu-right"
          icon-only
          tabindex="-1"
          :tooltip="
            expanded
              ? 'Hide the workflows in this stack'
              : 'Show the workflows in this stack'
          "
          :aria-expanded="String(expanded)"
          :aria-controls="panelId || undefined"
          @click="emit('toggle')"
        />
        <span class="wf-card__name" aria-hidden="true">{{ card.name }}</span>
      </div>
      <!-- Row 2, the strip: the base model's mark, a hairline, then one mark
           per LoRA (#1485). Names are in each mark's tooltip, and the card's
           accessible name reads them all. -->
      <div class="wf-card__row">
        <ul v-if="stripMarks.length" class="wf-card__strip" aria-hidden="true">
          <template v-for="mark in stripMarks" :key="mark.key">
            <li
              class="wf-card__mark"
              :class="{ 'wf-card__mark--slot': mark.slot }"
            >
              <Tooltip
                :text="mark.label"
                activator="parent"
                :describe="false"
              />
              <v-icon v-if="mark.slot" size="12">mdi-plus</v-icon>
              <ModelMark v-else :row="mark.row" />
            </li>
            <li v-if="mark.ruled" class="wf-card__rule" />
          </template>
          <li v-if="stripOverflow" class="wf-card__mark-more">
            +{{ stripOverflow }}
          </li>
        </ul>
        <span v-else class="wf-card__none" aria-hidden="true">{{
          modelsUnread ? "Models not read" : "No checkpoint"
        }}</span>
      </div>
      <!-- Row 3: the one name worth printing, the base model's, and how many
           LoRAs the strip holds. The checkpoint half is left out when row 2
           already said it, and the whole row when row 2 said both. -->
      <div class="wf-card__row">
        <template v-if="!modelsUnread">
          <span v-if="base" class="wf-card__base" aria-hidden="true">
            <Tooltip :text="base.label" activator="parent" :describe="false" />
            {{ base.text }}
          </span>
          <span
            v-else-if="stripMarks.length"
            class="wf-card__none"
            aria-hidden="true"
            >{{
              checkpointIsUnread ? "Base model not read" : "No checkpoint"
            }}</span
          >
          <span
            v-if="base?.quant"
            class="wf-card__none wf-card__quant"
            aria-hidden="true"
            >{{ base.quant }}</span
          >
          <span class="wf-card__none wf-card__lora-count" aria-hidden="true">{{
            loraCount
          }}</span>
        </template>
      </div>
      <div class="wf-card__row wf-card__row--facts">
        <span
          v-if="stack && facts.length"
          class="wf-card__none"
          aria-hidden="true"
          >differs by</span
        >
        <ChipRow :items="facts" aria-hidden="true" />
      </div>
    </div>

    <InfoPopover :card="card">
      <template #activator="{ props: popoverProps }">
        <AppButton
          v-bind="popoverProps"
          class="wf-card__info"
          variant="ghost"
          size="sm"
          icon-left="information-outline"
          icon-only
          tabindex="-1"
          tooltip="Everything about this workflow"
        />
      </template>
    </InfoPopover>
  </article>
</template>

<script setup>
// The uniform workflow card (v1.12 Workflows & Recipes, "The same in all three
// alternatives"). A cover at a fixed 6:5 whose tracks come from the strip it
// was handed - one picture across the whole box, two as a pair of columns,
// three as the 2fr/1fr mosaic - then four single-line rows (name, a strip of
// model marks, the base model's name beside a LoRA count, special facts) that
// clip to "+N" instead of wrapping, exactly
// --wf-meta-h tall whatever the card holds. ⓘ is pinned bottom-right.
//
// ▸ and ⓘ are real buttons at tabindex -1: the grid's roving cursor owns Tab.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { workflowCoverUrl } from "../../api/workflows";
import { quantBadge } from "../../utils/modelShelf";
import {
  cardAccessibleName,
  checkpointModel,
  coverCellStyle,
  factChips,
  isStack,
  checkpointUnread,
  lorasUnread,
  modelDisplayName,
} from "../../utils/workflowCard";
import AppButton from "./AppButton.vue";
import ChipRow from "./ChipRow.vue";
import InfoPopover from "./InfoPopover.vue";
import ModelMark from "./ModelMark.vue";
import Tooltip from "./Tooltip.vue";

// How many marks row 2 draws before it counts the rest (#1485). Sized for the
// NARROWEST host, a one-column stack panel: its 1px border and --space-4
// padding take the 240px column floor to a 214px card, whose row is 196px
// after the card's border and --space-3 padding. Six 24px marks, the hairline,
// "+N" and their 4px gaps are ~191px there; a seventh mark would not fit, and
// `overflow: hidden` would then clip the "+N" rather than a mark.
// `WorkflowCard.test.js` sums this against the tokens.
const STRIP_MARKS = 6;

const props = defineProps({
  /** One workflow card (see utils/workflowCard.js for the shape). */
  card: { type: Object, required: true },
  /** A stack card's panel is open. */
  expanded: { type: Boolean, default: false },
  /** The id of the panel ▸ opens, for `aria-controls`. */
  panelId: { type: String, default: "" },
  /** This card is a row inside its own stack's open panel. */
  member: { type: Boolean, default: false },
  /**
   * The card is selected.
   *
   * **The mark is the CARD's, not its cell's.** Both hosts wrapped a cell
   * around this component and put the shell's wash and `--selection-ring` on
   * that - and `.wf-card` paints an opaque `surface` across the whole of it,
   * so the mark was painted underneath the card and nothing showed. An
   * `outline` on the cell would not have helped either: children paint above
   * their parent's border box. It has to be drawn by whatever is on top, and
   * that is this.
   */
  selected: { type: Boolean, default: false },
});

const emit = defineEmits(["toggle", "run"]);

const stack = computed(() => isStack(props.card));
/**
 * Whether to wear the marks that say "there are more cards under this one".
 *
 * A member row is served the WHOLE stack's `stack_size`, so `stack` is true for
 * every row in the panel and each of them drew the layered count and a ▸ that
 * did nothing: no `@toggle` is bound there, and `openStack(memberKey)` would
 * not find the key among the grid's cards anyway. Worse for a reader, each ▸
 * offered `aria-expanded="false"` with nothing to expand.
 *
 * `stack` itself is left alone below: `factChips` branches on it, and the
 * "differs by" chips are exactly what a member row exists to show.
 */
const stackMark = computed(() => stack.value && !props.member);
/**
 * What the layered badge means, in words.
 *
 * The badge is a glyph and a bare number sitting opposite another glyph and
 * another bare number (the picture count), so which of the two is "how many
 * workflows" is a guess. The accessible name has said "stack of N workflows"
 * all along; this is the same sentence for the reader who can see it, and it
 * costs the card no layout.
 */
const stackLabel = computed(
  () => `${props.card.stack_size} workflows in this stack`,
);
/**
 * The cover pictures this card can actually draw.
 *
 * An entry without a `url` is dropped: `workflowCoverUrl` returns "" for one
 * and a cell with no picture in it is what #1456 removed, so since then the
 * cover's arrangement is counted from this list and an entry that cannot be
 * shown must not be counted either.
 */
const covers = computed(() =>
  (props.card.covers ?? []).filter((cover) => cover?.url).slice(0, 3),
);
/**
 * The cells the cover draws: one per picture it was handed, each with the URL
 * a browser can load and the crop that keeps the face in the cell.
 *
 * The join is `api/workflows.js`'s, not this component's: `url` arrives
 * API-relative and an `<img src>` never reaches the Axios interceptor that
 * would prefix it. See `workflowCoverUrl` for why that lives on the api layer.
 *
 * `style` is null for a picture whose crop rectangle has not been computed
 * yet, and the stylesheet's `object-fit: cover` then frames it exactly as it
 * framed every cover before #1465.
 *
 * A card can know it has pictures without having their covers yet
 * (`picture_count` arrives with the card, the strip can be empty). That card
 * still needs a cover, and the honest placeholder is the arrangement its
 * pictures are about to land in - not one box the size of the whole cover,
 * which is the `--empty` cover's own look and says "there is nothing here".
 */
const coverCells = computed(() => {
  const count = covers.value.length;
  if (!count) {
    return Array(Math.min(props.card.picture_count ?? 1, 3)).fill({
      src: "",
      style: null,
      id: null,
    });
  }
  return covers.value.map((cover) => ({
    src: workflowCoverUrl(cover),
    style: coverCellStyle(cover, count),
    // `?? null`, so a payload served before #1455 offers the menu no picture
    // rather than one named `undefined`.
    id: cover.picture_id ?? null,
  }));
});
// Ratings run 1-5; 0 or null is "not rated".
const rating = computed(() => props.card.rating > 0);
// Pictures, not loaded covers, decide "No pictures yet".
const hasPictures = computed(
  () => covers.value.length > 0 || (props.card.picture_count ?? 0) > 0,
);
// Per ROW, not per card: the recovery can find a LoRA and miss the loader
// beside it, and an empty row must not become a claim either way (#1466).
const checkpointIsUnread = computed(() => checkpointUnread(props.card));
const lorasAreUnread = computed(() => lorasUnread(props.card));
const modelsUnread = computed(
  () => checkpointIsUnread.value && lorasAreUnread.value,
);
const hasRecipe = computed(() => (props.card.variant_count ?? 1) !== 0);
/**
 * A payload slot as the shelf row `ModelMark` reads. Both base-model spellings,
 * because `baseModelKey` prefers the folded one: passing only the raw would
 * colour the same model differently here and on the shelf.
 */
function markRow(model) {
  return {
    display_name: model.title,
    filename: model.name,
    base_model: model.base_model,
    base_model_folded: model.base_model_folded,
    icon_sha256: model.icon,
  };
}
/**
 * What a mark's tooltip says: the model's name, plus the precision the server
 * took out of it - without which two quant builds of one model read
 * identically.
 */
function markLabel(model) {
  const quant = quantBadge(model.quant);
  const name = modelDisplayName(model);
  return quant ? `${name} · ${quant.label}` : name;
}
/**
 * The base model, as row 3 prints it. Only `checkpointModel`'s pick, never an
 * accessory slot: a VAE or a text encoder is named in ⓘ and nowhere on the
 * card, as it always was.
 */
const base = computed(() => {
  const model = checkpointModel(props.card);
  if (!model) return null;
  return {
    model,
    text: modelDisplayName(model),
    label: markLabel(model),
    quant: quantBadge(model.quant)?.label ?? null,
  };
});
/**
 * Row 2's marks: the base model leads, whatever order the file listed its
 * loaders in, then every LoRA in document order. A recipe slot is a dashed
 * "+" box, as its chip was dashed. Clipped to STRIP_MARKS.
 */
const allMarks = computed(() => {
  const loras = (props.card.loras ?? []).map((lora, i) =>
    lora.mark === "recipe"
      ? { key: `lora-${i}`, slot: true, label: "recipe LoRA" }
      : { key: `lora-${i}`, label: markLabel(lora), row: markRow(lora) },
  );
  if (!base.value) return loras;
  const model = base.value.model;
  return [
    {
      key: "base",
      label: base.value.label,
      row: markRow(model),
      // The hairline says "the base model ends here"; nothing to separate on
      // a card with no LoRAs.
      ruled: loras.length > 0,
    },
    ...loras,
  ];
});
const stripMarks = computed(() => allMarks.value.slice(0, STRIP_MARKS));
const stripOverflow = computed(
  () => allMarks.value.length - stripMarks.value.length,
);
// Counts the workflow's own LoRAs. A recipe slot is not one (the accessible
// name calls it a "recipe LoRA slot"), so it is only counted, as a slot, on a
// card that has no LoRAs of its own.
const loraCount = computed(() => {
  const loras = props.card.loras ?? [];
  const slots = loras.filter((lora) => lora.mark === "recipe").length;
  const count = loras.length - slots;
  if (count) return count === 1 ? "1 LoRA" : `${count} LoRAs`;
  if (slots) return slots === 1 ? "1 LoRA slot" : `${slots} LoRA slots`;
  return lorasAreUnread.value ? "LoRAs not read" : "No LoRAs";
});
const facts = computed(() => factChips(props.card, { short: true }));
const accessibleName = computed(() =>
  cardAccessibleName(props.card, { member: props.member }),
);
</script>

<style scoped>
/* 118 = 8 + 4 × 24 + 3 × 2 + 8: the meta block, which is fixed whatever the
   card holds and is `flex: none` so a change to that sum shows as a wrong
   height rather than being absorbed. Local on purpose (approved as
   component-local, not global).

   The COVER is not fixed. It used to be a flat 132px against a fluid card
   width, so the cells grew steadily more landscape as the window widened -
   1.2:1 at the 240px column floor and 1.8:1 by 360px - and nobody had chosen
   landscape at all; it fell out of mixing a pixel height with an `1fr` width.
   Pictures here are mostly portrait or square (832×1216, 1024×1024), so that
   shape threw away most of every cover. */
.wf-card {
  --wf-meta-h: 118px;

  position: relative;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
}

/* The shell's selection mark (`style.css` rule 3): the wash, plus
   `--selection-ring` because a card has no left edge to rail.

   **An OVERLAY, not the card's own background and shadow.** An inset
   box-shadow paints over the element's background but under its children, and
   most of this card is children - the cover's opaque `<img>`s. Put on
   the card itself the ring appeared along the text rows and stopped dead at
   the thumbnails, which is the same mistake as putting it on the cell one
   level up, made one level down. `.selection-overlay` in `ImageGrid.css` is
   the shipped answer for marking something with pictures in it: an absolutely
   positioned layer carrying both halves of the mark, above the content.

   `pointer-events: none` so ▸ and ⓘ underneath still take their clicks, and
   the radius is inherited so the ring follows the card's own corners. */
.wf-card--selected::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: var(--z-raised);
  border-radius: inherit;
  background: var(--active-wash);
  box-shadow: var(--selection-ring);
  pointer-events: none;
}

/* 6:5 is the cover at EVERY count; only the tracks inside it change, so cards
   in a row are still exactly as tall as each other - that is what the old fixed
   height was protecting, and it survives.

   What the tracks come to, against a cover W wide and (5/6)W tall:
     one    W × (5/6)W                         = 6/5
     two    (1/2)W × (5/6)W                    = 3/5, twice
     three  big (2/3)W × (5/6)W                = 4/5
            small (1/3)W × (5/12)W             = 4/5
   The 2px gap puts each a fraction of a pixel off that, which is not worth
   carrying a `calc` for.

   The crop follows from the shape, since the picture is `cover`-fitted and
   top-anchored below: an 832×1216 generation keeps its top 57% at 6:5, its top
   86% at 4:5, and ALL of its height at 3:5 (a cell narrower than the picture
   trims the sides instead). So the widest cut on this card lands on the card
   with one picture to show, which is the price of giving it the whole box. */
.wf-card__cover {
  position: relative;
  flex: none;
  box-sizing: border-box;
  overflow: hidden;
  display: grid;
  gap: var(--space-1);
  aspect-ratio: 6 / 5;
}

.wf-card__cover--1 {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

.wf-card__cover--2 {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr;
}

.wf-card__cover--3 {
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.wf-card__cover--3 .wf-card__pic:first-child {
  grid-row: 1 / 3;
}

/* Still painted, because a cell exists before its <img> has loaded. It is no
   longer a cell that will never hold a picture.

   `position: relative` because the crop below positions the <img> against this
   cell; without it the oversized img would be laid out against the page. */
.wf-card__pic {
  position: relative;
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

/* THE FALLBACK, not the crop. Since #1465 a cover carries the face-weighted
   rectangle `render_thumbnail` stored for it and `coverCellStyle` fits the
   cell's own ratio (6:5, 4:5 or 3:5) around it, as inline width/height/left/
   top that override everything here but `display`. What is left is the case
   that rectangle is NULL - a picture still being processed - and for that the
   crop is what it has always been.

   TOP-anchored, not centred, and that is the shipped convention rather than a
   choice made here: the picture grid's own cropped tiles carry
   `object-position: top center` (`ImageGrid.css`), and `utils/squareCrop.js`
   documents it as what the app's cover crop means. A centre crop takes the
   same slice off the top and the bottom of a portrait, and on a picture of a
   person the top is the face — so every head came off in the two short cells,
   which are much wider than they are tall.

   Absolutely positioned so the cropped case has something to translate
   against; at 100%/100% and inset 0 it fills the cell exactly as a static img
   did, so the fallback is unchanged by it. */
.wf-card__pic img {
  display: block;
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: top center;
}

/* The shipped scrim badge: a dark chip over an arbitrary photo. */
.wf-card__badge {
  position: absolute;
  top: var(--space-3);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  min-height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}

.wf-card__badge--start {
  left: var(--space-3);
  z-index: 1;
}

/* The badge family is inert decoration; this one is a control, so it takes
   the clicks back and drops the button chrome that would otherwise paint a
   second border over the pill. */
.wf-card__badge--button {
  border: 0;
  font-family: inherit;
  pointer-events: auto;
  cursor: pointer;
}

/* A pictureless stack's count sits top-left of the empty cover, so the
   cover's content starts below it. */
.wf-card__cover--stack {
  padding-top: var(--space-6);
}

.wf-card__badge--end {
  right: var(--space-3);
}

.wf-card__badge--bottom {
  top: auto;
  bottom: var(--space-3);
}

.wf-card__cover--empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  background: rgb(var(--v-theme-input-background));
}

/* The design's `.cpill`: an outlined pill, not a chip. The type is what this
   card would make, said once over an empty cover - a standing label rather than
   one of the row chips, which is why it is the only pill on the card. */
.wf-card__type {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
}

/* Row 2's strip of model marks (#1485): the shelf's own identity slot, at the
   shared --entity-thumb rather than a local size. */
.wf-card__strip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
  list-style: none;
}

.wf-card__mark {
  position: relative;
  display: inline-flex;
  flex: none;
}

/* A LoRA slot the recipe fills: not a model, so no mark, only the dashed box
   its chip used to be. */
.wf-card__mark--slot {
  box-sizing: border-box;
  align-items: center;
  justify-content: center;
  width: var(--entity-thumb);
  height: var(--entity-thumb);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The hairline after the base model: the one thing that says the first mark
   is a different kind of thing from the rest. */
.wf-card__rule {
  flex: none;
  width: 1px;
  height: var(--gutter-glyph);
  background: rgb(var(--v-theme-border));
}

.wf-card__mark-more {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* Row 3's one printed name: plain text, not a chip, because it is the model
   the card is named after rather than one datum among several. */
.wf-card__base {
  position: relative;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-xs);
}

.wf-card__lora-count {
  margin-left: auto;
}

.wf-card__empty-line {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wf-card__meta {
  flex: none;
  display: grid;
  grid-template-rows: repeat(4, var(--control-h-sm));
  row-gap: var(--space-1);
  padding: var(--space-3);
}

.wf-card__row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  white-space: nowrap;
}

.wf-card__row > .chip-row {
  flex: 1;
}

/* Row 4 shares the bottom-right corner with ⓘ, so it keeps that square free. */
.wf-card__row--facts {
  padding-right: calc(var(--control-h-sm) + var(--space-3));
}

.wf-card__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.wf-card__none {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* ▸ carries full ink where ⓘ recedes: opening a stack is the card's own
   affordance, ⓘ is the same escape hatch on every card. */
.wf-card__toggle {
  flex-shrink: 0;
  color: rgb(var(--v-theme-on-surface));
}

.wf-card__toggle :deep(.app-btn__icon) {
  transition: transform var(--dur-1) var(--ease-standard);
}

.wf-card__toggle--open :deep(.app-btn__icon) {
  transform: rotate(90deg);
}

@media (prefers-reduced-motion: reduce) {
  .wf-card__toggle :deep(.app-btn__icon) {
    transition: none;
  }
}

.wf-card__info {
  position: absolute;
  right: var(--space-3);
  bottom: var(--space-3);
}
</style>
