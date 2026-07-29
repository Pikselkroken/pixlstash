<template>
  <div class="tiermenu" role="group" aria-label="Which duplicates to include">
    <div class="tm-head">
      <v-icon size="16">mdi-filter-outline</v-icon>
      <span class="tm-title">Include</span>
      <span class="tm-sp"></span>
      <span class="tm-count">{{ groupCountLabel }}</span>
    </div>

    <!-- Every row, including the locked one, comes from the server's tier list.
         A locked row rather than an absent one, because an absent row reads as
         a missing feature and a locked one reads as an answer. -->
    <component
      :is="tier.locked ? 'div' : 'button'"
      v-for="tier in tiers"
      :key="tier.id"
      :type="tier.locked ? undefined : 'button'"
      class="tierrow"
      :class="{
        'tierrow--on': tier.enabled,
        'tierrow--locked': tier.locked,
      }"
      :disabled="tier.locked ? undefined : !isReachable(tier)"
      :aria-pressed="tier.locked ? undefined : tier.enabled"
      :title="reasonFor(tier)"
      @click="tier.locked ? undefined : emit('toggle', tier.id, !tier.enabled)"
    >
      <span class="cbox" :class="{ 'cbox--on': tier.enabled }">
        <v-icon v-if="tier.enabled" size="14">mdi-check</v-icon>
      </span>
      <span class="tname"
        >{{ tier.label }}
        <span v-if="tier.hint" class="trange">{{ tier.hint }}</span>
        <span v-if="tier.locked" class="tlock">always included</span></span
      >
      <span class="tcount">{{ formatCount(tier.count) }}</span>
    </component>

    <!-- The threshold and its floor are the server's. Stating a number here
         would put the same bound in two places that can drift apart. -->
    <div v-if="hasThreshold" class="tm-threshold">
      <label class="tm-threshold-label" :for="thresholdId"
        >Similar enough at</label
      >
      <input
        :id="thresholdId"
        class="tm-threshold-input"
        type="range"
        :min="minThreshold"
        :max="maxThreshold"
        step="0.01"
        :value="threshold"
        :disabled="!anyLooserTierOn"
        @change="emit('threshold', Number($event.target.value))"
      />
      <output class="tm-threshold-value">{{ thresholdPercent }}%</output>
    </div>

    <!-- The loosest tier states its own risk in place, so the warning arrives
         where the decision is made rather than in a preamble nobody reads. -->
    <p v-if="loosestIsOn" class="tierwarn">
      <v-icon size="16">mdi-alert-outline</v-icon>
      <span
        >Same-scene groups are often genuinely different pictures. Compare
        before stacking, these are the ones people get wrong.</span
      >
    </p>
  </div>
</template>

<script setup>
// The confidence gate in front of the queue.
//
// Tier 1 is always included and cannot be switched off. Every looser tier is a
// separate opt-in, and enabling one requires the tier above it, so a user cannot
// land on "same scene" suggestions without having deliberately walked down to
// them. A low threshold produces confident-looking garbage and destroys trust in
// the count, which is the whole reason this control is a ladder rather than a
// free slider.
//
// Nothing here is hardcoded: the tier ids, their prerequisites, which of them
// are always on, and the threshold's floor and ceiling all arrive from
// `GET /dedup/policy` through the store. This component renders them and reports
// what the user pressed.

import { computed, useId } from "vue";

const props = defineProps({
  /**
   * Tier rows from `useDedupStore.tierRows`, strongest evidence first:
   * `{ id, label, hint, count, locked, requires, enabled }`.
   */
  tiers: { type: Array, default: () => [] },
  /** How many groups the queue currently holds under this gate. */
  groupCount: { type: Number, default: 0 },
  /** The similarity threshold in force, or null before the policy has loaded. */
  threshold: { type: Number, default: null },
  /** `bounds.min_threshold` from the server. */
  minThreshold: { type: Number, default: null },
  /** `bounds.max_threshold` from the server. */
  maxThreshold: { type: Number, default: null },
});

const emit = defineEmits(["toggle", "threshold"]);

const thresholdId = useId();

const groupCountLabel = computed(() => {
  const n = Number(props.groupCount) || 0;
  return `${n.toLocaleString()} ${n === 1 ? "group" : "groups"}`;
});

const hasThreshold = computed(
  () =>
    Number.isFinite(props.threshold) &&
    Number.isFinite(props.minThreshold) &&
    Number.isFinite(props.maxThreshold),
);

const thresholdPercent = computed(() => Math.round(props.threshold * 100));

/** Whether any tier the threshold applies to is switched on. */
const anyLooserTierOn = computed(() =>
  props.tiers.some((tier) => !tier.locked && tier.enabled),
);

const loosestIsOn = computed(() => {
  const last = props.tiers[props.tiers.length - 1];
  return Boolean(last && !last.locked && last.enabled);
});

/**
 * Whether a tier may be toggled: its prerequisite has to be on first.
 * @param {Object} tier
 * @returns {boolean}
 */
function isReachable(tier) {
  if (!tier?.requires) return true;
  const prerequisite = props.tiers.find((t) => t.id === tier.requires);
  return Boolean(prerequisite?.enabled || prerequisite?.locked);
}

/**
 * Why a row is the way it is, as a tooltip.
 *
 * An unreachable row that just sits there greyed out is a dead end; saying what
 * to turn on first turns it into an instruction.
 *
 * @param {Object} tier
 * @returns {string|undefined}
 */
function reasonFor(tier) {
  if (tier.locked) return "Identical files are always included";
  if (isReachable(tier)) return undefined;
  const prerequisite = props.tiers.find((t) => t.id === tier.requires);
  return `Turn on ${prerequisite?.label ?? tier.requires} first, so looser suggestions are always a deliberate step down`;
}

/**
 * Format a tier count, leaving an unknown count as a placeholder rather than a
 * confident zero.
 * @param {number} value
 * @returns {string}
 */
function formatCount(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : "–";
}
</script>

<style scoped>
.tiermenu {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 300px;
  padding: var(--space-3);
  border-radius: var(--radius-lg);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-3);
}

.tm-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
}

.tm-title {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.tm-sp {
  flex: 1;
}

.tm-count {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.tierrow {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  color: inherit;
  font-family: var(--font-ui);
  font-size: var(--text-base);
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.tierrow:hover:not(:disabled):not(.tierrow--locked) {
  background: var(--hover-wash);
}

.tierrow:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.tierrow:disabled {
  opacity: 0.38;
  cursor: default;
}

.tierrow--on {
  background: var(--active-wash);
}

/* Locked, not disabled: it is on and stays on, so it must not read as an
   unavailable control. Rendered as a div for the same reason, so the keyboard
   does not stop on something that cannot be operated. */
.tierrow--locked {
  cursor: default;
  background: var(--active-wash);
}

.cbox {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: var(--space-5);
  height: var(--space-5);
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--v-theme-border));
}

.cbox--on {
  background: rgb(var(--v-theme-primary));
  border-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.tname {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.trange,
.tlock {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.tcount {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.tm-threshold {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
}

.tm-threshold-label {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

.tm-threshold-input {
  flex: 1;
  min-width: 0;
  accent-color: rgb(var(--v-theme-accent));
}

.tm-threshold-input:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.tm-threshold-input:disabled {
  opacity: 0.38;
}

.tm-threshold-value {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.tierwarn {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin: 0;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-warning), 0.12);
  border: 1px solid rgba(var(--v-theme-warning), 0.35);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: rgb(var(--v-theme-on-surface));
}
</style>
