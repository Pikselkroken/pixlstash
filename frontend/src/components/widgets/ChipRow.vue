<template>
  <div ref="rowEl" class="chip-row">
    <span
      v-for="(item, i) in items"
      v-show="i < visibleCount"
      :key="item.key ?? i"
      :class="['chip-row__chip', { 'chip-row__chip--dashed': item.dashed }]"
      >{{ item.label }}</span
    >
    <!-- Not a control: the owner's accessible name carries the whole list. -->
    <span v-if="hiddenCount > 0" class="chip-row__more" aria-hidden="true"
      >+{{ hiddenCount }}</span
    >

    <!-- Every chip at its natural width, so a chip hidden by the last fit can
         still be measured when the row grows again. -->
    <div ref="measureEl" class="chip-row__measure" aria-hidden="true">
      <span
        v-for="(item, i) in items"
        :key="item.key ?? i"
        class="chip-row__chip"
        >{{ item.label }}</span
      >
      <span class="chip-row__more">+{{ items.length }}</span>
    </div>
  </div>
</template>

<script setup>
// One single-line row of chips that never wraps: what does not fit is clipped
// and counted as "+N". Used by WorkflowCard's LoRA and facts rows, and by the
// stack panel's List "Differs by" column when F2 builds it.
//
// The chip is the design system's Tag xs (`components/core/Tag.jsx`): 18px, a
// 10% ink wash, a muted label, no border, and no icon. `dashed` is the one
// departure - a slot the recipe fills rather than a model the workflow carries
// - after the `.tag-chip--some` precedent.

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";

import { fitChipCount } from "../../utils/workflowCard";

const props = defineProps({
  /** `{ key?, label, dashed? }` */
  items: { type: Array, default: () => [] },
});

const emit = defineEmits(["overflow"]);

const rowEl = ref(null);
const measureEl = ref(null);
const visibleCount = ref(props.items.length);
const hiddenCount = computed(() =>
  Math.max(props.items.length - visibleCount.value, 0),
);

function measure() {
  const row = rowEl.value;
  const probe = measureEl.value;
  if (!row || !probe) return;
  const chips = [...probe.children];
  const more = chips.pop();
  const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
  visibleCount.value = fitChipCount(
    chips.map((c) => c.getBoundingClientRect().width),
    row.clientWidth,
    gap,
    more.getBoundingClientRect().width,
  );
}

watch(hiddenCount, (n) => emit("overflow", n));
watch(
  () => props.items,
  () => nextTick(measure),
  { deep: true },
);

// ponytail: one observer per row, and a hidden copy of every chip. That is fine
// for the tens-to-hundreds of cards F1a expects; if a grid ever holds
// thousands, one shared observer keyed by element is the upgrade.
let observer = null;
onMounted(() => {
  measure();
  observer = new ResizeObserver(measure);
  observer.observe(rowEl.value);
});
onBeforeUnmount(() => observer?.disconnect());

defineExpose({ measure });
</script>

<style scoped>
.chip-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

/* The design system's Tag, xs size. */
.chip-row__chip {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  box-sizing: border-box;
  height: var(--tag-h-xs);
  max-width: 100%;
  padding: 0 var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-on-surface)) 10%,
    transparent
  );
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-regular);
  line-height: var(--leading-snug);
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The one chip allowed to shrink is the first: a single name longer than the
   row ellipsizes rather than being replaced by a bare "+N". */
.chip-row > .chip-row__chip:first-child {
  flex-shrink: 1;
  min-width: 0;
}

/* A slot to be filled, not a model that is present: the `.tag-chip--some`
   treatment. With the resting chip unbordered, this outline is the only border
   in the row, which is the point. */
.chip-row__chip--dashed {
  border-color: rgba(var(--v-theme-on-surface), 0.35);
  border-style: dashed;
  background: transparent;
}

/* Not a Tag but an overflow count, so it carries no fill. */
.chip-row__more {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}

.chip-row__measure {
  position: absolute;
  top: 0;
  left: 0;
  display: flex;
  gap: var(--space-2);
  height: 0;
  overflow: hidden;
  visibility: hidden;
  pointer-events: none;
}

.chip-row__measure > .chip-row__chip {
  max-width: none;
}
</style>
