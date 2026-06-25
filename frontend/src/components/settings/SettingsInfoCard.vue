<template>
  <div class="info-card">
    <v-icon size="15" class="info-card__icon">mdi-information-outline</v-icon>
    <span class="info-card__text"><slot /></span>
  </div>
</template>

<script setup>
import { VIcon } from "vuetify/components";
</script>

<style scoped>
.info-card {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.6);
  background: rgb(var(--v-theme-input-background));
  border-radius: var(--radius-sm);
  padding: var(--space-3);
  margin-bottom: var(--space-2);
}

.info-card__icon {
  flex-shrink: 0;
  margin-top: 1px;
}
</style>
