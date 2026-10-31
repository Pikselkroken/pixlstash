<template>
  <!-- The way out of a view that stands in for another (the lightbox, a
       training run's page): a chevron, the name of where it goes, and the Esc
       keycap. The keycap is a promise, so the view that mounts this must bind
       Escape to the same exit. Narrow windows keep the chevron only; the
       aria-label keeps the name when the label is hidden. -->
  <AppBarButton
    icon="chevron-left"
    aria-keyshortcuts="Escape"
    :aria-label="label"
    :tooltip="tooltip"
  >
    <span class="view-back-label">{{ label }}</span>
    <kbd class="bar-btn-kbd" aria-hidden="true">Esc</kbd>
  </AppBarButton>
</template>

<script setup>
import AppBarButton from "./AppBarButton.vue";

defineProps({
  // Where the button goes, named as that view names itself.
  label: { type: String, required: true },
  tooltip: { type: String, required: true },
});
</script>

<style scoped>
@media (max-width: 900px) {
  .view-back-label,
  .bar-btn-kbd {
    display: none;
  }
}
</style>
