<template>
  <!-- Where the workflow's defaults come from, drawn rather than said: its
       pictures' star ratings, 1★ to 5★, with the bars the defaults are read
       from at full ink. One accessible name carries every count and the
       source, and the tooltip says the same. -->
  <span class="wfstars" role="img" :aria-label="summary" data-testid="wftab-stars">
    <Tooltip :text="summary" activator="parent" />
    <svg
      :width="WIDTH"
      :height="HEIGHT"
      :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
      aria-hidden="true"
    >
      <rect
        v-for="bar in bars"
        :key="bar.star"
        :x="bar.x"
        :y="HEIGHT - bar.h"
        :width="BAR"
        :height="bar.h"
        rx="1"
        :class="bar.source ? 'wfstars-hi' : 'wfstars-lo'"
      />
    </svg>
    <span class="wfstars-cap" aria-hidden="true">★ Star ratings</span>
  </span>
</template>

<script setup>
// The star-rating histogram beside "N pictures" in the Workflow tab's head
// (design: grouped panels). Drawn only for a workflow with at least one
// rating; the parent decides that.

import { computed } from "vue";

import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** Pictures per star, 1★ first: the card's `rating_counts`. */
  counts: { type: Array, required: true },
});

const BAR = 6;
const GAP = 2;
const HEIGHT = 16;
const WIDTH = 5 * BAR + 4 * GAP;

/** The defaults come from the 4★+ pictures, or from all when none is. */
const best = computed(() => (props.counts[3] || 0) + (props.counts[4] || 0));

const bars = computed(() => {
  const tallest = Math.max(1, ...props.counts.map((count) => count || 0));
  return [1, 2, 3, 4, 5].map((star, index) => {
    const count = props.counts[index] || 0;
    return {
      star,
      x: index * (BAR + GAP),
      // A rating with no pictures keeps a 1px stub, so five slots read as five.
      h: count ? Math.max(1, Math.round((HEIGHT * count) / tallest)) : 1,
      source: best.value ? star >= 4 : true,
    };
  });
});

const summary = computed(() => {
  const each = [1, 2, 3, 4, 5]
    .map((star, index) => `${star} ${star === 1 ? "star" : "stars"} ${props.counts[index] || 0}`)
    .join(", ");
  const source = best.value
    ? `The defaults come from the ${best.value} rated 4 stars or more.`
    : "None is rated 4 stars yet, so the defaults come from all its pictures.";
  return `Ratings: ${each}. ${source}`;
});
</script>

<style scoped>
.wfstars {
  display: inline-flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.wfstars svg {
  display: block;
}

/* Ink, never hue: amber and olive stay with Run and selection. The quiet bars
   take the secondary fade, not the design's 50%: the token file allows only
   two fades, and the disabled one says "inactive", which these are not. */
.wfstars-hi {
  fill: rgb(var(--v-theme-on-surface));
}

.wfstars-lo {
  fill: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfstars-cap {
  font-size: var(--text-2xs);
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
