<template>
  <div class="vmenu" role="group" aria-label="Which decisions to show">
    <div class="vm-head">
      <v-icon size="16">mdi-filter-outline</v-icon>
      <span class="vm-title">Show</span>
      <span class="vm-sp"></span>
      <span class="vm-count">{{ groupCountLabel }}</span>
    </div>

    <button
      v-for="verdict in verdicts"
      :key="verdict.id"
      type="button"
      class="vrow"
      :class="{ 'vrow--on': verdict.enabled }"
      :disabled="isLast(verdict)"
      :aria-pressed="verdict.enabled"
      :title="reasonFor(verdict)"
      @click="emit('toggle', verdict.id, !verdict.enabled)"
    >
      <span class="cbox" :class="{ 'cbox--on': verdict.enabled }">
        <v-icon v-if="verdict.enabled" size="14">mdi-check</v-icon>
      </span>
      <span class="vname"
        >{{ verdict.label }}
        <span v-if="verdict.hint" class="vhint">{{ verdict.hint }}</span></span
      >
      <span class="vcount">{{ formatCount(verdict.count) }}</span>
    </button>

    <!-- The one thing the decided page's filter has to say for itself: it is a
         different control from the queue's, not the same one behaving oddly. -->
    <p class="vnote">
      Decisions are listed whatever the tier gate says, so this page shows them
      all until you narrow it here.
    </p>
  </div>
</template>

<script setup>
// Which decisions the Decided page lists.
//
// The queue's tier gate is meaningless here: a decision was made under whatever
// policy was live at the time, and the server deliberately ignores the gate and
// the threshold on the decided page so a later policy change cannot hide a
// decision. What a user reviewing decisions actually wants to narrow by is the
// DECISION - the ones that were stacked, the ones kept separate, or both (owner
// call, 2026-07-30) - so the Duplicates toolbar swaps this menu in for
// `DedupTierMenu` while the Decided page is showing.
//
// Nothing here is hardcoded: the verdict ids come from `GET /dedup/policy`'s
// `bounds.verdicts` through the store, and the counts come from the decided
// page's own response, taken WITHOUT the filter in force so a row says what
// turning it back on would add rather than the zero its own exclusion produced.

import { computed } from "vue";

const props = defineProps({
  /**
   * Verdict rows from `useDedupStore.verdictRows`:
   * `{ id, label, hint, count, enabled }`, in the server's own order.
   */
  verdicts: { type: Array, default: () => [] },
  /** How many decided groups the page currently holds under this filter. */
  groupCount: { type: Number, default: 0 },
});

const emit = defineEmits(["toggle"]);

const groupCountLabel = computed(() => {
  const n = Number(props.groupCount) || 0;
  return `${n.toLocaleString()} ${n === 1 ? "group" : "groups"}`;
});

/** How many rows are switched on, so the last one can hold itself open. */
const enabledCount = computed(
  () => props.verdicts.filter((verdict) => verdict.enabled).length,
);

/**
 * Whether this row is the only one left on.
 *
 * Turning it off could only ever produce an empty page, which reads as a broken
 * queue rather than as a choice - so the row is held rather than allowed to
 * empty the list. The store enforces the same floor.
 *
 * @param {Object} verdict
 * @returns {boolean}
 */
function isLast(verdict) {
  return Boolean(verdict.enabled) && enabledCount.value <= 1;
}

/**
 * Why a row is the way it is, as a tooltip.
 *
 * A row that simply refuses to switch off is a dead control; saying what would
 * happen turns it into an explanation.
 *
 * @param {Object} verdict
 * @returns {string|undefined}
 */
function reasonFor(verdict) {
  if (isLast(verdict)) return "The last filter stays on, or the page is empty";
  return verdict.enabled
    ? `Hide the groups you ${verdict.label.toLowerCase()}`
    : `Show the groups you ${verdict.label.toLowerCase()} again`;
}

/**
 * Format a count, leaving an unknown one as a placeholder rather than a
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
/* The tier menu's grammar, deliberately: the two live behind the same toolbar
   button and swapping one for the other must read as the same control showing
   what is relevant, not as a second design. */
.vmenu {
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

.vm-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
}

.vm-title {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.vm-sp {
  flex: 1;
}

.vm-count {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.vrow {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  color: inherit;
  font-family: var(--font-ui);
  font-size: var(--text-base);
  text-align: left;
  transition: background var(--dur-1) var(--ease-standard);
}

.vrow:hover:not(:disabled) {
  background: var(--hover-wash);
}

/* The last row standing is ON, not unavailable, so it keeps the active wash and
   only loses its pointer - dimming it would say the opposite of what it means. */
.vrow:disabled {
  cursor: default;
}

.vrow--on {
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

.vname {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.vhint {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.vcount {
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.vnote {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-2xs);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.55);
}
</style>
