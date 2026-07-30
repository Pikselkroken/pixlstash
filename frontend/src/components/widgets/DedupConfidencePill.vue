<script setup>
/**
 * The per-group confidence chip in the duplicate queue.
 *
 * The one thing this component exists to protect: "Exact" is a different KIND of
 * claim from "94% similar", and rendering it as "100% similar" would make every
 * near-duplicate suggestion look as certain as a byte-identical match. So the
 * two tiers get two treatments, not two numbers:
 *
 *   • Exact: a filled accent chip with the equals glyph. A settled fact, and the
 *              glyph has to say so — an approximately-equals sign here would
 *              hedge the one claim in this queue that is not a measurement.
 *   • Near: a quiet outlined chip with the blur glyph. A measurement, with
 *              the percentage in tabular figures so a column of them lines up.
 *
 * `confidenceLabel` in `utils/dedup` owns the wording, because the compare view
 * and the auto-stack dialog have to say the same thing about the same group.
 */
import { computed } from "vue";

import { confidenceLabel } from "../../utils/dedup";

const props = defineProps({
  /** A queue group, carrying `kind` and `confidence`. */
  group: { type: Object, required: true },
});

const confidence = computed(() => confidenceLabel(props.group));
</script>

<template>
  <span
    class="conf-pill"
    :class="confidence.exact ? 'conf-pill--exact' : 'conf-pill--near'"
  >
    <v-icon class="conf-pill__ico" size="12">{{
      confidence.exact ? "mdi-equal" : "mdi-blur"
    }}</v-icon>
    <span class="conf-pill__label">{{ confidence.label }}</span>
  </span>
</template>

<style scoped>
.conf-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid transparent;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  font-weight: var(--weight-medium);
  white-space: nowrap;
}

/* Filled, because an exact match is the only claim in this queue that is not a
   judgement call, and it should be the one chip that reads as settled. */
.conf-pill--exact {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

/* Outlined and quiet: a measurement, not a verdict. */
.conf-pill--near {
  background: transparent;
  border-color: rgb(var(--v-theme-border));
  color: rgba(var(--v-theme-on-surface), 0.8);
}

/* Tabular figures so a column of percentages does not jitter as it updates. */
.conf-pill__label {
  font-variant-numeric: tabular-nums;
}

.conf-pill__ico {
  flex-shrink: 0;
}
</style>
