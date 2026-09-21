<template>
  <article
    class="msc"
    :class="{ 'msc--open': expanded, 'msc--on': selected }"
    role="group"
    :aria-label="accessibleName"
    data-testid="model-set-card"
  >
    <div
      v-if="card.covers.length"
      class="msc__cover"
      :class="`msc__cover--${card.covers.length}`"
    >
      <span
        v-for="cover in card.covers"
        :key="cover.picture_id"
        class="msc__pic"
      >
        <img :src="coverSrc(cover)" alt="" loading="lazy" />
      </span>
      <span class="msc__badge msc__badge--start" aria-hidden="true">
        <v-icon size="12">mdi-layers</v-icon>{{ card.recipes }} recipes
      </span>
      <span class="msc__badge msc__badge--end" aria-hidden="true">
        <v-icon size="12">mdi-image-multiple</v-icon
        >{{ card.pictures.toLocaleString() }}
      </span>
    </div>
    <div v-else class="msc__cover msc__cover--empty" aria-hidden="true">
      <span class="msc__empty-line">No picture to show</span>
    </div>

    <!-- The visible rows are hidden from assistive tech: the card's own label
         already reads all of them, including what "+N" clipped. -->
    <div class="msc__meta">
      <div class="msc__row">
        <AppButton
          class="msc__toggle"
          :class="{ 'msc__toggle--open': expanded }"
          variant="ghost"
          size="sm"
          icon-left="menu-right"
          icon-only
          tabindex="-1"
          :tooltip="
            expanded
              ? 'Hide the models in this set'
              : 'Show the models in this set'
          "
          :aria-expanded="String(expanded)"
          :aria-controls="panelId || undefined"
          @click="emit('toggle')"
        />
        <span class="msc__name" aria-hidden="true">{{ card.name }}</span>
        <span v-if="card.kindLabel" class="msc__kind" aria-hidden="true">{{
          card.kindLabel
        }}</span>
      </div>
      <!-- What SHAPE this set is - one VAE and two encoders, or none at all -
           which is the question a grid is scanned for. -->
      <div class="msc__row">
        <ChipRow
          v-if="kindChips.length"
          :items="kindChips"
          aria-hidden="true"
        />
        <span v-else class="msc__none" aria-hidden="true"
          >Nothing else has run with it</span
        >
      </div>
      <div class="msc__row msc__row--facts">
        <span class="msc__facts" aria-hidden="true">{{
          card.facts.join(" · ")
        }}</span>
      </div>
    </div>
  </article>
</template>

<script setup>
// One workflow set on the model shelf's set grid (#1438).
//
// **A card is one base model** - a checkpoint, or the diffusion file a Flux or
// Wan graph loads instead - and ▸ opens the tray of models that have run with it.
// Three meta rows: the name with its kind, the per-kind tally of everything else
// in the set, and the facts.
//
// **Deliberately not `WorkflowCard`.** The shape is the same on purpose - the
// 2fr/1fr cover mosaic, the scrim badges, the layered stack count, ▸, and four
// single-line rows that clip to "+N" - because a reader should not have to learn
// a second card. What is NOT shared is the vocabulary: that card's ⓘ panel says
// "Stack of 3 workflows" and "Saved recipes", and its picture list is keyed on a
// workflow. A set is neither, so borrowing the component would have put three
// wrong words on every card to save this file. The tokens, the badge treatment
// and the row rhythm are copied; nothing that says "workflow" is.
//
// There is no ⓘ here either: everything the card knows is already on it, and the
// detail a reader wants next is the file list, which is one ▸ away.
//
// **Selected means the card's BASE MODEL is selected**, never the set: the shelf's
// verbs write one file each and two of them destroy bytes, so a card standing for
// its whole tray would put a shared VAE behind a Delete aimed at a checkpoint. The
// other members are selected in the tray, one row each.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

import { pictureThumbnailUrl } from "../../api/pictures";
import AppButton from "./AppButton.vue";
import ChipRow from "./ChipRow.vue";

const props = defineProps({
  /** One card from `setCard` (see `utils/workflowSets.js`). */
  card: { type: Object, required: true },
  /** This card's tray is open. */
  expanded: { type: Boolean, default: false },
  /** The id of the tray ▸ opens, for `aria-controls`. */
  panelId: { type: String, default: "" },
  /** This card's base model is in the shelf's selection. */
  selected: { type: Boolean, default: false },
});

const emit = defineEmits(["toggle"]);

/**
 * The URL a browser loads one cover from.
 *
 * **An `<img src>` never reaches the Axios interceptor**, so nothing prepends
 * the API base and nothing appends the share token: the payload's
 * `{picture_id, version}` has to go through the api layer's own builder, or the
 * browser asks the PAGE origin for a path no route serves and every cover on
 * this grid breaks. `pictureThumbnailUrl` is where that path is spelled - the
 * same one the picture grid's tiles use, so a thumbnail the browser already
 * holds is not fetched twice.
 */
function coverSrc(cover) {
  return pictureThumbnailUrl(cover.picture_id, { version: cover.version });
}

