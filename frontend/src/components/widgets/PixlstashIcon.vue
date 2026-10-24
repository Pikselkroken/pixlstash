<template>
  <!-- `currentColor`, like ComfyuiIcon: whatever it sits in tints it, and it
       follows both themes without a second asset. `aria-hidden`, because the
       label beside it carries the name. -->
  <span
    class="pixlstash-icon"
    :style="{ width: `${size}px`, height: `${size}px` }"
    aria-hidden="true"
  ></span>
</template>

<script setup>
// The PixlStash logomark as a silhouette in the surrounding ink, as the Tasks
// panel draws it (`.tm-system-mark`): the brand's one mark is a raster, and a
// mask of it is the mark itself rather than a redrawing of it.

defineProps({
  /** Matches the `size` a sibling `<v-icon>` in the same row is given. */
  size: { type: [Number, String], default: 16 },
});
</script>

<style scoped>
.pixlstash-icon {
  display: inline-block;
  flex: none;
  background-color: currentColor;
  -webkit-mask: url("/Logo.png") center / contain no-repeat;
  mask: url("/Logo.png") center / contain no-repeat;
}
</style>
