<template>
  <div class="two-col">
    <slot />
  </div>
</template>

<script setup></script>

<style scoped>
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 var(--space-5);
  align-items: stretch;
}
</style>