/** Row 2: the per-kind tally of everything else in the set. */
const kindChips = computed(() =>
  props.card.kinds.map((entry, i) => ({
    key: `kind-${i}`,
    label: entry,
    icon: "cube-outline",
  })),
);

// "+N" is not a control, so this is the only place a screen reader hears the
// chips a narrow card clipped - and the only place it hears the whole set.
const accessibleName = computed(() => {
  const { name, kindLabel, kinds, facts } = props.card;
  return [
    name,
    kindLabel,
    kinds.length ? `with ${kinds.join(", ")}` : "nothing else has run with it",
    facts.join(", "),
  ]
    .filter(Boolean)
    .join(", ");
});
</script>

<style scoped>
/* 92 = 8 + 3 × 24 + 2 × 2 + 8: the meta block, fixed whatever the card holds
   and `flex: none` so a change to that sum shows as a wrong height rather than
   being absorbed. The figure and the `6 / 5` cover below are `WorkflowCard`'s
   own, so the two grids' cards are the same shape and a reader moving between
   them meets one rhythm. Local on purpose, as that card's are.

   **The COVER is not a fixed height, and that is the whole point of copying
   this rather than a round number.** A flat 132px against an `1fr` width grows
   steadily more landscape as the window widens - 1.2:1 at the 240px column
   floor, 1.8:1 by 360px - and nobody chose landscape; generated pictures here
   are mostly portrait or square. `WorkflowCard.vue` records removing exactly
   that, so reintroducing it here would have been the same bug on a second
   screen. */
.msc {
  --msc-meta-h: 92px;

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

/* Open and selected are two states and they read differently on purpose: open is
   a border in the active ink beside the rotated ▸, selected is the shell's own
   wash and ring. A card can be both, and then it wears both. */
.msc--open {
  border-color: var(--active-bar);
}

/* The shell's selection mark (`style.css` rule 3): the wash, plus
   `--selection-ring` because a card has no left edge to rail.

   **An OVERLAY, not the card's own background and shadow**, for the reason
   `WorkflowCard.vue` records at the same rule: an inset box-shadow paints over
   an element's background but UNDER its children, and the top 55% of this card
   is the cover's opaque `<img>`s. Put on the card itself the mark appears along
   the meta rows and stops dead at the pictures. `pointer-events: none` so ▸
   underneath still takes its click, and the radius is inherited so the ring
   follows the card's own corners. */
.msc--on::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: var(--z-raised);
  border-radius: inherit;
  background: var(--active-wash);
  box-shadow: var(--selection-ring);
  pointer-events: none;
}

.msc__cover {
  position: relative;
  flex: none;
  box-sizing: border-box;
  overflow: hidden;
  display: grid;
  gap: var(--space-1);
  aspect-ratio: 6 / 5;
}

/* The tracks follow the COVER COUNT, they are not a fixed 2fr/1fr mosaic with
   holes in it. A set with two pictures is two cells and one with a single
   picture fills the box — `WorkflowCard` learned this and the first copy of its
   geometry here did not, which is the duplication the #1479 review warned about
   landing as an actual defect rather than a risk.

   **Not extracted into a shared component, and only because of timing:** #1472
   is open on `WorkflowCard.vue`'s cover code (face-aware cropping), so lifting
   the mosaic out from under it would conflict with a PR already in review. The
   extraction is the right end state and wants to happen after that lands. */
.msc__cover--1 {
  grid-template-columns: 1fr;
  grid-template-rows: 1fr;
}

.msc__cover--2 {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr;
}

.msc__cover--3 {
  grid-template-columns: 2fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.msc__cover--3 .msc__pic:first-child {
  grid-row: 1 / 3;
}

.msc__pic {
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

.msc__pic img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.msc__cover--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4) var(--space-5);
  background: rgb(var(--v-theme-input-background));
}

.msc__empty-line {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The shipped scrim badge: a dark chip over an arbitrary photo. */
.msc__badge {
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

.msc__badge--start {
  left: var(--space-3);
  z-index: 1;
}

.msc__badge--end {
  right: var(--space-3);
}

.msc__meta {
  flex: none;
  box-sizing: border-box;
  height: var(--msc-meta-h);
  display: grid;
  grid-template-rows: repeat(4, var(--control-h-sm));
  row-gap: var(--space-1);
  padding: var(--space-3);
}

.msc__row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  white-space: nowrap;
}

.msc__row > .chip-row {
  flex: 1;
}

.msc__row--facts {
  padding-right: var(--space-3);
}

.msc__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.msc__kind {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  flex-shrink: 0;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
  white-space: nowrap;
}

.msc__facts {
  overflow: hidden;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
}

.msc__none {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* ▸ carries full ink: opening a stack is the card's own affordance. */
.msc__toggle {
  flex-shrink: 0;
  color: rgb(var(--v-theme-on-surface));
}

.msc__toggle :deep(.app-btn__icon) {
  transition: transform var(--dur-1) var(--ease-standard);
}

.msc__toggle--open :deep(.app-btn__icon) {
  transform: rotate(90deg);
}

@media (prefers-reduced-motion: reduce) {
  .msc__toggle :deep(.app-btn__icon) {
    transition: none;
  }
}
</style>
