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
      <!-- The stack panel's cover member, named on its own picture. The cover
           is what the others are compared against, so its special row is
           empty; without the flag it reads as "this one differs by nothing".
           Inside the cover box, bottom-left, because the host used to pin it to
           the whole cell's corner, which is the meta rows' text. -->
      <span v-if="coverFlag" class="stack-cover-flag">Cover</span>
    </div>
    <div
      v-else
      class="wf-card__cover wf-card__cover--empty"
      :class="{ 'wf-card__cover--stack': stackMark }"
    >
      <span v-if="card.type" class="wf-card__type" aria-hidden="true">{{
        card.type
      }}</span>
      <!-- The models this card's own file names, drawn the way the shelf draws
           a model (#1466). A card with no pictures AND no recipe has nothing
           else that identifies it, and this is the one thing about it that was
           read rather than guessed at.

           `aria-hidden` like the rest of the cover, and the label below is
           where those models are announced. It names the BASE MODEL and the
           LoRAs, which is what the rows say too - a recovered `vae` or `clip`
           is drawn here and named nowhere, the same silence every other card
           keeps about its accessory slots. -->
      <ul v-if="coverMarks.length" class="wf-card__marks" aria-hidden="true">
        <li v-for="mark in coverMarks" :key="mark.key" class="wf-card__mark">
          <Tooltip :text="mark.label" activator="parent" :describe="false" />
          <ModelMark :row="mark.row" />
        </li>
        <li v-if="coverOverflow" class="wf-card__mark-more">
          +{{ coverOverflow }}
        </li>
      </ul>
      <span v-else class="wf-card__empty-line" aria-hidden="true"
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
      <span v-if="coverFlag" class="stack-cover-flag">Cover</span>
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
      <div class="wf-card__row">
        <ChipRow
          v-if="checkpointChips.length"
          :items="checkpointChips"
          aria-hidden="true"
        />
        <span v-else class="wf-card__none" aria-hidden="true">{{
          checkpointIsUnread ? "Base model not read" : "No checkpoint"
        }}</span>
      </div>
      <div class="wf-card__row">
        <ChipRow v-if="loras.length" :items="loras" aria-hidden="true" />
        <span v-else class="wf-card__none" aria-hidden="true">{{
          lorasAreUnread ? "LoRAs not read" : "No LoRAs"
        }}</span>
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
// three as the 2fr/1fr mosaic - then four single-line rows (name, checkpoint,
// LoRAs, special facts) that clip to "+N" instead of wrapping, exactly
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
  loraChips,
  lorasUnread,
  modelDisplayName,
} from "../../utils/workflowCard";
import AppButton from "./AppButton.vue";
import ChipRow from "./ChipRow.vue";
import InfoPopover from "./InfoPopover.vue";
import ModelMark from "./ModelMark.vue";
import Tooltip from "./Tooltip.vue";

// How many marks the empty cover draws before it counts the rest. Four squares
// at --entity-thumb and their gaps are ~108px against a cover at least 240px
// wide. The row does not wrap and the marks do not shrink, so a fifth would
// overflow rather than move - which is what the slice is for.
const COVER_MARKS = 4;

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
  /** Flag this member as its stack's cover, on its own cover picture. */
  coverFlag: { type: Boolean, default: false },
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
/**
 * The models an empty cover draws, as `ModelMark` rows (#1466).
 *
 * The slot IS the row: `name`, `title`, `icon` and `base_model` are what the
 * mark reads, under the names the shelf gives them, so nothing is mapped on
 * the way. `filename` because `modelName` falls through `display_name` to it,
 * which is what puts the initials on a model the shelf has never scanned.
 *
 * **Only a card with no recipe** (`variant_count: 0`), which is the card that
 * had no cover at all before #1466: it has no pictures by construction, since
 * a picture arrives attached to a recipe. Every other pictureless card keeps
 * the shipped empty cover — a card whose pictures were all binned is a
 * different state from one nothing has ever run, and widening this to both
 * would be a change to a screen this issue was not about.
 */
const coverModels = computed(() => {
  if ((props.card.variant_count ?? 1) !== 0) return [];
  const named = [
    ...(props.card.models ?? []),
    ...(props.card.loras ?? []),
  ].filter((model) => model.name);
  // **The base model leads, whatever order the file listed its loaders in.**
  // The rest is document order, and the row is clipped to COVER_MARKS: a
  // graph that loads its VAE and both text encoders before its checkpoint
  // would otherwise have the one model the card is *about* sliced off the
  // end. `checkpointModel` picks the same one the name row was built from.
  const base = checkpointModel(props.card);
  return base ? [base, ...named.filter((model) => model !== base)] : named;
});
const coverMarks = computed(() =>
  coverModels.value.slice(0, COVER_MARKS).map((model, i) => ({
    // Indexed, because one workflow may load the same file twice and two
    // identical keys silently collapse into one mark.
    key: `mark-${i}`,
    label: modelDisplayName(model),
    // Both base-model spellings, because `baseModelKey` prefers the folded
    // one: passing only the raw would colour the same model differently here
    // and on the shelf.
    row: {
      display_name: model.title,
      filename: model.name,
      base_model: model.base_model,
      base_model_folded: model.base_model_folded,
      icon_sha256: model.icon,
    },
  })),
);
const coverOverflow = computed(() =>
  Math.max(coverModels.value.length - COVER_MARKS, 0),
);
// Per ROW, not per card: the recovery can find a LoRA and miss the loader
// beside it, and an empty row must not become a claim either way (#1466).
const checkpointIsUnread = computed(() => checkpointUnread(props.card));
const lorasAreUnread = computed(() => lorasUnread(props.card));
// The precision rides the checkpoint row as a SECOND chip rather than inside
// the first one's label. The name row above was built from this model's name,
// and `FP8` is a different kind of thing from the name - so it wears the fact
// treatment (no fill), and it clips to "+1" on a narrow card like every other
// chip instead of eating the name it qualifies. A model with no recorded
// precision gets no chip at all.
const checkpointChips = computed(() => {
  const model = checkpointModel(props.card);
  if (!model) return [];
  const quant = quantBadge(model.quant);
  return [
    { key: "checkpoint", label: modelDisplayName(model), icon: "cube-outline" },
    ...(quant
      ? [{ key: "checkpoint-quant", label: quant.label, fact: true }]
      : []),
  ];
});
const loras = computed(() => loraChips(props.card));
const facts = computed(() => factChips(props.card));
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

/* The stack panel's cover flag is the grid's chip (App.css), set on this
   card's badge geometry so it and the rating badge in the opposite corner sit
   on one baseline in one shape. */
.wf-card__cover .stack-cover-flag {
  left: var(--space-3);
  bottom: var(--space-3);
  display: inline-flex;
  align-items: center;
  min-height: var(--badge-size);
  border-radius: var(--radius-pill);
  line-height: var(--leading-snug);
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

/* The model marks on an empty cover: a plain row of the shelf's own identity
   slot, at the shared --entity-thumb rather than a local size. */
.wf-card__marks {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.wf-card__mark {
  position: relative;
  display: inline-flex;
}

.wf-card__mark-more {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
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
