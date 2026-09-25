<template>
  <!-- `currentColor`, like AiToolkitIcon: the button it sits in tints it, and
       it follows both themes without a second asset. `aria-hidden`, because
       the button carries the name. -->
  <svg
    :width="size"
    :height="size"
    viewBox="33 32.5 77 77"
    fill="currentColor"
    aria-hidden="true"
    focusable="false"
  >
    <path
      d="M92.2457 90.3796C92.3788 89.9299 92.4514 89.4559 92.4514 88.9576C92.4514 86.1986 90.2243 83.9622 87.4768 83.9622H65.1819C63.9836 83.9744 63.0032 83.0021 63.0032 81.7988C63.0032 81.58 63.0395 81.3734 63.088 81.1789L69.0914 60.1766C69.3455 59.2529 70.1928 58.5723 71.1853 58.5723L93.565 58.548C98.2854 58.548 102.267 55.3514 103.466 50.9881L106.831 39.2837C106.939 38.8704 107 38.4207 107 37.971C107 35.2242 104.785 33 102.05 33H74.9737C70.2775 33 66.3075 36.1722 65.0851 40.4869L62.8096 48.4722C62.5433 49.3838 61.7081 50.0522 60.7156 50.0522H54.216C49.5561 50.0522 45.6224 53.1637 44.3636 57.4298L36.1816 86.1257C36.0605 86.551 36 87.0129 36 87.4748C36 90.2338 38.2271 92.4701 40.9746 92.4701H47.3653C48.5636 92.4701 49.544 93.4424 49.544 94.6579C49.544 94.8645 49.5198 95.0711 49.4593 95.2656L47.1959 103.178C47.0869 103.603 47.0143 104.041 47.0143 104.491C47.0143 107.237 49.2293 109.462 51.9647 109.462L79.0527 109.437C83.761 109.437 87.731 106.253 88.9414 101.914L92.2335 90.3918L92.2457 90.3796Z"
    />
  </svg>
</template>

<script setup>
// The ComfyUI logomark, from Comfy Org's published brand assets ("Logomark"),
// with its fixed brand fill replaced by `currentColor` and the viewBox cropped
// to the mark so it fills a button icon. Referential use: it labels "open this
// in ComfyUI" and claims no endorsement.

defineProps({
  /** Matches the `size` a sibling `<v-icon>` in the same row is given. */
  size: { type: [Number, String], default: 16 },
});
</script>
