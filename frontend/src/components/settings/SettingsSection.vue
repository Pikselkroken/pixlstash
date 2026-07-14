<template>
  <div class="s-section" :class="{ 's-section--first': first }">
    <div v-if="title || $slots.action" class="s-section__head">
      <div v-if="title" class="s-section__title">{{ title }}</div>
      <div v-if="$slots.action" class="s-section__action">
        <slot name="action" />
      </div>
    </div>
    <div v-if="desc" class="s-section__desc">{{ desc }}</div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: "" },
  desc: { type: String, default: "" },
  // The first section in a pane has no top divider.
  first: { type: Boolean, default: false },
});
</script>

<style scoped>
.s-section {
  border-top: 1px solid rgb(var(--v-theme-divider));
  padding: var(--space-3) 0;
}

.s-section--first {
  border-top: none;
  padding: 0 0 var(--space-3);
}

.s-section__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  min-height: 24px;
  margin-bottom: var(--space-2);
}

.s-section__title {
  font-weight: var(--weight-semibold);
  font-size: var(--text-base);
}

.s-section__desc {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
  margin-bottom: var(--space-3);
}
</style>
