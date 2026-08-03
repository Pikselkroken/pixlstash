<template>
  <svg
    viewBox="0 0 56 30"
    width="56"
    height="30"
    fill="none"
    aria-hidden="true"
  >
    <!-- One grammar across all three: your machine on the left, pixlstash.dev
         on the right, and what travels between them. The difference between
         the options is the picture, which is the point. No red X anywhere:
         a red cross over a thumbnail reads as "images are broken", and red is
         reserved for genuine warnings elsewhere in the app. -->
    <rect
      x="1.5"
      y="8.5"
      width="17"
      height="13"
      rx="2.5"
      stroke="currentColor"
      stroke-width="1.6"
    />
    <rect
      x="37.5"
      y="8.5"
      width="17"
      height="13"
      rx="2.5"
      stroke="currentColor"
      stroke-width="1.6"
      :stroke-dasharray="variant === 'none' ? '3 3' : undefined"
      :opacity="variant === 'none' ? 0.4 : 1"
    />
    <!-- Nothing crosses the boundary. -->
    <line
      v-if="variant === 'none'"
      x1="28"
      y1="4"
      x2="28"
      y2="26"
      stroke="currentColor"
      stroke-width="1.6"
      stroke-linecap="round"
      opacity="0.45"
    />
    <template v-else>
      <line
        x1="21"
        y1="15"
        x2="33"
        y2="15"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
      />
      <polyline
        points="30,11.5 34,15 30,18.5"
        stroke="currentColor"
        stroke-width="1.6"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </template>
    <!-- The random number, riding along. -->
    <circle
      v-if="variant === 'id' || variant === 'checkid'"
      cx="27"
      cy="6"
      r="3.6"
      fill="currentColor"
    />
  </svg>
</template>

<script setup>
defineProps({
  /** One of "none", "check", "id", "checkid". */
  variant: { type: String, required: true },
});
</script>
