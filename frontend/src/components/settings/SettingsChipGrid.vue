<template>
  <div v-if="empty && !hasItems" class="chip-grid__empty">{{ empty }}</div>
  <div v-else class="chip-grid">
    <slot />
  </div>
</template>

<script setup>
import { computed, useSlots } from "vue";

defineProps({
  empty: { type: String, default: "" },
});

const slots = useSlots();
const hasItems = computed(() => {
  const nodes = slots.default ? slots.default() : [];
  // Flatten v-for fragments so an empty list reads as no items.
  return nodes.some((n) => {
    if (Array.isArray(n.children)) return n.children.length > 0;
    return n.children != null && n.children !== "";
  });
});
</script>

<style scoped>
.chip-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-1) var(--space-2);
  margin-bottom: var(--space-3);
  /* Each list caps and scrolls on its own so the whole tab stays within the
     pane height (no outer/global scrollbar). Tall enough to hold four full
     penalised-tag rows (~28px each + row gaps) without clipping the fourth. */
  max-height: 125px;
  overflow-y: auto;
}

.chip-grid__empty {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-bottom: var(--space-4);
}
</style>
