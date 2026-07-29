<template>
  <span
    v-if="layers > 0"
    class="sticks"
    aria-hidden="true"
    data-testid="stack-edge-ticks"
  >
    <span
      v-for="n in layers"
      :key="n"
      class="stick"
      :class="`stick--${n}`"
    ></span>
  </span>
</template>

<script setup>
// The edge ticks behind a collapsed stack's cover.
//
// Without them a stack of five reads as a single photo with a number pinned to
// it, and the user has to read the badge to know the tile is a deck at all.
// Two peeking edges give the shape away before anything is read.
//
// Pure decoration on purpose: `aria-hidden` and pointer-transparent, because
// the badge already carries the meaning and a screen reader announcing three
// empty layers would be noise. Two layers is the ceiling, because a third peek is
// invisible at grid scale and only softens the tile's corner.
//
// The caller places this BEFORE the cover image in DOM order so the ticks sit
// behind it; this component only claims `--z-base`, it does not fight the tile.

import { computed } from "vue";

const props = defineProps({
  /** How many pictures the tile stands for. Below 2 there is no deck to draw. */
  count: { type: Number, default: 0 },
});

const layers = computed(() => {
  if (props.count < 2) return 0;
  return props.count === 2 ? 1 : 2;
});
</script>

<style scoped>
.sticks {
  position: absolute;
  inset: 0;
  z-index: var(--z-base);
  pointer-events: none;
}

/* Same radius as the tile it hides behind, so the peeking corner follows the
   cover's curve instead of cutting across it (visual-language.md §6). */
.stick {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.25);
}

/* Up and to the right in `--space-1` steps: the hairline nudge the scale exists
   for. Anything larger stops reading as a deck and starts reading as a second,
   misaligned tile. */
.stick--1 {
  transform: translate(var(--space-1), calc(var(--space-1) * -1));
}

.stick--2 {
  transform: translate(calc(var(--space-1) * 2), calc(var(--space-1) * -2));
}
</style>
