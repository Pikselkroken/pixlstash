<template>
  <div class="field-block">
    <div class="field-block__title">{{ title }}</div>
    <div v-if="desc" class="field-block__desc">{{ desc }}</div>
    <div
      class="field-block__control"
      :class="{ 'field-block__control--top': top }"
    >
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: "" },
  desc: { type: String, default: "" },
  // Align the control to the top of the block instead of pinning it to the
  // bottom — used when two side-by-side blocks must line their content tops up.
  top: { type: Boolean, default: false },
});
</script>

<style scoped>
.field-block {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.field-block__title {
  font-weight: var(--weight-semibold);
  font-size: var(--text-base);
  margin-bottom: var(--space-2);
}

.field-block__desc {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
  margin-bottom: var(--space-3);
}

/* Push the control to the block's bottom so two side-by-side blocks with
   different description lengths still align their controls. */
.field-block__control {
  margin-top: auto;
}

/* Top-align instead — for content (e.g. plugin tables) that must line up at
   the top of both columns rather than the bottom. */
.field-block__control--top {
  margin-top: var(--space-3);
}
</style>
