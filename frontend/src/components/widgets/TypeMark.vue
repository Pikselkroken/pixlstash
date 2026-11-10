<template>
  <!-- A model's Type letter (#2168): the C, L, V, T, U or N the sidebar's
       Type rows, the list, a set card and the rail all draw a model under,
       from `SHELF_TYPES`. One component so the four cannot drift apart. The
       caller sizes it; the default is the row glyph's box. -->
  <i class="type-mark" :data-tint="tint" aria-hidden="true"
    >{{ mark }}<slot
  /></i>
</template>

<script setup>
defineProps({
  /** The letter, `SHELF_TYPES` `mark`; "?" for a file of no known Type. */
  mark: { type: String, required: true },
  /** `tertiary` (checkpoint), `secondary` (LoRA) or `neutral`. */
  tint: { type: String, default: "neutral" },
});
</script>

<style scoped>
.type-mark {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: var(--gutter-glyph);
  height: var(--gutter-glyph);
  border-radius: var(--radius-sm);
  font-style: normal;
  font-size: var(--text-3xs);
  font-weight: var(--weight-bold);
  color: rgb(var(--v-theme-on-tertiary));
  background: rgb(var(--v-theme-tertiary));
}

.type-mark[data-tint="secondary"] {
  color: rgb(var(--v-theme-on-secondary));
  background: rgb(var(--v-theme-secondary));
}

.type-mark[data-tint="neutral"] {
  color: rgb(var(--v-theme-on-surface));
  background: var(--track-trough);
}
</style>
