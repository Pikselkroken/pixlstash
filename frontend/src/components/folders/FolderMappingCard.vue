<script setup>
/**
 * The one card the add-a-library flow is told things in: the folder's verdict
 * ("A library you already made"), then the read's progress ("Working out what
 * your folders mean"), then what it found. Each swaps into the same frame
 * with the same type, so the dialog does not visibly change between them.
 */
defineProps({
  title: { type: String, required: true },
  lead: { type: String, default: "" },
  // A refusal: same card, warning border, and the caller offers no actions.
  warn: { type: Boolean, default: false },
});
</script>

<template>
  <div class="mapping-card" :class="{ 'mapping-card--warn': warn }">
    <div class="mapping-card__title">{{ title }}</div>
    <p v-if="lead" class="mapping-card__lead">{{ lead }}</p>
    <div v-if="$slots.default" class="mapping-card__body">
      <slot />
    </div>
    <div v-if="$slots.actions" class="mapping-card__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
/* A flex column with a reserved height and the actions pinned to the bottom,
   so the button row sits at the same y whether the body is the verdict's
   name field or the read's progress bar: the swap between them must not move
   anything. 216px (on the 4px grid) fits the taller of those two; a longer
   body (the finished read's list) grows the card from there. */
.mapping-card {
  display: flex;
  flex-direction: column;
  min-height: 216px;
  padding: var(--space-5);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.mapping-card--warn {
  border-color: rgb(var(--v-theme-warning));
}

.mapping-card__title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.mapping-card__lead {
  margin: var(--space-2) 0 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), 0.72);
}

.mapping-card__body {
  margin-top: var(--space-5);
}

.mapping-card__actions {
  display: flex;
  gap: var(--space-3);
  margin-top: auto;
  padding-top: var(--space-6);
}
</style>
