<!--
  The stats sidebar's horizontal bar chart: one 18px row per bucket, its label
  right-aligned in the 46px gutter, and the count printed inside the bar when it
  is wide enough to hold it and just outside when it is not.

  Four charts (tag confidence, manual score, smart score, resolution) were the
  same 90 lines of SVG four times over, differing only in which array they read,
  which fill they wore, and which filter a click toggled. They differ in one more
  way that matters: not every row is clickable. A confidence row does nothing
  until a tag is selected. So interactivity is a predicate, not a boolean, and it
  gates the row's class, its `role`, its `tabindex` and its title together - a
  row that announces itself as a button and then ignores the press is worse than
  one that never claimed to be one.

  The manual-score chart's "Unscored" row used to be the second example here.
  It is a filter of its own now (`unscored=1`), so that chart passes no
  predicate at all and every one of its rows is clickable.
-->
<template>
  <svg
    :width="260"
    :height="buckets.length * 18 + 4"
    :class="['stats-bar-chart', `stats-bar-chart--${fill}`]"
    :aria-label="ariaLabel"
  >
    <g
      v-for="(item, i) in buckets"
      :key="item.label"
      :class="[
        interactive(item, i) ? 'hist-bar-row' : 'hist-bar-row--disabled',
        { 'hist-bar-row--active': active(item, i) },
      ]"
      :transform="`translate(0, ${i * 18})`"
      :role="interactive(item, i) ? 'button' : undefined"
      :tabindex="interactive(item, i) ? 0 : undefined"
      :title="interactive(item, i) ? rowTitle(item, i) : undefined"
      @click="interactive(item, i) && emit('select', item, i)"
      @keydown.enter="interactive(item, i) && emit('select', item, i)"
    >
      <text x="46" y="9" text-anchor="end" class="hist-label">
        {{ item.label }}
      </text>
      <rect
        x="50"
        y="2"
        :width="barWidth(item)"
        height="13"
        rx="2"
        class="hist-bar-rect"
      />
      <text
        v-if="item.count > 0 && barWidth(item) >= 40"
        :x="50 + barWidth(item) - 3"
        y="9"
        text-anchor="end"
        class="bar-count-inner"
      >
        {{ item.count }}
      </text>
      <text
        v-else-if="item.count > 0"
        :x="50 + barWidth(item) + 3"
        y="9"
        text-anchor="start"
        class="bar-count-outer"
      >
        {{ item.count }}
      </text>
    </g>
  </svg>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  /** `[{ label, count }]`, in display order, top to bottom. */
  buckets: { type: Array, required: true },
  ariaLabel: { type: String, required: true },
  /** Which theme colour the bars wear: primary | secondary | tertiary. */
  fill: { type: String, default: "primary" },
  /** Whether this row can be clicked. Gates class, role, tabindex and title. */
  interactive: { type: Function, default: () => true },
  /** Whether this row's filter is currently on. */
  active: { type: Function, default: () => false },
  rowTitle: { type: Function, default: () => undefined },
});

const emit = defineEmits(["select"]);

// The tallest bar sets the scale, and an empty chart still divides by 1. A
// non-zero count always gets at least 2px, so "one picture" is a visible sliver
// rather than nothing.
const max = computed(() =>
  Math.max(1, ...props.buckets.map((b) => b.count ?? 0)),
);
const barWidth = (item) =>
  Math.max(item.count > 0 ? 2 : 0, (item.count / max.value) * 208);
</script>

<style scoped>
.stats-bar-chart {
  display: block;
  overflow: visible;
  /* The four charts differed only in this token, spelled out across four
     near-identical 12-line blocks. One custom property, set per variant. */
  --hist-fill: var(--v-theme-primary);
}
.stats-bar-chart--primary {
  --hist-fill: var(--v-theme-primary);
}
.stats-bar-chart--secondary {
  --hist-fill: var(--v-theme-secondary);
}
.stats-bar-chart--tertiary {
  --hist-fill: var(--v-theme-tertiary);
}

.hist-label {
  font-size: var(--text-2xs);
  fill: rgba(var(--v-theme-on-surface), 0.6);
  dominant-baseline: central;
}

.hist-bar-rect {
  fill: rgba(var(--hist-fill), 0.5);
}
.hist-bar-row {
  cursor: pointer;
  outline: none;
}
.hist-bar-row:hover .hist-bar-rect {
  fill: rgba(var(--hist-fill), 0.75);
}
.hist-bar-row--active .hist-bar-rect {
  fill: rgba(var(--hist-fill), 0.85);
  stroke: rgb(var(--hist-fill));
  stroke-width: 1;
}
.hist-bar-row--disabled {
  cursor: default;
}

.bar-count-inner {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  fill: rgba(var(--v-theme-on-primary), 0.85);
  dominant-baseline: central;
  pointer-events: none;
}
.bar-count-outer {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  fill: rgba(var(--v-theme-on-surface), 0.65);
  dominant-baseline: central;
  pointer-events: none;
}
</style>
