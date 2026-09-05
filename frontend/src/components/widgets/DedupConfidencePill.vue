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
 *              glyph has to say so - an approximately-equals sign here would
 *              hedge the one claim in this queue that is not a measurement.
 *   • Near, measured: the one visual-similarity claim, with a green check.
 *              The check says that this measurement supports stacking; red Xs
 *              remain reserved for actual counter-evidence in `why`.
 *   • Near, unmeasured: a quiet outlined `Similar` fallback. It carries no
 *              check, because a missing percentage must not masquerade as a
 *              measured positive.
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

const measured = computed(() => {
  const raw = props.group?.confidence;
  return raw !== null && raw !== undefined && Number.isFinite(Number(raw));
});

const treatment = computed(() => {
  if (confidence.value.exact) return "exact";
  return measured.value ? "near" : "unknown";
});

const title = computed(() => {
  if (confidence.value.exact) return "Exact match.";
  if (measured.value) return `${confidence.value.label}. Supports stacking.`;
  return "Similar. No similarity percentage is available.";
});
</script>

<template>
  <span
    class="conf-pill"
    :class="`conf-pill--${treatment}`"
    :title="title"
    :aria-label="title"
  >
    <v-icon class="conf-pill__ico" size="12" aria-hidden="true">{{
      confidence.exact ? "mdi-equal" : measured ? "mdi-check" : "mdi-blur"
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

/* Similarity passed the active criterion, so it is supporting evidence. This
   is not an aggregate verdict: actual counter-evidence keeps its own red X in
   DedupWhyPills. */
.conf-pill--near {
  background: rgba(var(--v-theme-primary), 0.12);
  border-color: rgba(var(--v-theme-primary), 0.35);
  color: rgb(var(--v-theme-on-surface));
}

.conf-pill--near .conf-pill__ico {
  color: rgb(var(--v-theme-primary));
}

/* An older/incomplete payload can name the classification without supplying a
   number. Keep that fallback neutral rather than drawing a measured check. */
.conf-pill--unknown {
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
