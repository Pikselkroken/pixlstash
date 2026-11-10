<script setup>
/**
 * How much a training run's previews look like its character, per step
 * (#2522): one point per column of the run board, on its step axis, so it
 * reads under the loss like the samples do.
 *
 * Props:
 *   withTrigger / withoutTrigger: one 0-100 likeness per column, or null where
 *     no preview of that column has a face.
 *   bestIndex: the column most like the character (-1 for none): its point is
 *     ringed and labelled.
 *   name: the character's name, for the peak's label.
 *   pitch: the board's column width in px; the chart is pitch x columns wide.
 *   leakAt: the likeness past which a preview without the trigger word is
 *     leaking; the no-trigger line crossing it gets a warning rule from there.
 *
 * One hue, raspberry, for both series: solid with the trigger word, dashed
 * without. The design system asks a second series to be an opacity ramp; a
 * raspberry line faded enough to read as one falls under the 3:1 floor for
 * marks on the dark canvas, so it is dashed instead.
 */
import { computed } from "vue";

const props = defineProps({
  withTrigger: { type: Array, default: () => [] },
  withoutTrigger: { type: Array, default: () => [] },
  bestIndex: { type: Number, default: -1 },
  name: { type: String, default: "" },
  pitch: { type: Number, required: true },
  leakAt: { type: Number, default: 20 },
  height: { type: Number, default: 84 },
});

// Room above 100 for the peak's label, and below 0 for the base line.
const TOP = 18;
const BOTTOM_PAD = 8;

const count = computed(() => Math.max(props.withTrigger.length, props.withoutTrigger.length));
const width = computed(() => count.value * props.pitch);
const x = (i) => (i + 0.5) * props.pitch;
const y = (value) => TOP + (1 - value / 100) * (props.height - BOTTOM_PAD - TOP);
const lastX = computed(() => x(count.value - 1));

/** The series as a path, broken where a column has no likeness. */
function pathOf(values) {
  let d = "";
  let open = false;
  values.forEach((value, i) => {
    if (value == null) {
      open = false;
      return;
    }
    d += `${open ? "L" : "M"}${x(i)},${y(value)} `;
    open = true;
  });
  return d.trim();
}

const withPath = computed(() => pathOf(props.withTrigger));
const withoutPath = computed(() => pathOf(props.withoutTrigger));
const dots = computed(() =>
  props.withTrigger
    .map((value, i) => ({ i, value }))
    .filter((dot) => dot.value != null && dot.i !== props.bestIndex),
);
const peak = computed(() => {
  const value = props.withTrigger[props.bestIndex];
  return value == null ? null : { x: x(props.bestIndex), y: y(value), value };
});
/** Where the previews without the trigger word first reach the leak line. */
const leakIndex = computed(() =>
  props.withoutTrigger.findIndex((value) => value != null && value >= props.leakAt),
);
const empty = computed(
  () => !props.withTrigger.some((v) => v != null) && !props.withoutTrigger.some((v) => v != null),
);

const summary = computed(() => {
  if (empty.value) return `No preview has been compared with ${props.name} yet.`;
  const parts = [];
  if (peak.value) parts.push(`Most like ${props.name}: ${peak.value.value}.`);
  if (leakIndex.value >= 0) {
    parts.push("The previews without the trigger word drift toward them.");
  }
  return parts.join(" ");
});
</script>

<template>
  <svg
    class="tlc"
    :viewBox="`0 0 ${width} ${height}`"
    :width="width"
    :height="height"
    role="img"
    :aria-label="summary"
  >
    <path class="tlc-base" :d="`M${x(0)} ${y(0)}H${lastX}`" />
    <!-- No scale before a score: on a one-column board the 50 label sits
         where the waiting sentence is centred, and there is nothing to read
         against it yet. -->
    <text v-if="empty" class="tlc-axis" :x="width / 2" :y="y(50) + 3" text-anchor="middle">
      Scored as the previews land
    </text>
    <template v-for="line in [50, 100]" v-else :key="line">
      <path class="tlc-grid" :d="`M${x(0)} ${y(line)}H${lastX}`" />
      <text class="tlc-axis" :x="lastX + 6" :y="y(line) + 3">{{ line }}</text>
    </template>
    <template v-if="leakIndex >= 0">
      <path class="tlc-leak" :d="`M${x(leakIndex)} ${y(leakAt)}H${lastX}`" />
      <text class="tlc-leak-label" :x="x(leakIndex) + 4" :y="y(leakAt) - 5">
        leaking past here
      </text>
    </template>
    <path v-if="withPath" class="tlc-with" :d="withPath" />
    <path v-if="withoutPath" class="tlc-without" :d="withoutPath" />
    <circle
      v-for="dot in dots"
      :key="dot.i"
      class="tlc-dot"
      :cx="x(dot.i)"
      :cy="y(dot.value)"
      r="2.6"
    />
    <template v-if="peak">
      <circle class="tlc-peak" :cx="peak.x" :cy="peak.y" r="4.5" />
      <text class="tlc-peak-label" :x="peak.x" :y="peak.y - 10" text-anchor="middle">
        {{ peak.value }} · most like {{ name }}
      </text>
    </template>
  </svg>
</template>

<style scoped>
.tlc {
  display: block;
  overflow: visible;
}

.tlc path {
  fill: none;
}

.tlc-base {
  stroke: rgba(var(--v-theme-on-background), 0.22);
}

.tlc-grid {
  stroke: rgba(var(--v-theme-on-background), 0.16);
  stroke-dasharray: 2 4;
}

.tlc-axis {
  fill: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
}

.tlc-with,
.tlc-without {
  stroke: rgb(var(--v-theme-secondary));
  stroke-linejoin: round;
}

.tlc-with {
  stroke-width: 2.25;
}

.tlc-without {
  stroke-width: 1.75;
  stroke-dasharray: 5 4;
}

.tlc-dot {
  fill: rgb(var(--v-theme-secondary));
}

.tlc .tlc-peak {
  fill: rgb(var(--v-theme-background));
  stroke: rgb(var(--v-theme-secondary));
  stroke-width: 2.25;
}

.tlc-peak-label {
  fill: rgb(var(--v-theme-on-background));
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.tlc-leak {
  stroke: rgb(var(--v-theme-surface-warning));
  stroke-opacity: 0.55;
  stroke-dasharray: 3 3;
}

.tlc-leak-label {
  fill: rgb(var(--v-theme-surface-warning));
  font-size: var(--text-2xs);
}
</style>
