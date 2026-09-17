<template>
  <article
    class="wf-card"
    :class="{ 'wf-card--stack': stack }"
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
      :class="{ 'wf-card__cover--stack': stack }"
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

    <span
      v-if="stack"
      class="wf-card__badge wf-card__badge--start"
      aria-hidden="true"
    >
      <v-icon size="12">mdi-layers</v-icon>{{ card.stack_size }}
    </span>

    <!-- The visible rows are hidden from assistive tech: the card's own label
         already reads all of them, including what "+N" clipped. -->
    <div class="wf-card__meta">
      <div class="wf-card__row">
        <AppButton
          v-if="stack"
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
// alternatives"). Every card is exactly --wf-card-h tall whatever it holds: a
// 2fr/1fr cover, then four single-line rows (name, checkpoint, LoRAs, special
// facts) that clip to "+N" instead of wrapping. ⓘ is pinned bottom-right.
//
// ▸ and ⓘ are real buttons at tabindex -1: the grid's roving cursor owns Tab.

import { computed } from "vue";
import { VIcon } from "vuetify/components";

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

const props = defineProps({
  /** One workflow card (see utils/workflowCard.js for the shape). */
  card: { type: Object, required: true },
  /** A stack card's panel is open. */
  expanded: { type: Boolean, default: false },
  /** The id of the panel ▸ opens, for `aria-controls`. */
  panelId: { type: String, default: "" },
});

const emit = defineEmits(["toggle", "run"]);

const stack = computed(() => isStack(props.card));
const covers = computed(() => (props.card.covers ?? []).slice(0, 3));
// Ratings run 1-5; 0 or null is "not rated".
const rating = computed(() => props.card.rating > 0);
// Pictures, not loaded covers, decide "No pictures yet".
const hasPictures = computed(
  () => covers.value.length > 0 || (props.card.picture_count ?? 0) > 0,
);
const checkpointChips = computed(() => {
  const model = checkpointModel(props.card);
  return model ? [{ key: "checkpoint", label: model.name }] : [];
});
const loras = computed(() => loraChips(props.card));
const facts = computed(() => factChips(props.card));
const accessibleName = computed(() => cardAccessibleName(props.card));
</script>

<style scoped>
/* 252 = 1 border + 132 cover + (8 + 4 × 24 + 3 × 2 + 8) meta + 1 border.
   Both sizes are local on purpose (approved as component-local, not global).
   The meta block is `flex: none` so a change to that sum shows as a wrong
   height rather than being absorbed. */
.wf-card {
  --wf-card-h: 252px;
  --wf-cover-h: 132px;

  position: relative;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  height: var(--wf-card-h);
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
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
  height: var(--wf-cover-h);
}

.wf-card__pic {
  overflow: hidden;
  background: rgb(var(--v-theme-input-background));
}

.wf-card__pic:first-child {
  grid-row: 1 / 3;
}

.wf-card__pic img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
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

/* A category, not a status, so the control tier's radius and the same Tag xs
   the rows use. */
.wf-card__type {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-on-surface)) 10%,
    transparent
  );
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

.wf-card__toggle {
  flex-shrink: 0;
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
