<script setup>
/**
 * The sidebar tree's disclosure caret: one stroked chevron that turns down
 * when open. Drawn rather than taken from MDI because the MDI chevron shows
 * about 5px of thin stroke in its box and was easy to miss (visual-language.md
 * §5.1). It fills the box its button gives it and takes the button's ink.
 */
defineProps({ open: { type: Boolean, default: false } });
</script>

<template>
  <svg
    :class="['sidebar-caret', { 'sidebar-caret--open': open }]"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
  >
    <path d="M10 7l5 5-5 5" />
  </svg>
</template>

<style scoped>
.sidebar-caret {
  display: block;
  width: 100%;
  height: 100%;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.sidebar-caret--open {
  transform: rotate(90deg);
}
</style>
