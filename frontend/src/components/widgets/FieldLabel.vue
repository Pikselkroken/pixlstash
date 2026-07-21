<template>
  <div class="field-label"><slot /></div>
</template>

<script setup></script>

<style scoped>
.field-label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-bottom: var(--space-2);
}
</style>
