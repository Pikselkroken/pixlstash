<template>
  <article
    class="wf-card"
    :class="{ 'wf-card--stack': stackMark, 'wf-card--selected': selected }"
    role="group"
    :aria-label="accessibleName"
    data-testid="workflow-card"
  >
    <div v-if="hasPictures" class="wf-card__cover">
      <span v-for="i in 3" :key="i" class="wf-card__pic">
        <img v-if="covers[i - 1]" :src="covers[i - 1]" alt="" loading="lazy" />
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
      <span class="wf-card__empty-line" aria-hidden="true"
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
      <div class="wf-card__row">
        <ChipRow
          v-if="checkpointChips.length"
          :items="checkpointChips"
          aria-hidden="true"
        />
        <span v-else class="wf-card__none" aria-hidden="true"
          >No checkpoint</span
        >
      </div>
      <div class="wf-card__row">
        <ChipRow v-if="loras.length" :items="loras" aria-hidden="true" />
        <span v-else class="wf-card__none" aria-hidden="true">No LoRAs</span>
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
// alternatives"). A 2fr/1fr cover at a fixed 6:5 - so its cells are 4:5,
// which is the shape the pictures actually are - then four single-line rows
// (name, checkpoint, LoRAs, special facts) that clip to "+N" instead of
// wrapping, exactly --wf-meta-h tall whatever the card holds. ⓘ is pinned
// bottom-right.
//
// ▸ and ⓘ are real buttons at tabindex -1: the grid's roving cursor owns Tab.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { workflowCoverUrl } from "../../api/workflows";
import {
  cardAccessibleName,
  checkpointModel,
  factChips,
  isStack,
  loraChips,
} from "../../utils/workflowCard";
import AppButton from "./AppButton.vue";
import ChipRow from "./ChipRow.vue";
import InfoPopover from "./InfoPopover.vue";
import Tooltip from "./Tooltip.vue";

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
 * The cover thumbnails, made absolute.
 *
 * The join is `api/workflows.js`'s, not this component's: `covers` arrives
 * API-relative and an `<img src>` never reaches the Axios interceptor that
 * would prefix it. See `workflowCoverUrl` for why that lives on the api layer.
 */
const covers = computed(() =>
  (props.card.covers ?? []).slice(0, 3).map(workflowCoverUrl),
);
// Ratings run 1-5; 0 or null is "not rated".
const rating = computed(() => props.card.rating > 0);
// Pictures, not loaded covers, decide "No pictures yet".
const hasPictures = computed(
  () => covers.value.length > 0 || (props.card.picture_count ?? 0) > 0,
);
const checkpointChips = computed(() => {
  const model = checkpointModel(props.card);
  return model
    ? [{ key: "checkpoint", label: model.name, icon: "cube-outline" }]
    : [];
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

/* 6:5 is the cover, and it makes every CELL 4:5.
   The big cell is two columns and two rows, so it is 2× wide and 2× tall and
   keeps the cover's own proportion; the small ones are a third of the width
   against half the height. Both land on 4×5:
     big   = (2/3)W ÷ (5/6)W = 4/5
     small = (1/3)W ÷ (5/12)W = 4/5
   The 2px gap makes each a third of a pixel off that, which is not worth
   carrying a `calc` for. Every card in a row is the same width, so they are
   all still exactly as tall as each other - that is what the old fixed height
   was protecting, and it survives. */
/* The shell's selection mark (`style.css` rule 3): the wash, plus
   `--selection-ring` because a card has no left edge to rail.

   **An OVERLAY, not the card's own background and shadow.** An inset
   box-shadow paints over the element's background but under its children, and
   most of this card is children - the cover's three opaque `<img>`s. Put on
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

.wf-card__cover {
  position: relative;
  flex: none;
  box-sizing: border-box;
  overflow: hidden;
  display: grid;
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: var(--space-1);
  aspect-ratio: 6 / 5;
}

.wf-card__pic {
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

.wf-card__pic:first-child {
  grid-row: 1 / 3;
}

/* TOP-anchored, not centred, and that is the shipped convention rather than a
   choice made here: the picture grid's own cropped tiles carry
   `object-position: top center` (`ImageGrid.css`), and `utils/squareCrop.js`
   documents it as what the app's cover crop means. A centre crop takes the
   same slice off the top and the bottom of a portrait, and on a picture of a
   person the top is the face — so every head came off in the two short cells,
   which are much wider than they are tall.

   It does not make the crop face-AWARE: nothing here knows where the face is.
   That needs the face box on the card payload, which the covers do not carry
   (see docs/frontend_architecture.md §5). Top-anchoring is the cheap half that
   is right most of the time. */
.wf-card__pic img {
  display: block;
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
