<template>
  <div v-if="hasThreshold" class="dth">
    <label class="dth-label" :for="thresholdId">{{ label }}</label>
    <input
      :id="thresholdId"
      class="dth-input"
      type="range"
      :min="min"
      :max="max"
      step="0.01"
      :value="threshold"
      :disabled="disabled"
      @change="emit('change', Number($event.target.value))"
    />
    <output class="dth-value">{{ thresholdPercent }}%</output>
  </div>
</template>

<script setup>
/**
 * The similarity-threshold slider, shared by every surface that tunes it.
 *
 * Two call sites operate the same number: the tier menu in front of the
 * duplicate queue and the sticky header on the Mixed stacks page. Owning the
 * label, the step and the percentage formatting in one component is what stops
 * the two from drifting into two subtly different controls for one setting.
 *
 * The value, its floor and its ceiling all come from `GET /dedup/policy`
 * through the store; nothing here is hardcoded. The control renders nothing at
 * all until all three are real numbers, so a surface mounted before the policy
 * has loaded shows no half-configured slider. It reports on `change` rather
 * than `input`: the emitted value is a commit, not every frame of a drag.
 *
 * @component DedupThresholdControl
 * @prop {number|null} threshold - The similarity threshold in force, or null
 *   before the policy has loaded.
 * @prop {number|null} min - `bounds.min_threshold` from the server.
 * @prop {number|null} max - `bounds.max_threshold` from the server.
 * @prop {boolean} disabled - Whether the slider is inoperable, e.g. when no
 *   tier the threshold applies to is switched on.
 * @prop {string} label - The visible label bound to the input.
 * @emits change - The committed threshold, as a Number.
 */

import { computed, useId } from "vue";

const props = defineProps({
  /** The similarity threshold in force, or null before the policy has loaded. */
  threshold: { type: Number, default: null },
  /** `bounds.min_threshold` from the server. */
  min: { type: Number, default: null },
  /** `bounds.max_threshold` from the server. */
  max: { type: Number, default: null },
  /** Whether the slider is inoperable on this surface right now. */
  disabled: { type: Boolean, default: false },
  /** The visible label bound to the input. */
  label: { type: String, default: "Similar enough at" },
});

const emit = defineEmits(["change"]);

const thresholdId = useId();

const hasThreshold = computed(
  () =>
    Number.isFinite(props.threshold) &&
    Number.isFinite(props.min) &&
    Number.isFinite(props.max),
);

const thresholdPercent = computed(() => Math.round(props.threshold * 100));
</script>

<style scoped>
.dth {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  /* A wide call site must not stretch one slider across a whole page: past a
     point the extra travel buys no precision and stops reading as a control. */
  max-width: var(--stats-panel-w);
  padding: var(--space-2) var(--space-3);
}

.dth-label {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

.dth-input {
  flex: 1;
  min-width: 0;
  accent-color: rgb(var(--v-theme-accent));
}

.dth-input:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.dth-input:disabled {
  opacity: var(--opacity-disabled);
}

.dth-value {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.7);
}
</style>
