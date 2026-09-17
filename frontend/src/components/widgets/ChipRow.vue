<template>
  <div ref="rowEl" class="chip-row">
    <span
      v-for="(item, i) in items"
      v-show="i < visibleCount"
      :key="item.key ?? i"
      :class="['chip-row__chip', `chip-row__chip--${item.variant || 'solid'}`]"
    >
      <v-icon v-if="item.icon" size="12" class="chip-row__icon">{{
        `mdi-${item.icon}`
      }}</v-icon>
      <span class="chip-row__label">{{ item.label }}</span>
    </span>
    <!-- Not a control: the owner's accessible name carries the whole list. -->
    <span
      v-if="hiddenCount > 0"
      class="chip-row__chip chip-row__chip--more"
      aria-hidden="true"
      >+{{ hiddenCount }}</span
    >

    <!-- Every chip at its natural width, so a chip hidden by the last fit can
         still be measured when the row grows again. -->
    <div ref="measureEl" class="chip-row__measure" aria-hidden="true">
      <span
        v-for="(item, i) in items"
        :key="item.key ?? i"
        :class="[
          'chip-row__chip',
          `chip-row__chip--${item.variant || 'solid'}`,
        ]"
      >
        <v-icon v-if="item.icon" size="12" class="chip-row__icon">{{
          `mdi-${item.icon}`
        }}</v-icon>
        <span class="chip-row__label">{{ item.label }}</span>
      </span>
      <span class="chip-row__chip chip-row__chip--more"
        >+{{ items.length }}</span
      >
    </div>
  </div>
</template>

<script setup>
// One single-line row of chips that never wraps: what does not fit is clipped
// and counted as "+N". Used by WorkflowCard's LoRA and facts rows.

import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { VIcon } from "vuetify/components";

import { fitChipCount } from "../../utils/workflowCard";

const props = defineProps({
  /** `{ key?, label, icon?, variant?: "solid" | "dashed" | "fact" }` */
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

.chip-row__chip {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: var(--space-2);
  height: var(--chip-h);
  max-width: 100%;
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
}

/* The one chip allowed to shrink is the first: a single name longer than the
   row ellipsizes rather than being replaced by a bare "+N". */
.chip-row > .chip-row__chip:first-child {
  flex-shrink: 1;
  min-width: 0;
}

.chip-row__label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-row__icon {
  flex-shrink: 0;
  opacity: var(--opacity-text-secondary);
}

/* The `.tag-chip--some` precedent: a slot to be filled, not a thing present. */
.chip-row__chip--dashed {
  border-style: dashed;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.chip-row__chip--fact {
  background: transparent;
}

.chip-row__chip--more {
  border-color: transparent;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
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
