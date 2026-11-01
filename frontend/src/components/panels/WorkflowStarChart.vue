<template>
  <!-- Where the workflow's defaults come from, drawn: its pictures by star
       rating, unrated first and then 1★ to 5★, in one 32px histogram. The
       bars the defaults are read from are solid raspberry. The accessible name
       carries every count, and so does the tooltip. -->
  <div
    class="wfstars"
    role="img"
    tabindex="0"
    :aria-label="summary"
    data-testid="wftab-stars"
  >
    <Tooltip :text="summary" activator="parent" />
    <div class="wfstars-bars" aria-hidden="true">
      <span
        v-for="bar in bars"
        :key="bar.label"
        class="wfstars-bar"
        :class="bar.source ? 'wfstars-hi' : 'wfstars-lo'"
        :style="{ height: `${bar.pct}%` }"
      ></span>
    </div>
    <div class="wfstars-labels" aria-hidden="true">
      <span v-for="bar in bars" :key="bar.label">{{ bar.label }}</span>
    </div>
  </div>
</template>

<script setup>
// The star-rating histogram in the rail's lead (#2165). Drawn only for a
// workflow with at least one rating; the parent decides that.

import { computed } from "vue";

import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /**
   * Pictures per rating, six of them: none, then 1★ to 5★. The parent takes
   * the unrated count out of the workflow's picture count.
   */
  counts: { type: Array, required: true },
  /**
   * Whether the default recipe was sampled from the pictures at all. A manual
   * workflow's is its document's own values, so the chart claims no source
   * and inks every bar alike.
   */
  sampled: { type: Boolean, default: false },
});

const LABELS = ["None", "1★", "2★", "3★", "4★", "5★"];

/** How many are rated 4★+: the defaults come from those, or from all when none is. */
const best = computed(() => (props.counts[4] || 0) + (props.counts[5] || 0));

const bars = computed(() => {
  const tallest = Math.max(1, ...props.counts.map((count) => count || 0));
  return LABELS.map((label, index) => {
    const count = props.counts[index] || 0;
    return {
      label,
      count,
      // A bar with no pictures keeps a 2px stub, so six slots read as six.
      pct: count ? Math.max(6, (count / tallest) * 100) : 6,
      source: props.sampled && best.value ? index >= 4 : true,
    };
  });
});

/**
 * Each bar as a reader hears it. The bars show ★, but a screen reader may say
 * "black star" or nothing for it, so the words are spelled out here (as they
 * were before #2165): "Unrated 165, 1 star 0, 2 stars 2…".
 */
function spoken(index) {
  if (index === 0) return "Unrated";
  return `${index} ${index === 1 ? "star" : "stars"}`;
}

const summary = computed(() => {
  const each = LABELS.map(
    (_, index) => `${spoken(index)} ${props.counts[index] || 0}`,
  ).join(", ");
  if (!props.sampled) return `Pictures by rating: ${each}.`;
  const source = best.value
    ? "The defaults come from its pictures rated 4 stars or more."
    : "None is rated 4 stars yet, so the defaults come from all its pictures.";
  return `Pictures by rating: ${each}. ${source}`;
});
</script>

<style scoped>
.wfstars {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

/* Thirty-two pixels tall, bars on one baseline. */
.wfstars-bars {
  display: flex;
  align-items: flex-end;
  gap: var(--space-1);
  height: 32px;
}

.wfstars-bar {
  flex: 1;
  min-width: 0;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}

.wfstars-labels {
  display: flex;
  gap: var(--space-1);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfstars-labels > span {
  flex: 1;
  min-width: 0;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Raspberry, the hue the Stats sidebar's manual-score chart wears for the
   same ratings. Amber and olive stay with Run and selection, teal with
   unsaved. The source bars are solid; the rest take that chart's half-strength
   resting fill. */
.wfstars-hi {
  background: rgb(var(--v-theme-secondary));
}

.wfstars-lo {
  background: rgba(var(--v-theme-secondary), 0.5);
}

.wfstars:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}
</style>
