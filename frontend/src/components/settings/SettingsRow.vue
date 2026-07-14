<template>
  <div class="s-row" :class="{ 's-row--dim': dim }">
    <div class="s-row__text">
      <div class="s-row__label">{{ label }}</div>
      <div v-if="sub" class="s-row__sub">{{ sub }}</div>
    </div>
    <div class="s-row__control">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  label: { type: String, default: "" },
  sub: { type: String, default: "" },
  // Dim when the row's control is inactive (e.g. a disabled dependent setting).
  dim: { type: Boolean, default: false },
});
</script>

<style scoped>
.s-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
  height: 100%;
}

.s-row--dim {
  opacity: 0.5;
}

.s-row__text {
  min-width: 0;
}

.s-row__label {
  font-weight: var(--weight-semibold);
  font-size: var(--text-sm);
}

.s-row__sub {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: var(--space-1);
  line-height: var(--leading-snug);
}

.s-row__control {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
</style>
